import cv2
import os
from datetime import datetime

from SCRIPTS.DEPENDANT.FLIR import FlirCamera
# from SCRIPTS.DEPENDANT.BASLER import BaslerCamera
# from SCRIPTS.DEPENDANT.SNAP7 import PLCCommunication
# from DEPENDANT.INFERENCE import MASKRCNN

BASE_DIR = '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/'

def main():
    flir_config = {
        "24521939": {
            "EXPOSURE": 15000.0,
            "FRAME_RATE": 1.0,
            "THROUGHT_PUT": 6500000,
        }
    }

    # basler_config = {
    #     "serial_number": "24782564",
    #     "exposure_time": 20000,
    #     "frame_rate": 10.0,
    # }

    with FlirCamera(flir_config) as flir:
        all_cam_list = flir.initialize()
        if any(all_cam_list):
            flir.start_acquisition(all_cam_list[0])

            t1 = datetime.now()
            while (datetime.now()-t1).total_seconds()<10:
                img = flir.capture(all_cam_list[0])
                cv2.imwrite(f'{BASE_DIR}/SCRIPTS/TEMP/temp_{datetime.now()}.jpg', img)

            flir.end_acquisition(all_cam_list[0])
            flir.deinitialize(all_cam_list[0])  # better to pass cam explicitly

    # with BaslerCamera(basler_config) as basler:
    #     if basler.initialize():
    #         basler.start_acquisition()

    #         t1 = datetime.now()
    #         while (datetime.now()-t1).total_seconds()<10:
    #             img = basler.capture()
    #             cv2.imwrite(f'/home/insightzz-server5/INSIGHTZZ/OMKAR/SURFACE_INSPECTION_TALOJA/SCRIPTS/TEMP/temp_basler_{datetime.now()}.jpg', img)

    #         basler.end_acquisition()
    #         basler.deinitialize()

    # mask_obj = MASKRCNN('Gears', BASE_DIR+'SCRIPTS/configs/COCO-InstanceSegmentation/mask_rcnn_R_101_FPN_3x.yaml', BASE_DIR+'SCRIPTS/MODEL/', 'model_final.pth', 0.5, 'SCRIPTS/MODEL/JSON.json', debugMode=True)

    # for img_path in os.listdir(BASE_DIR+'SCRIPTS/TEST_IMG/'):
    #     img = cv2.imread(BASE_DIR+'SCRIPTS/TEST_IMG/'+img_path)
    #     mask_img, labellist = mask_obj.run_inference(img)

        # cv2.imwrite('debug.jpg', mask_img)
        # print(labellist)

    # Example usage (not executed here):
    # config = {"ip": "192.0.0.55", "user": "admin", "password": "Admin@123", "save_path": "./ip_images", "name": "CAM1"}
    # ipcam = IPCamera(config)
    # if ipcam.initialize():
    # img = ipcam.capture()
    # ipcam.deinitialize()

if __name__ == "__main__":
    main()