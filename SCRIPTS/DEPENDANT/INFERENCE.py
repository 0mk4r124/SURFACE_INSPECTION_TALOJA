import cv2
import gc
import json
import os
import traceback
import torch
# import time

import numpy as np

from datetime import datetime
from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog
from detectron2.engine import DefaultPredictor
from detectron2.utils.visualizer import Visualizer, ColorMode

# =========================================================================
# White-spot polygon pass-through
# -------------------------------------------------------------------------
# For classes listed here, the segmentation mask is converted to a compact
# contour polygon (full-frame coordinates) and appended as element [9] of
# each label tuple. Everything else in the tuple keeps its position
# (indices 0-8 are unchanged), so existing consumers are unaffected.
#
# The mask is cropped to the detection box in torch BEFORE the numpy
# conversion, so the per-instance cost is tiny.
# =========================================================================
WS_MASK_PASSTHROUGH = os.environ.get("WS_MASK_PASSTHROUGH", "1") == "1"
WS_SHAPE_CLASSES = {
    c.strip().upper()
    for c in os.environ.get("WS_SHAPE_CLASSES", "WHITE_SPOT").split(",")
    if c.strip()
}


class MASKRCNN:

    def __init__(self, tag, CONFIG_PATH, mask_model_path, model_file, thr_acc, class_json, debugMode=False, GPU_ID=0):
        self.predictor = None
        self.GPU_ID = GPU_ID
        self.tag = tag
        self.mrcnn_config_fl = CONFIG_PATH
        self.mrcnn_model_loc=   mask_model_path
        self.mrcnn_model_fl = model_file
        self.detection_thresh = thr_acc
        self.class_json = class_json
        self.labelMap = {}
        self.debugMode = debugMode
        self.register_modeldatasets()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        return False  # Propagate any exceptions

    def __loadLablMap__(self):
        
        with open(self.class_json, "r") as fl:
            self.labelMap=json.load(fl)
        return self.labelMap
    
    def register_modeldatasets(self):
        try:
            tag = self.tag
            torch.backends.cudnn.benchmark = True
            torch.cuda.set_device(self.GPU_ID)
            self.labelMap = self.__loadLablMap__()
            self.class_array = np.array(list(self.labelMap.values()))
            self.class_list = list(self.labelMap.values())
            MetadataCatalog.get(tag).set(thing_classes=self.class_list)

            self.metadata = MetadataCatalog.get(tag)
            cfg = get_cfg()
            cfg.TEST.DETECTIONS_PER_IMAGE = 20
            cfg.merge_from_file(self.mrcnn_config_fl)
            if self.debugMode: cfg.merge_from_list(["MODEL.DEVICE", "cpu"])
            else: cfg.MODEL.DEVICE = f"cuda:{self.GPU_ID}"

            cfg.MODEL.ROI_HEADS.NUM_CLASSES = len(self.labelMap)
            cfg.OUTPUT_DIR = self.mrcnn_model_loc
            cfg.MODEL.WEIGHTS = os.path.join(cfg.OUTPUT_DIR, self.mrcnn_model_fl)
            cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = self.detection_thresh
            self.predictor = DefaultPredictor(cfg)
            
        except Exception as e:
            print(f"register_modeldatasets() Exception is: {e}")
            print(f"{traceback.format_exc()}")
            raise

    def get_centroid(self, xmin, xmax, ymin, ymax):
        cx = int((xmin + xmax) / 2.0)
        cy = int((ymin + ymax) / 2.0)
        return (cx, cy)

    def _extract_polys(self, instances, class_names, xs, xe, ys, ye, img_shape):
        """Contour polygons (full-frame coords) for shape-analysed classes only.
        Returns a list aligned with the detections; None where not applicable."""
        n = len(class_names)
        polys = [None] * n

        if not (WS_MASK_PASSTHROUGH and n and instances.has("pred_masks")):
            return polys

        want = [
            i for i, c in enumerate(class_names)
            if str(c).upper() in WS_SHAPE_CLASSES
        ]
        if not want:
            return polys

        H, W = img_shape[:2]

        for i in want:
            try:
                x1 = max(0, int(xs[i])); x2 = min(W - 1, int(xe[i]))
                y1 = max(0, int(ys[i])); y2 = min(H - 1, int(ye[i]))
                if x2 <= x1 or y2 <= y1:
                    continue

                # crop in torch first -> tiny numpy array
                sub = instances.pred_masks[i, y1:y2 + 1, x1:x2 + 1]
                sub = sub.numpy().astype(np.uint8)

                cnts, _ = cv2.findContours(
                    sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                if not cnts:
                    continue

                c = max(cnts, key=cv2.contourArea)
                c[:, :, 0] += x1          # back to full-frame coords
                c[:, :, 1] += y1
                polys[i] = c.reshape(-1, 2).tolist()

            except Exception:
                polys[i] = None

        return polys

    def run_inference(self, img):
        labellist = []

        try:
            with torch.inference_mode():
                use_amp = getattr(self, "use_amp", False)
                with torch.inference_mode():
                    with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
                        output = self.predictor(img)
                    instances = output["instances"].to("cpu")

                # torch.cuda.synchronize()
                # t0 = time.perf_counter()
                # output = self.predictor(img)
                # torch.cuda.synchronize()
                # print((time.perf_counter()-t0)*1000)
                
                # instances = output["instances"].to("cpu")

                boxes = instances.pred_boxes.tensor.numpy()
                classes = instances.pred_classes.numpy()
                scores = instances.scores.numpy()

                ys = boxes[:, 1].astype(int)
                xs = boxes[:, 0].astype(int)
                ye = boxes[:, 3].astype(int)
                xe = boxes[:, 2].astype(int)

                # centroid
                cx = ((xs + xe) * 0.5).astype(int)
                cy = ((ys + ye) * 0.5).astype(int)

                # widths and heights
                wh = np.stack([(xe - xs), (ye - ys)], axis=1).tolist()

                # Map class IDs to class names once
                class_names = self.class_array[classes]

                # ---- polygon extraction: ONLY for shape-analysed classes ----
                polys = self._extract_polys(
                    instances, class_names, xs, xe, ys, ye, img.shape
                )

                labellist = list(zip(
                    scores,
                    ys, ye, xs, xe,
                    class_names,
                    cy, cx,
                    wh,
                    polys          # index 9 (None for non-shape classes)
                ))

                # np.random.seed(100)
                # COLORS = np.random.randint(150, 200, size=(200, 3),dtype="uint8")
                # if self.debugMode is True:
                # visualizer = Visualizer(img[:, :, ::-1], metadata=self.metadata, scale=1, instance_mode=ColorMode.IMAGE)
                # img = visualizer.draw_instance_predictions(output["instances"].to("cpu"))
                # img = np.array(img.get_image()[:, :, ::-1])

        except Exception as e:
            print(f"run_inference() Exception is : {e}")
            print(f"{traceback.format_exc()}")

        return img, labellist
    

# # EXAMPLE USAGE:

#     model_file = "model_final.pth"
#     model_config_file = os.path.join(BASE_DIR, "SCRIPTS", "configs", "COCO-InstanceSegmentation", "mask_rcnn_R_101_FPN_3x.yaml")
#     model_location = os.path.join(BASE_DIR, "SCRIPTS", "MODEL")
#     class_json = os.path.join(model_location, "JSON.json")
#     gpu_id = 1
#     detection_threshold = 0.5
#     debugMode = False

#     mrcnn = MASKRCNN("anodes", model_config_file, model_location, model_file, detection_threshold, class_json, debugMode, GPU_ID=gpu_id)
#     masked_img, labels = mrcnn.run_inference(img)