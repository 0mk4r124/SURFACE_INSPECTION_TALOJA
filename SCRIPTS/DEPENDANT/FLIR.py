import os
import cv2
import gc
import time
import traceback
import PySpin


class FlirCamera:
    SAFE_THROUGHPUT_CAP = 1250000000  # 1250 MB/s per camera safe default for weak NICs
    FLUSH_FRAMES = 5                  # number of startup frames to discard
    INTERNAL_RETRY = 5                # capture internal retries on incomplete images
    INTERNAL_RETRY_DELAY = 0.005      # seconds between internal retries

    # --- fast path tuning (used by capture_raw only) -----------------------
    # At 70 fps the frame period is ~14 ms. Retrying an incomplete frame with a
    # 5 ms sleep burns two whole frame periods, so the fast path never retries:
    # it reports the error and the caller simply moves to the next frame.
    RAW_TIMEOUT_MS = 1000

    def __init__(self):
        self.system = PySpin.System.GetInstance()
        self.cam_list = self.system.GetCameras()
        self.processor = PySpin.ImageProcessor()
        self.processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
        self.cam_dict = {}      # serial -> camera object
        self.config = {}        # serial -> config dict
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
        # NOTE: the `except Exception: continue` below is LOAD-BEARING.
        # This camera is visible on more than one NIC (same L2 segment), so the
        # same serial appears several times in cam_list. Opening it through an
        # interface on a different subnet raises -1015 "Camera is on a wrong
        # subnet". Swallowing that and continuing lets the loop fall through to
        # the interface that actually works. Do not "fix" this.
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
                    # skip cameras we can't read serial for / wrong subnet paths
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
            except Exception:
                pass

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
                    requested = int(cfg.get("THROUGHPUT", 700000000))   # bytes/sec
                    min_t = int(deviceThroughput.GetMin())
                    max_t = int(deviceThroughput.GetMax())
                    requested = max(min_t, min(requested, max_t))
                    inc = int(deviceThroughput.GetInc()) or 1
                    requested = requested - (requested % inc)
                    deviceThroughput.SetValue(requested)
                    print(f"DeviceLinkThroughputLimit set to {requested:,} B/s")
            except Exception as e:
                print(f"Throughput warning: {e}")

            # ------------------------------------------------------------------
            # Stream buffer handling  ** CRITICAL FOR 70 FPS **
            # ------------------------------------------------------------------
            # Previously this was "NewestOnly", which SILENTLY DISCARDS every
            # queued frame the moment the consumer lags by one frame period.
            # That is why the NIC received the full 70 fps while only ~40 fps
            # reached disk - with no error anywhere in the logs.
            #
            # "OldestFirst" delivers every buffered frame, in order. Combined
            # with the threaded saver in CAPTURE.py it gives lossless capture.
            # Set BUFFER_MODE="NewestOnly" in CAMERA_CONFIGS to restore the old
            # (lossy, low-latency) behaviour for a live-preview-only camera.
            # ------------------------------------------------------------------
            try:
                handling_mode = PySpin.CEnumerationPtr(tlStreamSetup.GetNode('StreamBufferHandlingMode'))
                if PySpin.IsAvailable(handling_mode) and PySpin.IsWritable(handling_mode):
                    mode_name = str(cfg.get("BUFFER_MODE", "OldestFirst"))
                    entry = handling_mode.GetEntryByName(mode_name)
                    if not PySpin.IsAvailable(entry):
                        entry = handling_mode.GetEntryByName("OldestFirst")
                        mode_name = "OldestFirst"
                    if PySpin.IsAvailable(entry):
                        handling_mode.SetIntValue(entry.GetValue())
                        print(f"StreamBufferHandlingMode set to {mode_name}")
            except Exception:
                pass

            # --- Stream buffer count manual ---
            # 300 buffers x ~5 MB = ~1.5 GB pinned, ~4.3 s of slack at 70 fps.
            # (Was 1000 = ~5 GB, which was pointless under NewestOnly anyway.)
            try:
                stream_buffer_count_mode = PySpin.CEnumerationPtr(tlStreamSetup.GetNode('StreamBufferCountMode'))
                if PySpin.IsAvailable(stream_buffer_count_mode) and PySpin.IsWritable(stream_buffer_count_mode):
                    stream_buffer_count_mode.SetIntValue(
                        stream_buffer_count_mode.GetEntryByName('Manual').GetValue())
                buffer_count = PySpin.CIntegerPtr(tlStreamSetup.GetNode('StreamBufferCountManual'))
                if PySpin.IsAvailable(buffer_count) and PySpin.IsWritable(buffer_count):
                    want = int(cfg.get("BUFFER_COUNT", 300))
                    want = max(int(buffer_count.GetMin()), min(want, int(buffer_count.GetMax())))
                    buffer_count.SetValue(want)
                    print(f"StreamBufferCountManual set to {want}")
            except Exception:
                pass

            # --- Acquisition mode continuous ---
            try:
                node_acquisition_mode = PySpin.CEnumerationPtr(camNodeMap.GetNode('AcquisitionMode'))
                if PySpin.IsAvailable(node_acquisition_mode) and PySpin.IsWritable(node_acquisition_mode):
                    node_acquisition_mode.SetIntValue(
                        node_acquisition_mode.GetEntryByName('Continuous').GetValue())
            except Exception:
                pass

            # ==================================================================
            # EXPOSURE   ** the motion-blur control **
            # ==================================================================
            # Blur distance = surface speed x exposure time. Frame rate has no
            # effect on blur - at 70 fps the frame period is 14286 us, so even a
            # 20 us exposure costs nothing in fps. The only cost is light.
            #
            # CHANGED: the old code did `if min <= requested <= max: SetValue()`.
            # Any value below the sensor floor was SILENTLY IGNORED and the
            # camera kept its previous exposure - so EXPOSURE in CAMERA_CONFIGS
            # could look applied while nothing had changed. It now clamps into
            # range, reads the value back, and prints what actually landed.
            # ==================================================================
            try:
                ExposureAuto = PySpin.CEnumerationPtr(camNodeMap.GetNode("ExposureAuto"))
                if PySpin.IsAvailable(ExposureAuto) and PySpin.IsWritable(ExposureAuto):
                    ExposureAuto.SetIntValue(ExposureAuto.GetEntryByName("Off").GetValue())

                ExposureMode = PySpin.CEnumerationPtr(camNodeMap.GetNode("ExposureMode"))
                if PySpin.IsAvailable(ExposureMode) and PySpin.IsWritable(ExposureMode):
                    ExposureMode.SetIntValue(ExposureMode.GetEntryByName("Timed").GetValue())

                ExposureTime = PySpin.CFloatPtr(camNodeMap.GetNode("ExposureTime"))
                if PySpin.IsAvailable(ExposureTime) and PySpin.IsWritable(ExposureTime):
                    e_min = ExposureTime.GetMin()
                    e_max = ExposureTime.GetMax()
                    requested_e = float(cfg.get("EXPOSURE", e_min))
                    ExposureTime.SetValue(max(e_min, min(requested_e, e_max)))
                    applied_e = ExposureTime.GetValue()
                    print(f"Exposure requested {requested_e:.1f} us -> applied {applied_e:.1f} us "
                          f"(sensor range {e_min:.1f}-{e_max:.1f} us)")
                    if applied_e > (requested_e + 0.5):
                        print(f"WARNING: sensor floor is {e_min:.1f} us - cannot go as short as "
                              f"{requested_e:.1f} us.")
                elif PySpin.IsAvailable(ExposureTime):
                    print("WARNING: ExposureTime is NOT writable - EXPOSURE will be IGNORED. "
                          "ExposureAuto may still be on.")
            except Exception as e:
                print(f"Exposure setup failed: {e}")

            # ==================================================================
            # GAIN
            # ==================================================================
            # Buys back brightness when exposure is cut for blur, at the cost of
            # noise. Each 6 dB doubles the signal: 6=2x, 9.5=3x, 12=4x, 15.6=6x.
            #
            # Pair EXPOSURE and GAIN so brightness stays constant:
            #     300 us / 0.0 dB   (baseline)
            #     150 us / 6.0 dB
            #     100 us / 9.5 dB
            #      75 us / 12.0 dB
            #      50 us / 15.6 dB
            #
            # GainSelector MUST be set to "All" and GainAuto MUST be Off before
            # Gain becomes writable. Without both, the write is silently ignored
            # and the image just goes dark when exposure is cut.
            # ==================================================================
            try:
                gain_sel = PySpin.CEnumerationPtr(camNodeMap.GetNode("GainSelector"))
                if PySpin.IsAvailable(gain_sel) and PySpin.IsWritable(gain_sel):
                    all_entry = gain_sel.GetEntryByName("All")
                    if PySpin.IsAvailable(all_entry):
                        gain_sel.SetIntValue(all_entry.GetValue())

                gain_auto = PySpin.CEnumerationPtr(camNodeMap.GetNode("GainAuto"))
                if PySpin.IsAvailable(gain_auto) and PySpin.IsWritable(gain_auto):
                    gain_auto.SetIntValue(gain_auto.GetEntryByName("Off").GetValue())

                gain_node = PySpin.CFloatPtr(camNodeMap.GetNode("Gain"))
                if PySpin.IsAvailable(gain_node) and PySpin.IsWritable(gain_node):
                    g_min = gain_node.GetMin()
                    g_max = gain_node.GetMax()
                    requested_g = float(cfg.get("GAIN", 0.0))
                    gain_node.SetValue(max(g_min, min(requested_g, g_max)))
                    applied_g = gain_node.GetValue()
                    print(f"Gain requested {requested_g:.2f} dB -> applied {applied_g:.2f} dB "
                          f"(range {g_min:.2f}-{g_max:.2f} dB, linear x{10 ** (applied_g / 20):.2f})")
                    if abs(applied_g - requested_g) > 0.5:
                        print(f"WARNING: gain did not take. Requested {requested_g:.2f}, "
                              f"camera reports {applied_g:.2f} dB.")
                elif PySpin.IsAvailable(gain_node):
                    print("WARNING: Gain is NOT writable - GAIN will be IGNORED. "
                          "Check GainAuto / GainSelector.")
            except Exception as e:
                print(f"Gain setup failed: {e}")

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

                    applied_fps = fr_node.GetValue()
                    print(f"Requested FPS: {requested}, Applied FPS: {applied_fps}, Range: {min_fps}-{max_fps}")

                    # Loud warning: if the sensor cannot physically reach the
                    # requested rate, no amount of software tuning will help.
                    if applied_fps < (requested - 0.5):
                        print(f"WARNING: camera {serial_number} capped at {applied_fps:.2f} fps "
                              f"(requested {requested}). Check EXPOSURE / ROI / THROUGHPUT.")
            except Exception as e:
                print(f"Frame rate setup failed: {e}")

            # --- Pixel format (configurable, default BayerRG8) ---
            try:
                PixelFormat = PySpin.CEnumerationPtr(camNodeMap.GetNode("PixelFormat"))
                if PySpin.IsAvailable(PixelFormat) and PySpin.IsWritable(PixelFormat):
                    fmt_name = str(cfg.get("PIXEL_FORMAT", "BayerRG8"))
                    entry = PySpin.CEnumEntryPtr(PixelFormat.GetEntryByName(fmt_name))
                    if PySpin.IsAvailable(entry) and PySpin.IsReadable(entry):
                        PixelFormat.SetIntValue(entry.GetValue())
                        print(f"PixelFormat set to {fmt_name}")
                    else:
                        supported = [
                            PySpin.CEnumEntryPtr(e).GetSymbolic()
                            for e in PixelFormat.GetEntries()
                            if PySpin.IsReadable(PySpin.CEnumEntryPtr(e))
                        ]
                        print(f"PixelFormat '{fmt_name}' not available. Supported: {supported}")
            except Exception as e:
                print(f"PixelFormat setup failed: {e}")

            # Disable packet resend & Disable auto exposure
            # try:
            #     cam.GainAuto.SetValue(PySpin.GainAuto_Off)
            #     cam.GevPacketResendMode.SetValue(PySpin.GevPacketResendMode_Off)
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
                                img.Release()
                                time.sleep(0.01)
                                continue
                            img.Release()
                        except Exception:
                            try: img.Release()
                            except Exception: pass
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
        pf = image_result.GetPixelFormat()
        if pf == PySpin.PixelFormat_Mono8:
            return image_result.GetNDArray().copy()
        if pf == PySpin.PixelFormat_BayerRG8:
            return cv2.cvtColor(image_result.GetNDArray(), cv2.COLOR_BayerRG2GRAY)
        converted = self.processor.Convert(image_result, PySpin.PixelFormat_BGR8)
        return converted.GetNDArray()

    def capture(self, cam, timeout_ms: int = 5000, save_path: str = None):
        """
        SLOW PATH - unchanged. Used by the idle / trigger-wait preview branch,
        which runs at ~0.5 fps so the inline conversion cost does not matter.

        Attempts to get an image from cam with a few internal retries for
        Incomplete images. Returns (img, None) on success or (None, errstr).
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

                    return self.converter(image_result), None
                finally:
                    try: image_result.Release()
                    except Exception: pass

            # all attempts failed
            return None, last_err or "Capture failed after retries"
        except Exception as e:
            return None, f"Capture error: {e}\n{traceback.format_exc()}"

    # =====================================================================
    # FAST PATH - used by the 70 fps recording loop in CAPTURE.py
    # =====================================================================
    def capture_raw(self, cam, timeout_ms: int = None):
        """
        Grab one frame, copy the raw sensor buffer, release the stream buffer
        IMMEDIATELY, and return (ndarray, pixel_format, None).

        NO demosaic and NO JPEG encode happen here - both are done by saver
        worker threads in CAPTURE.py. That keeps this call at roughly a 5 MB
        memcpy (~1 ms) instead of ~21 ms, so the acquisition thread can never
        become the bottleneck and Spinnaker never has to drop a frame.

        Deliberately does NOT retry: at 70 fps the frame period is ~14 ms and a
        retry loop with sleeps would stall the cadence. A single bad frame is
        reported to the caller, which counts it against MAX_RETRIES and moves on.
        """
        try:
            if cam is None or not cam.IsStreaming():
                return None, None, "Camera not streaming or invalid"

            if timeout_ms is None:
                timeout_ms = self.RAW_TIMEOUT_MS

            try:
                image_result = cam.GetNextImage(timeout_ms)
            except Exception as e:
                return None, None, f"GetNextImage error: {e}"

            try:
                if image_result.IsIncomplete():
                    return None, None, f"Incomplete image status: {image_result.GetImageStatus()}"

                pf = image_result.GetPixelFormat()
                arr = image_result.GetNDArray().copy()   # copy BEFORE Release()
                return arr, pf, None
            finally:
                try: image_result.Release()
                except Exception: pass

        except Exception as e:
            return None, None, f"capture_raw error: {e}\n{traceback.format_exc()}"

    def convert_array(self, arr, pixel_format):
        """
        Demosaic / convert a raw array returned by capture_raw().

        Mirrors converter() exactly, so the resulting pixel data is IDENTICAL
        to what the old inline path produced - image quality is unchanged.

        Safe to call from worker threads: cv2 releases the GIL during cvtColor,
        so N workers really do scale across N cores.

        NOTE: PySpin's ImageProcessor.Convert() needs a live ImagePtr, which no
        longer exists once the buffer is released, so non-Bayer/non-Mono formats
        fall through as raw. Your CAMERA_CONFIGS uses BayerRG8, which is fully
        handled. If you ever switch to a format needing processor.Convert(), use
        capture() for that camera instead.
        """
        if arr is None:
            return None
        if pixel_format == PySpin.PixelFormat_Mono8:
            return arr
        if pixel_format == PySpin.PixelFormat_BayerRG8:
            return cv2.cvtColor(arr, cv2.COLOR_BayerRG2GRAY)
        return arr

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