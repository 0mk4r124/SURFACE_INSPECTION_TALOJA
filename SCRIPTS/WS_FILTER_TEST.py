"""
WS_FILTER_TEST - re-run one finished coil from DEBUGS/TRASH with the new
white-spot / oil-stain filter and save it as a separate TEST coil.

NOT a service. Not started by MANAGE.py. Run by hand from SCRIPTS/:

    python3 WS_FILTER_TEST.py

What it does
------------
1. Reads every frame of CYCLE_UID from DEBUGS/TRASH/<CYCLE_UID>/<cam>/
2. Runs the SAME model + rules as SINGLE_LIVE_LOGIC (BatchedMaskRCNN,
   LOGIC.process_labels, WS_SHAPE shape + contrast), so the panel numbers
   (Cin, Ring, symmetry, ellipse IoU, aspect) are the ones the formula was
   fitted on.
3. Scores every White Spot with BOTH formulas (old = research doc,
   new = research doc + contrast term, see below) and reclassifies the ones
   that score as oil to OIL_STAIN (O). Nothing is dropped.
4. Writes, inside DEBUGS:
     RESULT/TEST/<TEST_ROLL_NUM>/<TEST_ROLL_NUM>_defect_data.json   (same format as a real coil)
     RESULT/TEST/<TEST_ROLL_NUM>/<TEST_ROLL_NUM>_white_spots.csv     (every white spot, every number)
     RESULT/TEST/<TEST_ROLL_NUM>/crops/<WHITE_SPOT|OIL_STAIN|REVIEW>/  (crop per spot, for labelling)
   and ONE DEFECTS_HISTORY row: CYCLE_UID = TEST_ROLL_NUM, COIL_ID = TEST_COIL_ID.
   The test coil then opens in the web app History tab like any other coil.

Never touches: the original cycle's DB row or JSON, MILL_RUNS, live.json,
the TRASH images, MQTT, or the daily RESULT/<YYYYMMDD> folders
(so ACCURACY_REPORT does not pick up the test coil).
Nothing is written until every frame is done: Ctrl+C leaves no partial row.

Formulas (S > WS_THRESHOLD keeps White Spot, otherwise OIL_STAIN)
-----------------------------------------------------------------
  R = Cin / Ring, C = Cin - Ring, S2 = symmetry after slash, E = ellipse IoU, A = aspect

  old : S = -53.6 + 1.5 R + 15.6 S2 + 35.1 E + 1.6 A            (WHITE_SPOT_TP_FP_FORMULA.md, unchanged)
  new : S = -57.1 + 5.2 R + 17.5 S2 + 32.5 E + 1.8 A - 0.54 C   (refit on the cleaned 130 panels)

  new formula, guardrails: R and A are capped at 3.0 before scoring (the fit
  never saw larger values, and both weights are positive, so an elongated
  scratch with A = 5 would otherwise score as a strong white spot). Any input
  outside the fitted range is flagged REVIEW in the CSV.

  The C term uses absolute grey levels: it is only valid at the camera
  settings of the training coils (EXPOSURE 350 / GAIN 17). R, S2, E, A are
  not affected by exposure. If exposure or gain changes, use WS_FORMULA = "old"
  until the new formula is refit.
"""

# =========================================================================
# SET THESE
# =========================================================================
CYCLE_UID = "ROLL_20261026000000"      # cycle to test (its frames must still be in DEBUGS/TRASH)

TEST_ROLL_NUM = None                   # None -> "TEST_" + CYCLE_UID
TEST_COIL_ID  = None                   # None -> "TEST_" + original coil id (MILL_RUNS), else "TEST_" + CYCLE_UID

WS_FORMULA    = "new"                  # "new" or "old"
WS_THRESHOLD  = 0.0                    # S <= this -> OIL_STAIN. Lower it to keep more true spots (see CSV)
REVIEW_BAND   = 0.5                    # |S - threshold| <= this -> flagged REVIEW in CSV / crops

REPLACE_EXISTING = True                # re-running the same TEST_ROLL_NUM overwrites its own row + JSON
CREATE_TIME_MODE = "original"          # "original": original coil's CREATE_TIME + 1 s (keeps the test coil
                                       #   OFF the operator dashboard, which shows the newest row)
                                       # "now": stamp with the current time (test coil becomes the newest row)
RUN_WHILE_LIVE = False                 # False: refuse to start while a coil is being processed live
MAX_FRAMES     = 0                     # 0 = all frames; e.g. 500 for a quick trial run
SAVE_CROPS     = True                  # one jpg per white spot, sorted by verdict
BATCH          = 4                     # frames per GPU call (production uses 8; 4 leaves room for the live service)
EXPECTED_FRAME_W = 2448                # native camera width (CAPTURE.py BIN_H = 1)
# =========================================================================

import os
import sys

# Every white spot must reach the filter: keep the production shape gate in
# "flag" mode for THIS process only (nothing dropped / converted before scoring).
os.environ["WS_REJECT_MODE"] = "flag"
os.environ.setdefault("DEBUG_PARALLEL", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv
import json
import time
import traceback
import threading

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import cv2

import SINGLE_LIVE_LOGIC as S          # model engine, label builder, frame naming (library use only)
L = S.L                                # LOGIC, as imported by SINGLE_LIVE_LOGIC

from DEPENDANT.LOGGING import initializeLogger

log = initializeLogger("WS_FILTER_TEST", LOGS_PATH=L.LOGS_PATH)
# keep this test's messages out of the production SINGLE_LIVE_LOGIC / LOGIC logs
S.logger = log
L.logger = log

DECODE_WORKERS = 6
POST_WORKERS = 4
PRINT_EVERY = 500

# map / count visibility rule of the web app (script.js + views.py MAP_MIN_CONF)
MAP_MIN_CONF = {"W": 95, "O": 90, "S": 90, "D": 90}

# fitted input range (cleaned 130 panels, TP and FP together)
FIT_RANGE = {"R": (1.03, 2.90), "S2": (0.83, 0.98), "E": (0.90, 0.98),
             "A": (1.31, 2.92), "Cin": (5.2, 33.4), "Ring": (3.0, 17.9)}
CAP_R = 3.0
CAP_A = 3.0

LIVE_STATUSES = ("inprogress", "processing", "running")


# =========================================================================
# White-spot decision
# =========================================================================
def score_old(R, S2, E, A):
    return -53.6 + 1.5 * R + 15.6 * S2 + 35.1 * E + 1.6 * A


def score_new(R, S2, E, A, C):
    return (-57.1 + 5.2 * min(R, CAP_R) + 17.5 * S2 + 32.5 * E
            + 1.8 * min(A, CAP_A) - 0.54 * C)


def ws_decide(shape):
    """Returns a dict stored on the defect as item['wsf'].
    Missing panel values -> the detection is KEPT as White Spot (a missing
    field must never drop a real defect)."""
    out = {"v": "WHITE_SPOT", "used": WS_FORMULA, "s": None, "s_old": None,
           "s_new": None, "review": False, "oor": [], "why": ""}
    try:
        if not shape or shape.get("shape") == "UNKNOWN":
            out["why"] = "no_shape"
            return out
        ct, sym = shape.get("ct"), shape.get("sym")
        E, A = shape.get("iou"), shape.get("ar")
        if ct is None or sym is None or E is None or A is None or len(sym) < 2:
            out["why"] = "no_panel"
            return out
        cin, ring = float(ct[0]), float(ct[1])
        if ring <= 0:
            out["why"] = "ring<=0"
            return out
        R, C, S2, E, A = cin / ring, cin - ring, float(sym[1]), float(E), float(A)

        vals = {"R": R, "S2": S2, "E": E, "A": A, "Cin": cin, "Ring": ring}
        out["oor"] = [k for k, (lo, hi) in FIT_RANGE.items() if not lo <= vals[k] <= hi]

        s_old = score_old(R, S2, E, A)
        s_new = score_new(R, S2, E, A, C)
        s = s_new if WS_FORMULA == "new" else s_old

        out.update(s=round(s, 3), s_old=round(s_old, 3), s_new=round(s_new, 3),
                   R=round(R, 3), C=round(C, 2), S2=S2, E=E, A=A, cin=cin, ring=ring)
        out["v"] = "WHITE_SPOT" if s > WS_THRESHOLD else "OIL_STAIN"
        out["review"] = abs(s - WS_THRESHOLD) <= REVIEW_BAND or bool(out["oor"])
        return out
    except Exception:
        log.error(f"ws_decide failed: {traceback.format_exc()}")
        out["why"] = "error"
        return out


# =========================================================================
# Helpers
# =========================================================================
def fail(msg):
    print(f"\n[STOP] {msg}\n", flush=True)
    log.error(msg)
    sys.exit(1)


def visible(cls, conf):
    m = MAP_MIN_CONF.get(cls)
    return m is None or (conf is not None and conf >= m)


def live_coil_running():
    """True if live.json says a coil is being processed right now."""
    try:
        if not os.path.exists(L.LIVE_JSON_PATH):
            return False
        if time.time() - os.path.getmtime(L.LIVE_JSON_PATH) > 120:
            return False
        with open(L.LIVE_JSON_PATH, "r") as f:
            st = str(json.load(f).get("status", "")).lower()
        return st in LIVE_STATUSES
    except Exception:
        return False


def list_trash_frames(trash_dir):
    """{cam: [names in capture order]} - same ordering as SINGLE_LIVE_LOGIC."""
    frames = {}
    for cam in sorted(os.listdir(trash_dir)):
        cp = os.path.join(trash_dir, cam)
        if not os.path.isdir(cp):
            continue
        names = [n for n in os.listdir(cp) if n.lower().endswith((".jpg", ".jpeg", ".png"))]
        if names:
            frames[cam] = sorted(names, key=S.frame_key)
    return frames


def db_fetch_one(conn, sql, args):
    cur = conn.cursor()
    try:
        cur.execute(sql, args)
        return cur.fetchone()
    finally:
        cur.close()


def save_test_history(conn, cycle_uid, coil_id, details, json_path, create_time):
    """Same table and columns LOGIC.save_defect_history writes, with a
    chosen CREATE_TIME. Only ever touches the TEST_ row."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT ID FROM DEFECTS_HISTORY WHERE CYCLE_UID = %s", (cycle_uid,))
        if cur.fetchone():
            cur.execute("UPDATE DEFECTS_HISTORY SET COIL_ID=%s, DEFECT_DETAILS=%s, JSON_PATH=%s, "
                        "CREATE_TIME=%s WHERE CYCLE_UID=%s",
                        (coil_id, details, json_path, create_time, cycle_uid))
            action = "updated"
        else:
            cur.execute("INSERT INTO DEFECTS_HISTORY (CYCLE_UID, COIL_ID, DEFECT_DETAILS, JSON_PATH, CREATE_TIME) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (cycle_uid, coil_id, details, json_path, create_time))
            action = "inserted"
        conn.commit()
        return action
    finally:
        cur.close()


def original_frame_counts(json_path):
    """{frame name: (W count, W count >= map conf)} from the original coil JSON."""
    out = {}
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
        for rec in data.get("result", []):
            name = os.path.basename(str(rec.get("img") or rec.get("raw_img") or ""))
            ws = rec.get("W") if isinstance(rec.get("W"), list) else []
            out[name] = (len(ws), sum(1 for it in ws if visible("W", it.get("c"))))
    except Exception:
        log.error(f"could not read original JSON {json_path}: {traceback.format_exc()}")
    return out


# =========================================================================
# Main
# =========================================================================
def main():
    global TEST_ROLL_NUM, TEST_COIL_ID
    t0 = time.time()

    # ---------------- checks before any heavy work ----------------
    if not str(CYCLE_UID).startswith("ROLL_"):
        fail(f"CYCLE_UID looks wrong: {CYCLE_UID!r} (expected ROLL_YYYYMMDDHHMMSS)")
    if WS_FORMULA not in ("new", "old"):
        fail(f"WS_FORMULA must be 'new' or 'old', got {WS_FORMULA!r}")

    TEST_ROLL_NUM = TEST_ROLL_NUM or f"TEST_{CYCLE_UID}"
    if not TEST_ROLL_NUM.startswith("TEST_"):
        fail(f"TEST_ROLL_NUM must start with TEST_ (got {TEST_ROLL_NUM!r}) - protects real coil rows")
    if len(TEST_ROLL_NUM) > 50:
        fail(f"TEST_ROLL_NUM longer than 50 chars (DEFECTS_HISTORY.CYCLE_UID limit)")

    trash_dir = os.path.join(L.TRASH_PATH, CYCLE_UID)
    if not os.path.isdir(trash_dir):
        fail(f"No TRASH folder for this cycle: {trash_dir}\n"
             f"       (DISK_CLEANER removes TRASH images older than 1 day when the disk is over 90%)")
    frames = list_trash_frames(trash_dir)
    n_total = sum(len(v) for v in frames.values())
    if not n_total:
        fail(f"No images in {trash_dir}")

    if live_coil_running() and not RUN_WHILE_LIVE:
        fail("A coil is being processed live right now (live.json status in progress).\n"
             "       This test shares the GPU and would slow the live coil. Run it when the mill\n"
             "       is idle, or set RUN_WHILE_LIVE = True.")

    first_cam = next(iter(frames))
    probe = cv2.imread(os.path.join(trash_dir, first_cam, frames[first_cam][0]))
    if probe is None:
        fail(f"Cannot read {frames[first_cam][0]}")
    if probe.shape[1] != EXPECTED_FRAME_W:
        fail(f"TRASH frames are {probe.shape[1]} px wide, expected {EXPECTED_FRAME_W}.\n"
             f"       They were saved with SAVE_SCALE != 1, so contrast / shape values would not match\n"
             f"       the panels the formula was fitted on. Not running.")
    del probe

    conn = L.getDBconn()
    if conn is None:
        fail("DB connection failed (check db_user / db_pass / db_host / db_name)")

    orig_coil = L.get_coil_id_from_millruns(conn, CYCLE_UID) or ""
    TEST_COIL_ID = TEST_COIL_ID or f"TEST_{orig_coil or CYCLE_UID}"

    orig_row = db_fetch_one(conn, "SELECT COIL_ID, JSON_PATH, CREATE_TIME FROM DEFECTS_HISTORY "
                                  "WHERE CYCLE_UID = %s ORDER BY ID DESC LIMIT 1", (CYCLE_UID,))
    orig_json = orig_row[1] if orig_row else None
    orig_time = orig_row[2] if orig_row else None

    if db_fetch_one(conn, "SELECT ID FROM DEFECTS_HISTORY WHERE CYCLE_UID = %s", (TEST_ROLL_NUM,)) \
            and not REPLACE_EXISTING:
        fail(f"{TEST_ROLL_NUM} already exists in DEFECTS_HISTORY. Set REPLACE_EXISTING = True "
             f"or choose another TEST_ROLL_NUM.")

    out_dir = os.path.join(L.RESULT_PATH, "TEST", TEST_ROLL_NUM)
    os.makedirs(out_dir, exist_ok=True)
    crop_dir = os.path.join(out_dir, "crops")

    hdr = (f"WS_FILTER_TEST | source {CYCLE_UID} (coil {orig_coil or '-'}) -> {TEST_ROLL_NUM} / {TEST_COIL_ID}\n"
           f"  frames in TRASH: {n_total} ({', '.join(f'{c}:{len(v)}' for c, v in frames.items())})"
           f"{'' if not MAX_FRAMES else f' | limited to first {MAX_FRAMES}'}\n"
           f"  formula: {WS_FORMULA} | threshold {WS_THRESHOLD} | review band +/-{REVIEW_BAND}\n"
           f"  output : {out_dir}")
    print(hdr, flush=True)
    log.info(hdr.replace("\n", " |"))

    # ---------------- model ----------------
    engine = S.BatchedMaskRCNN()
    print(f"  model  : {S.MODEL_TAG} on {engine.device} | FP16={engine.use_amp} | batch {BATCH}", flush=True)

    # ---------------- state ----------------
    lock = threading.Lock()
    results = []
    csv_rows = []
    summary = {
        "cycle_uid": TEST_ROLL_NUM, "coil_id": TEST_COIL_ID,
        "total_frames": 0, "total_defects": 0,
        "defect_frames": 0, "total_defective_frames": 0,
        "O": 0, "W": 0, "S": 0, "D": 0, "C": 0,
    }
    st = defaultdict(int)          # white-spot test counters
    rerun_frame_w = {}             # frame name -> W before filter (fidelity check)

    def post(cam, rank, name, img, raw):
        try:
            diameter, eg_setpoint, distance = S.frame_meta(name)
            labels = S.build_labels(raw)
            boxes, _ = L.process_labels(labels, img.shape, img=img)
            boxes = {k: list(v) for k, v in boxes.items()}

            ws_items = boxes.get("W", [])
            keep_w, to_oil, rows = [], [], []
            for i, it in enumerate(ws_items):
                shp = it.get("shape") or {}
                d = ws_decide(shp)
                it["wsf"] = d
                conf = it.get("c")
                (keep_w if d["v"] == "WHITE_SPOT" else to_oil).append(it)

                crop_path = ""
                if SAVE_CROPS:
                    try:
                        x1, x2, y1, y2 = it["b"]
                        sub = "REVIEW" if d["review"] else d["v"]
                        crop_path = os.path.join(crop_dir, sub, f"{os.path.splitext(name)[0]}_{i}.jpg")
                        os.makedirs(os.path.dirname(crop_path), exist_ok=True)
                        cv2.imwrite(crop_path, img[y1:y2, x1:x2], [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    except Exception:
                        crop_path = ""

                sym = shp.get("sym") or [None, None]
                pos = it.get("pos_mm")
                rows.append({
                    "cam": cam, "rank": rank, "frame": name,
                    "length_m": round(float(distance) / 1000.0, 2) if str(distance).replace(".", "", 1).isdigit() else "",
                    "pos_mm": pos, "side": ("OS" if pos is not None and pos < 0 else "DS"),
                    "conf": conf, "shape": shp.get("shape"), "shape_why": shp.get("why", ""),
                    "cin": d.get("cin"), "ring": d.get("ring"), "C": d.get("C"), "R": d.get("R"),
                    "sym1": sym[0], "S2": sym[1], "E": shp.get("iou"), "sol": shp.get("sol"),
                    "A": shp.get("ar"), "tilt": shp.get("tilt"),
                    "maj_mm": shp.get("maj_mm"), "min_mm": shp.get("min_mm"), "a_mm2": shp.get("a_mm2"),
                    "s_old": d["s_old"], "s_new": d["s_new"], "used": d["used"], "score": d["s"],
                    "verdict": d["v"], "review": int(d["review"]), "out_of_range": ";".join(d["oor"]),
                    "missing": d["why"],
                    "old_says": ("" if d["s_old"] is None else
                                 ("WHITE_SPOT" if d["s_old"] > WS_THRESHOLD else "OIL_STAIN")),
                    "new_says": ("" if d["s_new"] is None else
                                 ("WHITE_SPOT" if d["s_new"] > WS_THRESHOLD else "OIL_STAIN")),
                    "on_map_before": int(visible("W", conf)),
                    "on_map_after": int(visible("W" if d["v"] == "WHITE_SPOT" else "O", conf)),
                    "crop": crop_path, "img": os.path.join(trash_dir, cam, name),
                })

            if ws_items:
                boxes["W"] = keep_w
                if to_oil:
                    boxes["O"] = boxes.get("O", []) + to_oil
            boxes = {k: v for k, v in boxes.items() if v}       # same shape as process_labels output
            counts = {k: len(v) for k, v in boxes.items()}
            total = sum(counts.values())
            severity = L.calculate_severity(total)

            scaled = L.scale_defect_boxes(boxes, img.shape, L.SAVE_SCALE)
            if L.SAVE_SCALE and L.SAVE_SCALE != 1.0:
                comp_h = int(round(img.shape[0] * L.SAVE_SCALE))
                comp_w = int(round(img.shape[1] * L.SAVE_SCALE))
            else:
                comp_h, comp_w = img.shape[0], img.shape[1]

            img_path = os.path.join(trash_dir, cam, name)       # existing TRASH image, not copied
            defect_json = {
                "r": rank, "d": diameter, "e": eg_setpoint, "l": distance,
                "s": severity, **scaled, "t": counts,
                "img": img_path, "raw_img": img_path,
                "compressed_width": comp_w, "compressed_height": comp_h,
                "scale": L.SAVE_SCALE,
            }

            with lock:
                rerun_frame_w[name] = (len(ws_items), sum(1 for it in ws_items if visible("W", it.get("c"))))
                s = summary
                s["processed_at"] = datetime.now().isoformat()
                s["last_severity"] = severity
                s["distance"] = distance
                if total > 0:
                    for k in ("O", "W", "S", "D", "C"):
                        s[k] += counts.get(k, 0)
                    s["total_defects"] += sum(counts.get(k, 0) for k in ("O", "W", "S", "D", "C"))
                    s["defect_frames"] += 1
                    s["total_defective_frames"] += 1
                s["total_frames"] += 1
                results.append(defect_json)
                csv_rows.extend(rows)
                for r in rows:
                    st["w_in"] += 1
                    st["w_in_vis"] += r["on_map_before"]
                    if r["verdict"] == "WHITE_SPOT":
                        st["w_kept"] += 1
                        st["w_kept_vis"] += r["on_map_after"]
                    else:
                        st["to_oil"] += 1
                        st["to_oil_vis_as_O"] += r["on_map_after"]
                        st["to_oil_was_vis_W"] += r["on_map_before"]
                    st["review"] += r["review"]
                    st["oor"] += int(bool(r["out_of_range"]))
                    st["missing"] += int(bool(r["missing"]))
                    if r["old_says"] and r["new_says"] and r["old_says"] != r["new_says"]:
                        st["old_new_disagree"] += 1
        except Exception:
            log.error(f"post failed {cam}/{name}: {traceback.format_exc()}")
            with lock:
                st["post_errors"] += 1

    def load(path):
        img = cv2.imread(path)
        if img is None:
            return None, None
        return img, engine.preprocess(img)

    # ---------------- run ----------------
    jobs = []
    for cam, names in frames.items():
        for rank, name in enumerate(names, start=1):
            jobs.append((cam, rank, name))
    if MAX_FRAMES:
        jobs = jobs[:MAX_FRAMES]

    done = 0
    unreadable = 0
    t_run = time.time()
    with ThreadPoolExecutor(DECODE_WORKERS) as loader, ThreadPoolExecutor(POST_WORKERS) as poster:
        ahead = BATCH * 4
        futs = [loader.submit(load, os.path.join(trash_dir, c, n)) for c, _, n in jobs[:ahead]]
        nxt = len(futs)
        i = 0
        post_futs = []
        while i < len(jobs):
            batch_jobs = jobs[i:i + BATCH]
            loaded = [f.result() for f in futs[:len(batch_jobs)]]
            futs = futs[len(batch_jobs):]
            while nxt < len(jobs) and len(futs) < ahead:
                c, _, n = jobs[nxt]
                futs.append(loader.submit(load, os.path.join(trash_dir, c, n)))
                nxt += 1

            ok = [(j, im, rs) for j, (im, rs) in zip(batch_jobs, loaded) if im is not None]
            unreadable += len(batch_jobs) - len(ok)
            if ok:
                try:
                    raws = engine.infer([im for _, im, _ in ok], [rs for _, _, rs in ok])
                except Exception:
                    log.error(f"batch failed, retrying one by one: {traceback.format_exc()}")
                    raws = []
                    for _, im, rs in ok:
                        try:
                            raws.append(engine.infer([im], [rs])[0])
                        except Exception:
                            log.error(f"frame failed: {traceback.format_exc()}")
                            raws.append(None)
                for (j, im, _), raw in zip(ok, raws):
                    if raw is None:
                        unreadable += 1
                        continue
                    post_futs.append(poster.submit(post, j[0], j[1], j[2], im, raw))

            i += len(batch_jobs)
            done = i
            if done % PRINT_EVERY < BATCH or done == len(jobs):
                el = time.time() - t_run
                fps = done / max(el, 1e-6)
                eta = (len(jobs) - done) / max(fps, 1e-6)
                with lock:
                    msg = (f"  {done}/{len(jobs)} frames | {fps:.1f} fps | eta {eta / 60:.1f} min | "
                           f"white spots so far {st['w_in']} -> kept {st['w_kept']}, to oil {st['to_oil']}")
                print(msg, flush=True)
                log.info(msg)
            # keep memory bounded: drop finished post futures
            if len(post_futs) > 256:
                post_futs = [f for f in post_futs if not f.done()]
        for f in post_futs:
            f.result()

    results.sort(key=lambda x: (x.get("r", 0)))

    # ---------------- write JSON ----------------
    json_path = os.path.join(out_dir, f"{TEST_ROLL_NUM}_defect_data.json")
    tmp = json_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"cycle_uid": TEST_ROLL_NUM, "coil_id": TEST_COIL_ID,
                   "source_cycle_uid": CYCLE_UID, "source_coil_id": orig_coil,
                   "ws_filter": {"formula": WS_FORMULA, "threshold": WS_THRESHOLD,
                                 "review_band": REVIEW_BAND},
                   "result": results}, f)
    os.replace(tmp, json_path)

    # ---------------- write CSV ----------------
    csv_path = os.path.join(out_dir, f"{TEST_ROLL_NUM}_white_spots.csv")
    if csv_rows:
        csv_rows.sort(key=lambda r: (r["cam"], r["rank"]))
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)

    # ---------------- write DB row ----------------
    if CREATE_TIME_MODE == "original" and orig_time is not None:
        create_time = orig_time + timedelta(seconds=1)
    else:
        if CREATE_TIME_MODE == "original":
            print("  note: original coil has no DEFECTS_HISTORY row - using current time", flush=True)
        create_time = datetime.now()

    details = dict(summary)
    details["ws_test"] = {"source": CYCLE_UID, "formula": WS_FORMULA, "thr": WS_THRESHOLD,
                          "w_in": st["w_in"], "kept": st["w_kept"], "to_oil": st["to_oil"],
                          "review": st["review"]}
    details_txt = json.dumps(details)
    if len(details_txt) > 3000:                          # DEFECT_DETAILS max_length
        details.pop("ws_test", None)
        details_txt = json.dumps(details)
    try:
        conn.ping(reconnect=True)
    except Exception:
        conn = L.getDBconn()
    action = save_test_history(conn, TEST_ROLL_NUM, TEST_COIL_ID, details_txt, json_path, create_time)

    # ---------------- fidelity check vs the original coil ----------------
    fid = ""
    if orig_json and os.path.exists(orig_json):
        orig = original_frame_counts(orig_json)
        common = [n for n in rerun_frame_w if n in orig]
        same = sum(1 for n in common if rerun_frame_w[n][0] == orig[n][0])
        o_all = sum(orig[n][0] for n in common)
        o_vis = sum(orig[n][1] for n in common)
        r_all = sum(rerun_frame_w[n][0] for n in common)
        r_vis = sum(rerun_frame_w[n][1] for n in common)
        fid = (f"  re-run vs original (same {len(common)} frames, BEFORE the filter):\n"
               f"     white spots original {o_all} (on map {o_vis}) | re-run {r_all} (on map {r_vis})\n"
               f"     frames with the same white-spot count: {same}/{len(common)}"
               f" ({100.0 * same / max(1, len(common)):.1f}%)\n")
        if len(common) and same / len(common) < 0.95:
            fid += ("     WARNING: below 95% - the re-run does not reproduce the original detections\n"
                    "     closely (model / threshold / env changed since?). Compare with care.\n")
    else:
        fid = "  re-run vs original: original coil JSON not found, comparison skipped\n"

    mins = (time.time() - t0) / 60.0
    rep = (
        f"\nDONE in {mins:.1f} min | DEFECTS_HISTORY row {action}: {TEST_ROLL_NUM} / {TEST_COIL_ID}\n"
        f"  frames processed {summary['total_frames']} | unreadable/failed {unreadable} | post errors {st['post_errors']}\n"
        + fid +
        f"  white spots scored : {st['w_in']} (on map, conf>={MAP_MIN_CONF['W']}%: {st['w_in_vis']})\n"
        f"  kept WHITE_SPOT    : {st['w_kept']} (on map: {st['w_kept_vis']})\n"
        f"  -> OIL_STAIN       : {st['to_oil']} (were on map as W: {st['to_oil_was_vis_W']}, "
        f"now on map as O at conf>={MAP_MIN_CONF['O']}%: {st['to_oil_vis_as_O']})\n"
        f"  flagged REVIEW     : {st['review']} (score within +/-{REVIEW_BAND} or input outside the fitted range: {st['oor']})\n"
        f"  kept, no panel data: {st['missing']}\n"
        f"  old vs new formula disagree on {st['old_new_disagree']} spots (see old_says / new_says in the CSV)\n"
        f"  JSON : {json_path}\n"
        f"  CSV  : {csv_path if csv_rows else '(no white spots)'}\n"
        + (f"  crops: {crop_dir}\n" if SAVE_CROPS and csv_rows else "")
        + f"  TRASH images are referenced, not copied: DISK_CLEANER may remove them later.\n"
    )
    print(rep, flush=True)
    log.info(rep.replace("\n", " |"))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nInterrupted - nothing was written to the DB.", flush=True)
        sys.exit(130)
    except Exception:
        log.critical(f"WS_FILTER_TEST crashed:\n{traceback.format_exc()}")
        print(traceback.format_exc(), flush=True)
        sys.exit(1)
