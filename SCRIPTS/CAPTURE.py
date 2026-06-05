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

LOCATION = "TALOJA"
CAMERA_CONFIGS = {
    "24521939": {
        "EXPOSURE": 2000.0,
        "FRAME_RATE": 122.0,
        "PACKET_SIZE": 7000,
        "PACKET_DELAY": 10,
        "THROUGHPUT": 700000000,
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

# save_queue = queue.Queue(maxsize=200_000_000)
# max_threads = 20
# active_savers = []
logger = initializeLogger("CAPTURE", LOGS_PATH)

def init_cameras(flir, serial_number, result_dict, mq_obj):
    try:
        status, err = flir.initialize(serial_number)
        if not status:
            result_dict[serial_number] = (False, err)
            return
        result_dict[serial_number] = (True, None)
    except Exception as e:
        mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Inactive", "error": f"{e}"})
        result_dict[serial_number] = (False, f"exception during init: {e}\n{traceback.format_exc()}")

def process_capture(flir, mq_obj, logger):

    cam_objects = {}
    retries = {}
    MAX_RETRIES = 10
    counter = 0
    
    # saver = threading.Thread(target=queue_monitor, args=(logger, flir), daemon=True)
    # saver.start()

    for cam_serial, config in CAMERA_CONFIGS.items():
        retries[cam_serial] = 0
        cam_objects[cam_serial], err = flir.start_acquisition(cam_serial)
        if err:
            logger.error(f"Failed to start acquisition for camera {cam_serial}: {err}")
            return
    
    while True:
        try:

            data = mq_obj.pop("taloja/capture")
            if data:
                action = data.get("action", "")
                roll_num = data.get("roll_num", "")

                if action == "start":
                    current_roll_num = roll_num
                    logger.info(f"Received start signal for roll {roll_num}, starting acquisition loop")
                    save_path = os.path.join(RAW_PATH, LOCATION, roll_num, cam_serial)
                    os.makedirs(save_path, exist_ok=True)

                    while True:
                        counter += 1

                        data = mq_obj.pop("taloja/capture")
                        if data:
                            action = data.get("action", "")
                            roll_num = data.get("roll_num", roll_num)
                            diameter = data.get("diameter", 570)
                            mill_speed = data.get("mill_speed", 0)
                            eg_setpoint = data.get("eg_setpoint", 0)
                            width = data.get("width", 0)
                            length = data.get("length", 0)
                        else:
                            diameter = 570
                            mill_speed = 0
                            eg_setpoint = 0
                            width = 0
                            length = 0

                        if action == "stop" and roll_num == current_roll_num:
                            counter = 0
                            logger.info("Received stop signal, ending acquisition loop")
                            break

                        for cam_serial, config in CAMERA_CONFIGS.items():
                            cam = cam_objects[cam_serial]

                            img, err = flir.capture(cam)
                            if err:
                                if retries[cam_serial] < MAX_RETRIES:
                                    retries[cam_serial] += 1
                                    print(f"{err}")
                                    logger.error(f"Capture error for camera {cam_serial}: {err}")
                                    continue
                                else:
                                    flir.end_acquisition(cam_serial)
                                    logger.critical(f"Max retries reached for camera {cam_serial}. Restarting full service...")

                                    # Stop all cameras cleanly before restart
                                    for cs in list(cam_objects.keys()):
                                        try: flir.end_acquisition(cs)
                                        except: pass
                                        try: flir.deinitialize(cs)
                                        except: pass

                                    # Raise exception so main() restarts
                                    raise RuntimeError("Force restart due to max retries")

                            cv2.imwrite(f"{save_path}/{roll_num}__{diameter}_{mill_speed}_{eg_setpoint}_{width}_{length}__{counter}__{datetime.now():%Y%m%d_%H%M%S_%f}.jpg", img)
                            if counter%100 == 0: 
                                cv2.imwrite(f"{TEMP_PATH}/{LOCATION}/{cam_serial}.jpg", img)
                                mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Active"})
                            # save_queue.put((os.path.join(save_path, f"{roll_num}__{diameter}_{mill_speed}_{eg_setpoint}_{width}_{length}__{counter}__{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"), img))
                            retries[cam_serial] = 0

            else:
                save_path = os.path.join(TEMP_PATH, LOCATION)
                os.makedirs(save_path, exist_ok=True)

                for cam_serial, config in CAMERA_CONFIGS.items():

                    fname = f"{cam_serial}.jpg"
                    comp_path = os.path.join(save_path, fname)
                    cam = cam_objects[cam_serial]

                    img, err = flir.capture(cam)
                    if err:
                        if  [cam_serial] < MAX_RETRIES:
                            retries[cam_serial] += 1
                            print(f"{err}")
                            logger.error(f"Capture error for camera {cam_serial}: {err}")
                            continue
                        else:
                            flir.end_acquisition(cam_serial)
                            logger.critical(f"Max retries reached for camera {cam_serial}. Restarting full service...")

                            # Stop all cameras cleanly before restart
                            for cs in list(cam_objects.keys()):
                                try: flir.end_acquisition(cs)
                                except: pass
                                try: flir.deinitialize(cs)
                                except: pass

                            # Raise exception so main() restarts
                            raise RuntimeError("Force restart due to max retries")
                        
                    cv2.imwrite(comp_path, img)
                    retries[cam_serial] = 0
                    print("Waiting for the trigger")
                    mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Active"})
                    time.sleep(2)

        except Exception as e:
            mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Inactive", "error": f"{e}"})
            print(f"process_capture() Exception: {e}")
            logger.error(traceback.format_exc())
            raise
        
def main():
    mq_obj = MQTT("DATA_CAPTURE")
    mq_obj.subscribe("taloja/capture")
    logger.info("Starting main capture manager")

    while True:

        try:

            mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Initialising"})

            print("Initializing FLIR cameras...") 
            flir = FlirCamera()
            
            for cam_serial, config in CAMERA_CONFIGS.items():
                print(f"Setting config for camera {cam_serial}")
                logger.debug(f"Setting config for camera {cam_serial}")
                flir.set_config(cam_serial, config)

            init_results = {}
            for cam_serial, config in CAMERA_CONFIGS.items():
                init_cameras(flir, cam_serial, init_results, mq_obj)

            working_cams = [s for s, res in init_results.items() if res[0]]
            print(f"Camera initialization results: {working_cams}")
            logger.info(f"Camera initialization results: {working_cams}")

            if len(working_cams) == len(CAMERA_CONFIGS):
                logger.info("All cameras initialized successfully in main")
            else:
                logger.warning("Some cameras failed to initialize in main")
                for cam_serial, config in CAMERA_CONFIGS.items():
                    d_status, d_err = flir.deinitialize(cam_serial)
                    if d_status: logger.error(f"Deinitialize camera {cam_serial}: {d_err}")
                    else: logger.error(f"Failed to deinitialize camera {cam_serial}: {d_err}")

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
            mq_obj.publish("taloja/health_check", {"module": "cam", "status": "Inactive", "error": f"{e}"})
            print(f"Outer loop error: {e}")
            logger.error(traceback.format_exc())

        # time.sleep(2)

if __name__ == "__main__":
    while True:
        try: main()
        except:
            print("Error in main() !")
            logger.critical(f"{traceback.format_exc()}")
            print("Restarting in 5 secs")
            time.sleep(5)
            
    # import cProfile
    # import pstats

    # # Profile and save to a file
    # cProfile.run("main()", "/home/insightzz/insightzz/ANODE_TRACKING_LAPANGA/SCRIPTS/VIZ/CAPTURE_ABF.prof")
