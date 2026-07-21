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
from threading import Lock, Event

from DEPENDANT.INFERENCE import MASKRCNN
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

LOCATION = "TALOJA"

# /home/deepali/OMKAR/CODES/TALOJA/SURFACE_INSPECTION_TALOJA /home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA
BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

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

# ---- live.json tuning ----
LIVE_JSON_INTERVAL = 10   # seconds between live.json writes
LIVE_RESULTS_KEEP  = 50   # only the last N results go into live.json (full list stays in final JSON)

# ---- speed / storage tuning ----
DEBUG_IMG_INTERVAL = 2.0  # write <cam>_debug.jpg at most once per this many secs
MQTT_UI_INTERVAL   = 1.0  # publish ui/logic + health at most once per this many secs
SAVE_SCALE   = float(os.environ.get('SAVE_SCALE', '0.5'))  # stored trash copy is downscaled (inference stays full-size)
SAVE_QUALITY = int(os.environ.get('SAVE_QUALITY', '60'))   # JPEG quality of stored trash copy

# ---- background orphan recovery ----
ORPHAN_MAX = 3            # up to this many orphan cycles recovered in parallel (background)
ORPHAN_YIELD_SLEEP = 0.25 # per-frame pause for bg orphans while a LIVE cycle is running (GPU priority to live)

update_lock = Lock()
summary_lock = Lock()
result_lock = Lock()
debug_lock = Lock()
_last_debug_write = {}   # cam_serial -> last debug jpg write time

LIVE_ACTIVE = Event()     # set while a streaming (live) cycle is running
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

def find_orphan_cycle(base, idle_secs=30):
    """
    Restart safety: if LOGIC was down when a coil finished, a raw folder can
    be left behind with no live start/stop session. This finds such a folder
    (no new writes for `idle_secs`) so it can be processed & cleaned in batch.
    Uses write-time staleness, NOT folder size.
    Returns a cycle_uid or None.
    """
    if not os.path.exists(base):
        return None
    now = time.time()
    for cycle_uid in sorted(os.listdir(base)):
        cdir = os.path.join(base, cycle_uid)
        if not os.path.isdir(cdir):
            continue
        newest, has_files = 0, False
        for root, _, files in os.walk(cdir):
            for fn in files:
                has_files = True
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, fn)))
                except OSError:
                    pass
        if has_files and (now - newest) > idle_secs:
            return cycle_uid
    return None

def find_orphan_cycles(base, idle_secs=30, exclude=None):
    """Like find_orphan_cycle but returns ALL stale cycle folders (minus `exclude`)."""
    exclude = exclude or set()
    found = []
    if not os.path.exists(base):
        return found
    now = time.time()
    for cycle_uid in sorted(os.listdir(base)):
        if cycle_uid in exclude:
            continue
        cdir = os.path.join(base, cycle_uid)
        if not os.path.isdir(cdir):
            continue
        newest, has_files = 0, False
        for root, _, files in os.walk(cdir):
            for fn in files:
                has_files = True
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, fn)))
                except OSError:
                    pass
        if has_files and (now - newest) > idle_secs:
            found.append(cycle_uid)
    return found

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
        if "OIL_STAIN" == raw_cls:
            cls = "O"
        elif "OIL_STAIN_FAINT" == raw_cls:
            cls = "O"
        elif "WHITE_SPOT" == raw_cls:
            cls = "W"
        elif "SCORING" == raw_cls:
            if score < 0.90:
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
        if score<0.60:
            continue

        # padding
        x1 = max(0, x1 - PADDING)
        y1 = max(0, y1 - PADDING)
        x2 = min(w, x2 + PADDING)
        y2 = min(h, y2 + PADDING)

        box_center_x = (x1 + x2) / 2.0
        # visual frame maps full width to 300 mm (±150mm from center)
        distance_mm = ((box_center_x - (w / 2.0)) / float(w)) * 300.0

        defect_boxes[cls].append({
            "b": [x1, x2, y1, y2],
            "c": int(round(score, 3)*100),
            "pos_mm": round(distance_mm, 2)
        })

    counts = {k: len(v) for k, v in defect_boxes.items()}
    return defect_boxes, counts

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

            cv2.rectangle(
                img,
                (x1, y1),
                (x2, y2),
                color,
                2
            )

            (tw, th), _ = cv2.getTextSize(
                label,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                2
            )

            text_y = max(25, y1 - 10)

            cv2.rectangle(
                img,
                (x1, text_y - th - 6),
                (x1 + tw + 10, text_y + 4),
                color,
                -1
            )

            cv2.putText(
                img,
                label,
                (x1 + 5, text_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2
            )

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

def process_images(cycle_uid, coil_id, cam_path, image_items, model, cam_serial, defect_summary, mq_obj, results, bg_orphan=False):
    date_str = datetime.now().strftime('%Y%m%d')
    last_update = datetime.now()
    last_mq = 0.0

    items = list(image_items)
    if not items:
        return results

    # loader: decodes the NEXT image from disk while the GPU runs the current one
    # saver:  compresses + writes the small trash copy without blocking the GPU
    loader = ThreadPoolExecutor(max_workers=1)
    saver = ThreadPoolExecutor(max_workers=1)
    next_future = loader.submit(cv2.imread, os.path.join(cam_path, items[0][1]))

    try:
        for i, (rank, image_name) in enumerate(items):
            try:
                img_path = os.path.join(cam_path, image_name)

                meta = image_name.split("__")[1].split("_")
                diameter = meta[0]
                eg_setpoint = meta[2]
                distance = meta[4]

                img = next_future.result() if next_future is not None else cv2.imread(img_path)

                # prefetch the next image immediately
                if i + 1 < len(items):
                    next_future = loader.submit(
                        cv2.imread, os.path.join(cam_path, items[i + 1][1])
                    )
                else:
                    next_future = None

                if img is None:
                    continue

                # bg orphans yield GPU priority to the LIVE cycle
                if bg_orphan and LIVE_ACTIVE.is_set():
                    time.sleep(ORPHAN_YIELD_SLEEP)

                # ---- inference: ALWAYS on the full-size image ----
                # per-model lock: live + bg orphan threads share the models safely
                infer_lock = getattr(model, "infer_lock", None)
                if infer_lock is not None:
                    with infer_lock:
                        masked_img, labels = model.run_inference(img)
                else:
                    masked_img, labels = model.run_inference(img)

                boxes, counts = process_labels(labels, img.shape)
                total = sum(counts.values())
                severity = calculate_severity(total)

                # ---- counters under lock, publish OUTSIDE the lock ----
                with summary_lock:
                    defect_summary["processed_at"] = datetime.now().isoformat()
                    defect_summary["last_severity"] = severity
                    defect_summary["distance"] = distance
                    if total > 0:
                        defect_summary["O"] += counts.get("S", 0)
                        defect_summary["W"] += counts.get("D", 0)
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
                save_dir = os.path.join(TRASH_PATH, cycle_uid, cam_serial)
                save_path = os.path.join(save_dir, image_name)
                saver.submit(save_small_compressed, img, save_path)

                # ---- TEMP debug jpg: at most once per DEBUG_IMG_INTERVAL per camera ----
                # bg orphans never touch the debug preview (it belongs to the live cycle)
                write_debug = False
                if not bg_orphan:
                    with debug_lock:
                        if (now_t - _last_debug_write.get(cam_serial, 0.0)) > DEBUG_IMG_INTERVAL:
                            _last_debug_write[cam_serial] = now_t
                            write_debug = True
                if write_debug:
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
                    **boxes,
                    "t": counts,
                    "img": save_path,
                    "raw_img": save_path,
                }

                with result_lock:
                    results.append(defect_json)

                # live.json refresh (throttled) - bg orphans never write live.json
                if not bg_orphan:
                    try:
                        with update_lock:
                            if (datetime.now() - last_update).total_seconds() > LIVE_JSON_INTERVAL:
                                update_live_json(cycle_uid, coil_id, defect_summary, results=results, status="inprogress")
                                last_update = datetime.now()
                    except Exception:
                        pass

            except Exception:
                logger.error(f"Error processing {image_name}: {traceback.format_exc()}")

    finally:
        loader.shutdown(wait=False)
        saver.shutdown(wait=True)   # ensure all trash copies are on disk before finalize

    return results

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

    logger.info(f"Processing cycle: {cycle_uid} (bg_orphan={bg_orphan})")
    results = []
    defect_summary = {
        "cycle_uid": cycle_uid,
        "coil_id": coil_id,
        "total_frames": 0,
        "total_defects": 0,
        "defect_frames": 0,
        "total_defective_frames": 0,
        "O": 0, "W": 0, "S":0,
        "D": 0, "C": 0,
    }

    if not bg_orphan:
        reset_live_cache()

    for cam_serial in os.listdir(base_path):
        cam_path = os.path.join(base_path, cam_serial)
        image_list = get_sorted_images(cam_path)

        # Split into two halves, preserving original rank (1-based)
        ranked_images = list(enumerate(image_list, start=1))
        first_half = [(rank, img) for rank, img in ranked_images if rank % 2 == 1]
        second_half = [(rank, img) for rank, img in ranked_images if rank % 2 == 0]

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(process_images, cycle_uid, coil_id, cam_path, first_half,  model_1, cam_serial, defect_summary, mq_obj, results, bg_orphan)
            future_2 = executor.submit(process_images, cycle_uid, coil_id, cam_path, second_half, model_2, cam_serial, defect_summary, mq_obj, results, bg_orphan)

            future_1.result()
            future_2.result()

    print(f"Cycle Done: {cycle_uid}")
    logger.info(f"Cycle Done: {cycle_uid}")

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

def process_cycle_stream(cycle_uid, model_1, model_2, mq_obj):
    """
    Stream-process a cycle while capture is still running.
    Starts on TRIGGER 'start' (handled in main), ends on TRIGGER 'stop'.
    Deletes the raw folder ONLY after 'stop' + final drain.
    """
    base_path = os.path.join(RAW_PATH, LOCATION, cycle_uid)
    date_str = cycle_uid.split("_")[1][:8]

    conn = getDBconn()
    try:
        coil_id = get_coil_id_from_millruns(conn, cycle_uid)
    except Exception:
        coil_id = ""

    print(f"Streaming cycle: {cycle_uid}")
    logger.info(f"Streaming cycle: {cycle_uid}")

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

    processed  = defaultdict(set)          # cam_serial -> {processed image names}
    next_rank  = defaultdict(lambda: 1)    # cam_serial -> next rank to assign
    stop_received = False
    last_live = datetime.now()
    last_health = 0.0

    # ONE persistent worker pool for the whole cycle (was: new pool per batch)
    executor = ThreadPoolExecutor(max_workers=2)

    LIVE_ACTIVE.set()   # bg orphan workers slow down while this is set
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

                    f1 = executor.submit(process_images, cycle_uid, coil_id, cam_path,
                                         first_half,  model_1, cam_serial,
                                         defect_summary, mq_obj, results)
                    f2 = executor.submit(process_images, cycle_uid, coil_id, cam_path,
                                         second_half, model_2, cam_serial,
                                         defect_summary, mq_obj, results)
                    f1.result()
                    f2.result()

                    for _, name in batch:
                        processed[cam_serial].add(name)

            # ---- 3) Keep live.json fresh during the run ----
            if (datetime.now() - last_live).total_seconds() > LIVE_JSON_INTERVAL:
                try:
                    update_live_json(cycle_uid, coil_id, defect_summary,
                                     results=results, status="inprogress")
                except Exception:
                    pass
                last_live = datetime.now()

            # ---- 4) Exit once stopped AND fully drained ----
            if stop_received and not got_new:
                break
            if not got_new:
                time.sleep(0.5)

    finally:
        LIVE_ACTIVE.clear()
        executor.shutdown(wait=True)

    # ================= FINALIZE (only reached after STOP) =================
    print(f"Cycle Done (stream): {cycle_uid}")
    logger.info(f"Cycle Done (stream): {cycle_uid}")

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

# ---- live.json: in-memory cache, no re-reading the file on every write ----
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
                    data["result"] = list(results[-LIVE_RESULTS_KEEP:])

            data["last_updated"] = datetime.now().isoformat()

            os.makedirs(os.path.dirname(LIVE_JSON_PATH), exist_ok=True)

            # atomic write: dump to temp file, then swap in
            tmp_path = LIVE_JSON_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_path, LIVE_JSON_PATH)

    except Exception:
        logger.error(f"Failed to update live json: {traceback.format_exc()}")

def _run_orphan_bg(cycle_uid, model_1, model_2, mq_obj):
    """Background orphan recovery worker. Always releases its slot."""
    try:
        process_cycle(cycle_uid, model_1, model_2, mq_obj, bg_orphan=True)
        logger.info(f"Orphan recovered: {cycle_uid}")
    except Exception:
        logger.critical(f"orphan recovery error ({cycle_uid}):\n{traceback.format_exc()}")
    finally:
        with _orphan_lock:
            _active_orphans.discard(cycle_uid)

def main():

    model_1 = MASKRCNN(
        "taloja1",
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_4JULY"),
        "model_final.pth",
        0.60,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_4JULY/TALOJA_MODEL_4JULY.json"),
        False,
        GPU_ID=0
    )

    model_2 = MASKRCNN(
        "taloja2",
        os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml"),
        os.path.join(MODEL_DIR, "TALOJA_MODEL_4JULY"),
        "model_final.pth",
        0.60,
        os.path.join(MODEL_DIR, "TALOJA_MODEL_4JULY/TALOJA_MODEL_4JULY.json"),
        False,
        GPU_ID=0
    )

    # per-model inference locks: live + bg orphan threads can share models safely
    model_1.infer_lock = Lock()
    model_2.infer_lock = Lock()

    mq_obj = MQTT("capture_monitor")
    mq_obj.subscribe("taloja/capture")      # <-- receive TRIGGER start/stop

    base = os.path.join(RAW_PATH, LOCATION)

    # background pool: up to ORPHAN_MAX orphan cycles recover in parallel
    # while main stays free to accept a live START instantly
    orphan_pool = ThreadPoolExecutor(max_workers=ORPHAN_MAX)

    print("LOGIC service started, waiting for triggers...")
    logger.info("LOGIC service started, waiting for triggers")

    last_idle_pub = 0.0
    last_orphan_check = 0.0

    while True:
        now_t = time.time()

        # idle heartbeats: once per second is plenty (was: every 0.2s loop pass)
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

        # ---- 2) Orphan recovery: NON-BLOCKING, up to ORPHAN_MAX in background ----
        # main never blocks on orphans, so a live START is always handled instantly
        if now_t - last_orphan_check > 15.0:
            last_orphan_check = now_t
            with _orphan_lock:
                free_slots = ORPHAN_MAX - len(_active_orphans)
                busy = set(_active_orphans)
            if free_slots > 0:
                for uid in find_orphan_cycles(base, idle_secs=30, exclude=busy)[:free_slots]:
                    with _orphan_lock:
                        if uid in _active_orphans:
                            continue
                        _active_orphans.add(uid)
                    print(f"Recovering orphan cycle in background: {uid}")
                    logger.info(f"Recovering orphan cycle in background: {uid}")
                    orphan_pool.submit(_run_orphan_bg, uid, model_1, model_2, mq_obj)

        time.sleep(0.2)


if __name__ == "__main__":
    while True:
        try:
            main()
        except:
            logger.critical(f"main() crashed:\n{traceback.format_exc()}")
            print("Error in main() ! Restarting in 5 secs")
            time.sleep(5)