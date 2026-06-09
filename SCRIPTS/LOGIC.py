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

from DEPENDANT.INFERENCE import MASKRCNN
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

LOCATION = "TALOJA"

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

MODEL_DIR = os.path.join(BASE_FILE_PATH, "DEBUGS", "MODEL")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
RESULT_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RESULT")
TRASH_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TRASH")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

# Database configuration
db_user = os.environ.get('db_user', "root")
db_pass = os.environ.get('db_pass', "insightzz@123")
db_host = os.environ.get('db_host', "127.0.0.1")
db_name = os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA")

PADDING = 50
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

def save_defect_history(conn, cycle_uid, defect_json, defect_str, img_path):
    cursor = None

    try:
        cursor = conn.cursor()

        query = """
            INSERT INTO DEFECTS_HISTORY 
            (CYCLE_UID, DEFECT_DETAIL, DEFECT_STR, IMG_PATH, CREATE_TIME)
            VALUES (%s, %s, %s, %s, %s)
        """

        cursor.execute(query, (
            cycle_uid,
            json.dumps(defect_json),
            defect_str.replace('_', ','),
            img_path,
            datetime.now()
        ))

        conn.commit()

    except Exception as e:
        print(f"DB Error: {e}")
        logger.error(f"{json.dumps(defect_json)}")
        logger.error(f"{traceback.format_exc()}")

    finally:
        if cursor: cursor.close()

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

def process_images(cycle_uid, cam_path, image_items, model):
    date_str = datetime.now().strftime('%Y%m%d')
    conn = getDBconn()

    for rank, image_name in image_items:
        try:
            img_path = os.path.join(cam_path, image_name)
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
                    new_name = f"{rank}_{defect_str}_{image_name}"
                else:
                    folder = "NODEFECT"
                    defect_str = "NODEFECT"
                    new_name = f"{rank}_{image_name}"

                save_dir = os.path.join(RESULT_PATH, date_str, cycle_uid, folder)
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, f"{new_name}.jpg")
                cv2.imwrite(save_path, annotated)

                defect_json = {
                    "r": rank,
                    "s": severity,
                    **boxes,
                    "t": counts,
                }
                # ? SAVE TO DEFECTS_HISTORY / DB
                save_defect_history(conn, cycle_uid, defect_json, defect_str, save_path)


                # results.append((rank, {
                #     "annotated": annotated,
                #     "counts": counts,
                #     "severity": severity,
                #     "boxes": boxes,
                #     "defect_str": defect_str,
                #     "new_name": new_name,
                #     "folder": folder,
                # }))
            except Exception as e:
                print(f"Error processing {image_name}: {e}")
                logger.error(f"{traceback.format_exc()}")

        except Exception as e:
            print(f"Error processing {image_name}: {e}")

    return True

def process_cycle(cycle_uid, model_1, model_2):
    base_path = os.path.join(RAW_PATH, LOCATION, cycle_uid)
    if not os.path.exists(base_path):
        return
    print(f"\nProcessing cycle: {cycle_uid}")
    cycle_totals = defaultdict(int)

    for cam_serial in os.listdir(base_path):
        cam_path = os.path.join(base_path, cam_serial)
        image_list = get_sorted_images(cam_path)

        # Split into two halves, preserving original rank (1-based)
        ranked_images = list(enumerate(image_list, start=1))
        mid = len(ranked_images) // 2
        first_half  = ranked_images[:mid]
        second_half = ranked_images[mid:]

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(process_images, cycle_uid, cam_path, first_half,  model_1)
            future_2 = executor.submit(process_images, cycle_uid, cam_path, second_half, model_2)
            results_1 = future_1.result()
            results_2 = future_2.result()

    print(f"Cycle Done")

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

    reciever = MQTT("capture_monitor")
    reciever.subscribe("taloja/client/capture")
    bunch_num = 'INACTIVE'

    while True:

        base = os.path.join(RAW_PATH, LOCATION)
        print("WAITING FOR DATA")

        if not os.path.exists(base):
            time.sleep(2)
            continue

        for cycle_uid in os.listdir(base):

            try: data = reciever.data
            except: data = {}
            if any(list(data.keys())):
                bunch_num = str(data["value"])

            if bunch_num != cycle_uid:
                process_cycle(cycle_uid, model_1, model_2)

        time.sleep(2)

if __name__=="__main__":
    while True:
        try: main()
        except:
            print("Error in main() !")
            logger.critical(f"{traceback.format_exc()}")
            print("Restarting in 5 secs")
            time.sleep(5)