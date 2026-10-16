import cv2
import gc
import os
import pymysql
import time
import threading
import traceback
import queue

from datetime import datetime

from DEPENDANT.FLIR import FlirCamera, PySpin
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

# ---------------------------------------------------------------------------
# OpenCV threading
# ---------------------------------------------------------------------------
# One OpenCV thread per call. We parallelise with our own saver threads, so
# letting OpenCV also fan out across 64 cores on every cvtColor/imencode just
# causes thread thrashing - it was showing up as all 64 cores at 20-60% load
# for a workload that genuinely needs ~1.5 cores. This LOWERS total CPU.
# ---------------------------------------------------------------------------
cv2.setNumThreads(1)

LOCATION = "TALOJA"
CAMERA_CONFIGS = {
    "24521939": {
        # ------------------------------------------------------------------
        # EXPOSURE controls motion blur. FRAME_RATE does not.
        # At 30 fps the frame period is 33333 us, so even a 5 us exposure has
        # zero effect on achievable fps - the only cost of going shorter is light.
        #
        # Rim speed = pi * D * RPM / 60.  Worst case D=1400mm @ 1000rpm = 73.3 m/s.
        # Blur distance = rim speed * exposure:
        #     300 us -> 22.0 mm   <-- current setting
        #      50 us ->  3.67 mm
        #      20 us ->  1.47 mm
        #      10 us ->  0.73 mm
        #       5 us ->  0.37 mm
        #
        # STEP DOWN GRADUALLY and add light/gain at each step. Dropping straight
        # to 5 us with the present lighting gives a black frame.
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # EXPOSURE and GAIN MUST BE CHANGED TOGETHER.
        # Cutting exposure alone makes the image proportionally darker.
        # Each row below holds the SAME brightness as the original 300us/0dB:
        #
        #   EXPOSURE   GAIN     blur @20 m/s   blur @31.4 m/s   noise
        #     300 us   0.0 dB      6.00 mm         9.42 mm      baseline
        #     150 us   6.0 dB      3.00 mm         4.71 mm      1.4x
        #     100 us   9.5 dB      2.00 mm         3.14 mm      1.7x
        #      75 us  12.0 dB      1.50 mm         2.35 mm      2.0x
        #      50 us  15.6 dB      1.00 mm         1.57 mm      2.4x
        #
        # Move down ONE ROW AT A TIME and check p99.9 stays near its old value.
        # ------------------------------------------------------------------
        "EXPOSURE": 250.0,          # paired with GAIN below - do not change alone
        "GAIN": 19.9,                # dB. 6=2x, 9.5=3x, 12=4x, 15.6=6x, 19.9=~9.9x (was 350us @ 17.0dB)
        "GAMMA_ENABLE": False,      # linear response for inspection
        # ------------------------------------------------------------------
        # BINNING MUST STAY 1/1.
        # BIN_V=2 halves the frame to 2448x1024 and destroys fine surface
        # texture (observed: 608 kB -> 140 kB per frame). It also breaks the
        # Detectron2 input geometry, the mm/px scale and the defect box
        # coordinates in LOGIC.py. Do not enable without re-validating all three.
        # ------------------------------------------------------------------
        "BIN_H": 1,
        "BIN_V": 1,                 # native 2448x2048 - do not change

        # Strobe: fires the LED driver only during exposure. Set to your wired
        # line ("Line1" opto-isolated / "Line2" GPIO - check your model pinout).
        # Leave as None until the strobe controller is physically connected.
        "STROBE_LINE": None,        # e.g. "Line2"
        "STROBE_SOURCE": "ExposureActive",
        "STROBE_INVERT": False,

        # 30 fps: matches what SINGLE_LIVE_LOGIC can infer in real time
        # (was 70.0). Each frame now covers ~2.3x more strip length.
        "FRAME_RATE": 30.0,
        "PIXEL_FORMAT": "BayerRG8",
        "PACKET_SIZE": 7000,
        "PACKET_DELAY": 10,
        "THROUGHPUT": 700000000,
        "BUFFER_MODE": "OldestFirst",   # never silently drop frames
        "BUFFER_COUNT": 300,            # ~1.5 GB pinned, ~10 s slack @ 30 fps
    },
}

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

TEMP_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

# Database configuration
db_user = os.environ.get('db_user', "root")
db_pass = os.environ.get('db_pass', "insightzz@123")
db_host = os.environ.get('db_host', "127.0.0.1")
db_name = os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA")

# ---------------------------------------------------------------------------
# Capture throughput tuning (sustained 30 fps, see FRAME_RATE)
# ---------------------------------------------------------------------------
# JPEG_QUALITY 95 is EXACTLY what cv2.imwrite() used before, so stored image
# quality is bit-for-bit unchanged by this refactor.
#
# SAVE_QUEUE_MAX is a hard RAM ceiling: 512 frames x ~5 MB = ~2.5 GB. The old
# maxsize=200_000_000 was effectively unbounded and would have OOM'd the box.
# ---------------------------------------------------------------------------
SAVE_WORKERS    = int(os.environ.get('CAPTURE_SAVE_WORKERS', '8'))
SAVE_QUEUE_MAX  = int(os.environ.get('CAPTURE_SAVE_QUEUE', '512'))
JPEG_QUALITY    = int(os.environ.get('CAPTURE_JPEG_QUALITY', '95'))
HEALTH_INTERVAL = float(os.environ.get('CAPTURE_HEALTH_INTERVAL', '0.5'))
DRAIN_TIMEOUT   = float(os.environ.get('CAPTURE_DRAIN_TIMEOUT', '60'))

logger = initializeLogger("CAPTURE", LOGS_PATH)

# ---- saver pipeline state -------------------------------------------------
save_queue = queue.Queue(maxsize=SAVE_QUEUE_MAX)
_saver_threads = []
_saver_stop = threading.Event()
_dropped = 0
_saved = 0
_stats_lock = threading.Lock()


def init_cameras(flir, serial_number, result_dict, logger):
    try:
        status, err = flir.initialize(serial_number)
        if not status:
            result_dict[serial_number] = (False, err)
            return
        result_dict[serial_number] = (True, None)
    except Exception as e:
        result_dict[serial_number] = (False, f"exception during init: {e}\n{traceback.format_exc()}")


# =========================================================================
# Background saver pipeline
# =========================================================================
# The acquisition loop now does ONLY: GetNextImage -> memcpy -> enqueue.
# Demosaic (cvtColor) and JPEG encode happen here, in parallel worker threads.
# cv2 releases the GIL during both, so N workers scale across N cores.
# =========================================================================
def saver_worker(flir, logger):
    global _saved
    while not _saver_stop.is_set():
        try:
            item = save_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if item is None:                 # shutdown sentinel
            save_queue.task_done()
            break

        path, arr, pf = item
        try:
            img = flir.convert_array(arr, pf)
            if img is None:
                logger.error(f"convert_array returned None for {path}")
            else:
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                if ok:
                    with open(path, "wb") as f:
                        f.write(buf.tobytes())
                    with _stats_lock:
                        _saved += 1
                else:
                    logger.error(f"imencode failed for {path}")
        except Exception:
            logger.error(f"Saver worker failed for {path}: {traceback.format_exc()}")
        finally:
            save_queue.task_done()


def start_savers(flir, logger):
    """Spawn the saver pool. Safe to call once per process_capture()."""
    stop_savers(logger, drain=False)          # clear any pool from a prior run
    _saver_stop.clear()
    for i in range(SAVE_WORKERS):
        t = threading.Thread(
            target=saver_worker,
            args=(flir, logger),
            name=f"saver-{i + 1}",
            daemon=True,
        )
        t.start()
        _saver_threads.append(t)
    logger.info(f"Started {SAVE_WORKERS} saver threads (queue max {SAVE_QUEUE_MAX}, jpeg q{JPEG_QUALITY})")


def drain_savers(logger, timeout=DRAIN_TIMEOUT):
    """Block until every queued frame has been written, or timeout."""
    deadline = time.time() + timeout
    while not save_queue.empty() and time.time() < deadline:
        time.sleep(0.05)
    remaining = save_queue.qsize()
    if remaining:
        logger.warning(f"Drain timed out with {remaining} frames still queued")
    else:
        logger.info("Save queue drained")
    return remaining


def stop_savers(logger, drain=True):
    if drain and _saver_threads:
        drain_savers(logger)
    _saver_stop.set()
    for _ in list(_saver_threads):
        try:
            save_queue.put_nowait(None)
        except queue.Full:
            pass
    for t in list(_saver_threads):
        try:
            t.join(timeout=2)
        except Exception:
            pass
    _saver_threads.clear()
    if logger:
        logger.info(f"Savers stopped. Queue remaining: {save_queue.qsize()}")


def enqueue_save(path, arr, pf, logger):
    """
    Hand a raw frame to the saver pool.

    Bounded on purpose. If the disk genuinely cannot keep up we drop and shout
    loudly rather than growing RAM until the server dies. The short timeout
    gives a little elasticity for momentary write stalls.
    """
    global _dropped
    try:
        save_queue.put((path, arr, pf), timeout=0.05)
        return True
    except queue.Full:
        with _stats_lock:
            _dropped += 1
            n = _dropped
        if n % 50 == 1:
            logger.error(
                f"SAVE QUEUE FULL - {n} frames dropped so far. "
                f"Disk cannot sustain the camera frame rate with {SAVE_WORKERS} workers. "
                f"Check that {RAW_PATH} is on NVMe/SSD."
            )
        return False


def publish_health(mq_obj, last_health):
    """Throttled health beat. Carries backpressure telemetry to HEALTH_MONITOR."""
    now_t = time.time()
    if (now_t - last_health) < HEALTH_INTERVAL:
        return last_health
    with _stats_lock:
        saved, dropped = _saved, _dropped
    mq_obj.publish("taloja/health", {
        "service": "CAPTURE",
        "qsize": save_queue.qsize(),
        "saved": saved,
        "dropped": dropped,
    })
    return now_t


# =========================================================================
# Capture
# =========================================================================
def process_capture(flir, mq_obj, logger):

    cam_objects = {}
    retries = {}
    MAX_RETRIES = 10
    counter = 0
    current_roll_num = ""
    last_health = 0.0

    for cam_serial, config in CAMERA_CONFIGS.items():
        retries[cam_serial] = 0
        cam_objects[cam_serial], err = flir.start_acquisition(cam_serial)
        if err:
            logger.error(f"Failed to start acquisition for camera {cam_serial}: {err}")
            return

    start_savers(flir, logger)

    try:
        while True:
            try:

                data = mq_obj.data  # Live model data
                # data = {"action": "start", "roll_num": f"TEST_ROLL_{datetime.now().strftime('%Y%m%d%H%M%S')}", "diameter": 570, "mill_speed": 0, "eg_setpoint": 0, "width": 0, "length": 0} # Live data collection
                last_health = publish_health(mq_obj, last_health)

                if data and data.get("_consumed") is not True:
                    action = data.get("action", "")
                    roll_num = data.get("roll_num", "")
                    mq_obj.data = {**data, "_consumed": True}

                    if action == "start":
                        current_roll_num = roll_num
                        logger.info(f"Received start signal for roll {roll_num}, starting acquisition loop")

                        # One folder PER CAMERA. The old code built a single
                        # save_path from whatever cam_serial happened to leak
                        # out of an earlier loop - fine with one camera, silently
                        # wrong the moment a second one is added.
                        save_paths = {}
                        for cs in CAMERA_CONFIGS:
                            p = os.path.join(RAW_PATH, LOCATION, roll_num, cs)
                            os.makedirs(p, exist_ok=True)
                            save_paths[cs] = p

                        # per-roll counters
                        loop_start = time.time()
                        with _stats_lock:
                            saved_at_start = _saved
                            dropped_at_start = _dropped

                        while True:
                            counter += 1

                            data = mq_obj.data
                            last_health = publish_health(mq_obj, last_health)

                            if data and data.get("_consumed") is not True:
                                action = data.get("action", "")
                                roll_num = data.get("roll_num", roll_num)

                                diameter = data.get("diameter", 570)
                                mill_speed = data.get("mill_speed", 0)
                                eg_setpoint = data.get("eg_setpoint", 0)
                                width = data.get("width", 0)
                                length = data.get("length", 0)
                            else:
                                diameter = data.get("diameter", 570)
                                mill_speed = data.get("mill_speed", 0)
                                eg_setpoint = data.get("eg_setpoint", 0)
                                width = data.get("width", 0)
                                length = data.get("length", 0)

                            if action == "stop" and roll_num == current_roll_num:
                                counter = 0
                                logger.info("Received stop signal, draining save queue before exit")
                                drain_savers(logger)

                                elapsed = max(1e-6, time.time() - loop_start)
                                with _stats_lock:
                                    saved_n = _saved - saved_at_start
                                    dropped_n = _dropped - dropped_at_start
                                logger.info(
                                    f"Roll {current_roll_num} finished: {saved_n} frames in "
                                    f"{elapsed:.1f}s = {saved_n / elapsed:.1f} fps, dropped={dropped_n}"
                                )
                                print(f"Roll {current_roll_num}: {saved_n / elapsed:.1f} fps, dropped={dropped_n}")

                                logger.info("Ending acquisition loop")
                                break

                            for cam_serial, config in CAMERA_CONFIGS.items():
                                cam = cam_objects[cam_serial]

                                # FAST PATH: grab + memcpy only (~1 ms).
                                arr, pf, err = flir.capture_raw(cam)
                                if err:
                                    if retries[cam_serial] < MAX_RETRIES:
                                        retries[cam_serial] += 1
                                        print(f"{err}")
                                        logger.error(f"Capture error for camera {cam_serial}: {err}")
                                        continue
                                    else:
                                        flir.end_acquisition(cam_serial)
                                        logger.critical(
                                            f"Max retries reached for camera {cam_serial}. Restarting full service...")

                                        # Stop all cameras cleanly before restart
                                        for cs in list(cam_objects.keys()):
                                            try: flir.end_acquisition(cs)
                                            except Exception: pass
                                            try: flir.deinitialize(cs)
                                            except Exception: pass

                                        # Raise exception so main() restarts
                                        raise RuntimeError("Force restart due to max retries")

                                ts = datetime.now()
                                fname = (
                                    f"{roll_num}__{diameter}_{mill_speed}_{eg_setpoint}"
                                    f"_{width}_{length}__{counter}__{ts:%Y%m%d_%H%M%S_%f}.jpg"
                                )
                                enqueue_save(os.path.join(save_paths[cam_serial], fname), arr, pf, logger)
                                retries[cam_serial] = 0

                else:
                    # -------------------------------------------------------
                    # IDLE / trigger-wait preview. ~0.5 fps, so the original
                    # inline convert + imwrite path is kept exactly as it was.
                    # -------------------------------------------------------
                    save_path = os.path.join(TEMP_PATH, LOCATION)
                    os.makedirs(save_path, exist_ok=True)

                    for cam_serial, config in CAMERA_CONFIGS.items():

                        fname = f"{cam_serial}.jpg"
                        comp_path = os.path.join(save_path, fname)
                        cam = cam_objects[cam_serial]

                        img, err = flir.capture(cam)
                        if err:
                            # BUGFIX: was `if  [cam_serial] < MAX_RETRIES:`
                            if retries[cam_serial] < MAX_RETRIES:
                                retries[cam_serial] += 1
                                print(f"{err}")
                                logger.error(f"Capture error for camera {cam_serial}: {err}")
                                continue
                            else:
                                flir.end_acquisition(cam_serial)
                                logger.critical(
                                    f"Max retries reached for camera {cam_serial}. Restarting full service...")

                                # Stop all cameras cleanly before restart
                                for cs in list(cam_objects.keys()):
                                    try: flir.end_acquisition(cs)
                                    except Exception: pass
                                    try: flir.deinitialize(cs)
                                    except Exception: pass

                                # Raise exception so main() restarts
                                raise RuntimeError("Force restart due to max retries")

                        cv2.imwrite(comp_path, img)
                        retries[cam_serial] = 0
                        print(f"Waiting for the trigger       - {datetime.now()}")
                        time.sleep(2)

            except Exception as e:
                print(f"process_capture() Exception: {e}")
                logger.error(traceback.format_exc())
                raise
    finally:
        # Never lose the tail of a coil because of a crash/restart.
        stop_savers(logger)


def main():
    mq_obj = MQTT("DATA_CAPTURE")
    mq_obj.subscribe("taloja/capture")
    logger.info("Starting main capture manager")

    while True:

        try:
            print("Initializing FLIR cameras...")
            flir = FlirCamera()

            for cam_serial, config in CAMERA_CONFIGS.items():
                print(f"Setting config for camera {cam_serial}")
                logger.debug(f"Setting config for camera {cam_serial}")
                flir.set_config(cam_serial, config)

            init_results = {}
            for cam_serial, config in CAMERA_CONFIGS.items():
                init_cameras(flir, cam_serial, init_results, logger)

            working_cams = [s for s, res in init_results.items() if res[0]]
            print(f"Camera initialization results: {working_cams}")
            logger.info(f"Camera initialization results: {working_cams}")

            if len(working_cams) == len(CAMERA_CONFIGS):
                logger.info("All cameras initialized successfully in main")
            else:
                logger.warning("Some cameras failed to initialize in main")
                for cam_serial, config in CAMERA_CONFIGS.items():
                    d_status, d_err = flir.deinitialize(cam_serial)
                    if d_status:
                        logger.error(f"Deinitialize camera {cam_serial}: {d_err}")
                    else:
                        logger.error(f"Failed to deinitialize camera {cam_serial}: {d_err}")

                del flir
                gc.collect()
                time.sleep(2)
                continue

            # Start capture process
            try:
                process_capture(flir, mq_obj, logger)
            except Exception as e:
                logger.error(f"Capture aborted: {e}")
            finally:
                try:
                    flir.release_all()
                    logger.info("Released all cameras")
                except Exception as ee:
                    logger.error(f"release_all failed: {ee}")

                del flir
                gc.collect()
                print("Restarting capture in 5 seconds...")
                logger.warning("Restarting capture in 5 seconds...")
                time.sleep(5)
            continue

        except Exception as e:
            print(f"Outer loop {e}")
            logger.error(traceback.format_exc())


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception:
            print("Error in main() !")
            logger.critical(f"{traceback.format_exc()}")
            print("Restarting in 5 secs")
            time.sleep(5)