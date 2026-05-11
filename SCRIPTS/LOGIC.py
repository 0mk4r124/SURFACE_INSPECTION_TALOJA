import cv2
import os
import json
import shutil
import time
import traceback
import pymysql

from datetime import datetime
from collections import defaultdict

from DEPENDANT.INFERENCE import MASKRCNN
from DEPENDANT.MQTT import MQTT

# ---------------- CONFIG ---------------- #
MODEL_DIR = '/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/MODEL/'

TEMP_IMAGE_PATH = "/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/RAW_IMG/"
RESULT_IMAGE_PATH = "/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/RESULT/"
TRASH_PATH = "/home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA/TRASH/"

LOCATION = "TALOJA"

PADDING = 50
ALLOWED_DEFECTS = {"DENT", "VIBRATION", "SILVER_MARK"}

# ---------------- DB ---------------- #
def save_defect_history(cycle_uid, defect_json, defect_str, img_path):
    conn = None
    cursor = None

    try:
        conn = pymysql.connect(
            host="127.0.0.1",
            user="root",
            password="insightzz@123",
            database="SURFACE_INSPECTION_TALOJA"
        )

        cursor = conn.cursor()

        query = """
            INSERT INTO DEFECTS_HISTORY 
            (CYCLE_UID, DEFECT_DETAIL, DEFECT_STR, IMG_PATH, CREATE_TIME)
            VALUES (%s, %s, %s, %s)
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
        print(traceback.format_exc())

    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ---------------- SORT ---------------- #
def get_sorted_images(folder):
    files = [
        f for f in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, f))
    ]
    return sorted(files, key=lambda x: os.path.getmtime(os.path.join(folder, x)))


# ---------------- SEVERITY ---------------- #
def calculate_severity(total):
    if total <= 2:
        return "LOW"
    elif total <= 6:
        return "MEDIUM"
    return "HIGH"


# ---------------- PROCESS LABELS ---------------- #
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
            cls = "SILVER_MARK"
        elif "DENT" in raw_cls:
            cls = "DENT"
        elif "VIBRATION" in raw_cls:
            cls = "VIBRATION"
        else:
            continue

        if cls not in ALLOWED_DEFECTS:
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


# ---------------- DRAW ---------------- #
def draw_boxes(img, defect_boxes):
    for cls, items in defect_boxes.items():
        for obj in items:
            x1, x2, y1, y2 = obj["b"]
            conf = obj["c"]

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(img, f"{cls} ({conf}%)", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return img


# ---------------- SUMMARY ---------------- #
def add_summary(img, counts, severity):
    total = sum(counts.values())
    top3 = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
    summary = " | ".join([f"{k}-{v}" for k, v in top3])

    cv2.putText(img, f"SEVERITY: {severity}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(img, f"TOTAL:    {total}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.putText(img, f"SUMMARY:  {summary}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    return img


# ---------------- PROCESS ONE CYCLE ---------------- #
def process_cycle(cycle_uid, model):

    base_path = os.path.join(TEMP_IMAGE_PATH, LOCATION, cycle_uid)
    date_str = datetime.now().strftime('%Y%m%d')

    if not os.path.exists(base_path):
        return

    print(f"\nProcessing cycle: {cycle_uid}")
    cycle_totals = defaultdict(int)

    for cam_serial in os.listdir(base_path):

        cam_path = os.path.join(base_path, cam_serial)
        image_list = get_sorted_images(cam_path)

        for rank, image_name in enumerate(image_list, start=1):

            img_path = os.path.join(cam_path, image_name)
            img = cv2.imread(img_path)
            if img is None:
                continue

            try:
                masked_img, labels = model.run_inference(img)
                try: cv2.imwrite("test.jpg", masked_img)
                except: pass

                boxes, counts = process_labels(labels, img.shape)
                total = sum(counts.values())
                severity = calculate_severity(total)

                for k, v in counts.items():
                    cycle_totals[k] += v

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

                save_dir = os.path.join(RESULT_IMAGE_PATH, date_str, cycle_uid, folder)
                os.makedirs(save_dir, exist_ok=True)

                save_path = os.path.join(save_dir, f"{new_name}.jpg")
                cv2.imwrite(save_path, annotated)

                defect_json = {
                    "r": rank,
                    "s": severity,
                    **boxes,
                    "t": counts
                }

                # ✅ SAVE TO DEFECTS_HISTORY
                save_defect_history(cycle_uid, defect_json, defect_str, save_path)

            except Exception as e:
                print(f"Error processing {image_name}: {e}")

    print(f"Cycle Summary: {dict(cycle_totals)}")

    # ---- MOVE TO TRASH ---- #
    trash_dest = os.path.join(TRASH_PATH, cycle_uid)
    shutil.move(base_path, trash_dest)
    print(f"Moved to TRASH: {cycle_uid}")


# ---------------- STABILITY CHECK ---------------- #
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


# ---------------- MAIN ---------------- #
def main():

    model = MASKRCNN(
        "taloja",
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_8MAY"),
        "model_final.pth",
        0.80,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_8MAY/TALOJA_MODEL_8MAY.json"),
        False,
        GPU_ID=0
    )

    reciever = MQTT("capture_monitor")
    reciever.subscribe("taloja/client/capture")
    bunch_num = 'INACTIVE'

    while True:

        base = os.path.join(TEMP_IMAGE_PATH, LOCATION)

        if not os.path.exists(base):
            time.sleep(2)
            continue

        for cycle_uid in os.listdir(base):

            try: data = reciever.data
            except: data = {}
            if any(list(data.keys())):
                bunch_num = str(data["value"])

            if bunch_num != cycle_uid:
                process_cycle(cycle_uid, model)

        time.sleep(2)


if __name__ == "__main__":
    main()