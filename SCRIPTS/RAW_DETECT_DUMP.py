"""
RAW_DETECT_DUMP - what does the model REALLY output on a frame?

Standalone diagnostic (not a service, no DEPENDANT imports, writes nothing
to the DB). Answers questions the coil map cannot:

  * which class names does TALOJA_MODEL_21OCT output at all? (label map)
  * on a "dent" frame, did the model predict DENT at a low score, or a DENT
    that was hidden by a higher-score OIL_STAIN in the cross-class dedup?
  * why is there no SCORING / DENT on the map: never predicted, below a
    threshold, shorter than SCORING_MIN_LEN_MM, or an unmapped class name?

Every detection down to --thresh is printed with the verdict the live rules
(LOGIC.process_labels) would give it. Annotated copies go to
DEBUGS/TEMP_IMG/RAW_DUMP/.

Run from SCRIPTS/ (raw OR trash frames both work, full-size preferred):
    python3 RAW_DETECT_DUMP.py <img or folder> [more ...] [--thresh 0.10] [--cpu]

GPU note: loads ONE extra model (~3 GB). Use --cpu if the live service is
busy (slow, ~10-20 s/frame, fine for a handful of frames).
"""

import os
import sys
import json
import argparse

import cv2
import numpy as np
import torch

from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')
MODEL_DIR   = os.path.join(BASE_FILE_PATH, "DEBUGS", "MODEL")
MODEL_CFG   = os.path.join(MODEL_DIR, "configs/COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml")
MODEL_FOLD  = os.path.join(MODEL_DIR, "TALOJA_MODEL_21OCT")
MODEL_FILE  = "model_final.pth"
CLASS_JSON  = os.path.join(MODEL_FOLD, "TALOJA_MODEL_21OCT.json")
OUT_DIR     = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG", "RAW_DUMP")

# mirror of LOGIC.py rules (keep in sync by hand - this file imports nothing from it)
FRAME_WIDTH_MM     = float(os.environ.get("FRAME_WIDTH_MM", "400"))
SCORING_MIN_LEN_MM = float(os.environ.get("SCORING_MIN_LEN_MM", "40"))
DEDUP_OVERLAP      = float(os.environ.get("DEDUP_OVERLAP", "0.80"))
LIVE_THRESH        = 0.50
MAP_MIN_CONF       = {"W": 90, "O": 90}

COLORS = {"O": (255, 0, 0), "W": (200, 200, 200), "S": (0, 255, 255),
          "D": (0, 0, 255), "C": (0, 255, 0), "?": (255, 0, 255)}


def map_class(raw):
    raw = str(raw).upper()
    if "OIL" in raw:          return "O"
    if raw == "WHITE_SPOT":   return "W"
    if raw == "SCORING":      return "S"
    if raw == "DENT":         return "D"
    if raw == "SCRATCH":      return "C"
    return "?"


def live_verdict(cls, score, x1, y1, x2, y2, w):
    if score < LIVE_THRESH:
        return f"below model thresh {LIVE_THRESH}"
    if cls == "?":
        return "UNMAPPED class name -> dropped"
    if cls == "O" and score <= 0.75:
        return "oil <= 0.75 -> dropped"
    if cls == "W" and score <= 0.80:
        return "white spot <= 0.80 -> dropped"
    if cls == "S":
        min_px = SCORING_MIN_LEN_MM * w / FRAME_WIDTH_MM
        if max(x2 - x1, y2 - y1) <= min_px:
            return f"scoring shorter than {SCORING_MIN_LEN_MM:.0f} mm -> dropped"
    if cls in MAP_MIN_CONF and round(score * 100) < MAP_MIN_CONF[cls]:
        return f"kept, but hidden on map (< {MAP_MIN_CONF[cls]}%)"
    return "KEPT"


def overlap_small(a, b):
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    sa = (a[2] - a[0]) * (a[3] - a[1])
    sb = (b[2] - b[0]) * (b[3] - b[1])
    return iw * ih / float(min(sa, sb) or 1)


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(os.path.join(p, f) for f in os.listdir(p)
                          if f.lower().endswith((".jpg", ".jpeg", ".png")))
        elif os.path.isfile(p):
            out.append(p)
        else:
            print(f"not found: {p}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--thresh", type=float, default=0.10, help="model score threshold for this dump")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()

    with open(CLASS_JSON) as f:
        label_map = json.load(f)
    names = list(label_map.values())
    print(f"label map ({CLASS_JSON}):")
    for k, v in label_map.items():
        print(f"   {k}: {v!r:20s} -> {map_class(v)}")

    cfg = get_cfg()
    cfg.TEST.DETECTIONS_PER_IMAGE = 50
    cfg.merge_from_file(MODEL_CFG)
    cfg.MODEL.DEVICE = "cpu" if (a.cpu or not torch.cuda.is_available()) else "cuda:0"
    cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(label_map)
    cfg.MODEL.WEIGHTS = os.path.join(MODEL_FOLD, MODEL_FILE)
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = a.thresh
    pred = DefaultPredictor(cfg)
    os.makedirs(OUT_DIR, exist_ok=True)

    for path in collect(a.paths):
        img = cv2.imread(path)
        if img is None:
            print(f"\n{path}: unreadable"); continue
        h, w = img.shape[:2]
        mmpp = FRAME_WIDTH_MM / w
        with torch.inference_mode():
            inst = pred(img)["instances"].to("cpu")
        boxes = inst.pred_boxes.tensor.numpy()
        scores = inst.scores.numpy()
        cls_ids = inst.pred_classes.numpy()

        order = np.argsort(-scores)
        print(f"\n{os.path.basename(path)}  ({w}x{h}, {len(order)} detections >= {a.thresh})")
        if w < 2000:
            print("   NOTE: frame is downscaled - mm sizes and the scoring length gate are approximate")
        vis = img.copy()
        for i in order:
            x1, y1, x2, y2 = boxes[i]
            raw = names[cls_ids[i]]
            c = map_class(raw)
            v = live_verdict(c, float(scores[i]), x1, y1, x2, y2, w)
            ov = [f"{map_class(names[cls_ids[j]])}@{scores[j]:.2f} ovl={overlap_small(boxes[i], boxes[j]):.2f}"
                  for j in order if j != i and overlap_small(boxes[i], boxes[j]) > 0.3]
            print(f"   {raw:14s} {scores[i]:.3f}  box=({int(x1)},{int(y1)})-({int(x2)},{int(y2)})  "
                  f"{(x2 - x1) * mmpp:5.1f}x{(y2 - y1) * mmpp:5.1f} mm  -> {v}"
                  + (f"   overlaps: {', '.join(ov)}" if ov else ""))
            col = COLORS.get(c, COLORS["?"])
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
            cv2.putText(vis, f"{raw} {scores[i]:.2f}", (int(x1), max(15, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
        print(f"   (cross-class dedup keeps only the higher score when ovl > {DEDUP_OVERLAP})")
        out = os.path.join(OUT_DIR, os.path.basename(path))
        cv2.imwrite(out, vis)
        print(f"   annotated -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
