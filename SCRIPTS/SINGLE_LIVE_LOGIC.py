"""
SINGLE_LIVE_LOGIC - ONE service, ONE model, all inference.

Replaces LOGIC_LIVE.py + LOGIC_ORPHAN.py (run only this one).

Why: the split services ran 2 + up to 6 Mask R-CNN copies, each fed by its
own 24+24+24 CPU threads, with no OpenCV/PyTorch thread limits on a
64-thread box. Measured result: ~2-3.5 s of model time and ~8 s total per
frame (0.12 fps per model). This service does the opposite:

  - ONE model on the GPU, fed in BATCHES (default 8 frames per GPU call)
  - frame decode + resize in parallel CPU workers (was inside the model
    lock, one frame at a time); resize is the SAME detectron2/PIL code as
    DefaultPredictor, so the model sees bit-identical input
  - only box/score/class + white-spot mask crops come back to the CPU
    (was: every full-resolution mask, ~100 MB per frame)
  - each JPEG decoded ONCE (trash copy is made from the same decoded frame)
  - small, fixed thread pools; cv2 / torch / BLAS CPU threads capped

Scheduling (single GPU queue):
  1. LIVE coil first, always (MQTT start/stop, same topics as before)
  2. when the live coil is caught up, or no coil is running, the backlog
     (orphan cycle folders in RAW_IMG) is processed in small slices
     (BACKLOG_SLICE frames), so a live coil never waits more than ~2 s
  3. a new START while the previous coil is still draining: the old coil
     moves to the backlog with its progress kept; the new coil becomes live

Outputs are the SAME as before (reused from LOGIC.py, imported as a library):
  - per-cycle JSON in DEBUGS/RESULT/<date>/<uid>/<uid>_defect_data.json
  - DEFECTS_HISTORY row, live.json (live coil only), ui/logic + health MQTT
  - TRASH copies, raw folder deleted after finalize
  - <cam>_debug.jpg: annotated live frame for the dashboard Camera Feed,
    updated with live.json (defect frames shown at once)
  - defect rules: LOGIC.process_labels (thresholds, scoring length gate,
    white-spot shape gate, dedup) - unchanged

Run (from SCRIPTS/):
    python3 SINGLE_LIVE_LOGIC.py                       # the service
    python3 SINGLE_LIVE_LOGIC.py --bench <jpg folder>  # speed test, writes nothing
    python3 SINGLE_LIVE_LOGIC.py --bench <jpg folder> --compare 30
        # also runs the OLD per-frame path on 30 frames and reports whether
        # both paths find the same defects
"""

import os

# ---- CPU thread caps: MUST be set before numpy / cv2 / torch are imported ----
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "2")
# LOGIC.py starts its own trash-saver pool at import; this service has its own
# in-memory saver, so keep LOGIC's pool minimal. LOGIC's [DBG] trace off.
os.environ.setdefault("SAVE_WORKERS", "1")
os.environ.setdefault("SAVE_QUEUE_MAX", "8")
os.environ.setdefault("DEBUG_PARALLEL", "0")

import sys
import json
import time
import shutil
import argparse
import threading
import traceback

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from queue import Queue, Full
from threading import Lock

import cv2
import numpy as np

cv2.setNumThreads(1)

import torch

torch.set_num_threads(int(os.environ.get("TORCH_CPU_THREADS", "4")))

from detectron2.config import get_cfg
from detectron2.modeling import build_model
from detectron2.checkpoint import DetectionCheckpointer
import detectron2.data.transforms as T

import LOGIC as L                       # library: rules, JSON, DB, live.json

from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger
from DEPENDANT.INFERENCE import WS_MASK_PASSTHROUGH, WS_SHAPE_CLASSES

logger = initializeLogger("SINGLE_LIVE_LOGIC", LOGS_PATH=L.LOGS_PATH)


# =========================================================================
# Tuning (env-overridable from MANAGE.py)
# =========================================================================
MODEL_TAG        = os.environ.get("SINGLE_MODEL_TAG", "taloja1")
GPU_ID           = int(os.environ.get("SINGLE_GPU_ID", "0"))
INFER_FP16       = os.environ.get("INFER_FP16", "1") == "1"   # same default as before
BATCH_SIZE       = int(os.environ.get("SINGLE_BATCH", "8"))       # frames per GPU call
DECODE_WORKERS   = int(os.environ.get("SINGLE_DECODE_WORKERS", "8"))
POST_WORKERS     = int(os.environ.get("SINGLE_POST_WORKERS", "4"))
TRASH_WORKERS    = int(os.environ.get("SINGLE_TRASH_WORKERS", "6"))
TRASH_QUEUE_MAX  = int(os.environ.get("SINGLE_TRASH_QUEUE", "64"))   # x ~15 MB each
LIVE_SLICE_MAX   = int(os.environ.get("SINGLE_LIVE_SLICE", "256"))   # live frames per pass
BACKLOG_SLICE    = int(os.environ.get("SINGLE_BACKLOG_SLICE", "64")) # backlog frames per pass
FILE_SETTLE_SECS = float(os.environ.get("SINGLE_FILE_SETTLE", "1.0"))  # skip files younger than this (mid-write)
STOP_GRACE_SECS  = float(os.environ.get("SINGLE_STOP_GRACE", "2.0"))   # after STOP, wait for last files
ORPHAN_IDLE_SECS = float(os.environ.get("ORPHAN_IDLE_SECS", "30"))
ORPHAN_SCAN_SECS = float(os.environ.get("ORPHAN_SCAN_INTERVAL", "15"))
STATUS_EVERY     = float(os.environ.get("SINGLE_STATUS_EVERY", "30"))

# ---- live dashboard image (DEBUGS/TEMP_IMG/TALOJA/<cam>_debug.jpg) ----
# Updated from the LIVE coil as frames are processed (alongside live.json):
#   - a frame WITH defects is shown at once (at most every LIVE_IMG_DEFECT_GAP s)
#   - a clean frame at most every LIVE_IMG_INTERVAL s, but never sooner than
#     LIVE_IMG_HOLD_DEFECT s after a defect frame (so defects stay visible)
# Written atomically (tmp + rename), so the browser never gets half a JPEG.
LIVE_IMG_INTERVAL    = float(os.environ.get("LIVE_IMG_INTERVAL", "1.0"))
LIVE_IMG_DEFECT_GAP  = float(os.environ.get("LIVE_IMG_DEFECT_GAP", "0.5"))
LIVE_IMG_HOLD_DEFECT = float(os.environ.get("LIVE_IMG_HOLD_DEFECT", "2.0"))
LIVE_IMG_SCALE       = float(os.environ.get("LIVE_IMG_SCALE", "0.5"))   # 2448x2048 -> 1224x1024
LIVE_IMG_QUALITY     = int(os.environ.get("LIVE_IMG_QUALITY", "80"))
LOOP_SLEEP       = 0.3

# Model files - MUST match LOGIC._build_model
MODEL_CONFIG = os.path.join(L.MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
MODEL_FOLDER = os.path.join(L.MODEL_DIR, "TALOJA_MODEL_17OCT")
MODEL_FILE   = "model_final.pth"
CLASS_JSON   = os.path.join(L.MODEL_DIR, "TALOJA_MODEL_17OCT/TALOJA_MODEL_17OCT.json")
SCORE_THRESH = 0.50


# =========================================================================
# Batched Mask R-CNN
# =========================================================================
class BatchedMaskRCNN:
    """
    Same model, weights, thresholds and preprocessing as MASKRCNN /
    DefaultPredictor, but N frames per GPU call.

    preprocess() is DefaultPredictor's exact preprocessing (same
    ResizeShortestEdge transform, same PIL resize), run by the decode
    workers in parallel; infer() only uploads + runs the model.
    --compare checks old and new paths give the same detections.
    """

    def __init__(self, tag=MODEL_TAG, device=None):
        self.tag = tag
        if device is None:
            device = f"cuda:{GPU_ID}" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        if self.device.type == "cuda":
            torch.cuda.set_device(self.device)
            torch.backends.cudnn.benchmark = True

        with open(CLASS_JSON, "r") as f:
            label_map = json.load(f)
        self.class_array = np.array(list(label_map.values()))

        cfg = get_cfg()
        cfg.TEST.DETECTIONS_PER_IMAGE = 20
        cfg.merge_from_file(MODEL_CONFIG)
        cfg.MODEL.DEVICE = str(self.device)
        cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(label_map)
        cfg.OUTPUT_DIR = MODEL_FOLDER
        cfg.MODEL.WEIGHTS = os.path.join(MODEL_FOLDER, MODEL_FILE)
        cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = SCORE_THRESH
        self.cfg = cfg

        self.model = build_model(cfg)
        self.model.eval()
        DetectionCheckpointer(self.model).load(cfg.MODEL.WEIGHTS)

        self.min_size = cfg.INPUT.MIN_SIZE_TEST
        self.max_size = cfg.INPUT.MAX_SIZE_TEST
        self.flip_rgb = cfg.INPUT.FORMAT == "RGB"
        self.use_amp = INFER_FP16 and self.device.type == "cuda"
        # identical to DefaultPredictor.__init__ / __call__
        self.aug = T.ResizeShortestEdge([self.min_size, self.min_size], self.max_size)

    def preprocess(self, img):
        """DefaultPredictor.__call__ preprocessing, CPU, thread-safe.
        Returns the resized HxWx3 uint8 array the model will see."""
        if self.flip_rgb:
            img = img[:, :, ::-1]
        return self.aug.get_transform(img).apply_image(img)

    def _to_input(self, img, resized):
        h, w = img.shape[:2]
        t = torch.from_numpy(np.ascontiguousarray(resized)).to(self.device, non_blocking=True)
        # == torch.as_tensor(image.astype("float32").transpose(2, 0, 1))
        t = t.permute(2, 0, 1).float()
        return {"image": t, "height": h, "width": w}

    @torch.inference_mode()
    def infer(self, imgs, resized=None):
        """imgs: list of HxWx3 uint8 BGR (full size). resized: matching
        preprocess() outputs (computed here if not given). Returns one 'raw'
        per image: (boxes Nx4 float32 [x1,y1,x2,y2], scores N, class_names N,
        crops: list of None | (x1, y1, uint8 mask crop) for shape classes)."""
        if resized is None:
            resized = [self.preprocess(im) for im in imgs]
        inputs = [self._to_input(im, rs) for im, rs in zip(imgs, resized)]
        with torch.autocast("cuda", dtype=torch.float16, enabled=self.use_amp):
            outs = self.model(inputs)

        raws = []
        for out, im in zip(outs, imgs):
            inst = out["instances"]
            n = len(inst)
            boxes = inst.pred_boxes.tensor.float().cpu().numpy()
            scores = inst.scores.float().cpu().numpy()
            classes = inst.pred_classes.cpu().numpy()
            names = self.class_array[classes] if n else self.class_array[:0]

            crops = [None] * n
            if WS_MASK_PASSTHROUGH and n and inst.has("pred_masks"):
                H, W = im.shape[:2]
                for i in range(n):
                    if str(names[i]).upper() not in WS_SHAPE_CLASSES:
                        continue
                    # same int() truncation as INFERENCE.run_inference
                    x1 = max(0, int(boxes[i, 0])); x2 = min(W - 1, int(boxes[i, 2]))
                    y1 = max(0, int(boxes[i, 1])); y2 = min(H - 1, int(boxes[i, 3]))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    sub = inst.pred_masks[i, y1:y2 + 1, x1:x2 + 1]
                    crops[i] = (x1, y1, sub.to("cpu").numpy().astype(np.uint8))
            raws.append((boxes, scores, names, crops))
        del outs, inputs
        return raws


def build_labels(raw):
    """raw -> the exact label tuples MASKRCNN.run_inference returns:
    (score, ys, ye, xs, xe, class_name, cy, cx, [w, h], polygon|None)."""
    boxes, scores, names, crops = raw
    if len(scores) == 0:
        return []
    xs = boxes[:, 0].astype(int)
    ys = boxes[:, 1].astype(int)
    xe = boxes[:, 2].astype(int)
    ye = boxes[:, 3].astype(int)
    cx = ((xs + xe) * 0.5).astype(int)
    cy = ((ys + ye) * 0.5).astype(int)
    wh = np.stack([(xe - xs), (ye - ys)], axis=1).tolist()

    polys = [None] * len(names)
    for i, c in enumerate(crops):
        if c is None:
            continue
        try:
            x1, y1, sub = c
            cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            cc = max(cnts, key=cv2.contourArea)
            cc[:, :, 0] += x1
            cc[:, :, 1] += y1
            polys[i] = cc.reshape(-1, 2).tolist()
        except Exception:
            polys[i] = None

    return list(zip(scores, ys, ye, xs, xe, names, cy, cx, wh, polys))


# =========================================================================
# Frame discovery
# =========================================================================
def frame_key(name):
    """Capture order: CAPTURE names frames ...__<counter>__<timestamp>.jpg"""
    try:
        return (int(name.split("__")[2]), name)
    except Exception:
        return (10 ** 12, name)


def frame_meta(name):
    """(diameter, eg_setpoint, distance) from the CAPTURE file name."""
    try:
        meta = name.split("__")[1].split("_")
        return meta[0], meta[2], meta[4]
    except Exception:
        return "0", "0", "0"


class Job:
    """One cycle folder being processed (live coil or backlog/orphan)."""

    def __init__(self, uid, live, mqtt_triggered=True):
        self.uid = uid
        self.live = live                    # currently THE live coil
        self.started_as_live = live
        self.mqtt_triggered = mqtt_triggered
        self.base_path = os.path.join(L.RAW_PATH, L.LOCATION, uid)
        try:
            self.date_str = uid.split("_")[1][:8]
            datetime.strptime(self.date_str, "%Y%m%d")
        except Exception:
            self.date_str = datetime.now().strftime("%Y%m%d")

        self.conn = L.getDBconn()
        try:
            self.coil_id = L.get_coil_id_from_millruns(self.conn, uid)
        except Exception:
            self.coil_id = ""

        self.lock = Lock()
        self.results = []
        self.summary = {
            "cycle_uid": uid, "coil_id": self.coil_id,
            "total_frames": 0, "total_defects": 0,
            "defect_frames": 0, "total_defective_frames": 0,
            "O": 0, "W": 0, "S": 0, "D": 0, "C": 0,
        }
        self.seen = {}                      # cam -> set(names queued)
        self.next_rank = {}                 # cam -> next rank
        self.pending = deque()              # (cam, cam_path, rank, name)
        self.unsettled = 0                  # files still too new to read
        self.stopped = False
        self.stop_time = 0.0
        self.t_start = time.time()
        self.last_new = time.time()         # last time a new file appeared
        self.done = 0
        self.defect_dirty = False
        self.last_ui = 0.0
        self.last_img = 0.0                 # last live dashboard image
        self.last_defect_img = 0.0          # last live image that had defects

    def discover(self, settle=FILE_SETTLE_SECS):
        """Queue new frames in capture order. Files younger than `settle`
        may still be mid-write (CAPTURE writes in place) and are left for
        the next pass; nothing after the first unsettled frame is queued, so
        ranks always follow capture order."""
        self.unsettled = 0
        if not os.path.isdir(self.base_path):
            return 0
        added = 0
        now = time.time()
        for cam in sorted(os.listdir(self.base_path)):
            cam_path = os.path.join(self.base_path, cam)
            if not os.path.isdir(cam_path):
                continue
            seen = self.seen.setdefault(cam, set())
            try:
                new = [n for n in os.listdir(cam_path) if n not in seen]
            except OSError:
                continue
            if not new:
                continue
            self.last_new = now
            new.sort(key=frame_key)
            rank = self.next_rank.get(cam, 1)
            for i, name in enumerate(new):
                if settle > 0:
                    try:
                        if now - os.path.getmtime(os.path.join(cam_path, name)) < settle:
                            self.unsettled += len(new) - i
                            break
                    except OSError:
                        continue
                seen.add(name)
                self.pending.append((cam, cam_path, rank, name))
                rank += 1
                added += 1
            self.next_rank[cam] = rank
        return added

    def take(self, n):
        out = []
        while self.pending and len(out) < n:
            out.append(self.pending.popleft())
        return out


# =========================================================================
# Processor: decode -> GPU batch -> post -> trash save
# =========================================================================
class Processor:

    def __init__(self, engine, mq_obj):
        self.engine = engine
        self.mq = mq_obj
        self.decode_pool = ThreadPoolExecutor(DECODE_WORKERS, thread_name_prefix="decode")
        self.post_pool = ThreadPoolExecutor(POST_WORKERS, thread_name_prefix="post")
        self.trash_q = Queue(maxsize=TRASH_QUEUE_MAX)
        for i in range(TRASH_WORKERS):
            threading.Thread(target=self._trash_worker, name=f"trash-{i}", daemon=True).start()
        # perf counters (status line)
        self.stat_lock = Lock()
        self.win_frames = 0
        self.win_gpu = 0.0
        self.win_start = time.time()

    # ---- trash copies (from the already-decoded frame, no second decode) ----
    def _trash_worker(self):
        while True:
            img, path = self.trash_q.get()
            try:
                L.save_small_compressed(img, path)
            except Exception:
                logger.error(f"trash save failed {path}: {traceback.format_exc()}")
            finally:
                self.trash_q.task_done()

    def _queue_trash(self, img, path):
        try:
            self.trash_q.put_nowait((img, path))
        except Full:
            t0 = time.time()
            self.trash_q.put((img, path))           # back-pressure, never drop
            waited = time.time() - t0
            if waited > 1.0:
                logger.warning(f"trash save queue full - waited {waited:.1f}s (disk slow?)")

    # ---- per-frame post-processing (CPU, parallel) ----
    def _post(self, job, item, img, raw):
        cam, cam_path, rank, name = item
        try:
            diameter, eg_setpoint, distance = frame_meta(name)
            labels = build_labels(raw)
            boxes, counts = L.process_labels(labels, img.shape)
            total = sum(counts.values())
            severity = L.calculate_severity(total)

            scaled = L.scale_defect_boxes(boxes, img.shape, L.SAVE_SCALE)
            if L.SAVE_SCALE and L.SAVE_SCALE != 1.0:
                comp_h = int(round(img.shape[0] * L.SAVE_SCALE))
                comp_w = int(round(img.shape[1] * L.SAVE_SCALE))
            else:
                comp_h, comp_w = img.shape[0], img.shape[1]

            save_path = os.path.join(L.TRASH_PATH, job.uid, cam, name)
            self._queue_trash(img, save_path)

            defect_json = {
                "r": rank, "d": diameter, "e": eg_setpoint, "l": distance,
                "s": severity, **scaled, "t": counts,
                "img": save_path, "raw_img": save_path,
                "compressed_width": comp_w, "compressed_height": comp_h,
                "scale": L.SAVE_SCALE,
            }

            now_t = time.time()
            publish = None
            write_debug = False
            with job.lock:
                s = job.summary
                s["processed_at"] = datetime.now().isoformat()
                s["last_severity"] = severity
                s["distance"] = distance
                if total > 0:
                    for k in ("O", "W", "S", "D", "C"):
                        s[k] += counts.get(k, 0)
                    s["total_defects"] += sum(counts.get(k, 0) for k in ("O", "W", "S", "D", "C"))
                    s["defect_frames"] = s.get("defect_frames", 0) + 1
                    s["total_defective_frames"] = s.get("total_defective_frames", 0) + 1
                    job.defect_dirty = True
                s["total_frames"] = s.get("total_frames", 0) + 1
                job.results.append(defect_json)
                job.done += 1
                if job.live and (total > 0 or now_t - job.last_ui > L.MQTT_UI_INTERVAL):
                    job.last_ui = now_t
                    publish = dict(s)
                if job.live:
                    if total > 0:
                        write_debug = now_t - job.last_img >= LIVE_IMG_DEFECT_GAP
                    else:
                        write_debug = (now_t - job.last_img >= LIVE_IMG_INTERVAL and
                                       now_t - job.last_defect_img >= LIVE_IMG_HOLD_DEFECT)
                    if write_debug:
                        job.last_img = now_t
                        if total > 0:
                            job.last_defect_img = now_t

            if publish is not None:
                self.mq.publish("ui/logic", publish)

            if write_debug:
                self._write_live_image(cam, img, boxes, counts, severity, distance)
        except Exception:
            logger.error(f"post-processing failed {job.uid}/{name}: {traceback.format_exc()}")

    # ---- live dashboard image ----
    def _write_live_image(self, cam, img, boxes, counts, severity, distance):
        """Annotated frame for the dashboard Camera Feed. Downscaled first,
        then boxes drawn on the small image (labels stay readable)."""
        try:
            if LIVE_IMG_SCALE and LIVE_IMG_SCALE != 1.0:
                small = cv2.resize(img, None, fx=LIVE_IMG_SCALE, fy=LIVE_IMG_SCALE,
                                   interpolation=cv2.INTER_AREA)
            else:
                small = img.copy()
            sboxes = L.scale_defect_boxes(boxes, img.shape, LIVE_IMG_SCALE)
            small = L.draw_boxes(small, sboxes)
            small = L.add_summary(small, counts, severity, distance)
            ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), LIVE_IMG_QUALITY])
            if not ok:
                return
            out_dir = os.path.join(L.TEMP_PATH, L.LOCATION)
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{cam}_debug.jpg")
            tmp = f"{path}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(buf.tobytes())
            os.replace(tmp, path)
        except Exception:
            logger.error(f"live image write failed: {traceback.format_exc()}")

    # ---- decode worker: read JPEG + DefaultPredictor preprocessing ----
    def _load(self, path):
        img = cv2.imread(path)
        if img is None:
            return None, None
        return img, self.engine.preprocess(img)

    # ---- GPU with fallback ----
    def _infer(self, imgs, resized):
        try:
            return self.engine.infer(imgs, resized)
        except Exception:
            logger.error(f"batch inference failed ({len(imgs)} frames), retrying one by one: "
                         f"{traceback.format_exc()}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            out = []
            for im, rs in zip(imgs, resized):
                try:
                    out.append(self.engine.infer([im], [rs])[0])
                except Exception:
                    logger.error(f"single-frame inference failed: {traceback.format_exc()}")
                    out.append(None)
            return out

    def run(self, job, items):
        """Process `items` of `job` completely (decode ahead, GPU in batches,
        post in parallel). Returns when every item has been post-processed."""
        if not items:
            return
        it = iter(items)
        inflight = deque()

        def submit_next():
            item = next(it, None)
            if item is None:
                return False
            path = os.path.join(item[1], item[3])
            inflight.append((item, self.decode_pool.submit(self._load, path)))
            return True

        for _ in range(BATCH_SIZE * 3):
            if not submit_next():
                break

        post_futs = deque()
        while inflight:
            batch = []
            while inflight and len(batch) < BATCH_SIZE:
                item, fut = inflight.popleft()
                submit_next()
                try:
                    img, rs = fut.result()
                except Exception:
                    img, rs = None, None
                if img is None:
                    logger.warning(f"unreadable frame skipped: {os.path.join(item[1], item[3])}")
                    continue
                batch.append((item, img, rs))
            if not batch:
                continue

            t0 = time.time()
            raws = self._infer([b[1] for b in batch], [b[2] for b in batch])
            gpu_secs = time.time() - t0
            with self.stat_lock:
                self.win_frames += len(batch)
                self.win_gpu += gpu_secs

            for (item, img, _rs), raw in zip(batch, raws):
                if raw is None:
                    continue
                post_futs.append(self.post_pool.submit(self._post, job, item, img, raw))
            while len(post_futs) > BATCH_SIZE * 4:          # bound RAM
                post_futs.popleft().result()

        while post_futs:
            post_futs.popleft().result()

    def perf_window(self):
        with self.stat_lock:
            secs = max(1e-6, time.time() - self.win_start)
            fps = self.win_frames / secs
            gpu_ms = 1000.0 * self.win_gpu / max(1, self.win_frames)
            self.win_frames = 0
            self.win_gpu = 0.0
            self.win_start = time.time()
        return fps, gpu_ms


# =========================================================================
# Finalize (same outputs as LOGIC.process_cycle / process_cycle_stream)
# =========================================================================
def finalize(job, proc, show_live):
    with job.lock:
        results = sorted(job.results, key=lambda x: x.get("r", 0))
        summary = dict(job.summary)

    secs = time.time() - job.t_start
    msg = (f"Cycle Done: {job.uid} ({'live' if job.started_as_live else 'backlog'}) "
           f"frames={summary.get('total_frames', 0)} defects={summary.get('total_defects', 0)} "
           f"took={secs:.0f}s")
    print(msg, flush=True)
    logger.info(msg)

    json_dir = os.path.join(L.RESULT_PATH, job.date_str, job.uid)
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, f"{job.uid}_defect_data.json")

    if not job.started_as_live and os.path.exists(json_path):
        # same as LOGIC.process_cycle: append to an existing cycle JSON
        try:
            with open(json_path, "r") as f:
                existing = json.load(f)
            if isinstance(existing.get("result"), list):
                existing["result"].extend(results)
            else:
                existing["result"] = results
            with open(json_path, "w") as f:
                json.dump(existing, f)
            logger.info(f"Existing JSON updated: {json_path}")
        except Exception:
            logger.error(f"Failed to update JSON: {traceback.format_exc()}")
    else:
        with open(json_path, "w") as f:
            json.dump({"cycle_uid": job.uid, "coil_id": job.coil_id, "result": results}, f)
        logger.info(f"New JSON created: {json_path}")

    try:
        if job.conn is not None:
            job.conn.ping(reconnect=True)
        else:
            job.conn = L.getDBconn()
    except Exception:
        job.conn = L.getDBconn()
    L.save_defect_history(job.conn, job.uid, job.coil_id, json.dumps(summary), json_path)

    if show_live:
        try:
            L.update_live_json(job.uid, job.coil_id, summary, results=results, status="completed")
        except Exception:
            pass

    try:
        if os.path.exists(job.base_path):
            shutil.rmtree(job.base_path)
    except Exception:
        logger.error(f"could not delete raw folder {job.base_path}: {traceback.format_exc()}")

    try:
        if job.conn is not None:
            job.conn.close()
    except Exception:
        pass


# =========================================================================
# Service
# =========================================================================
class Service:

    def __init__(self, engine):
        self.mq = MQTT("logic_single")
        self.mq.subscribe("taloja/capture")
        self.proc = Processor(engine, self.mq)
        self.base = os.path.join(L.RAW_PATH, L.LOCATION)
        self.state_lock = Lock()
        self.live = None              # Job
        self.backlog = {}             # uid -> Job
        self.last_orphan_scan = 0.0
        self.last_fallback = 0.0
        self.last_status = time.time()
        self.live_json_last = 0.0

        threading.Thread(target=self._health_loop, name="health", daemon=True).start()
        threading.Thread(target=self._live_json_loop, name="live_json", daemon=True).start()

    # ---- background: health + live.json ----
    def _health_loop(self):
        while True:
            try:
                self.mq.publish("taloja/health", {"service": "LOGIC"})
                with self.state_lock:
                    live = self.live
                if live is None:
                    self.mq.publish("main/logic", {"roll_num": "--"})
            except Exception:
                pass
            time.sleep(1.0)

    def _live_json_loop(self):
        while True:
            try:
                with self.state_lock:
                    job = self.live
                if job is not None:
                    gap = time.time() - self.live_json_last
                    if gap > L.LIVE_JSON_INTERVAL or (job.defect_dirty and gap > L.LIVE_JSON_MIN_GAP):
                        with job.lock:
                            summary = dict(job.summary)
                            results = list(job.results)
                            job.defect_dirty = False
                        with self.state_lock:
                            still_live = self.live is job
                        if still_live:   # never overwrite a finished coil's 'completed'
                            L.update_live_json(job.uid, job.coil_id, summary,
                                               results=results, status="inprogress")
                            self.live_json_last = time.time()
            except Exception:
                logger.error(f"live.json writer error: {traceback.format_exc()}")
            time.sleep(0.25)

    # ---- live coil control ----
    def _start_live(self, uid, mqtt_triggered):
        with self.state_lock:
            job = self.backlog.pop(uid, None)
            if job is not None:                 # was being recovered: keep progress
                job.live = True
                job.mqtt_triggered = mqtt_triggered
            else:
                job = Job(uid, live=True, mqtt_triggered=mqtt_triggered)
            self.live = job
        L.reset_live_cache()
        try:
            L.update_live_json(job.uid, job.coil_id, dict(job.summary), results=[], status="inprogress")
        except Exception:
            pass
        self.live_json_last = time.time()
        mode = "mqtt" if mqtt_triggered else "FALLBACK"
        print(f"Streaming cycle: {uid} (mode={mode})", flush=True)
        logger.info(f"Streaming cycle: {uid} (mode={mode})")

    def _demote_live(self, reason):
        """Current live coil -> backlog, progress kept."""
        with self.state_lock:
            job = self.live
            if job is None:
                return
            job.live = False
            job.stopped = True
            self.backlog[job.uid] = job
            self.live = None
        logger.info(f"{job.uid} moved to backlog ({reason}), "
                    f"{job.done} done, {len(job.pending)} queued")
        print(f"{job.uid} moved to backlog ({reason})", flush=True)

    def _handle_mqtt(self):
        while True:
            msg = self.mq.pop("taloja/capture")
            if msg is None:
                return
            action = msg.get("action", "")
            roll = msg.get("roll_num", "")
            if action == "start" and roll:
                if self.live is not None and self.live.uid == roll:
                    continue                                  # duplicate start
                if self.live is not None:
                    self._demote_live(f"new START {roll}")
                print(f"START received for {roll}", flush=True)
                logger.info(f"START received for {roll}")
                self._start_live(roll, mqtt_triggered=True)
            elif action == "stop" and roll and self.live is not None and roll == self.live.uid:
                if not self.live.stopped:
                    self.live.stopped = True
                    self.live.stop_time = time.time()
                    print(f"STOP received for {roll}", flush=True)
                    logger.info(f"STOP received for {roll}")

    def _maybe_fallback_live(self):
        if not L.LIVE_FALLBACK or self.live is not None:
            return
        if time.time() - self.last_fallback < 1.0:
            return
        self.last_fallback = time.time()
        with self.state_lock:
            exclude = set(self.backlog)
        uid = L.find_active_cycle(self.base, exclude=exclude)
        if uid:
            logger.warning(f"No MQTT start seen but {uid} is being written - adopting as LIVE (fallback)")
            print(f"Adopting actively-written cycle as LIVE (fallback): {uid}", flush=True)
            self._start_live(uid, mqtt_triggered=False)

    def _live_step(self):
        """Process the live coil. True if work was done (loop again at once)."""
        job = self.live
        if job is None:
            return False
        job.discover(settle=FILE_SETTLE_SECS)    # never read a frame CAPTURE is still writing
        if job.pending:
            self.proc.run(job, job.take(LIVE_SLICE_MAX))
            return True

        idle = time.time() - job.last_new
        if not job.stopped:
            if not job.mqtt_triggered and idle > L.LIVE_FALLBACK_STOP_IDLE:
                print(f"Fallback live {job.uid}: idle {idle:.0f}s - treating as STOP", flush=True)
                logger.info(f"Fallback live {job.uid}: idle {idle:.0f}s - treating as STOP")
                job.stopped, job.stop_time = True, time.time()
            elif job.mqtt_triggered and idle > L.LIVE_STALL_TIMEOUT:
                logger.warning(f"LIVE {job.uid}: no STOP and no new images for {idle:.0f}s "
                               f"- auto-finalizing (lost STOP?)")
                print(f"LIVE {job.uid}: auto-finalizing after {idle:.0f}s idle (lost STOP?)", flush=True)
                job.stopped, job.stop_time = True, time.time()

        if (job.stopped and job.unsettled == 0
                and time.time() - job.stop_time > STOP_GRACE_SECS
                and idle > STOP_GRACE_SECS):
            with self.state_lock:
                self.live = None            # live.json writer stops before 'completed'
            finalize(job, self.proc, show_live=True)
            return True
        return False

    def _scan_orphans(self):
        if time.time() - self.last_orphan_scan < ORPHAN_SCAN_SECS:
            return
        self.last_orphan_scan = time.time()
        with self.state_lock:
            exclude = set(self.backlog)
            if self.live is not None:
                exclude.add(self.live.uid)
        for uid in L.find_orphan_cycles(self.base, idle_secs=ORPHAN_IDLE_SECS, exclude=exclude):
            with self.state_lock:
                if uid in self.backlog or (self.live is not None and self.live.uid == uid):
                    continue
                self.backlog[uid] = Job(uid, live=False)
            logger.info(f"Backlog cycle queued: {uid}")
            print(f"Backlog cycle queued: {uid}", flush=True)

    def _backlog_step(self):
        """One small slice of the oldest backlog cycle. True if work done."""
        with self.state_lock:
            if not self.backlog:
                return False
            # coils that started live first, then oldest uid first
            job = sorted(self.backlog.values(),
                         key=lambda j: (0 if j.started_as_live else 1, j.uid))[0]
        job.discover(settle=FILE_SETTLE_SECS)
        if job.pending:
            self.proc.run(job, job.take(BACKLOG_SLICE))
            return True
        if job.unsettled == 0 and time.time() - job.last_new > STOP_GRACE_SECS:
            finalize(job, self.proc, show_live=False)
            with self.state_lock:
                self.backlog.pop(job.uid, None)
            return True
        return False

    def _status(self):
        if time.time() - self.last_status < STATUS_EVERY:
            return
        self.last_status = time.time()
        fps, gpu_ms = self.proc.perf_window()
        with self.state_lock:
            live = self.live
            backlog = list(self.backlog.values())
        live_txt = (f"{live.uid} done={live.done} queued={len(live.pending)} "
                    f"waiting={live.unsettled}{' STOPPED' if live.stopped else ''}"
                    if live else "none")
        bl_txt = ", ".join(f"{j.uid}:{j.done}" for j in sorted(backlog, key=lambda j: j.uid)[:4])
        msg = (f"[STATUS] {fps:.1f} fps (gpu {gpu_ms:.0f} ms/frame, batch {BATCH_SIZE}) | "
               f"live: {live_txt} | backlog: {len(backlog)} cycle(s) {bl_txt} | "
               f"trash_q={self.proc.trash_q.qsize()}")
        print(msg, flush=True)
        logger.info(msg)

    def loop(self):
        print("SINGLE_LIVE_LOGIC started, waiting for triggers...", flush=True)
        logger.info("SINGLE_LIVE_LOGIC started")
        while True:
            self._handle_mqtt()
            self._maybe_fallback_live()
            self._status()
            if self._live_step():
                continue
            self._scan_orphans()
            if self._backlog_step():
                continue
            time.sleep(LOOP_SLEEP)


# =========================================================================
# Bench / compare (writes nothing: no DB, no RESULT, no MQTT, no deletes)
# =========================================================================
def _iou(a, b):
    if tuple(a) == tuple(b):
        return 1.0
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def bench(folder, n, compare, batches):
    files = sorted((f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))),
                   key=frame_key)[:n]
    if not files:
        print(f"No images in {folder}")
        return 1
    print(f"Bench on {len(files)} frames from {folder}")
    print(f"settings: FP16={INFER_FP16} decode_workers={DECODE_WORKERS} "
          f"torch_cpu_threads={torch.get_num_threads()}")

    t0 = time.time()
    engine = BatchedMaskRCNN()
    print(f"model  : {MODEL_TAG} loaded in {time.time() - t0:.1f}s on {engine.device} "
          f"(input {engine.min_size}/{engine.max_size})")

    def _load(f):
        im = cv2.imread(os.path.join(folder, f))
        return (im, engine.preprocess(im)) if im is not None else None

    t0 = time.time()
    with ThreadPoolExecutor(DECODE_WORKERS) as ex:
        loaded = [x for x in ex.map(_load, files) if x is not None]
    dec = time.time() - t0
    imgs = [x[0] for x in loaded]
    rss = [x[1] for x in loaded]
    print(f"decode+resize : {len(imgs) / dec:6.1f} fps with {DECODE_WORKERS} threads "
          f"({imgs[0].shape[1]}x{imgs[0].shape[0]} -> {rss[0].shape[1]}x{rss[0].shape[0]})")

    sync = (lambda: torch.cuda.synchronize()) if engine.device.type == "cuda" else (lambda: None)
    results = {}
    for b in batches:
        engine.infer(imgs[:b], rss[:b]); engine.infer(imgs[:b], rss[:b]); sync()   # warm-up
        t0 = time.time()
        k = 0
        for i in range(0, len(imgs), b):
            engine.infer(imgs[i:i + b], rss[i:i + b])
            k += len(imgs[i:i + b])
        sync()
        fps = k / (time.time() - t0)
        results[b] = fps
        mem = (f" | GPU mem peak {torch.cuda.max_memory_allocated() / 2**30:.1f} GB"
               if engine.device.type == "cuda" else "")
        print(f"GPU    : batch {b:2d} -> {fps:6.1f} fps{mem}")

    raws = engine.infer(imgs[:min(len(imgs), BATCH_SIZE)])
    t0 = time.time()
    for _ in range(5):
        for im, raw in zip(imgs, raws):
            L.process_labels(build_labels(raw), im.shape)
    post_ms = 1000 * (time.time() - t0) / (5 * len(raws))
    print(f"post   : {post_ms:.1f} ms/frame on 1 thread ({POST_WORKERS} threads in service)")

    best_b = max(results, key=results.get)
    print(f"\n==> best GPU speed: {results[best_b]:.1f} fps at batch {best_b}. "
          f"Camera at 30 fps needs > 30 fps; the rest goes to backlog coils.")

    if compare > 0:
        _compare(engine, imgs[:compare])
    return 0


def _compare(engine, imgs):
    print(f"\nCompare with OLD path (MASKRCNN/DefaultPredictor, FP16={INFER_FP16}) on {len(imgs)} frames...")
    old = L._build_model(MODEL_TAG)
    old.use_amp = INFER_FP16 and engine.device.type == "cuda"

    t0 = time.time()
    old_labels = [old.run_inference(im)[1] for im in imgs]
    old_ms = 1000 * (time.time() - t0) / len(imgs)

    new_labels = []
    for i in range(0, len(imgs), BATCH_SIZE):
        for raw in engine.infer(imgs[i:i + BATCH_SIZE]):
            new_labels.append(build_labels(raw))

    n_old = n_new = matched = 0
    dscore = []
    same_counts = 0
    for im, lo, ln in zip(imgs, old_labels, new_labels):
        n_old += len(lo)
        n_new += len(ln)
        used = set()
        for o in lo:
            ob = (o[3], o[1], o[4], o[2])                  # xs, ys, xe, ye
            best, bj = 0.0, None
            for j, nl in enumerate(ln):
                if j in used or str(nl[5]) != str(o[5]):
                    continue
                v = _iou(ob, (nl[3], nl[1], nl[4], nl[2]))
                if v > best:
                    best, bj = v, j
            if bj is not None and best >= 0.8:
                used.add(bj)
                matched += 1
                dscore.append(abs(float(o[0]) - float(ln[bj][0])))
        _, co = L.process_labels(lo, im.shape)
        _, cn = L.process_labels(ln, im.shape)
        same_counts += int(dict(co) == dict(cn))

    print(f"old path: {old_ms:.0f} ms/frame (single frame, as running today)")
    print(f"detections: old={n_old} new={n_new} matched(IoU>=0.8, same class)={matched}"
          + (f" ({100 * matched / n_old:.1f}% of old)" if n_old else ""))
    if dscore:
        print(f"score difference on matched: mean {np.mean(dscore):.4f}, max {np.max(dscore):.4f}")
    print(f"frames with identical final defect counts (after LOGIC rules): "
          f"{same_counts}/{len(imgs)}")
    del old
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# =========================================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", metavar="FOLDER", help="speed test on a folder of frames (writes nothing)")
    ap.add_argument("--n", type=int, default=200, help="frames to use for --bench")
    ap.add_argument("--compare", type=int, default=0, help="also compare N frames with the old path")
    ap.add_argument("--batches", default="1,2,4,8,12", help="batch sizes to test in --bench")
    args = ap.parse_args()

    if args.bench:
        sys.exit(bench(args.bench, args.n, args.compare,
                       [int(b) for b in args.batches.split(",") if b.strip()]))

    engine = BatchedMaskRCNN()
    msg = (f"SINGLE_LIVE_LOGIC model ready: {MODEL_TAG} on {engine.device} | FP16={engine.use_amp} "
           f"batch={BATCH_SIZE} decode={DECODE_WORKERS} post={POST_WORKERS} trash={TRASH_WORKERS}")
    print(msg, flush=True)
    logger.info(msg)

    service = Service(engine)
    while True:
        try:
            service.loop()
        except Exception:
            logger.critical(f"service crashed:\n{traceback.format_exc()}")
            print("Error in SINGLE_LIVE_LOGIC ! Restarting in 5 secs", flush=True)
            time.sleep(5)