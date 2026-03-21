import os
import cv2
import time
import traceback
from pypylon import pylon

def _get_exposure_node(nodemap):
    for name in ["ExposureTime", "ExposureTimeAbs"]:
        try:
            node = nodemap.GetNode(name)
            if node and node.IsWritable():
                return node
        except Exception:
            continue
    return None

class BaslerCamera:
    def __init__(self, config: dict):
        self.config = config
        self.camera = None
        self.converter = pylon.ImageFormatConverter()

    # Context manager entry
    def __enter__(self):
        return self

    # Context manager exit
    def __exit__(self, exc_type, exc_value, tb):
        self.__del__()

    def initialize(self):
        try:
            serial_number = self.config.get("serial_number")
            for device in pylon.TlFactory.GetInstance().EnumerateDevices():
                if device.GetSerialNumber() == serial_number:
                    self.camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(device))
                    self.camera.Open()

                    nodemap = self.camera.GetNodeMap()

                    # ---- Safe ExposureTime ----
                    # Disable auto first
                    try:
                        exp_auto = nodemap.GetNode("ExposureAuto")
                        if exp_auto and exp_auto.IsWritable():
                            exp_auto.SetValue(exp_auto.GetEntryByName("Off").GetValue())
                    except Exception as e:
                        print("ExposureAuto not available:", e)

                    # Get exposure node safely
                    exposure = _get_exposure_node(nodemap)
                    if exposure:
                        exp_val = self.config.get("exposure_time", exposure.GetMin())
                        exp_val = max(exposure.GetMin(), min(exp_val, exposure.GetMax()))
                        exposure.SetValue(exp_val)
                        print(f"ExposureTime set to {exp_val}")
                    else:
                        print("No valid ExposureTime node found.")

                    # ---- Safe FrameRate ----
                    try:
                        fr_enable = nodemap.GetNode("AcquisitionFrameRateEnable")
                        if fr_enable and fr_enable.IsWritable():
                            fr_enable.SetValue(True)

                        fr = nodemap.GetNode("AcquisitionFrameRate")
                        if fr and fr.IsWritable():
                            fr_val = self.config.get("frame_rate", fr.GetMin())
                            fr.SetValue(max(fr.GetMin(), min(fr_val, fr.GetMax())))
                            print(f"FrameRate set to {fr_val}")
                        else:
                            print("FrameRate node not available.")
                    except Exception as e:
                        print("Error setting frame rate:", e)


                    # Setup converter
                    self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
                    self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
                    return True
            return False
        except Exception as e:
            print(f"Basler initialize error: {e}")
            print(traceback.format_exc())
            return False

    def start_acquisition(self):
        if self.camera:
            self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

    def capture(self):
        if self.camera and self.camera.IsGrabbing():
            grab_result = self.camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grab_result.GrabSucceeded():
                image = self.converter.Convert(grab_result)
                img = image.GetArray()
                grab_result.Release()

                # Save if path is provided
                save_path = self.config.get("save_path")
                if save_path:
                    os.makedirs(save_path, exist_ok=True)
                    filename = os.path.join(save_path, f"{self.config['serial_number']}_{time.time()}.jpg")
                    cv2.imwrite(filename, img)
                return img
            grab_result.Release()
        return None

    def end_acquisition(self):
        if self.camera and self.camera.IsGrabbing():
            self.camera.StopGrabbing()

    def deinitialize(self):
        if self.camera:
            self.camera.Close()
            self.camera = None

    def __del__(self):
        """Ensure camera is closed and resources are freed."""
        try:
            if self.camera:
                if self.camera.IsGrabbing():
                    self.camera.StopGrabbing()
                if self.camera.IsOpen():
                    self.camera.Close()
            self.camera = None
            print("Basler resources released successfully.")
        except Exception as e:
            print(f"Basler destructor error: {e}")

# # Example usage
# if __name__ == "__main__":
#     basler_config = {
#         "serial_number": "Your_Basler_Serial",
#         "exposure_time": 20000,
#         "frame_rate": 10.0,
#         "save_path": "./basler_images"
#     }

    # flir_config = {
    #     "serial_number": {
    #         "EXPOSURE": 15000.0,
    #         "FRAME_RATE": 1.0,
    #         "THROUGHT_PUT": 6500000,
    #     }
    # }

#     # Basler example
#     basler = BaslerCamera(basler_config)
#     if basler.initialize():
#         basler.start_acquisition()
#         img = basler.capture()
#         basler.end_acquisition()
#         basler.deinitialize()

#     # FLIR example
#     flir = FlirCamera(flir_config)
#     if flir.initialize():
#         flir.start_acquisition()
#         img = flir.capture()
#         flir.end_acquisition()
#         flir.deinitialize()
