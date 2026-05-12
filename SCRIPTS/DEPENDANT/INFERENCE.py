import cv2
import gc
import json
import os
import traceback
import torch

import numpy as np

from datetime import datetime
from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog
from detectron2.engine import DefaultPredictor
from detectron2.utils.visualizer import Visualizer, ColorMode

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
            torch.cuda.set_device(self.GPU_ID)
            self.labelMap = self.__loadLablMap__()
            self.class_list = np.array(list(self.labelMap.values()))
            MetadataCatalog.get(tag).set(thing_classes=self.class_list)

            self.metadata = MetadataCatalog.get(tag)
            cfg = get_cfg()
            cfg.TEST.DETECTIONS_PER_IMAGE = 500
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
    
    def run_inference(self, img):
        labellist = []

        try:
            output = self.predictor(img)       
            
            boxes_surface = output["instances"].pred_boxes.tensor.to("cpu").numpy()
            pred_class_surface = output["instances"].pred_classes.to("cpu").numpy()
            scores_surface = output["instances"].scores.to("cpu").numpy()
            np.random.seed(100)
            COLORS = np.random.randint(150, 200, size=(200, 3),dtype="uint8")

            ys = boxes_surface[:, 1].astype(int)
            xs = boxes_surface[:, 0].astype(int)
            ye = boxes_surface[:, 3].astype(int)
            xe = boxes_surface[:, 2].astype(int)

            # centroid
            cx = ((xs + xe) * 0.5).astype(int)
            cy = ((ys + ye) * 0.5).astype(int)

            # widths and heights
            wh = np.stack([(xe - xs), (ye - ys)], axis=1).tolist()

            # Map class IDs to class names once
            class_names = self.class_list[pred_class_surface]

            labellist = list(zip(
                scores_surface,
                ys, ye, xs, xe,
                class_names,
                cy, cx,
                wh
            ))

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