import cv2
import os
import json
import shutil
import time
import traceback
import pymysql
import threading
from queue import Queue, Full

from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Event

from DEPENDANT.INFERENCE import MASKRCNN
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger
from DEPENDANT.WS_SHAPE import analyze_polygon_shape, scale_shape, draw_shape

LOCATION = "TALOJA"

# /home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA /home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA
BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/')

MODEL_DIR = os.path.join(BASE_FILE_PATH, "DEBUGS", "MODEL")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
TEMP_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG")
RESULT_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RESULT")
TRASH_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TRASH")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")
LIVE_JSON_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_JSON", "live.json")

# Database configuration
db_user = os.environ.get('db_user', "root")
db_pass = os.environ.get('db_pass', "insightzz@123")
db_host = os.environ.get('db_host', "127.0.0.1")
db_name = os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA")

PADDING = 50

# =========================================================================
# DEBUG / PARALLEL-RUN INSTRUMENTATION
#
#   DEBUG_PARALLEL=1  -> per-thread prints + logs (model checkout, batch
#                        splits, per-frame timings, throughput stats).
#   DEBUG_PARALLEL=0  -> disable ALL of it for max speed. No other change
#                        needed; every dbg()/perf call becomes a no-op.
# =========================================================================
DEBUG_PARALLEL   = os.environ.get('DEBUG_PARALLEL', '1') == '1'
DEBUG_STATS_EVERY = int(os.environ.get('DEBUG_STATS_EVERY', '120'))  # perf line every N frames per thread

def dbg(msg):
    """Parallel-run debug trace: [DBG][thread-name] message -> stdout + log."""
    if not DEBUG_PARALLEL:
        return
    line = f"[DBG][{threading.current_thread().name}] {msg}"
    print(line, flush=True)
    logger.info(line)

# ---- live.json tuning ----
# Faster UI: write every 2s during a live cycle (was 10s), and force an
# immediate write when a defect is found (min 0.5s gap so writes stay cheap).
LIVE_JSON_INTERVAL = float(os.environ.get('LIVE_JSON_INTERVAL', '2'))
LIVE_JSON_MIN_GAP  = float(os.environ.get('LIVE_JSON_MIN_GAP', '0.5'))
# live.json "result" = ALL defect frames since coil start (full coil map)
#                      + the last N frames (recent/live view).
# Clean frames older than the tail are dropped, so write size stays small
# (proportional to defect frames, not total frames).
LIVE_RESULTS_KEEP  = int(os.environ.get('LIVE_RESULTS_KEEP', '50'))   # recent-frames tail
LIVE_FULL_DEFECT_MAP = os.environ.get('LIVE_FULL_DEFECT_MAP', '1') == '1'

# ---- speed / storage tuning ----
DEBUG_IMG_INTERVAL = 2.0  # write <cam>_debug.jpg at most once per this many secs
MQTT_UI_INTERVAL   = 1.0  # publish ui/logic + health at most once per this many secs
SAVE_SCALE   = float(os.environ.get('SAVE_SCALE', '0.8'))  # stored trash copy is downscaled (inference stays full-size)
SAVE_QUALITY = int(os.environ.get('SAVE_QUALITY', '80'))   # JPEG quality of stored trash copy
SAVE_QUEUE_MAX = int(os.environ.get('SAVE_QUEUE_MAX', '1024'))
SAVE_WORKERS = int(os.environ.get('SAVE_WORKERS', '24'))

# ---- background orphan recovery ----
ORPHAN_MAX = int(os.environ.get('ORPHAN_MAX', '3'))   # orphan cycles recovered in parallel
# Orphans now run on their OWN dedicated models (see NUM_MODELS), so they no
# longer need to yield the GPU to the live cycle. Set >0 only if live latency
# ever suffers under heavy backlog drain.
ORPHAN_YIELD_SLEEP = float(os.environ.get('ORPHAN_YIELD_SLEEP', '0'))

# ---- live self-healing (missed TRIGGER protection) ----
# If no MQTT 'start' was seen but a cycle folder in RAW_IMG is ACTIVELY being
# written, adopt it as the live cycle anyway (mqtt start was lost/missed).
# A fallback-adopted live cycle auto-finalizes once the folder goes idle.
LIVE_FALLBACK = os.environ.get('LIVE_FALLBACK', '1') == '1'
LIVE_FALLBACK_ACTIVE_SECS = float(os.environ.get('LIVE_FALLBACK_ACTIVE_SECS', '10'))   # folder written within this = live
LIVE_FALLBACK_STOP_IDLE = float(os.environ.get('LIVE_FALLBACK_STOP_IDLE', '30'))       # fallback live: idle this long = coil done
# Safety net even for MQTT-triggered live cycles: if the STOP message is lost
# and no new images arrive for this long, finalize instead of hanging forever.
LIVE_STALL_TIMEOUT = float(os.environ.get('LIVE_STALL_TIMEOUT', '300'))

# ---- GPU scaling ----
# Total MASKRCNN instances. models[0..1] are reserved for the LIVE cycle
# (original behavior); models[2:] form a checkout pool for orphan recovery.
# 2 models ~ 12 GB observed on the A5000 -> 4 fits 24 GB with FP16 on.
# If CUDA OOM appears in the logs, set NUM_MODELS=3.
NUM_MODELS = int(os.environ.get('NUM_MODELS', '8'))
INFER_FP16 = os.environ.get('INFER_FP16', '1') == '1'   # FP16 autocast on Ampere: ~1.7-2x

# ---- CPU-side pipeline threads (64-core box has plenty of headroom) ----
LOADER_WORKERS = int(os.environ.get('LOADER_WORKERS', '24'))
SAVER_POOL_WORKERS = int(os.environ.get('SAVER_POOL_WORKERS', '24'))

update_lock = Lock()
summary_lock = Lock()
result_lock = Lock()
debug_lock = Lock()
_last_debug_write = {}   # cam_serial -> last debug jpg write time

LIVE_ACTIVE = Event()     # set while a streaming (live) cycle is running
_live_uid_lock = Lock()
_current_live_uid = None  # uid of the coil currently streaming live (or None)
_active_orphans = set()   # cycle_uids currently being recovered in background
_orphan_lock = Lock()

ALLOWED_DEFECTS = {"O", "W", "S", "D", "C"}
DEFECT_CONFIG = {
    "O": {"name": "OIL_STAIN", "color": (255, 0, 0)},      # Blue
    "W": {"name": "WHITE_SPOT",      "color": (128, 128, 128)},      # Gray
    "S": {"name": "SCORING",    "color": (0, 255, 255)},      # Yellow
    "D": {"name": "DENT",   "color": (0, 0, 255)},    # Red
    "C": {"name": "SCRATCH",   "color": (0, 255, 0)},    # Green
}

# White-spot shape gate:
#   "flag" (default) = keep rejected spots, just mark them (current behaviour)
#   "drop"           = a white spot that fails the oval/egg test is discarded
WS_REJECT_MODE = os.environ.get("WS_REJECT_MODE", "flag").lower()

logger = initializeLogger("LOGIC", LOGS_PATH=LOGS_PATH)


# =========================================================================
# DB helpers
# =========================================================================
def getDBconn():
    conn = None
    try:
        conn = pymysql.connect(
            host= db_host,
            user= db_user,
            password= db_pass,
            database= db_name
        )
    except Exception:
        logger.error(f"DB connect failed: {traceback.format_exc()}")

    return conn

def save_defect_history(conn, cycle_uid, coil_id, defect_details, json_path):
    cursor = None

    try:
        cursor = conn.cursor()

        check_query = """
            SELECT 1
            FROM DEFECTS_HISTORY
            WHERE CYCLE_UID = %s
            LIMIT 1
        """

        cursor.execute(check_query, (cycle_uid,))
        record_exists = cursor.fetchone() is not None

        if record_exists:

            update_query = """
                UPDATE DEFECTS_HISTORY
                SET COIL_ID = %s,
                    DEFECT_DETAILS = %s,
                    JSON_PATH = %s,
                    CREATE_TIME = %s
                WHERE CYCLE_UID = %s
            """

            cursor.execute(update_query, (
                coil_id,
                defect_details,
                json_path,
                datetime.now(),
                cycle_uid
            ))

        else:

            insert_query = """
                INSERT INTO DEFECTS_HISTORY
                (CYCLE_UID, COIL_ID, DEFECT_DETAILS, JSON_PATH, CREATE_TIME)
                VALUES (%s, %s, %s, %s, %s)
            """

            cursor.execute(insert_query, (
                cycle_uid,
                coil_id,
                defect_details,
                json_path,
                datetime.now()
            ))

        conn.commit()

    except Exception:
        logger.error(f"save_defect_history failed: {traceback.format_exc()}")

    finally:
        if cursor:
            cursor.close()

def get_coil_id_from_millruns(conn, cycle_uid):
    if conn is None:
        return ""

    cursor = None
    try:
        cursor = conn.cursor()
        query = "SELECT coil_id FROM MILL_RUNS WHERE cycle_uid = %s LIMIT 1"
        cursor.execute(query, (cycle_uid,))
        row = cursor.fetchone()
        if row:
            return row[0]
    except Exception:
        logger.error(f"Failed to fetch coil_id for {cycle_uid}: {traceback.format_exc()}")
    finally:
        if cursor:
            cursor.close()

    return ""


# =========================================================================
# Orphan / active-cycle discovery
# =========================================================================
def _cycle_newest_mtime(cdir):
    """
    Newest write time of a cycle folder, WITHOUT walking every image file.
    On Linux a directory's mtime updates whenever a file is created/renamed
    inside it, so the cam-subdir mtimes are a reliable 'still being written?'
    signal. The old per-file os.walk took minutes on a large backlog and
    stalled the whole main loop.
    Returns (newest_mtime, has_files).
    """
    newest = 0.0
    has_files = False
    try:
        newest = os.path.getmtime(cdir)
        for entry in os.scandir(cdir):
            try:
                newest = max(newest, entry.stat().st_mtime)
                if entry.is_dir():
                    if not has_files:
                        with os.scandir(entry.path) as it:
                            has_files = next(it, None) is not None
                elif entry.is_file():
                    has_files = True
            except OSError:
                continue
    except OSError:
        pass
    return newest, has_files


def find_orphan_cycles(base, idle_secs=30, exclude=None):
    """Return ALL stale raw cycle folders (no writes for idle_secs), minus exclude."""
    exclude = exclude or set()
    found = []
    if not os.path.exists(base):
        return found
    t0 = time.time()
    now = time.time()
    for cycle_uid in sorted(os.listdir(base)):
        if cycle_uid in exclude:
            continue
        cdir = os.path.join(base, cycle_uid)
        if not os.path.isdir(cdir):
            continue
        newest, has_files = _cycle_newest_mtime(cdir)
        if has_files and (now - newest) > idle_secs:
            found.append(cycle_uid)
    scan_secs = time.time() - t0
    if scan_secs > 1.0 or (DEBUG_PARALLEL and found):
        dbg(f"orphan scan took {scan_secs:.2f}s found={found}")
    return found


def find_active_cycle(base, active_secs=LIVE_FALLBACK_ACTIVE_SECS, exclude=None):
    """
    Return the cycle folder currently being WRITTEN (newest write within
    active_secs), or None. Used as a fallback when the MQTT 'start' from
    TRIGGER was lost: an actively-captured coil can never become an orphan
    (it is never idle), so without this it would sit unprocessed for its
    entire run.
    """
    exclude = exclude or set()
    if not os.path.exists(base):
        return None
    now = time.time()
    best, best_m = None, 0.0
    for cycle_uid in os.listdir(base):
        if cycle_uid in exclude:
            continue
        cdir = os.path.join(base, cycle_uid)
        if not os.path.isdir(cdir):
            continue
        newest, has_files = _cycle_newest_mtime(cdir)
        if has_files and (now - newest) <= active_secs and newest > best_m:
            best, best_m = cycle_uid, newest
    return best


# =========================================================================
# Image save pipeline (background, never blocks GPU loop)
# =========================================================================
def save_small_compressed(img, output_path, scale=SAVE_SCALE, quality=SAVE_QUALITY):
    """
    Store a SMALL copy of the frame: downscaled by `scale` and JPEG-compressed.
    Inference always runs on the full-size image; only the stored copy shrinks.
    Runs in a background saver thread so it never blocks the GPU loop.
    """
    try:
        if img is None:
            return False
        if scale and scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        ok, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            with open(output_path, "wb") as f:
                f.write(encoded.tobytes())
            return True
        return False
    except Exception:
        logger.error(f"save_small_compressed failed for {output_path}: {traceback.format_exc()}")
        return False

def save_small_compressed_from_path(src_path, output_path, scale=SAVE_SCALE, quality=SAVE_QUALITY):
    try:
        if not os.path.exists(src_path):
            return False
        img = cv2.imread(src_path)
        if img is None:
            return False
        return save_small_compressed(img, output_path, scale=scale, quality=quality)
    except Exception:
        logger.error(f"save_small_compressed_from_path failed for {src_path}: {traceback.format_exc()}")
        return False


class ImageSaver:
    def __init__(self, workers=SAVE_WORKERS, queue_size=SAVE_QUEUE_MAX):
        self.q = Queue(maxsize=queue_size)
        self.workers = []
        for _ in range(workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self.workers.append(t)

    def enqueue(self, src_path, output_path, scale=SAVE_SCALE, quality=SAVE_QUALITY):
        try:
            self.q.put_nowait((src_path, output_path, scale, quality))
        except Full:
            try:
                self.q.put((src_path, output_path, scale, quality), timeout=0.1)
            except Full:
                logger.warning(f"Save queue full, dropping save for {output_path}")

    def _worker(self):
        while True:
            try:
                src_path, output_path, scale, quality = self.q.get()
                save_small_compressed_from_path(src_path, output_path, scale, quality)
            except Exception:
                logger.error(f"ImageSaver worker error: {traceback.format_exc()}")
            finally:
                try:
                    self.q.task_done()
                except Exception:
                    pass


# module-wide background saver
image_saver = ImageSaver()


# =========================================================================
# Label / drawing helpers
# =========================================================================
def get_sorted_images(folder):
    files = [
        f for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    ]
    return sorted(files, key=lambda x: os.path.getmtime(os.path.join(folder, x)))

def calculate_severity(total):
    if total <= 2:
        return "LOW"
    elif total <= 6:
        return "MEDIUM"
    return "HIGH"

def process_labels(labels, img_shape):
    h, w = img_shape[:2]
    defect_boxes = defaultdict(list)

    for label in labels:
        try:
            score = float(label[0])

            # ✅ FIXED: XY swap
            y1 = int(label[1])
            y2 = int(label[2])
            x1 = int(label[3])
            x2 = int(label[4])

            raw_cls = str(label[5]).upper()

            # polygon from INFERENCE (index 9); None for non-shape classes
            poly = label[9] if len(label) > 9 else None

        except:
            continue

        # normalize + filter 
        if "OIL_STAIN" == raw_cls:
            cls = "O"
        elif "OIL_SPREAD" == raw_cls:
            cls = "O"
        elif "OIL_STAIN_FAINT" == raw_cls:
            cls = "O"
        elif "WHITE_SPOT" == raw_cls:
            cls = "W"
        elif "SCORING" == raw_cls:
            if score < 0.95:
                cls = "O"
            else:
                cls = "S"
        elif "DENT" == raw_cls:
            cls = "D"
        elif "SCRATCH" == raw_cls:
            cls = "C"
        else:
            continue

        if cls not in ALLOWED_DEFECTS:
            continue
        if score < 0.60:
            continue

        # padding
        x1 = max(0, x1 - PADDING)
        y1 = max(0, y1 - PADDING)
        x2 = min(w, x2 + PADDING)
        y2 = min(h, y2 + PADDING)

        box_center_x = (x1 + x2) / 2.0
        # visual frame maps full width to 300 mm (±150mm from center)
        distance_mm = ((box_center_x - (w / 2.0)) / float(w)) * 300.0

        item = {
            "b": [x1, x2, y1, y2],
            "c": int(round(score, 3)*100),
            "pos_mm": round(distance_mm, 2)
        }

        # ---- white spot shape gate: symmetric egg / oval, not overstretched ----
        if cls == "W" and poly:
            shp = analyze_polygon_shape(poly, mm_per_px=300.0 / float(w))
            if shp:
                item["shape"] = shp
                if WS_REJECT_MODE == "drop" and not shp.get("ok", True):
                    continue

        defect_boxes[cls].append(item)

    counts = {k: len(v) for k, v in defect_boxes.items()}
    return defect_boxes, counts


def scale_defect_boxes(defect_boxes, orig_shape, scale):
    if not scale or scale == 1.0:
        return defect_boxes

    orig_h, orig_w = orig_shape[:2]
    new_w = int(round(orig_w * scale))
    new_h = int(round(orig_h * scale))
    scaled = defaultdict(list)

    for cls, items in defect_boxes.items():
        for item in items:
            x1, x2, y1, y2 = item["b"]
            scaled_item = item.copy()
            scaled_item["b"] = [
                max(0, min(new_w, int(round(x1 * scale)))),
                max(0, min(new_w, int(round(x2 * scale)))),
                max(0, min(new_h, int(round(y1 * scale)))),
                max(0, min(new_h, int(round(y2 * scale))))
            ]
            if scaled_item.get("shape"):
                scaled_item["shape"] = scale_shape(scaled_item["shape"], scale)
            scaled[cls].append(scaled_item)

    return scaled


def draw_boxes(img, defect_boxes):

    h, w = img.shape[:2]
    center_x = w // 2

    for cls, items in defect_boxes.items():

        cls_upper = cls.upper()

        config = DEFECT_CONFIG.get(
            cls_upper,
            {"name": cls_upper, "color": (255, 255, 255)}
        )

        color = config["color"]
        defect_name = config["name"]

        for obj in items:

            x1, x2, y1, y2 = obj["b"]
            conf = obj["c"]

            box_center_x = (x1 + x2) / 2

            distance_mm = abs(
                ((box_center_x - center_x) / float(w)) * 300
            )

            side_prefix = "OS" if box_center_x < center_x else "DS"

            label = (
                f"{side_prefix}-{defect_name} "
                f"({conf:.0f}%) "
                f"{distance_mm:.1f}mm"
            )

            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

            # fitted ellipse: yellow = accepted oval/egg, red = rejected
            shp = obj.get("shape")
            if shp:
                draw_shape(img, shp)
                label += f" [{shp.get('shape','?')}"
                if shp.get("maj_mm"):
                    label += f" {shp['maj_mm']}x{shp['min_mm']}mm"
                label += "]"

            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)

            text_y = max(25, y1 - 10)

            cv2.rectangle(img, (x1, text_y - th - 6), (x1 + tw + 10, text_y + 4), color, -1)

            cv2.putText(img, label, (x1 + 5, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    return img

def add_summary(img, counts, severity, distance):

    total = sum(counts.values())
    summary = []

    for key in counts:
        if counts[key]:
            summary.append(f"{key}:{counts[key]}")

    summary_text = " | ".join(summary)

    cv2.putText(img, f"Length : {distance} mm", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, f"SEVERITY : {severity}", (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, f"TOTAL DEFECTS : {total}", (20, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, f"SUMMARY : {summary_text}", (20, 160),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    return img


# =========================================================================
# live.json: in-memory cache, atomic writes. ONLY the live cycle writes it;
# background orphans never touch it.
# =========================================================================
_live_cache = None
_live_cache_lock = Lock()

def reset_live_cache():
    global _live_cache
    with _live_cache_lock:
        _live_cache = None

def update_live_json(cycle_uid=None, coil_id=None, defect_summary=None, results=None, status="processing"):
    """
    live.json = running summary + only the LAST N results, so write cost stays
    constant. State is kept in memory (no disk read-back on every write) and
    written atomically (temp file + os.replace) so the UI never sees a
    half-written file. Full results live only in the final cycle JSON.
    """
    global _live_cache
    try:
        with _live_cache_lock:
            if _live_cache is None:
                _live_cache = {
                    "status": status,
                    "cycle_uid": cycle_uid or "",
                    "coil_length_mm": 12000000,
                    "current_position_mm": 0,
                    "speed_mpm": 0,
                    "total_defects": 0,
                    "total_frames": 0,
                    "total_defective_frames": 0,
                    "start_time": datetime.now().isoformat(),
                    "last_updated": datetime.now().isoformat(),
                    "result": []
                }

            data = _live_cache
            data["status"] = status
            if cycle_uid:
                data["cycle_uid"] = cycle_uid
            if coil_id:
                data["coil_id"] = coil_id

            if defect_summary:
                data["current_position_mm"] = defect_summary.get("distance", data.get("current_position_mm", 0))
                data["total_defects"] = defect_summary.get("total_defects", data.get("total_defects", 0))
                data["total_frames"] = defect_summary.get("total_frames", data.get("total_frames", 0))
                data["total_defective_frames"] = defect_summary.get("total_defective_frames", defect_summary.get("defect_frames", data.get("total_defective_frames", 0)))

            if results is not None:
                with result_lock:
                    if not LIVE_FULL_DEFECT_MAP or LIVE_RESULTS_KEEP <= 0:
                        # legacy behavior: recent tail only (or everything if KEEP<=0)
                        data["result"] = list(results) if LIVE_RESULTS_KEEP <= 0 else list(results[-LIVE_RESULTS_KEEP:])
                    else:
                        # FULL COIL MAP: every defect frame since coil start,
                        # plus the recent tail of the last N frames.
                        recent = list(results[-LIVE_RESULTS_KEEP:])
                        older = results[:-LIVE_RESULTS_KEEP] if len(results) > LIVE_RESULTS_KEEP else []
                        # a frame with defects has a non-empty "t" counts dict
                        defect_frames = [x for x in older if x.get("t")]
                        defect_frames.sort(key=lambda x: x.get("r", 0))
                        data["result"] = defect_frames + recent

            data["last_updated"] = datetime.now().isoformat()

            os.makedirs(os.path.dirname(LIVE_JSON_PATH), exist_ok=True)

            # atomic write: dump to temp file, then swap in
            tmp_path = LIVE_JSON_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_path, LIVE_JSON_PATH)

    except Exception:
        logger.error(f"Failed to update live json: {traceback.format_exc()}")


# =========================================================================
# Core per-image processing loop (runs in worker threads)
# =========================================================================
def process_images(cycle_uid, coil_id, cam_path, image_items, model, cam_serial,
                   defect_summary, mq_obj, results, bg_orphan=False,
                   loader_executor=None, saver_executor=None):
    date_str = datetime.now().strftime('%Y%m%d')
    last_update = datetime.now()
    last_mq = 0.0

    items = list(image_items)
    if not items:
        return results

    model_tag = getattr(model, "tag", "model")
    dbg(f"process_images START | cycle={cycle_uid} cam={cam_serial} "
        f"model={model_tag} frames={len(items)} bg_orphan={bg_orphan}")

    # perf counters (debug only)
    t_thread_start = time.time()
    frames_done = 0
    infer_time_acc = 0.0
    load_wait_acc = 0.0
    stats_window_start = t_thread_start
    stats_window_frames = 0
    debug_counter = 0

    # loader: decodes the NEXT image from disk while the GPU runs the current one
    local_loader = False
    if loader_executor is None:
        loader_executor = ThreadPoolExecutor(max_workers=2)
        local_loader = True

    save_dir = os.path.join(TRASH_PATH, cycle_uid, cam_serial)
    os.makedirs(save_dir, exist_ok=True)

    next_future = loader_executor.submit(cv2.imread, os.path.join(cam_path, items[0][1]))

    try:
        for i, (rank, image_name) in enumerate(items):
            try:
                img_path = os.path.join(cam_path, image_name)

                meta = image_name.split("__")[1].split("_")
                diameter = meta[0]
                eg_setpoint = meta[2]
                distance = meta[4]

                t0 = time.time()
                img = next_future.result() if next_future is not None else cv2.imread(img_path)
                load_wait = time.time() - t0
                # print(img.shape)

                # prefetch the next image immediately
                if i + 1 < len(items):
                    next_future = loader_executor.submit(
                        cv2.imread, os.path.join(cam_path, items[i + 1][1])
                    )
                else:
                    next_future = None

                if img is None:
                    logger.warning(f"Unreadable image skipped: {img_path}")
                    continue

                # bg orphans run on their own dedicated models; only yield if
                # explicitly configured (ORPHAN_YIELD_SLEEP env, default 0)
                if bg_orphan and ORPHAN_YIELD_SLEEP > 0 and LIVE_ACTIVE.is_set():
                    time.sleep(ORPHAN_YIELD_SLEEP)

                # ---- inference: ALWAYS on the full-size image ----
                # per-model lock: threads sharing one model instance stay safe
                t1 = time.time()
                infer_lock = getattr(model, "infer_lock", None)
                if infer_lock is not None:
                    with infer_lock:
                        masked_img, labels = model.run_inference(img)
                else:
                    masked_img, labels = model.run_inference(img)
                infer_time = time.time() - t1

                # ---- perf accounting (debug only) ----
                frames_done += 1
                infer_time_acc += infer_time
                load_wait_acc += load_wait
                stats_window_frames += 1
                if DEBUG_PARALLEL and stats_window_frames >= DEBUG_STATS_EVERY:
                    win_secs = max(1e-6, time.time() - stats_window_start)
                    fps = stats_window_frames / win_secs
                    dbg(f"[PERF] cycle={cycle_uid} cam={cam_serial} model={model_tag} "
                        f"bg={bg_orphan} | {stats_window_frames} frames in {win_secs:.1f}s "
                        f"({fps:.2f} fps) | avg_infer={1000*infer_time_acc/frames_done:.0f}ms "
                        f"avg_load_wait={1000*load_wait_acc/frames_done:.0f}ms "
                        f"done={frames_done}/{len(items)}")
                    stats_window_start = time.time()
                    stats_window_frames = 0

                boxes, counts = process_labels(labels, img.shape)
                total = sum(counts.values())
                severity = calculate_severity(total)

                # if total > 0:
                #     dbg(f"[DEFECT] cycle={cycle_uid} cam={cam_serial} model={model_tag} "
                #         f"rank={rank} img={image_name} counts={dict(counts)} sev={severity}")

                scaled_boxes = scale_defect_boxes(boxes, img.shape, SAVE_SCALE)
                compressed_shape = (
                    int(round(img.shape[0] * SAVE_SCALE)) if SAVE_SCALE and SAVE_SCALE != 1.0 else img.shape[0],
                    int(round(img.shape[1] * SAVE_SCALE)) if SAVE_SCALE and SAVE_SCALE != 1.0 else img.shape[1]
                )

                # ---- counters under lock, publish OUTSIDE the lock ----
                with summary_lock:
                    defect_summary["processed_at"] = datetime.now().isoformat()
                    defect_summary["last_severity"] = severity
                    defect_summary["distance"] = distance
                    if total > 0:
                        defect_summary["O"] += counts.get("O", 0)
                        defect_summary["W"] += counts.get("W", 0)
                        defect_summary["S"] += counts.get("S", 0)
                        defect_summary["D"] += counts.get("D", 0)
                        defect_summary["C"] += counts.get("C", 0)
                        defect_summary["total_defects"] += counts.get("O", 0) + counts.get("W", 0) + counts.get("S", 0) + counts.get("D", 0) + counts.get("C", 0)
                        defect_summary["defect_frames"] = defect_summary.get("defect_frames", 0) + 1
                        defect_summary["total_defective_frames"] = defect_summary.get("total_defective_frames", 0) + 1
                    defect_summary["total_frames"] = defect_summary.get("total_frames", 0) + 1
                    summary_snapshot = dict(defect_summary)

                now_t = time.time()
                if (total > 0 or (now_t - last_mq) > MQTT_UI_INTERVAL):
                    # bg orphans send health only; ui/logic stays reserved for the live cycle
                    if not bg_orphan:
                        mq_obj.publish("ui/logic", summary_snapshot)
                    mq_obj.publish("taloja/health", {"service": "LOGIC"})
                    last_mq = now_t

                # ---- TRASH: small compressed copy, saved in background ----
                save_path = os.path.join(save_dir, image_name)
                # enqueue a path-based save task (worker will read + compress)
                image_saver.enqueue(img_path, save_path)

                # ---- TEMP debug jpg: at most once per DEBUG_IMG_INTERVAL per camera ----
                # bg orphans never touch the debug preview (it belongs to the live cycle)
                write_debug = True
                debug_counter += 1
                if not bg_orphan:
                    with debug_lock:
                        if (now_t - _last_debug_write.get(cam_serial, 0.0)) > DEBUG_IMG_INTERVAL:
                            _last_debug_write[cam_serial] = now_t
                            write_debug = True
                if write_debug and debug_counter>50:
                    debug_counter = 0 
                    annotated = draw_boxes(img.copy(), boxes)
                    annotated = add_summary(annotated, counts, severity, distance)
                    cv2.imwrite(
                        f"{TEMP_PATH}/{LOCATION}/{cam_serial}_debug.jpg",
                        annotated,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                    )

                defect_json = {
                    "r": rank,
                    "d": diameter,
                    "e": eg_setpoint,
                    "l": distance,
                    "s": severity,
                    **scaled_boxes,
                    "t": counts,
                    "img": save_path,
                    "raw_img": save_path,
                    "compressed_width": compressed_shape[1],
                    "compressed_height": compressed_shape[0],
                    "scale": SAVE_SCALE,
                }

                with result_lock:
                    results.append(defect_json)

                # ---- live.json refresh: fast UI ----
                # Interval write every LIVE_JSON_INTERVAL, plus an IMMEDIATE
                # write when a defect is found (min LIVE_JSON_MIN_GAP apart).
                # bg orphans NEVER write live.json - it belongs to the live cycle.
                if not bg_orphan:
                    try:
                        with update_lock:
                            gap = (datetime.now() - last_update).total_seconds()
                            if gap > LIVE_JSON_INTERVAL or (total > 0 and gap > LIVE_JSON_MIN_GAP):
                                update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="inprogress")
                                last_update = datetime.now()
                    except Exception:
                        pass

            except Exception:
                logger.error(f"Error processing {image_name}: {traceback.format_exc()}")

    finally:
        if local_loader:
            loader_executor.shutdown(wait=False)

    total_secs = max(1e-6, time.time() - t_thread_start)
    dbg(f"process_images DONE | cycle={cycle_uid} cam={cam_serial} model={model_tag} "
        f"bg={bg_orphan} | {frames_done} frames in {total_secs:.1f}s "
        f"({frames_done/total_secs:.2f} fps overall)")

    return results


# =========================================================================
# Batch cycle processing (used by background orphan recovery)
# =========================================================================
def process_cycle(cycle_uid, model_1, model_2, mq_obj, bg_orphan=False):
    base_path = os.path.join(RAW_PATH, LOCATION, cycle_uid)
    date_str = cycle_uid.split("_")[1][:8]
    conn = getDBconn()

    try:
        coil_id = get_coil_id_from_millruns(conn, cycle_uid)
    except Exception:
        coil_id = ""

    if not os.path.exists(base_path):
        return

    t_cycle_start = time.time()
    logger.info(f"Processing cycle: {cycle_uid} (bg_orphan={bg_orphan})")
    dbg(f"process_cycle START | cycle={cycle_uid} bg_orphan={bg_orphan} "
        f"models=({getattr(model_1,'tag','?')},{getattr(model_2,'tag','?')})")

    results = []
    defect_summary = {
        "cycle_uid": cycle_uid,
        "coil_id": coil_id,
        "total_frames": 0,
        "total_defects": 0,
        "defect_frames": 0,
        "total_defective_frames": 0,
        "O": 0, "W": 0, "S": 0,
        "D": 0, "C": 0,
    }

    if not bg_orphan:
        reset_live_cache()

    loader_executor = ThreadPoolExecutor(max_workers=LOADER_WORKERS)
    saver_executor = ThreadPoolExecutor(max_workers=SAVER_POOL_WORKERS)
    try:
        for cam_serial in os.listdir(base_path):
            cam_path = os.path.join(base_path, cam_serial)
            image_list = get_sorted_images(cam_path)

            # Split into two halves, preserving original rank (1-based)
            ranked_images = list(enumerate(image_list, start=1))
            first_half = [(rank, img) for rank, img in ranked_images if rank % 2 == 1]
            second_half = [(rank, img) for rank, img in ranked_images if rank % 2 == 0]

            dbg(f"process_cycle SPLIT | cycle={cycle_uid} cam={cam_serial} "
                f"total={len(ranked_images)} odd={len(first_half)}->"
                f"{getattr(model_1,'tag','m1')} even={len(second_half)}->"
                f"{getattr(model_2,'tag','m2')}")

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_1 = executor.submit(process_images, cycle_uid, coil_id, cam_path, first_half,  model_1, cam_serial, defect_summary, mq_obj, results, bg_orphan, loader_executor, saver_executor)
                future_2 = executor.submit(process_images, cycle_uid, coil_id, cam_path, second_half, model_2, cam_serial, defect_summary, mq_obj, results, bg_orphan, loader_executor, saver_executor)

                future_1.result()
                future_2.result()
    finally:
        loader_executor.shutdown(wait=False)
        saver_executor.shutdown(wait=True)

    cycle_secs = time.time() - t_cycle_start
    print(f"Cycle Done: {cycle_uid}")
    logger.info(f"Cycle Done: {cycle_uid}")
    dbg(f"process_cycle DONE | cycle={cycle_uid} bg_orphan={bg_orphan} "
        f"frames={defect_summary.get('total_frames',0)} "
        f"defects={defect_summary.get('total_defects',0)} "
        f"took={cycle_secs:.1f}s "
        f"({defect_summary.get('total_frames',0)/max(1e-6,cycle_secs):.2f} fps)")

    defect_json = {
        "cycle_uid": cycle_uid,
        "coil_id": coil_id,
        "result": results
    }

    json_dir = os.path.join(RESULT_PATH, date_str, cycle_uid)
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, f"{cycle_uid}_defect_data.json")

    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                existing_data = json.load(f)

            if isinstance(existing_data.get("result"), list) and isinstance(results, list):
                existing_data["result"].extend(results)
            else:
                existing_data["result"] = results

            with open(json_path, "w") as f:
                json.dump(existing_data, f)

            logger.info(f"Existing JSON updated: {json_path}")

        except Exception:
            logger.error(f"Failed to update JSON: {traceback.format_exc()}")

    else:
        with open(json_path, "w") as f:
            json.dump(defect_json, f)
        logger.info(f"New JSON created: {json_path}")

    save_defect_history(conn, cycle_uid, coil_id, json.dumps(defect_summary), json_path)

    # bg orphans never write live.json - the live view belongs to the streaming cycle
    if not bg_orphan:
        try:
            update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="completed")
        except Exception:
            pass

    shutil.rmtree(base_path)


# =========================================================================
# Streaming (LIVE) cycle processing - primary path
# =========================================================================
def process_cycle_stream(cycle_uid, model_1, model_2, mq_obj, mqtt_triggered=True):
    """
    Stream-process a cycle while capture is still running.
    Normally starts on TRIGGER 'start' and ends on TRIGGER 'stop'.
    mqtt_triggered=False -> FALLBACK mode: the cycle was adopted because its
    folder was actively being written but no MQTT start was seen (message
    lost). In fallback mode, the coil is finalized once the folder stays
    idle for LIVE_FALLBACK_STOP_IDLE seconds.
    Even in MQTT mode, if the STOP message is lost and no new images arrive
    for LIVE_STALL_TIMEOUT seconds, the coil auto-finalizes instead of
    hanging forever.
    Deletes the raw folder ONLY after stop + final drain.
    Writes live.json fast (LIVE_JSON_INTERVAL + immediate on defects) so the
    UI always shows the LIVE coil; orphan cycles stay invisible in live.json.
    """
    base_path = os.path.join(RAW_PATH, LOCATION, cycle_uid)
    date_str = cycle_uid.split("_")[1][:8]

    conn = getDBconn()
    try:
        coil_id = get_coil_id_from_millruns(conn, cycle_uid)
    except Exception:
        coil_id = ""

    t_cycle_start = time.time()
    mode = "mqtt" if mqtt_triggered else "FALLBACK"
    print(f"Streaming cycle: {cycle_uid} (mode={mode})")
    logger.info(f"Streaming cycle: {cycle_uid} (mode={mode})")
    dbg(f"LIVE stream START | cycle={cycle_uid} mode={mode} "
        f"models=({getattr(model_1,'tag','m1')},{getattr(model_2,'tag','m2')})")

    results = []
    defect_summary = {
        "cycle_uid": cycle_uid,
        "coil_id": coil_id,
        "total_frames": 0,
        "total_defects": 0,
        "defect_frames": 0,
        "total_defective_frames": 0,
        "O": 0, "W": 0, "S": 0,
        "D": 0, "C": 0,
    }

    reset_live_cache()
    # immediate first write so the UI flips to the new coil instantly
    try:
        update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="inprogress")
    except Exception:
        pass

    processed  = defaultdict(set)          # cam_serial -> {processed image names}
    next_rank  = defaultdict(lambda: 1)    # cam_serial -> next rank to assign
    stop_received = False
    last_live = datetime.now()
    last_health = 0.0
    last_new_time = time.time()            # last time we saw fresh images

    # ONE persistent worker pool for the whole cycle (was: new pool per batch)
    executor = ThreadPoolExecutor(max_workers=2)
    loader_executor = ThreadPoolExecutor(max_workers=LOADER_WORKERS)
    saver_executor = ThreadPoolExecutor(max_workers=SAVER_POOL_WORKERS)

    LIVE_ACTIVE.set()
    global _current_live_uid
    with _live_uid_lock:
        _current_live_uid = cycle_uid
    try:
        while True:
            now_t = time.time()
            if now_t - last_health > MQTT_UI_INTERVAL:
                mq_obj.publish("taloja/health", {"service": "LOGIC"})
                last_health = now_t

            # ---- 1) Watch for STOP from TRIGGER ----
            data = mq_obj.data
            if data and data.get("_consumed") is not True:
                if data.get("action") == "stop" and data.get("roll_num") == cycle_uid:
                    mq_obj.data = {**data, "_consumed": True}
                    print(f"STOP received for {cycle_uid}")
                    logger.info(f"STOP received for {cycle_uid}")
                    stop_received = True
                    # let the last image finish writing before the final drain
                    time.sleep(1)
                # a 'start' for a *different* cycle is left un-consumed on purpose,
                # so main() can pick it up after we finish this one.

            # ---- 2) Process any NEW images, per camera ----
            got_new = False
            if os.path.exists(base_path):
                for cam_serial in sorted(os.listdir(base_path)):
                    cam_path = os.path.join(base_path, cam_serial)
                    if not os.path.isdir(cam_path):
                        continue

                    all_imgs = get_sorted_images(cam_path)
                    new_imgs = [f for f in all_imgs if f not in processed[cam_serial]]

                    # While still capturing, skip the newest file (may be mid-write).
                    # After stop, capture has ended, so process everything.
                    if not stop_received and new_imgs:
                        new_imgs = new_imgs[:-1]

                    if not new_imgs:
                        continue
                    got_new = True

                    # Assign continuous ranks, split odd/even across the two models
                    batch, r = [], next_rank[cam_serial]
                    for name in new_imgs:
                        batch.append((r, name))
                        r += 1
                    next_rank[cam_serial] = r

                    first_half  = [(rk, im) for rk, im in batch if rk % 2 == 1]
                    second_half = [(rk, im) for rk, im in batch if rk % 2 == 0]

                    dbg(f"LIVE batch | cycle={cycle_uid} cam={cam_serial} "
                        f"new={len(batch)} odd={len(first_half)}->"
                        f"{getattr(model_1,'tag','m1')} even={len(second_half)}->"
                        f"{getattr(model_2,'tag','m2')}")

                    f1 = executor.submit(process_images, cycle_uid, coil_id, cam_path,
                                         first_half,  model_1, cam_serial,
                                         defect_summary, mq_obj, results,
                                         False, loader_executor, saver_executor)
                    f2 = executor.submit(process_images, cycle_uid, coil_id, cam_path,
                                         second_half, model_2, cam_serial,
                                         defect_summary, mq_obj, results,
                                         False, loader_executor, saver_executor)
                    f1.result()
                    f2.result()

                    for _, name in batch:
                        processed[cam_serial].add(name)

            # ---- 3) Keep live.json fresh during the run ----
            if (datetime.now() - last_live).total_seconds() > LIVE_JSON_INTERVAL:
                try:
                    update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="inprogress")
                except Exception:
                    pass
                last_live = datetime.now()

            # ---- 4) Exit once stopped AND fully drained ----
            if got_new:
                last_new_time = time.time()
            if stop_received and not got_new:
                break
            if not got_new:
                idle_for = time.time() - last_new_time
                if not stop_received:
                    if not mqtt_triggered and idle_for > LIVE_FALLBACK_STOP_IDLE:
                        print(f"Fallback live {cycle_uid}: idle {idle_for:.0f}s - treating as STOP")
                        logger.info(f"Fallback live {cycle_uid}: idle {idle_for:.0f}s - treating as STOP")
                        stop_received = True
                        continue
                    if mqtt_triggered and idle_for > LIVE_STALL_TIMEOUT:
                        logger.warning(f"LIVE {cycle_uid}: no STOP and no new images for "
                                       f"{idle_for:.0f}s - auto-finalizing (lost STOP?)")
                        print(f"LIVE {cycle_uid}: auto-finalizing after {idle_for:.0f}s idle (lost STOP?)")
                        stop_received = True
                        continue
                time.sleep(0.5)

    finally:
        LIVE_ACTIVE.clear()
        with _live_uid_lock:
            _current_live_uid = None
        executor.shutdown(wait=True)
        loader_executor.shutdown(wait=False)
        saver_executor.shutdown(wait=True)

    # ================= FINALIZE (only reached after STOP) =================
    cycle_secs = time.time() - t_cycle_start
    print(f"Cycle Done (stream): {cycle_uid}")
    logger.info(f"Cycle Done (stream): {cycle_uid}")
    dbg(f"LIVE stream DONE | cycle={cycle_uid} "
        f"frames={defect_summary.get('total_frames',0)} "
        f"defects={defect_summary.get('total_defects',0)} "
        f"took={cycle_secs:.1f}s")

    defect_json = {
        "cycle_uid": cycle_uid,
        "coil_id": coil_id,
        "result": results,
    }
    json_dir = os.path.join(RESULT_PATH, date_str, cycle_uid)
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, f"{cycle_uid}_defect_data.json")
    with open(json_path, "w") as f:
        json.dump(defect_json, f)
    logger.info(f"New JSON created: {json_path}")

    save_defect_history(conn, cycle_uid, coil_id, json.dumps(defect_summary), json_path)

    try:
        update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="completed")
    except Exception:
        pass

    # ---- delete raw ONLY after stop + finalize ----
    if os.path.exists(base_path):
        shutil.rmtree(base_path)


# =========================================================================
# Orphan model allocator + background orphan recovery worker
# =========================================================================
class ModelAllocator:
    """
    NON-BLOCKING, least-busy model assignment for orphan recovery.

    Why not an exclusive checkout queue: with 2 spare models a greedy checkout
    lets ONE orphan hold both models while every other orphan blocks -> only
    1 orphan runs at a time. Instead, orphan models are SHARED (each model's
    per-model infer_lock keeps that safe), and every new orphan cycle simply
    gets the two LEAST-LOADED models. Result: all ORPHAN_MAX orphan cycles
    run at the same time, and GPU work stays evenly spread across the pool.

    If NUM_MODELS is raised (e.g. 5), each orphan naturally lands on its own
    model with zero sharing.
    """
    def __init__(self, models):
        self.models = list(models)
        self.usage = {id(m): 0 for m in self.models}
        self.lock = Lock()

    def acquire_pair(self):
        with self.lock:
            ordered = sorted(self.models, key=lambda m: self.usage[id(m)])
            m1 = ordered[0]
            m2 = ordered[1] if len(ordered) > 1 else ordered[0]
            self.usage[id(m1)] += 1
            self.usage[id(m2)] += 1
            return m1, m2

    def release_pair(self, m1, m2):
        with self.lock:
            self.usage[id(m1)] = max(0, self.usage[id(m1)] - 1)
            self.usage[id(m2)] = max(0, self.usage[id(m2)] - 1)

    def snapshot(self):
        """tag -> number of orphan threads currently using that model."""
        with self.lock:
            return {getattr(m, "tag", "?"): self.usage[id(m)] for m in self.models}


def _run_orphan_bg(cycle_uid, orphan_alloc, mq_obj):
    """
    Background orphan recovery worker.

    Gets the two least-busy orphan models (shared, never blocks) so up to
    ORPHAN_MAX orphan cycles ALL run in parallel alongside the live coil.
    Never writes live.json (bg_orphan=True) - the UI only shows the live coil.
    """
    m1, m2 = orphan_alloc.acquire_pair()
    dbg(f"ORPHAN assign | cycle={cycle_uid} "
        f"models=({getattr(m1,'tag','?')},{getattr(m2,'tag','?')}) "
        f"pool_load={orphan_alloc.snapshot()}")

    try:
        process_cycle(cycle_uid, m1, m2, mq_obj, bg_orphan=True)
        logger.info(f"Orphan recovered: {cycle_uid}")
        print(f"Orphan recovered: {cycle_uid}")
    except Exception:
        logger.critical(f"orphan recovery error ({cycle_uid}):\n{traceback.format_exc()}")
    finally:
        orphan_alloc.release_pair(m1, m2)
        with _orphan_lock:
            _active_orphans.discard(cycle_uid)
        dbg(f"ORPHAN release | cycle={cycle_uid} "
            f"pool_load_now={orphan_alloc.snapshot()}")


def orphan_scheduler(base, orphan_alloc, orphan_pool, mq_obj):
    """
    ALWAYS-RUNNING background scheduler thread.

    Previously the orphan scan lived in main()'s loop - but main() blocks
    inside process_cycle_stream for the entire duration of a live coil, so
    with back-to-back coils orphans NEVER got scheduled and taloja3/4 sat
    idle. This thread runs independently: orphans start and run in parallel
    with the live cycle, on their own models.

    Also owns the [STATUS] heartbeat, so the heartbeat keeps printing even
    while main() is busy streaming a live coil. If [STATUS] lines ever stop,
    something is truly stuck.
    """
    logger.info("Orphan scheduler thread started")
    dbg("Orphan scheduler thread started")
    while True:
        try:
            # ---- STATUS heartbeat (debug only) ----
            if DEBUG_PARALLEL:
                with _orphan_lock:
                    active = list(_active_orphans)
                try:
                    raw_cycles = [d for d in os.listdir(base)
                                  if os.path.isdir(os.path.join(base, d))]
                except OSError:
                    raw_cycles = []
                try:
                    mq_snap = dict(mq_obj.data) if isinstance(mq_obj.data, dict) else mq_obj.data
                except Exception:
                    mq_snap = "?"
                with _live_uid_lock:
                    live_uid = _current_live_uid
                dbg(f"[STATUS] live={live_uid} "
                    f"orphans_running={len(active)} {active} "
                    f"orphan_model_load={orphan_alloc.snapshot()} "
                    f"save_queue={image_saver.q.qsize()} "
                    f"raw_cycles={len(raw_cycles)} {raw_cycles[:5]} "
                    f"last_mqtt={mq_snap}")

            # ---- schedule orphans (runs even while a live coil streams) ----
            with _orphan_lock:
                free_slots = ORPHAN_MAX - len(_active_orphans)
                busy = set(_active_orphans)
            with _live_uid_lock:
                if _current_live_uid:
                    busy.add(_current_live_uid)   # never treat the live coil as an orphan

            if free_slots > 0:
                for uid in find_orphan_cycles(base, idle_secs=30, exclude=busy)[:free_slots]:
                    with _orphan_lock:
                        if uid in _active_orphans:
                            continue
                        _active_orphans.add(uid)
                    print(f"Recovering orphan cycle in background: {uid}")
                    logger.info(f"Recovering orphan cycle in background: {uid}")
                    orphan_pool.submit(_run_orphan_bg, uid, orphan_alloc, mq_obj)

        except Exception:
            logger.error(f"orphan scheduler error: {traceback.format_exc()}")

        time.sleep(15)


# =========================================================================
# Main service loop
# =========================================================================
def _build_model(tag):
    return MASKRCNN(
        tag,
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_20AUG"),
        "model_final.pth",
        0.80,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_20AUG/TALOJA_MODEL_20AUG.json"),
        False,
        GPU_ID=0
    )

def main():

    # ---- LIVE models (reserved: never used by orphan recovery) ----
    model_1 = _build_model("taloja1")
    model_2 = _build_model("taloja2")

    # ---- extra models DEDICATED to background orphan recovery ----
    all_models = [model_1, model_2]
    for i in range(3, NUM_MODELS + 1):
        try:
            m = _build_model(f"taloja{i}")
            all_models.append(m)
            logger.info(f"Loaded extra orphan model taloja{i}")
            dbg(f"Loaded extra orphan model taloja{i}")
        except Exception:
            logger.error(f"Could not load model taloja{i} (likely GPU memory): "
                         f"{traceback.format_exc()}")
            break

    # per-model inference locks + FP16 flag
    for m in all_models:
        m.infer_lock = Lock()
        m.use_amp = INFER_FP16

    # orphan pool: models[2:] if available, else fall back to
    # sharing the live models (original 2-model behavior).
    # Shared via infer_lock + least-busy allocation, so ALL ORPHAN_MAX
    # orphan cycles can run concurrently even with only 2 spare models.
    orphan_models = all_models[2:] if len(all_models) > 2 else all_models[:2]
    orphan_alloc = ModelAllocator(orphan_models)

    logger.info(f"Model pool ready: {len(all_models)} total, "
                f"{len(orphan_models)} in orphan pool, "
                f"FP16={INFER_FP16}, DEBUG_PARALLEL={DEBUG_PARALLEL}")
    print(f"Model pool ready: {len(all_models)} total "
          f"(live: {getattr(model_1,'tag','m1')},{getattr(model_2,'tag','m2')} | "
          f"orphan pool: {[getattr(m,'tag','?') for m in orphan_models]}) "
          f"FP16={INFER_FP16} DEBUG_PARALLEL={DEBUG_PARALLEL}", flush=True)

    mq_obj = MQTT("capture_monitor")
    mq_obj.subscribe("taloja/capture")      # <-- receive TRIGGER start/stop

    base = os.path.join(RAW_PATH, LOCATION)

    # background pool: up to ORPHAN_MAX orphan cycles recover in parallel.
    # Scheduling happens in a DEDICATED daemon thread so orphans keep getting
    # scheduled even while main() is blocked streaming a live coil.
    orphan_pool = ThreadPoolExecutor(max_workers=ORPHAN_MAX,
                                     thread_name_prefix="orphan")
    threading.Thread(target=orphan_scheduler,
                     args=(base, orphan_alloc, orphan_pool, mq_obj),
                     name="orphan_scheduler", daemon=True).start()

    print("LOGIC service started, waiting for triggers...")
    logger.info("LOGIC service started, waiting for triggers")

    last_idle_pub = 0.0

    while True:
        now_t = time.time()

        # idle heartbeats: once per second is plenty
        if now_t - last_idle_pub > 1.0:
            mq_obj.publish("taloja/health", {"service": "LOGIC"})
            mq_obj.publish("main/logic", {"roll_num": "--"})
            last_idle_pub = now_t

        # ---- 1) Trigger-driven streaming (primary path) ----
        data = mq_obj.data
        if data and data.get("_consumed") is not True:
            action   = data.get("action", "")
            roll_num = data.get("roll_num", "")
            if action == "start" and roll_num:
                mq_obj.data = {**data, "_consumed": True}
                print(f"START received for {roll_num}")
                logger.info(f"START received for {roll_num}")
                try:
                    process_cycle_stream(roll_num, model_1, model_2, mq_obj)
                except Exception:
                    logger.critical(f"process_cycle_stream error:\n{traceback.format_exc()}")
                continue

        # ---- 1b) FALLBACK live adoption: if TRIGGER's start was lost but a
        # cycle folder is ACTIVELY being written, stream it anyway. Without
        # this, a coil with a missed start sits unprocessed for its whole run
        # (an actively-written folder can never become an orphan).
        if LIVE_FALLBACK and not LIVE_ACTIVE.is_set():
            with _orphan_lock:
                busy = set(_active_orphans)
            active_uid = find_active_cycle(base, exclude=busy)
            if active_uid:
                logger.warning(f"No MQTT start seen but {active_uid} is being written - "
                               f"adopting as LIVE (fallback)")
                print(f"Adopting actively-written cycle as LIVE (fallback): {active_uid}")
                try:
                    process_cycle_stream(active_uid, model_1, model_2, mq_obj,
                                         mqtt_triggered=False)
                except Exception:
                    logger.critical(f"fallback stream error:\n{traceback.format_exc()}")
                continue

        # ---- orphan recovery is handled by the orphan_scheduler thread ----
        # (it runs continuously, even while a live coil is streaming above)

        time.sleep(0.2)


if __name__ == "__main__":

    while True:
        try:
            main()
        except Exception:
            logger.critical(f"main() crashed:\n{traceback.format_exc()}")
            print("Error in main() ! Restarting in 5 secs")
            time.sleep(5)