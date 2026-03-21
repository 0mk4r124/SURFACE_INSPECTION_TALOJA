import os
import cv2
import gc
import time
import traceback
import PySpin

class FlirCamera:
    SAFE_THROUGHPUT_CAP = 1250000000  # 1250 Mbps per camera safe default for weak NICs
    FLUSH_FRAMES = 5                  # number of startup frames to discard
    INTERNAL_RETRY = 5               # capture internal retries on incomplete images
    INTERNAL_RETRY_DELAY = 0.005      # seconds between internal retries

    def __init__(self):
        self.system = PySpin.System.GetInstance()
        self.cam_list = self.system.GetCameras()
        self.processor = PySpin.ImageProcessor()
        self.processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
        self.cam_dict = {}   # serial -> camera object
        self.config = {}     # serial -> config dict
        self.acquiring = set()  # serials currently acquiring

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.release_all()

    def set_config(self, serial_number: str, config: dict):
        # Keep a copy; user-level code may set PACKET_SIZE, PACKET_DELAY, FRAME_RATE, THROUGHPUT
        self.config[serial_number] = config.copy()
        return True, None

    def initialize(self, serial_number: str):
        try:
            if serial_number in self.cam_dict:
                return True, None  # already initialized

            for cam in self.cam_list:
                try:
                    node_serial = PySpin.CStringPtr(cam.GetTLDeviceNodeMap().GetNode("DeviceSerialNumber"))
                    if node_serial and node_serial.GetValue() == serial_number:
                        cam.Init()
                        time.sleep(0.5)
                        self.cam_dict[serial_number] = cam
                        status, err = self.configure(serial_number)
                        if not status:
                            try:
                                cam.DeInit()
                            except Exception:
                                pass
                            time.sleep(0.5)
                            return False, err
                        return True, None
                except Exception:
                    # skip cameras we can't read serial for
                    continue

            return False, f"Camera with serial {serial_number} not found"
        except Exception as e:
            return False, f"Initialize error for {serial_number}: {e}\n{traceback.format_exc()}"

    def configure(self, serial_number: str):
        try:
            if serial_number not in self.cam_dict:
                return False, f"Camera {serial_number} not initialized"

            cam = self.cam_dict[serial_number]
            tlStreamSetup = cam.GetTLStreamNodeMap()
            camNodeMap = cam.GetNodeMap()
            cfg = self.config.get(serial_number, {})

            # --- Chunk data (optional, safe) ---
            try:
                chunk_mode_active = PySpin.CBooleanPtr(camNodeMap.GetNode("ChunkModeActive"))
                if PySpin.IsAvailable(chunk_mode_active) and PySpin.IsWritable(chunk_mode_active):
                    chunk_mode_active.SetValue(True)
                chunk_selector = PySpin.CEnumerationPtr(camNodeMap.GetNode("ChunkSelector"))
                if PySpin.IsAvailable(chunk_selector) and PySpin.IsWritable(chunk_selector):
                    entry = chunk_selector.GetEntryByName("Timestamp")
                    if PySpin.IsAvailable(entry):
                        chunk_selector.SetIntValue(entry.GetValue())
                        chunk_enable = PySpin.CBooleanPtr(camNodeMap.GetNode("ChunkEnable"))
                        if PySpin.IsAvailable(chunk_enable) and PySpin.IsWritable(chunk_enable):
                            chunk_enable.SetValue(True)
            except Exception:
                pass

            # --- Packet size auto OFF ---
            try:
                packet_size_auto = PySpin.CEnumerationPtr(camNodeMap.GetNode("GevSCPSPacketSizeAuto"))
                if PySpin.IsAvailable(packet_size_auto) and PySpin.IsWritable(packet_size_auto):
                    entry_off = packet_size_auto.GetEntryByName("Off")
                    if PySpin.IsAvailable(entry_off):
                        packet_size_auto.SetIntValue(entry_off.GetValue())
            except Exception:
                pass

            # --- Packet size (safe default 1500) ---
            try:
                packet_size = PySpin.CIntegerPtr(camNodeMap.GetNode("GevSCPSPacketSize"))
                if PySpin.IsAvailable(packet_size) and PySpin.IsWritable(packet_size):
                    pkt = int(cfg.get("PACKET_SIZE", 1500))
                    pkt = max(packet_size.GetMin(), min(pkt, packet_size.GetMax()))
                    packet_size.SetValue(pkt)
            except: pass

            # --- Packet delay (safe default 15000) ---
            try:
                packet_delay = PySpin.CIntegerPtr(camNodeMap.GetNode("GevSCPD"))
                if PySpin.IsAvailable(packet_delay) and PySpin.IsWritable(packet_delay):
                    delay = int(cfg.get("PACKET_DELAY", 15000))
                    delay = max(int(packet_delay.GetMin()), min(delay, int(packet_delay.GetMax())))
                    packet_delay.SetValue(delay)
            except Exception:
                pass

            # --- Stream Packet Resend: disable for weak NICs to avoid floods ---
            try:
                resendFramesNode = PySpin.CBooleanPtr(tlStreamSetup.GetNode("StreamPacketResendEnable"))
                if PySpin.IsAvailable(resendFramesNode) and PySpin.IsWritable(resendFramesNode):
                    resendFramesNode.SetValue(False)
            except Exception:
                pass

            # --- Set throughput limit but clamp to SAFE_THROUGHPUT_CAP ---
            try:
                deviceThroughput = PySpin.CIntegerPtr(camNodeMap.GetNode('DeviceLinkThroughputLimit'))
                if PySpin.IsAvailable(deviceThroughput) and PySpin.IsWritable(deviceThroughput):
                    requested = int(cfg.get("THROUGHPUT", 700000000))   # 700 MB/s = perfect for 122 FPS BayerRG8
                    min_t = int(deviceThroughput.GetMin())
                    max_t = int(deviceThroughput.GetMax())
                    requested = max(min_t, min(requested, max_t))
                    inc = int(deviceThroughput.GetInc()) or 1
                    requested = requested - (requested % inc)
                    deviceThroughput.SetValue(requested)
                    print(f"✅ DeviceLinkThroughputLimit set to {requested:,} B/s")
            except Exception as e:
                print(f"Throughput warning: {e}")

            # --- Stream buffer handling (NewestOnly) ---
            try:
                handling_mode = PySpin.CEnumerationPtr(tlStreamSetup.GetNode('StreamBufferHandlingMode'))
                if PySpin.IsAvailable(handling_mode) and PySpin.IsWritable(handling_mode):
                    newest = handling_mode.GetEntryByName("NewestOnly")
                    if PySpin.IsAvailable(newest):
                        handling_mode.SetIntValue(newest.GetValue())
            except Exception:
                pass

            # --- Stream buffer count manual ---
            try:
                stream_buffer_count_mode = PySpin.CEnumerationPtr(tlStreamSetup.GetNode('StreamBufferCountMode'))
                if PySpin.IsAvailable(stream_buffer_count_mode) and PySpin.IsWritable(stream_buffer_count_mode):
                    stream_buffer_count_mode.SetIntValue(stream_buffer_count_mode.GetEntryByName('Manual').GetValue())
                buffer_count = PySpin.CIntegerPtr(tlStreamSetup.GetNode('StreamBufferCountManual'))
                if PySpin.IsAvailable(buffer_count) and PySpin.IsWritable(buffer_count):
                    # Keep moderate-high buffer for Spinnaker
                    buffer_count.SetValue(1000)
            except Exception:
                pass

            # --- Acquisition mode continuous ---
            try:
                node_acquisition_mode = PySpin.CEnumerationPtr(camNodeMap.GetNode('AcquisitionMode'))
                if PySpin.IsAvailable(node_acquisition_mode) and PySpin.IsWritable(node_acquisition_mode):
                    node_acquisition_mode.SetIntValue(node_acquisition_mode.GetEntryByName('Continuous').GetValue())
            except Exception:
                pass

            # --- Exposure ---
            try:
                ExposureAuto = PySpin.CEnumerationPtr(camNodeMap.GetNode("ExposureAuto"))
                if PySpin.IsAvailable(ExposureAuto) and PySpin.IsWritable(ExposureAuto):
                    ExposureAuto.SetIntValue(ExposureAuto.GetEntryByName("Off").GetValue())

                ExposureTime = PySpin.CFloatPtr(camNodeMap.GetNode("ExposureTime"))
                if PySpin.IsAvailable(ExposureTime) and PySpin.IsWritable(ExposureTime):
                    requiredExposureVal = float(cfg.get("EXPOSURE", ExposureTime.GetMin()))
                    if ExposureTime.GetMin() <= requiredExposureVal <= ExposureTime.GetMax():
                        ExposureTime.SetValue(requiredExposureVal)
            except Exception:
                pass

            # --- Frame rate control ---
            try:
                # Enable manual frame rate control
                fr_enable = PySpin.CBooleanPtr(camNodeMap.GetNode("AcquisitionFrameRateEnable"))
                if PySpin.IsAvailable(fr_enable) and PySpin.IsWritable(fr_enable):
                    fr_enable.SetValue(True)

                # Access frame rate node
                fr_node = PySpin.CFloatPtr(camNodeMap.GetNode("AcquisitionFrameRate"))
                if PySpin.IsAvailable(fr_node) and PySpin.IsWritable(fr_node):

                    requested = float(cfg.get("FRAME_RATE", 5.0))

                    min_fps = fr_node.GetMin()
                    max_fps = fr_node.GetMax()

                    # Clamp to nearest allowed FPS
                    if requested < min_fps:
                        final_fps = min_fps
                    elif requested > max_fps:
                        final_fps = max_fps
                    else:
                        final_fps = requested

                    fr_node.SetValue(final_fps)

                    # Optional verification:
                    applied_fps = fr_node.GetValue()
                    print(f"Requested FPS: {requested}, Applied FPS: {applied_fps}, Range: {min_fps}-{max_fps}")

            except Exception as e:
                print(f"Frame rate setup failed: {e}")

            # --- Pixel format safe default ---
            try:
                PixelFormat = PySpin.CEnumerationPtr(camNodeMap.GetNode("PixelFormat"))
                if PySpin.IsAvailable(PixelFormat) and PySpin.IsWritable(PixelFormat):
                    PixelFormat.SetIntValue(PixelFormat.GetEntryByName("BayerRG8").GetValue())
            except Exception:
                pass

            # Disable packet resend & Disable auto exposure
            # try:
            #     cam.GainAuto.SetValue(PySpin.GainAuto_Off)
            #     cam.GevPacketResendMode.SetValue(PySpin.GevPacketResendMode_Off)
            # except: pass

            # ----------------------------
            # STREAM SETTINGS (CRITICAL)
            # ----------------------------
            # try:
            #     stream_buffer_count = PySpin.CIntegerPtr(
            #         camNodeMap.GetNode("StreamDefaultBufferCount"))
            #     stream_buffer_count.SetValue(512)

            #     # Disable auto buffer count
            #     buffer_mode = PySpin.CEnumerationPtr(
            #         camNodeMap.GetNode("StreamBufferCountMode"))
            #     manual_mode = buffer_mode.GetEntryByName("Manual")
            #     buffer_mode.SetIntValue(manual_mode.GetValue())

            # except: pass

            return True, None

        except Exception as e:
            return False, f"Configure error for {serial_number}: {e}\n{traceback.format_exc()}"

    def start_acquisition(self, serial_number: str):
        try:
            if serial_number not in self.cam_dict:
                return None, f"Camera {serial_number} not initialized"
            cam = self.cam_dict[serial_number]

            if not cam.IsInitialized():
                cam.Init()

            # Ensure config is applied again (safe)
            self.configure(serial_number)

            # Begin acquisition and allow camera to settle
            if not cam.IsStreaming():
                cam.BeginAcquisition()
                # discard a few frames to flush stale/incomplete frames
                for _ in range(self.FLUSH_FRAMES):
                    try:
                        img = cam.GetNextImage(100)
                        try:
                            if img.IsIncomplete():
                                # just release and continue
                                img.Release()
                                time.sleep(0.01)
                                continue
                            img.Release()
                        except Exception:
                            try: img.Release()
                            except: pass
                    except Exception:
                        # no image available within timeout; continue
                        time.sleep(0.01)
                time.sleep(0.2)

            self.acquiring.add(serial_number)
            return cam, None
        except Exception as e:
            return None, f"Start acquisition error for {serial_number}: {e}\n{traceback.format_exc()}"

    def is_streaming(self, serial_number: str):
        try:
            if serial_number not in self.cam_dict:
                return False
            cam = self.cam_dict[serial_number]
            return cam.IsStreaming()
        except Exception:
            return False
        
    def converter(self, image_result):
        converted = self.processor.Convert(image_result, PySpin.PixelFormat_BGR8)
        img = converted.GetNDArray()

        return img

    def capture(self, cam, timeout_ms: int = 5000, save_path: str = None):
        """
        Attempts to get an image from cam with a few internal retries for Incomplete images.
        Returns (img, None) on success or (None, errstr) on persistent failure.
        """
        try:
            if cam is None or not cam.IsStreaming():
                return None, "Camera not streaming or invalid"

            last_err = None
            for attempt in range(self.INTERNAL_RETRY):
                try:
                    image_result = cam.GetNextImage(timeout_ms)
                except Exception as e:
                    last_err = f"GetNextImage error: {e}"
                    time.sleep(self.INTERNAL_RETRY_DELAY)
                    continue

                try:
                    if image_result.IsIncomplete():
                        status = image_result.GetImageStatus()
                        image_result.Release()
                        last_err = f"Incomplete image status: {status}"
                        time.sleep(self.INTERNAL_RETRY_DELAY)
                        continue

                    # return image_result, None
                    return self.converter(image_result), None
                finally:
                    try: image_result.Release()
                    except Exception: pass

            # all attempts failed
            return None, last_err or "Capture failed after retries"
        except Exception as e:
            return None, f"Capture error: {e}\n{traceback.format_exc()}"

    def end_acquisition(self, serial_number: str):
        try:
            if serial_number not in self.cam_dict:
                return False, f"Camera {serial_number} not initialized"
            cam = self.cam_dict[serial_number]
            if cam.IsStreaming():
                cam.EndAcquisition()
            if serial_number in self.acquiring:
                self.acquiring.remove(serial_number)
            return True, None
        except Exception as e:
            return False, f"End acquisition error for {serial_number}: {e}\n{traceback.format_exc()}"

    def deinitialize(self, serial_number: str):
        try:
            if serial_number not in self.cam_dict:
                return False, f"Camera {serial_number} not initialized"

            cam = self.cam_dict[serial_number]
            try:
                if cam.IsStreaming():
                    cam.EndAcquisition()
            except Exception:
                pass
            try:
                if cam.IsInitialized():
                    cam.DeInit()
            except Exception:
                pass
            del self.cam_dict[serial_number]
            if serial_number in self.acquiring:
                self.acquiring.remove(serial_number)
            return True, None
        except Exception as e:
            return False, f"Deinitialize error for {serial_number}: {e}\n{traceback.format_exc()}"

    def release_all(self):
        try:
            for serial, cam in list(self.cam_dict.items()):
                try:
                    if cam.IsStreaming():
                        cam.EndAcquisition()
                except Exception:
                    pass
                try:
                    if cam.IsInitialized():
                        cam.DeInit()
                except Exception:
                    pass
            self.cam_dict.clear()
            try:
                self.cam_list.Clear()
            except Exception:
                pass
            try:
                self.system.ReleaseInstance()
            except Exception:
                pass
            time.sleep(0.1)
            gc.collect()
            return True, None
        except Exception as e:
            return False, f"Release all error: {e}\n{traceback.format_exc()}"

    def __del__(self):
        try:
            self.release_all()
        except Exception:
            pass
