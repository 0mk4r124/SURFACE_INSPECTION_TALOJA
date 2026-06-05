import cv2
import os
import json
import shutil
import time
import traceback
import pymysql

from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from DEPENDANT.INFERENCE import MASKRCNN
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

LOCATION = "TALOJA"

# /home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA /home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA
BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/')

MODEL_DIR = os.path.join(BASE_FILE_PATH, "DEBUGS", "MODEL")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
TEMP_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG")
RESULT_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RESULT")
TRASH_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TRASH")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

# Database configuration
db_user = os.environ.get('db_user', "root")
db_pass = os.environ.get('db_pass', "insightzz@123")
db_host = os.environ.get('db_host', "127.0.0.1")
db_name = os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA")

PADDING = 50
summary_lock = Lock()
ALLOWED_DEFECTS = {"D", "V", "S"}

logger = initializeLogger("LOGIC", LOGS_PATH=LOGS_PATH)

def getDBconn():
    conn = None
    try: 
        conn = pymysql.connect(
            host= db_host,
            user= db_user,
            password= db_pass,
            database= db_name
        )
    except Exception as e:
        print(f"DB Error: {e}")
        logger.error(f"{traceback.format_exc()}")

    return conn

def save_defect_history(conn, cycle_uid, defect_details, img_path, json_path):
    cursor = None

    try:
        cursor = conn.cursor()

        # Check if record exists
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
                SET DEFECT_DETAILS = %s,
                    IMG_PATH = %s,
                    JSON_PATH = %s,
                    CREATE_TIME = %s
                WHERE CYCLE_UID = %s
            """

            cursor.execute(update_query, (
                defect_details,
                img_path,
                json_path,
                datetime.now(),
                cycle_uid
            ))

        else:

            insert_query = """
                INSERT INTO DEFECTS_HISTORY
                (CYCLE_UID, DEFECT_DETAILS, IMG_PATH, JSON_PATH, CREATE_TIME)
                VALUES (%s, %s, %s, %s, %s)
            """

            cursor.execute(insert_query, (
                cycle_uid,
                defect_details,
                img_path,
                json_path,
                datetime.now()
            ))

        conn.commit()

    except Exception as e:
        print(f"DB Error: {e}")
        logger.error(f"{traceback.format_exc()}")

    finally:
        if cursor:
            cursor.close()

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

        except:
            continue

        # normalize + filter
        if "SILVER" in raw_cls:
            cls = "S"
        elif "DENT" in raw_cls:
            cls = "D"
        elif "VIBRATION" in raw_cls:
            cls = "V"
        else:
            continue

        if cls not in ALLOWED_DEFECTS:
            continue
        if score<0.95:
            continue

        # padding
        x1 = max(0, x1 - PADDING)
        y1 = max(0, y1 - PADDING)
        x2 = min(w, x2 + PADDING)
        y2 = min(h, y2 + PADDING)

        defect_boxes[cls].append({
            "b": [x1, x2, y1, y2],
            "c": int(round(score, 3)*100)
        })

    counts = {k: len(v) for k, v in defect_boxes.items()}
    return defect_boxes, counts

def draw_boxes(img, defect_boxes):
    for cls, items in defect_boxes.items():
        for obj in items:
            x1, x2, y1, y2 = obj["b"]
            conf = obj["c"]

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(img, f"{cls} ({conf}%)", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return img

def add_summary(img, counts, severity):
    total = sum(counts.values())
    top3 = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
    summary = " | ".join([f"{k}-{v}" for k, v in top3])

    cv2.putText(img, f"SEVERITY: {severity}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(img, f"TOTAL:    {total}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(img, f"SUMMARY:  {summary}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    return img

def process_images(cycle_uid, cam_path, image_items, model, cam_serial, defect_summary, mq_obj):
    defect_json, counts = {}, {}
    date_str = datetime.now().strftime('%Y%m%d')

    for rank, image_name in image_items:
        try:
            img_path = os.path.join(cam_path, image_name)

            diameter = (image_name.split("__")[1]).split("_")[0]
            eg_setpoint = (image_name.split("__")[1]).split("_")[2]
            distance = (image_name.split("__")[1]).split("_")[4]

            img = cv2.imread(img_path)
            if img is None:
                continue
            try:
                masked_img, labels = model.run_inference(img)

                boxes, counts = process_labels(labels, img.shape)
                total = sum(counts.values())
                severity = calculate_severity(total)

                annotated = draw_boxes(img.copy(), boxes)
                annotated = add_summary(annotated, counts, severity)

                # ---- FILE NAMING ---- #
                if total > 0:
                    folder = "DEFECT"
                    defect_str = "_".join([f"{k}{v}" for k, v in counts.items()])
                    new_name = f"{rank}_{distance}_{defect_str}.jpg"

                    with summary_lock:
                        defect_summary["processed_at"] = datetime.now().isoformat()
                        defect_summary["defect_frames"] = severity
                        defect_summary["severity"] = severity
                        defect_summary["distance"] = distance
                        defect_summary["S"] += counts.get("S", 0)
                        defect_summary["D"] += counts.get("D", 0)
                        defect_summary["V"] += counts.get("V", 0)
                        defect_summary["total_defects"] += counts.get("V", 0) + counts.get("S", 0) + counts.get("D", 0)
                        mq_obj.publish("main/logic", defect_summary)
                else:
                    folder = "NODEFECT"
                    defect_str = "NODEFECT"
                    new_name = f"{rank}_{distance}.jpg"

                    with summary_lock:
                        defect_summary["processed_at"] = datetime.now().isoformat()
                        defect_summary["severity"] = severity
                        defect_summary["distance"] = distance
                        mq_obj.publish("main/logic", defect_summary)

                save_dir = os.path.join(RESULT_PATH, date_str, cycle_uid, folder)
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, f"{new_name}.jpg")
                cv2.imwrite(save_path, annotated)
                cv2.imwrite(f"{TEMP_PATH}/{LOCATION}/{cam_serial}_debug.jpg", annotated)

                defect_json = {
                    "r": rank,
                    "d": diameter,
                    "e": eg_setpoint,
                    "l": distance,
                    "s": severity,
                    **boxes,
                    "t": counts,
                }

            except Exception as e:
                print(f"Error processing {image_name}: {e}")
                logger.error(f"{traceback.format_exc()}")

        except Exception as e:
            print(f"Error processing {image_name}: {e}")

    return defect_json

def process_cycle(cycle_uid, model_1, model_2, mq_obj):
    base_path = os.path.join(RAW_PATH, LOCATION, cycle_uid)
    date_str = datetime.now().strftime('%Y%m%d')
    conn = getDBconn()
    if not os.path.exists(base_path):
        return
    
    print(f"\nProcessing cycle: {cycle_uid}")
    results = []
    defect_summary = {
        "cycle_uid": cycle_uid,
        "total_frames": 0,
        "total_defects": 0,
        "defect_frames": 0,
        "D": 0, "V": 0, "S":0,
    }

    for cam_serial in os.listdir(base_path):
        cam_path = os.path.join(base_path, cam_serial)
        image_list = get_sorted_images(cam_path)

        # Split into two halves, preserving original rank (1-based)
        ranked_images = list(enumerate(image_list, start=1))
        mid = len(ranked_images) // 2
        first_half  = ranked_images[:mid]
        second_half = ranked_images[mid:]

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(process_images, cycle_uid, cam_path, first_half,  model_1, cam_serial, defect_summary, mq_obj)
            future_2 = executor.submit(process_images, cycle_uid, cam_path, second_half, model_2, cam_serial, defect_summary, mq_obj)
            
            results_1 = future_1.result()
            results.append(results_1)
            
            results_2 = future_2.result()
            results.append(results_2)

    print(f"Cycle Done")

    defect_json = {
        "cycle_uid": cycle_uid,
        "result": results
    }

    json_dir = os.path.join(RESULT_PATH, date_str, cycle_uid)
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, f"{cycle_uid}_defect_data.json")

    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                existing_data = json.load(f)

            # If "result" is a list, append new results
            if isinstance(existing_data.get("result"), list) and isinstance(results, list):
                existing_data["result"].extend(results)
            else:
                # Otherwise overwrite/update
                existing_data["result"] = results

            with open(json_path, "w") as f:
                json.dump(existing_data, f, indent=4)

            print("Existing JSON updated successfully.")

        except Exception as e:
            print(f"Failed to update JSON: {e}")

    else:
        with open(json_path, "w") as f:
            json.dump(defect_json, f, indent=4)

    print("New JSON file created successfully.")

    img_dir = os.path.join(RESULT_PATH, date_str, cycle_uid)
    # save_defect_history(conn, cycle_uid, json.dumps(defect_summary), img_dir, json_path)

    defect_summary = {
        "cycle_uid": "",
        "total_frames": 0,
        "total_defects": 0,
        "defect_frames": 0,
        "D": 0, "V": 0, "S":0,
    }

    # ---- MOVE TO TRASH ---- #
    trash_dest = os.path.join(TRASH_PATH, cycle_uid)
    shutil.move(base_path, trash_dest)
    print(f"Moved to TRASH: {cycle_uid}")

def is_folder_stable(folder):
    size1 = get_size(folder)
    time.sleep(2)
    size2 = get_size(folder)
    return size1 == size2

def get_size(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total

def main():

    model_1 = MASKRCNN(
        "taloja1",
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_22MAY"),
        "model_final.pth",
        0.90,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_22MAY/TALOJA_MODEL_22MAY.json"),
        False,
        GPU_ID=0
    )

    model_2 = MASKRCNN(
        "taloja2",
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_22MAY"),
        "model_final.pth",
        0.90,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_22MAY/TALOJA_MODEL_22MAY.json"),
        False,
        GPU_ID=0
    )

    mq_obj = MQTT("capture_monitor")

    while True:

        base = os.path.join(RAW_PATH, LOCATION)
        mq_obj.publish("main/logic", {"roll_num": "--"})
        print("WAITING FOR DATA")

        if not os.path.exists(base):
            time.sleep(2)
            continue

        for cycle_uid in os.listdir(base):
            while True:
                if len(os.listdir(os.path.join(base, cycle_uid))) > 0: 
                    process_cycle(cycle_uid, model_1, model_2, mq_obj)
                else: break

        time.sleep(2)

if __name__=="__main__":
    while True:
        try: main()
        except:
            print("Error in main() !")
            logger.critical(f"{traceback.format_exc()}")
            print("Restarting in 5 secs")
            time.sleep(5)