# # #!/usr/bin/env python3
# # """
# # FPS_DIAG.py  -  Standalone FPS bottleneck isolator for SURFACE_INSPECTION_TALOJA

# # Place in:  ROOT/SCRIPTS/FPS_DIAG.py   (so 'from DEPENDANT.FLIR import ...' resolves)
# # Run:       cd ROOT/SCRIPTS && python3 FPS_DIAG.py

# # Does NOT touch any production service. Reuses DEPENDANT/FLIR.py exactly as-is
# # so it measures YOUR real camera configuration, not a clean-room one.

# # It runs 4 stages against the same camera:
# #     A) grab only            -> what the camera + link + driver can actually deliver
# #     B) grab + convert       -> cost of Bayer->GRAY cvtColor
# #     C) grab + convert + write -> cost of cv2.imwrite (the suspected bottleneck)
# #     D) same as C, but write to /dev/shm -> separates CPU encode cost from disk cost

# # The key output is FRAME ID GAPS:
# #     gaps == 0  -> you consumed every frame the camera sent. Camera-side limit.
# #     gaps > 0   -> camera sent more than you consumed. Host-side limit (your loop).
# # """

# # import os
# # import sys
# # import time
# # import statistics
# # import traceback
# # from datetime import datetime

# # import cv2

# # sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# # from DEPENDANT.FLIR import FlirCamera, PySpin

# # # ----------------------------------------------------------------------------
# # # EDIT THESE TO MATCH YOUR PRODUCTION CAPTURE.py
# # # ----------------------------------------------------------------------------
# # CAM_SERIAL = "24521939"
# # CAM_CONFIG = {
# #     "EXPOSURE": 300.0,
# #     "FRAME_RATE": 70.0,
# #     "PIXEL_FORMAT": "Mono8",
# #     "PACKET_SIZE": 7000,
# #     "PACKET_DELAY": 10,
# #     "THROUGHPUT": 700000000,
# # }

# # BASE_FILE_PATH = os.environ.get(
# #     "BASE_FILE_PATH",
# #     "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/",
# # )
# # DISK_OUT = os.path.join(BASE_FILE_PATH, "DEBUGS", "FPS_DIAG")
# # SHM_OUT = "/dev/shm/FPS_DIAG"

# # FRAMES_PER_STAGE = 400          # ~6 s per stage at 70 fps
# # JPEG_QUALITY = 95               # match whatever cv2.imwrite default you rely on
# # GRAB_TIMEOUT_MS = 1000


# # # ----------------------------------------------------------------------------
# # def dump_camera_nodes(cam):
# #     """Read back what the camera ACTUALLY applied, after all configure() calls."""
# #     nm = cam.GetNodeMap()
# #     tl = cam.GetTLDeviceNodeMap()

# #     def rd_float(node_map, name):
# #         try:
# #             n = PySpin.CFloatPtr(node_map.GetNode(name))
# #             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
# #                 return n.GetValue()
# #         except Exception:
# #             pass
# #         return None

# #     def rd_int(node_map, name):
# #         try:
# #             n = PySpin.CIntegerPtr(node_map.GetNode(name))
# #             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
# #                 return n.GetValue()
# #         except Exception:
# #             pass
# #         return None

# #     def rd_enum(node_map, name):
# #         try:
# #             n = PySpin.CEnumerationPtr(node_map.GetNode(name))
# #             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
# #                 return n.GetCurrentEntry().GetSymbolic()
# #         except Exception:
# #             pass
# #         return None

# #     def rd_bool(node_map, name):
# #         try:
# #             n = PySpin.CBooleanPtr(node_map.GetNode(name))
# #             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
# #                 return n.GetValue()
# #         except Exception:
# #             pass
# #         return None

# #     print("\n" + "=" * 70)
# #     print("CAMERA STATE AS ACTUALLY APPLIED")
# #     print("=" * 70)

# #     rows = [
# #         ("Width",                       rd_int(nm, "Width")),
# #         ("Height",                      rd_int(nm, "Height")),
# #         ("PixelFormat",                 rd_enum(nm, "PixelFormat")),
# #         ("PixelSize",                   rd_enum(nm, "PixelSize")),
# #         ("BinningHorizontal",           rd_int(nm, "BinningHorizontal")),
# #         ("BinningVertical",             rd_int(nm, "BinningVertical")),
# #         ("---",                         "---"),
# #         ("ExposureAuto",                rd_enum(nm, "ExposureAuto")),
# #         ("ExposureTime (us)",           rd_float(nm, "ExposureTime")),
# #         ("GainAuto",                    rd_enum(nm, "GainAuto")),
# #         ("Gain",                        rd_float(nm, "Gain")),
# #         ("AcquisitionFrameRateEnable",  rd_bool(nm, "AcquisitionFrameRateEnable")),
# #         ("AcquisitionFrameRate",        rd_float(nm, "AcquisitionFrameRate")),
# #         # >>> THE MOST IMPORTANT LINE IN THIS ENTIRE SCRIPT <<<
# #         ("AcquisitionResultingFrameRate", rd_float(nm, "AcquisitionResultingFrameRate")),
# #         ("---",                         "---"),
# #         ("DeviceLinkSpeed (Bps)",       rd_int(nm, "DeviceLinkSpeed")),
# #         ("DeviceLinkThroughputLimit",   rd_int(nm, "DeviceLinkThroughputLimit")),
# #         ("DeviceLinkCurrentThroughput", rd_int(nm, "DeviceLinkCurrentThroughput")),
# #         ("DeviceMaxThroughput",         rd_int(nm, "DeviceMaxThroughput")),
# #         ("GevSCPSPacketSize",           rd_int(nm, "GevSCPSPacketSize")),
# #         ("GevSCPD (packet delay)",      rd_int(nm, "GevSCPD")),
# #         ("DeviceTemperature (C)",       rd_float(nm, "DeviceTemperature")),
# #         ("DeviceCurrentSpeed",          rd_enum(tl, "DeviceCurrentSpeed")),
# #     ]
# #     for k, v in rows:
# #         if k == "---":
# #             print("-" * 70)
# #         else:
# #             print(f"  {k:<32} : {v}")

# #     w = rd_int(nm, "Width") or 0
# #     h = rd_int(nm, "Height") or 0
# #     resulting = rd_float(nm, "AcquisitionResultingFrameRate")
# #     if w and h:
# #         bpp = 1  # BayerRG8 / Mono8
# #         need = w * h * bpp * 70
# #         print("-" * 70)
# #         print(f"  Frame size            : {w}x{h} = {w*h/1e6:.2f} MP ({w*h*bpp/1e6:.2f} MB/frame @8bit)")
# #         print(f"  Bandwidth req @70fps  : {need/1e6:.1f} MB/s")
# #         print(f"  1GigE ceiling         : ~118 MB/s   {'>>> EXCEEDED <<<' if need > 118e6 else 'OK'}")
# #         print(f"  Disk write req @70fps : ~{need/1e6*0.25:.0f} MB/s of JPEG (assuming 4:1)")
# #     if resulting is not None and resulting < 69.0:
# #         print("-" * 70)
# #         print(f"  !!! CAMERA IS CLAMPED. AcquisitionResultingFrameRate = {resulting:.2f}")
# #         print(f"  !!! The camera will NEVER send 70 fps with this config.")
# #         print(f"  !!! Cause is exposure / ROI / pixel format / throughput limit - NOT your code.")
# #     print("=" * 70 + "\n")


# # def dump_stream_stats(cam, label):
# #     """Spinnaker transport-layer counters: the ground truth on dropped frames."""
# #     try:
# #         s = cam.GetTLStreamNodeMap()
# #     except Exception:
# #         return

# #     def rd(name):
# #         try:
# #             n = PySpin.CIntegerPtr(s.GetNode(name))
# #             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
# #                 return n.GetValue()
# #         except Exception:
# #             pass
# #         return None

# #     print(f"  [TL stream counters after {label}]")
# #     for name in [
# #         "StreamDeliveredFrameCount",
# #         "StreamLostFrameCount",
# #         "StreamDroppedFrameCount",
# #         "StreamInputBufferCount",
# #         "StreamOutputBufferCount",
# #         "StreamFailedBufferCount",
# #         "StreamPacketResendReceivedCount",
# #         "StreamPacketResendRequestCount",
# #     ]:
# #         v = rd(name)
# #         if v is not None:
# #             print(f"     {name:<34} : {v}")


# # def pct(vals, p):
# #     if not vals:
# #         return 0.0
# #     vs = sorted(vals)
# #     i = min(len(vs) - 1, int(round((p / 100.0) * (len(vs) - 1))))
# #     return vs[i]


# # def run_stage(cam, flir, label, do_convert, write_dir):
# #     """Time one stage and report where the milliseconds go."""
# #     if write_dir:
# #         os.makedirs(write_dir, exist_ok=True)

# #     grab_ms, conv_ms, write_ms, loop_ms = [], [], [], []
# #     frame_ids = []
# #     incomplete = 0

# #     # warm up - discard first 30
# #     for _ in range(30):
# #         try:
# #             im = cam.GetNextImage(GRAB_TIMEOUT_MS)
# #             im.Release()
# #         except Exception:
# #             pass

# #     t_stage = time.perf_counter()
# #     t_prev = t_stage

# #     for i in range(FRAMES_PER_STAGE):
# #         t0 = time.perf_counter()
# #         try:
# #             image_result = cam.GetNextImage(GRAB_TIMEOUT_MS)
# #         except Exception as e:
# #             print(f"    GetNextImage failed at {i}: {e}")
# #             break
# #         t1 = time.perf_counter()

# #         try:
# #             if image_result.IsIncomplete():
# #                 incomplete += 1
# #                 image_result.Release()
# #                 continue

# #             try:
# #                 frame_ids.append(image_result.GetFrameID())
# #             except Exception:
# #                 pass

# #             img = None
# #             if do_convert:
# #                 img = flir.converter(image_result)
# #             t2 = time.perf_counter()

# #             if write_dir and img is not None:
# #                 fname = os.path.join(
# #                     write_dir,
# #                     f"diag_{i:06d}_{datetime.now():%H%M%S_%f}.jpg",
# #                 )
# #                 cv2.imwrite(fname, img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
# #             t3 = time.perf_counter()
# #         finally:
# #             try:
# #                 image_result.Release()
# #             except Exception:
# #                 pass

# #         grab_ms.append((t1 - t0) * 1000.0)
# #         conv_ms.append((t2 - t1) * 1000.0)
# #         write_ms.append((t3 - t2) * 1000.0)
# #         loop_ms.append((t3 - t_prev) * 1000.0)
# #         t_prev = t3

# #     elapsed = time.perf_counter() - t_stage
# #     n = len(grab_ms)
# #     fps = n / elapsed if elapsed > 0 else 0.0

# #     # frame id gaps = frames the camera produced that we never received
# #     gaps = 0
# #     if len(frame_ids) > 1:
# #         for a, b in zip(frame_ids, frame_ids[1:]):
# #             d = b - a
# #             if d > 1:
# #                 gaps += (d - 1)

# #     print(f"\n  --- STAGE: {label} ---")
# #     print(f"    frames ok          : {n}   incomplete: {incomplete}")
# #     print(f"    elapsed            : {elapsed:.2f} s")
# #     print(f"    ACHIEVED FPS       : {fps:.2f}")
# #     print(f"    FRAME ID GAPS      : {gaps}   <-- frames camera sent that we dropped")
# #     if n:
# #         print(f"    grab  ms  p50/p95/p99/max : {pct(grab_ms,50):6.2f} / {pct(grab_ms,95):6.2f} / {pct(grab_ms,99):6.2f} / {max(grab_ms):6.2f}")
# #         print(f"    conv  ms  p50/p95/p99/max : {pct(conv_ms,50):6.2f} / {pct(conv_ms,95):6.2f} / {pct(conv_ms,99):6.2f} / {max(conv_ms):6.2f}")
# #         print(f"    write ms  p50/p95/p99/max : {pct(write_ms,50):6.2f} / {pct(write_ms,95):6.2f} / {pct(write_ms,99):6.2f} / {max(write_ms):6.2f}")
# #         print(f"    LOOP  ms  p50/p95/p99/max : {pct(loop_ms,50):6.2f} / {pct(loop_ms,95):6.2f} / {pct(loop_ms,99):6.2f} / {max(loop_ms):6.2f}")
# #         print(f"    (budget for 70 fps = 14.28 ms)")
# #         slow = sum(1 for v in loop_ms if v > 14.28)
# #         print(f"    iterations over budget    : {slow}/{n} ({100.0*slow/n:.1f}%)")

# #     dump_stream_stats(cam, label)
# #     return fps, gaps


# # def main():
# #     print(f"FPS_DIAG  -  {datetime.now()}")
# #     print(f"BASE_FILE_PATH = {BASE_FILE_PATH}")
# #     print(f"Target: {CAM_SERIAL}  requested FPS = {CAM_CONFIG['FRAME_RATE']}")

# #     flir = FlirCamera()
# #     flir.set_config(CAM_SERIAL, CAM_CONFIG)

# #     status, err = flir.initialize(CAM_SERIAL)
# #     if not status:
# #         print(f"initialize failed: {err}")
# #         return

# #     cam, err = flir.start_acquisition(CAM_SERIAL)
# #     if err or cam is None:
# #         print(f"start_acquisition failed: {err}")
# #         flir.release_all()
# #         return

# #     try:
# #         dump_camera_nodes(cam)

# #         results = {}
# #         results["A grab only"] = run_stage(cam, flir, "A: grab only (no convert, no write)",
# #                                            do_convert=False, write_dir=None)
# #         results["B +convert"] = run_stage(cam, flir, "B: grab + Bayer convert",
# #                                           do_convert=True, write_dir=None)
# #         results["C +write disk"] = run_stage(cam, flir, f"C: grab + convert + imwrite -> {DISK_OUT}",
# #                                              do_convert=True, write_dir=DISK_OUT)
# #         results["D +write tmpfs"] = run_stage(cam, flir, f"D: grab + convert + imwrite -> {SHM_OUT} (RAM)",
# #                                               do_convert=True, write_dir=SHM_OUT)

# #         print("\n" + "=" * 70)
# #         print("SUMMARY")
# #         print("=" * 70)
# #         for k, (fps, gaps) in results.items():
# #             print(f"  {k:<18} : {fps:7.2f} fps   (dropped {gaps} frames)")
# #         print("-" * 70)
# #         print("  READ IT LIKE THIS:")
# #         print("   A already < 70          -> camera/link/config limit. Fix FLIR.py + network.")
# #         print("   A ~70 but C ~30         -> host limit. imwrite is the bottleneck.")
# #         print("   C ~30 but D ~70         -> DISK is the bottleneck, not CPU encode.")
# #         print("   C ~= D ~= 30            -> CPU JPEG encode is the bottleneck.")
# #         print("   gaps > 0 anywhere       -> NewestOnly is silently discarding your frames.")
# #         print("=" * 70)

# #     except Exception:
# #         print(traceback.format_exc())
# #     finally:
# #         try:
# #             flir.end_acquisition(CAM_SERIAL)
# #         except Exception:
# #             pass
# #         try:
# #             flir.release_all()
# #         except Exception:
# #             pass
# #         print("\nCleanup: remove test images with:")
# #         print(f"  rm -rf {DISK_OUT} {SHM_OUT}")


# # if __name__ == "__main__":
# #     main()


# # import PySpin
# # system = PySpin.System.GetInstance()
# # cam = system.GetCameras().GetBySerial("24521939")
# # cam.Init()
# # nm = cam.GetNodeMap()

# # isp = PySpin.CBooleanPtr(nm.GetNode("IspEnable"))
# # print("IspEnable available:", PySpin.IsAvailable(isp),
# #       "writable:", PySpin.IsWritable(isp))
# # if PySpin.IsAvailable(isp):
# #     print("IspEnable currently:", isp.GetValue())
# #     if PySpin.IsWritable(isp):
# #         isp.SetValue(True)
# #         pf = PySpin.CEnumerationPtr(nm.GetNode("PixelFormat"))
# #         fmts = [PySpin.CEnumEntryPtr(e).GetSymbolic()
# #                 for e in pf.GetEntries()
# #                 if PySpin.IsReadable(PySpin.CEnumEntryPtr(e))]
# #         print("Formats WITH Isp:", fmts)
# #         fr = PySpin.CFloatPtr(nm.GetNode("AcquisitionFrameRate"))
# #         print("Max FPS with Isp on:", fr.GetMax())
# #         isp.SetValue(False)   # restore

# # cam.DeInit()
# # del cam
# # system.ReleaseInstance()#!/usr/bin/env python3









# #!/usr/bin/env python3
# """
# FLIR streaming diagnostic - finds WHY no frames arrive.

# Run with the Capture service stopped and SpinView closed:

#     python3 SCRIPTS/STREAM_DIAG.py

# Checks, in order:
#   1. Trigger mode      - TriggerMode=On with no trigger signal = 0 fps, black
#   2. Packet size / MTU - camera packet > NIC MTU = 0 fps (silent)
#   3. Acquisition mode
#   4. Actually streams and reports per-frame status + stream statistics
# """
# import os
# import subprocess
# import sys
# import time

# SERIAL = "24521939"
# NIC = "ens10f0np0"

# try:
#     import PySpin
# except ImportError:
#     print("PySpin not importable.")
#     sys.exit(1)


# def ip_str(v):
#     v = int(v)
#     return "%d.%d.%d.%d" % ((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255)


# def sh(cmd):
#     try:
#         return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
#     except Exception as e:
#         return f"<{e}>"


# def enum_get(nm, name):
#     try:
#         n = PySpin.CEnumerationPtr(nm.GetNode(name))
#         if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#             return n.GetCurrentEntry().GetSymbolic()
#     except Exception:
#         pass
#     return None


# def enum_set(nm, name, value):
#     try:
#         n = PySpin.CEnumerationPtr(nm.GetNode(name))
#         if PySpin.IsAvailable(n) and PySpin.IsWritable(n):
#             e = n.GetEntryByName(value)
#             if PySpin.IsAvailable(e):
#                 n.SetIntValue(e.GetValue())
#                 return True
#     except Exception:
#         pass
#     return False


# def int_get(nm, name):
#     try:
#         n = PySpin.CIntegerPtr(nm.GetNode(name))
#         if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#             return int(n.GetValue())
#     except Exception:
#         pass
#     return None


# def int_set(nm, name, value):
#     try:
#         n = PySpin.CIntegerPtr(nm.GetNode(name))
#         if PySpin.IsAvailable(n) and PySpin.IsWritable(n):
#             v = max(int(n.GetMin()), min(int(value), int(n.GetMax())))
#             n.SetValue(v)
#             return v
#     except Exception:
#         pass
#     return None


# def main():
#     print("=" * 72)
#     print("HOST NETWORK")
#     print("=" * 72)
#     mtu = sh(f"cat /sys/class/net/{NIC}/mtu")
#     print(f"{NIC} MTU        : {mtu}")
#     print(f"{NIC} link       : {sh(f'cat /sys/class/net/{NIC}/operstate')}")
#     print(f"rmem_max         : {sh('sysctl -n net.core.rmem_max')}")
#     print(f"firewall         : {sh('sudo -n ufw status 2>/dev/null || echo (needs sudo - check manually)')}")
#     print(f"ping camera      : {sh('ping -c2 -W2 192.168.3.1 | tail -2')}")

#     print()
#     print("=" * 72)
#     print("OPEN CAMERA")
#     print("=" * 72)
#     system = PySpin.System.GetInstance()
#     cam_list = system.GetCameras()

#     target = None
#     for c in cam_list:
#         try:
#             tl = c.GetTLDeviceNodeMap()
#             sn = PySpin.CStringPtr(tl.GetNode("DeviceSerialNumber"))
#             cam_ip = int_get(tl, "GevDeviceIPAddress")
#             if PySpin.IsReadable(sn) and sn.GetValue() == SERIAL:
#                 print(f"  candidate: cam ip {ip_str(cam_ip) if cam_ip else '-'}")
#                 if target is None:
#                     target = c
#         except Exception:
#             continue
#     del c, tl

#     if target is None:
#         print("Camera not found.")
#         cam_list.Clear(); system.ReleaseInstance(); return 1

#     target.Init()
#     nm = target.GetNodeMap()
#     print("Init() OK")

#     print()
#     print("=" * 72)
#     print("SUSPECT 1 - TRIGGER MODE")
#     print("=" * 72)
#     tm = enum_get(nm, "TriggerMode")
#     print(f"  TriggerMode      : {tm}")
#     print(f"  TriggerSource    : {enum_get(nm, 'TriggerSource')}")
#     print(f"  AcquisitionMode  : {enum_get(nm, 'AcquisitionMode')}")
#     if tm == "On":
#         print("  >>> TriggerMode is ON. The camera waits for a trigger and sends")
#         print("      NOTHING on its own. This alone gives 0 fps and a black view.")
#     print(f"  forcing TriggerMode Off : {enum_set(nm, 'TriggerMode', 'Off')}")
#     print(f"  forcing AcquisitionMode Continuous : {enum_set(nm, 'AcquisitionMode', 'Continuous')}")

#     print()
#     print("=" * 72)
#     print("SUSPECT 2 - PACKET SIZE vs MTU")
#     print("=" * 72)
#     pkt = int_get(nm, "GevSCPSPacketSize")
#     print(f"  camera packet size : {pkt}")
#     print(f"  NIC MTU            : {mtu}")
#     try:
#         if pkt and mtu.isdigit() and pkt > int(mtu) - 36:
#             print("  >>> PACKET SIZE EXCEEDS MTU. Every image packet is dropped by the")
#             print("      kernel. Control still works, so you get 0 fps and no error.")
#     except Exception:
#         pass
#     print(f"  forcing packet size 1500 -> {int_set(nm, 'GevSCPSPacketSize', 1500)}")
#     print(f"  packet delay             -> {int_set(nm, 'GevSCPD', 1000)}")

#     print()
#     print("=" * 72)
#     print("SAFE EXPOSURE (so a real frame is visible)")
#     print("=" * 72)
#     enum_set(nm, "ExposureAuto", "Off")
#     enum_set(nm, "GainSelector", "All")
#     enum_set(nm, "GainAuto", "Off")
#     try:
#         et = PySpin.CFloatPtr(nm.GetNode("ExposureTime"))
#         if PySpin.IsWritable(et):
#             et.SetValue(20000.0)
#             print(f"  ExposureTime -> {et.GetValue():.0f} us (long, for visibility only)")
#         gn = PySpin.CFloatPtr(nm.GetNode("Gain"))
#         if PySpin.IsWritable(gn):
#             gn.SetValue(10.0)
#             print(f"  Gain         -> {gn.GetValue():.2f} dB")
#     except Exception as e:
#         print(f"  exposure/gain set failed: {e}")

#     # host-side stream buffers
#     tls = target.GetTLStreamNodeMap()
#     enum_set(tls, "StreamBufferHandlingMode", "OldestFirst")

#     print()
#     print("=" * 72)
#     print("STREAM TEST - 10 frames")
#     print("=" * 72)
#     rc = 1
#     try:
#         target.BeginAcquisition()
#         print("BeginAcquisition() OK")
#         good = incomplete = timeout = 0
#         for i in range(10):
#             try:
#                 img = target.GetNextImage(3000)
#                 if img.IsIncomplete():
#                     incomplete += 1
#                     print(f"  frame {i}: INCOMPLETE status={img.GetImageStatus()}")
#                 else:
#                     a = img.GetNDArray()
#                     good += 1
#                     print(f"  frame {i}: OK {a.shape} min={a.min()} max={a.max()} mean={a.mean():.1f}")
#                 img.Release()
#             except Exception as e:
#                 timeout += 1
#                 print(f"  frame {i}: NO IMAGE - {e}")
#         print(f"\n  good={good}  incomplete={incomplete}  timeout={timeout}")

#         for stat in ["StreamDeliveredFrameCount", "StreamLostFrameCount",
#                      "StreamFailedBufferCount", "StreamIncompleteFrameCount"]:
#             v = int_get(tls, stat)
#             if v is not None:
#                 print(f"  {stat:30s} = {v}")

#         target.EndAcquisition()
#         rc = 0 if good else 1
#     except Exception as e:
#         print(f"BeginAcquisition/stream FAILED: {e}")

#     print()
#     print("=" * 72)
#     print("VERDICT")
#     print("=" * 72)
#     if rc == 0:
#         print("Frames ARE arriving at packet size 1500.")
#         print("If max==0 the sensor sees no light; if max>0 the pipeline is fine.")
#         print("Next: raise packet size only AFTER setting NIC MTU 9000.")
#     else:
#         print("No frames even at packet size 1500 with trigger off.")
#         print("Check: firewall (sudo ufw disable), and that no other client holds")
#         print("the camera. Report the per-frame lines above.")

#     try:
#         target.DeInit()
#     except Exception:
#         pass
#     del target, nm, tls
#     cam_list.Clear()
#     system.ReleaseInstance()
#     time.sleep(0.2)
#     return rc


# if __name__ == "__main__":
#     sys.exit(main())
"""
WS_MODAL_EXPORT.py
==================
Exports every defect the run-details modal shows for one coil, exactly as the
modal's two download buttons would save them - but all at once.

    ~/Downloads/<COIL_ID>/
        WHITE_SPOT/
            <COIL>_F<frame>_W_<m>m.jpg      <- "Download" button  (box + label + DEFECT DETAILS pane)
            <COIL>_F<frame>_RAW.jpg         <- "Raw" button       (original file bytes, untouched)
            ...
        OIL_STAIN/  DENT/  SCORING/  SCRATCH/   (same layout; a type with no defects gets no folder)
        _EXPORT_SUMMARY.csv                 <- one row per defect (same values as the details pane)

Same selection as the modal (script.js):
  - CoilMap.prepareData()       : one entry per defect in frame["W"]
  - flat 'l' fallback           : if every defect in the coil has the same length, length = frame rank
  - getFilteredDefects()        : needs frame "img", conf passes MAP_MIN_CONF, sorted by length then frame
  - passesMapConf()             : MAP_MIN_CONF below (scores <= 1 are treated as 0-1 and x100)
So if the modal says "1 / 75" with only White Spot on, WHITE_SPOT/ gets 75 info images.

Same names as the modal (script.js):
  - downloadDefectImage()       : <coil>_F<frame>_W_<floor(length/1000)>m.jpg
  - downloadRawDefectImage()    : <coil>_F<frame>_RAW<ext>
  The raw frame is saved once per frame (it is the same file for every defect in it).
  Two defects with the same name (same type + frame + metre) get "_2", "_3" ...
  (the browser would have saved them as "name (1).jpg").

Standalone: no CLI args, no DB, no imports from DEPENDANT. Set CYCLE_UID below and run:
    python3 WS_MODAL_EXPORT.py
Reads the cycle JSON from DEBUGS/RESULT/<date>/<uid>/<uid>_defect_data.json and the
frames from the paths stored in it (DEBUGS/TRASH/<uid>/...). Nothing is written
inside DEBUGS.
"""

import os
import re
import csv
import sys
import glob
import json
import math
import shutil

from PIL import Image, ImageDraw, ImageFont

# =========================================================================
# SETTINGS - edit these
# =========================================================================
CYCLE_UID = "ROLL_20261029133729"          # <-- put the cycle UID here

# Same as MAP_MIN_CONF in WEB_APP/static/js/script.js (NOT api/views.py, which has W: 90).
# Change here when script.js changes.
MAP_MIN_CONF = {"W": 95, "O": 90, "S": 90, "D": 95}   # types not listed (C) export at any score
EXPORT_TYPES = ["W", "D", "S"]    # modal filter buttons left ON (all by default)

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

# Same convention as LOGIC.py: BASE_FILE_PATH = project root (DEBUGS is appended).
BASE_FILE_PATH = os.environ.get(
    "BASE_FILE_PATH", "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/")
RESULT_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RESULT")

JSON_PATH_OVERRIDE = ""                     # set a full path to skip the RESULT search
COIL_ID_OVERRIDE = ""                       # set if the JSON coil_id differs from the modal's COIL ID
OVERWRITE = False                           # True: replace an existing ~/Downloads/<CYCLE_UID>
JPEG_QUALITY = 95                           # modal download uses canvas.toBlob(..., 0.95)

# =========================================================================
# Constants copied from script.js - keep in step with it
# =========================================================================
FRAME_WIDTH_MM = 400
DEFECT_NAMES = {"O": "Oil Stain", "W": "White Spot", "D": "Dent", "S": "Scoring", "C": "Scratch"}
DEFECT_COLORS = {"O": "#0000ff", "W": "#808080", "S": "#ffff00", "D": "#ff0000", "C": "#00ff00"}
ALL_TYPES = ["O", "W", "D", "S", "C"]       # prepareData() order
TYPE_FOLDERS = {"W": "WHITE_SPOT", "O": "OIL_STAIN", "D": "DENT", "S": "SCORING", "C": "SCRATCH"}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/UbuntuMono-B.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
]


# =========================================================================
# JS-compatible helpers
# =========================================================================
def js_str(v):
    """String(v) as JS would print a JSON value."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    return str(v)


def js_num(v):
    """Number(v); NaN for anything that is not numeric."""
    if v is None:
        return 0.0                               # Number(null) === 0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def to_fixed(v, d):
    """Number(v).toFixed(d) (half-away-from-zero, like JS for typical values)."""
    x = js_num(v)
    q = 10 ** d
    r = math.floor(abs(x) * q + 0.5 + 1e-9) / q
    return f"{math.copysign(r, x):.{d}f}" if r != 0 else f"{0:.{d}f}"


def n(v, d=1):
    """renderDefectDetails() n(): '-' for null/NaN, else toFixed(d)."""
    if v is None:
        return "-"
    x = js_num(v)
    if x != x:
        return "-"
    return to_fixed(x, d)


def safe(s):
    """safe() in script.js: String(s ?? '').replace(/[^\\w.-]+/g, '_')"""
    return re.sub(r"[^\w.-]+", "_", "" if s is None else js_str(s), flags=re.ASCII)


def js_parse_int(v):
    """parseInt(frame.l || 0)"""
    if not v:
        return 0
    m = re.match(r"\s*([+-]?\d+)", js_str(v))
    return int(m.group(1)) if m else 0


def passes_map_conf(dtype, c):
    """passesMapConf() in script.js"""
    min_c = MAP_MIN_CONF.get(dtype)
    if min_c is None:
        return True
    x = js_num(c)
    if x != x:
        return False
    if x <= 1:
        x *= 100
    return x >= min_c


# =========================================================================
# Load + select (prepareData + getFilteredDefects)
# =========================================================================
def find_json(uid):
    if JSON_PATH_OVERRIDE:
        return JSON_PATH_OVERRIDE
    hits = sorted(glob.glob(os.path.join(RESULT_PATH, "*", uid, f"{uid}_defect_data.json")))
    if not hits:
        hits = sorted(glob.glob(os.path.join(RESULT_PATH, "**", f"{uid}_defect_data.json"), recursive=True))
    if not hits:
        sys.exit(f"[ERROR] No JSON for {uid} under {RESULT_PATH} (expected <date>/{uid}/{uid}_defect_data.json)")
    if len(hits) > 1:
        print(f"[WARN] {len(hits)} JSONs found for {uid}, using the newest:\n  " + "\n  ".join(hits))
        hits.sort(key=os.path.getmtime)
    return hits[-1]


def prepare_defects(result):
    defects = []
    for frame in result or []:
        for dtype in ALL_TYPES:
            items = frame.get(dtype)
            if not items:
                continue
            for d in items:
                d = d or {}
                defects.append({
                    "type": dtype,
                    "frame": frame.get("r"),
                    "length": js_parse_int(frame.get("l") or 0),
                    "widthPos": d.get("pos_mm") or 0,
                    "confidence": d.get("c") or 0,
                    "img": frame.get("img") or None,
                    "rawImg": frame.get("raw_img") or None,
                    "box": d.get("b") or None,
                    "severity": frame.get("s") or None,
                    "diameter": frame.get("d"),
                    "egSetpoint": frame.get("e"),
                    "imgW": frame.get("compressed_width") or None,
                    "wMm": d.get("w_mm"),
                    "hMm": d.get("h_mm"),
                    "shape": d.get("shape") or None,
                })
    # same fallback as prepareData(): 'l' flat for the whole roll -> use frame rank
    if defects:
        lens = [d["length"] for d in defects]
        if min(lens) == max(lens):
            for d in defects:
                d["length"] = d["frame"] or 0
    return defects


def filtered_defects(defects):
    out = [d for d in defects
           if d["type"] in EXPORT_TYPES and d["img"] and passes_map_conf(d["type"], d["confidence"])]
    out.sort(key=lambda d: (js_num(d["length"]), js_num(d["frame"] or 0)))
    return out


# =========================================================================
# Details pane rows (renderDefectDetails, as innerText)
# =========================================================================
def defect_metrics(p, nat_w):
    img_w = js_num(p["imgW"]) if p["imgW"] else 0
    if not img_w or img_w != img_w:
        img_w = nat_w or 0                       # older JSONs: point._natW
    mm_px = (FRAME_WIDTH_MM / img_w) if img_w > 0 else 0
    box = p["box"] if isinstance(p["box"], list) and len(p["box"]) == 4 else None

    w_mm = h_mm = off_mm = None
    side = "-"
    if box and mm_px:
        x1, x2, y1, y2 = [js_num(v) for v in box]
        w_mm = abs(x2 - x1) * mm_px
        h_mm = abs(y2 - y1) * mm_px
        off = ((x1 + x2) / 2) - (img_w / 2)
        off_mm = abs(off * mm_px)
        side = "OS" if off < 0 else ("DS" if off > 0 else "CENTER")

    if p["wMm"] is not None:
        w_mm = js_num(p["wMm"])
    if p["hMm"] is not None:
        h_mm = js_num(p["hMm"])
    pos = js_num(p["widthPos"])
    if pos == pos and (pos != 0 or off_mm is None):
        off_mm = abs(pos)
        side = "OS" if pos < 0 else ("DS" if pos > 0 else "CENTER")

    len_mm = js_num(p["length"]) or 0
    if len_mm != len_mm:
        len_mm = 0
    area = (w_mm * h_mm) if (w_mm is not None and h_mm is not None) else None
    return {"wMm": w_mm, "hMm": h_mm, "offMm": off_mm, "side": side, "lenMm": len_mm, "areaMm": area}


def detail_rows(p, coil_id, cycle_uid, nat_w):
    m = defect_metrics(p, nat_w)
    name = DEFECT_NAMES.get(p["type"], p["type"] or "-")
    box = p["box"]
    rows = [
        ["COIL ID", coil_id or "-"],
        ["CYCLE UID", cycle_uid or "-"],
        ["DEFECT", f"{name} ({p['type'] or '-'})"],
        ["CONFIDENCE", f"{js_str(p['confidence'])} %" if p["confidence"] is not None else "-"],
        ["FRAME", js_str(p["frame"]) if p["frame"] is not None else "-"],
        ["LENGTH", f"{to_fixed(m['lenMm'] / 1000, 2)} m ({js_str(float(m['lenMm']))} mm)"],
        ["POSITION", "-" if m["offMm"] is None else f"{m['side']} {n(m['offMm'])} mm"],
        ["WIDTH", f"{n(m['wMm'])} mm"],
        ["HEIGHT", f"{n(m['hMm'])} mm"],
        ["AREA", "-" if m["areaMm"] is None else f"{n(m['areaMm'], 0)} mm²"],
        ["SEVERITY", js_str(p["severity"]) if p["severity"] else "-"],
        ["DIAMETER", js_str(p["diameter"]) if p["diameter"] is not None else "-"],
        ["EG SETPOINT", js_str(p["egSetpoint"]) if p["egSetpoint"] is not None else "-"],
        ["BOX (px)", (f"{js_str(box[0])}, {js_str(box[2])} → {js_str(box[1])}, {js_str(box[3])}"
                      if isinstance(box, list) else "-")],
        ["FRAME WIDTH", f"{FRAME_WIDTH_MM} mm"],
    ]

    sh = p["shape"]
    if isinstance(sh, dict) and sh.get("shape"):
        rows.append(["SHAPE", js_str(sh["shape"]) + (f" ({js_str(sh['why'])})" if sh.get("why") else "")])
        if sh.get("maj_mm") is not None:
            rows.append(["SPOT SIZE", f"{n(sh.get('maj_mm'))} × {n(sh.get('min_mm'))} mm"])
        if sh.get("ar") is not None:
            rows.append(["ASPECT", f"{n(sh['ar'], 2)} (max {n(sh.get('ar_max'), 1)}{', clean' if sh.get('clean') else ''})"])
        if sh.get("iou") is not None:
            rows.append(["ELLIPSE IOU", f"{n(sh['iou'], 2)} sol {n(sh.get('sol'), 2)}"])
        sym = sh.get("sym")
        if isinstance(sym, list):
            rows.append(["SYMMETRY", f"{n(sym[0] if len(sym) > 0 else None, 2)} / {n(sym[1] if len(sym) > 1 else None, 2)}"])
        ct = sh.get("ct")
        if isinstance(ct, list):
            g = lambda i: ct[i] if len(ct) > i else None
            rows.append(["CONTRAST", f"{n(g(2))} (in {n(g(0))} / ring {n(g(1))})"])
        if sh.get("wss") is not None:
            rows.append(["WS SCORE", f"{n(sh['wss'], 2)} (old {n(sh.get('wss_old'), 2)}{', review' if sh.get('rv') else ''})"])
    return rows, m


# =========================================================================
# Drawing (openDefectImage box/label + downloadDefectImage details pane)
# =========================================================================
_font_cache = {}


def mono_bold(size):
    if size in _font_cache:
        return _font_cache[size]
    f = None
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                break
            except Exception:
                pass
    if f is None:
        try:
            f = ImageFont.load_default(size=size)    # Pillow >= 10.1
        except TypeError:
            f = ImageFont.load_default()
            print("[WARN] No TrueType mono font and old Pillow - text size will not scale")
    _font_cache[size] = f
    return f


def text_w(draw, s, font):
    return draw.textlength(s, font=font)


def rect_overlap(a, b):
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0


def draw_box_and_label(img, p):
    """openDefectImage(): box (lineWidth 3) + '<side> WHITE SPOT <off>mm' label. Returns label rect."""
    draw = ImageDraw.Draw(img)
    W, H = img.size
    bx1, bx2, by1, by2 = [js_num(v) for v in p["box"]]
    off_px = (bx1 + bx2) / 2 - W / 2
    off_mm = to_fixed(abs(off_px * (FRAME_WIDTH_MM / W)), 1)
    side = "OS" if off_px < 0 else "DS"
    label = f"{side} {DEFECT_NAMES.get(p['type'], p['type'] or 'DEFECT').upper()} {off_mm}mm"
    color = DEFECT_COLORS.get(p["type"], "#ffffff")

    # canvas strokeRect with lineWidth 3 straddles the path: 1.5 px each side
    draw.rectangle([bx1 - 1, by1 - 1, bx2 + 1, by2 + 1], outline=color, width=3)

    font = mono_bold(18)
    text_h = 26
    label_w = text_w(draw, label, font) + 12
    label_x = max(0, min(bx1, W - label_w))
    label_y = by1 - 8
    if label_y - text_h < 0:
        label_y = (by2 + 8 + text_h) if (by2 + 8 + text_h <= H) else (max(by1, 0) + text_h)

    draw.rectangle([label_x, label_y - text_h, label_x + label_w, label_y], fill=color)
    draw.text((label_x + 6, label_y - 6), label, fill="#000000", font=font, anchor="ls")
    return [label_x, label_y - text_h, label_w, text_h]


def draw_details_pane(img, rows, p, label_rect):
    """downloadDefectImage(): white DEFECT DETAILS pane in the corner that avoids the box + label."""
    if not rows:
        return img
    W, H = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    fs = max(12, round(W / 80))
    pad, margin = round(fs * 0.8), round(fs * 0.6)

    def measure(f):
        font = mono_bold(f)
        return (max(text_w(draw, r[0], font) for r in rows),
                max(text_w(draw, r[1], font) for r in rows))

    key_w, val_w = measure(fs)
    while key_w + val_w + pad * 3 > W * 0.45 and fs > 10:
        fs -= 1
        key_w, val_w = measure(fs)
    font = mono_bold(fs)

    line_h = round(fs * 1.55)
    panel_w = key_w + val_w + pad * 3
    panel_h = pad * 2 + line_h * (len(rows) + 1)

    avoid = []
    if isinstance(p["box"], list):
        x1, x2, y1, y2 = [js_num(v) for v in p["box"]]
        avoid.append([x1, y1, x2 - x1, y2 - y1])
    if label_rect:
        avoid.append(label_rect)

    cands = [[max(0, x), max(0, y), panel_w, panel_h] for x, y in [
        (W - panel_w - margin, margin),
        (margin, margin),
        (W - panel_w - margin, H - panel_h - margin),
        (margin, H - panel_h - margin)]]
    best, best_ov = cands[0], float("inf")
    for c in cands:
        ov = sum(rect_overlap(c, a) for a in avoid)
        if ov < best_ov:
            best, best_ov = c, ov
        if ov == 0:
            break
    px, py = best[0], best[1]

    draw.rectangle([px, py, px + panel_w, py + panel_h], fill=(255, 255, 255, round(0.92 * 255)))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)
    lw = max(1, round(fs / 8))
    draw.rectangle([px, py, px + panel_w, py + panel_h], outline="#00c9a7", width=lw)

    draw.text((px + pad, py + pad + line_h / 2), "DEFECT DETAILS", fill="#00a88c", font=font, anchor="lm")
    for i, (k, v) in enumerate(rows):
        y = py + pad + line_h * (i + 1.5)
        draw.text((px + pad, y), k, fill="#7a838f", font=font, anchor="lm")
        draw.text((px + pad * 2 + key_w, y), v, fill="#16323b", font=font, anchor="lm")
    return img.convert("RGB")


# =========================================================================
# Main
# =========================================================================
def unique_name(stem, ext, used):
    """Modal name; a repeat (same frame + same metre) gets _2, _3 ... (browser would add ' (1)')."""
    name, k = f"{stem}{ext}", 1
    while name in used:
        k += 1
        name = f"{stem}_{k}{ext}"
    used.add(name)
    return name


def main():
    uid = CYCLE_UID.strip()
    if not uid:
        sys.exit("[ERROR] Set CYCLE_UID at the top of the script.")

    json_path = find_json(uid)
    print(f"JSON      : {json_path}")
    with open(json_path, "r") as f:
        data = json.load(f)

    coil_id = COIL_ID_OVERRIDE or data.get("coil_id") or ""
    cycle_uid = data.get("cycle_uid") or uid
    print(f"Coil ID   : {coil_id or '(none)'}")

    spots = filtered_defects(prepare_defects(data.get("result", [])))
    per_type = {t: sum(1 for d in spots if d["type"] == t) for t in EXPORT_TYPES}
    print("Defects   : " + ", ".join(
        f"{TYPE_FOLDERS[t]} {per_type[t]}"
        + (f" (>= {MAP_MIN_CONF[t]} %)" if t in MAP_MIN_CONF else " (any score)")
        for t in EXPORT_TYPES) + f"  = {len(spots)} total, same lists as the modal")
    if not spots:
        print("Nothing to export.")
        return

    coil_s = safe(coil_id or "coil")
    out_root = os.path.join(DOWNLOADS_DIR, safe(coil_id) if coil_id else uid)
    if os.path.exists(out_root):
        if not OVERWRITE:
            sys.exit(f"[ERROR] {out_root} already exists. Delete it or set OVERWRITE = True.")
        shutil.rmtree(out_root)
    os.makedirs(out_root)
    print(f"Output    : {out_root}\n")

    used = {t: set() for t in EXPORT_TYPES}       # file names already written, per type folder
    summary = []
    n_ok = n_missing = n_err = 0

    for i, p in enumerate(spots, 1):
        t = p["type"]
        type_dir = os.path.join(out_root, TYPE_FOLDERS.get(t, safe(t)))
        os.makedirs(type_dir, exist_ok=True)

        # names exactly as downloadDefectImage() / downloadRawDefectImage()
        stem = f"{coil_s}_F{safe(p['frame'])}_{safe(t)}_{math.floor(js_num(p['length']) / 1000)}m"
        src = p["rawImg"] or p["img"] or ""
        m_ext = re.search(r"\.[A-Za-z0-9]+$", src)
        ext = m_ext.group(0).lower() if m_ext else ".jpg"
        raw_name = f"{coil_s}_F{safe(p['frame'])}_RAW{ext}"
        info_name = unique_name(stem, ".jpg", used[t])
        tag = f"  [{i:>4}/{len(spots)}] {TYPE_FOLDERS.get(t, t)}/{info_name}"
        status = "ok"

        if not src or not os.path.isfile(src) or os.path.getsize(src) == 0:
            status = "image missing"
            n_missing += 1
            rows, m = detail_rows(p, coil_id, cycle_uid, None)
            print(f"{tag}  -> IMAGE MISSING: {src or '(no path in JSON)'}")
        else:
            try:
                raw_dst = os.path.join(type_dir, raw_name)
                if raw_name not in used[t]:                  # one raw per frame: same file for every defect in it
                    shutil.copyfile(src, raw_dst)            # raw = original bytes
                    used[t].add(raw_name)

                with Image.open(src) as im:
                    img = im.convert("RGB")
                rows, m = detail_rows(p, coil_id, cycle_uid, img.size[0])
                label_rect = None
                if isinstance(p["box"], list) and len(p["box"]) == 4:
                    label_rect = draw_box_and_label(img, p)
                img = draw_details_pane(img, rows, p, label_rect)
                img.save(os.path.join(type_dir, info_name), "JPEG", quality=JPEG_QUALITY)
                n_ok += 1
                print(tag)
            except Exception as e:
                status = f"error: {e}"
                n_err += 1
                rows, m = detail_rows(p, coil_id, cycle_uid, None)
                print(f"{tag}  -> ERROR: {e}")

        rd = dict(rows)
        summary.append([i, TYPE_FOLDERS.get(t, t), info_name, raw_name, rd.get("FRAME"),
                        rd.get("CONFIDENCE"), rd.get("LENGTH"), rd.get("POSITION"), rd.get("WIDTH"),
                        rd.get("HEIGHT"), rd.get("AREA"), rd.get("SHAPE", ""), rd.get("SPOT SIZE", ""),
                        rd.get("WS SCORE", ""), src, status])

    with open(os.path.join(out_root, "_EXPORT_SUMMARY.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"COIL ID: {coil_id}", f"CYCLE UID: {cycle_uid}", f"JSON: {json_path}"])
        w.writerow(["#", "TYPE", "INFO IMAGE", "RAW IMAGE", "FRAME", "CONFIDENCE", "LENGTH",
                    "POSITION", "WIDTH", "HEIGHT", "AREA", "SHAPE", "SPOT SIZE", "WS SCORE",
                    "SOURCE PATH", "STATUS"])
        w.writerows(summary)

    print(f"\nDone: {n_ok} exported, {n_missing} image missing, {n_err} errors  (of {len(spots)})")
    print(f"Folder: {out_root}")
    if n_missing:
        print("[WARN] Missing images are usually TRASH frames already removed by disk cleanup.")


if __name__ == "__main__":
    main()