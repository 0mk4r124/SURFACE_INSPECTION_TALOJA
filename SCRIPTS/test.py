# #!/usr/bin/env python3
# """
# FPS_DIAG.py  -  Standalone FPS bottleneck isolator for SURFACE_INSPECTION_TALOJA

# Place in:  ROOT/SCRIPTS/FPS_DIAG.py   (so 'from DEPENDANT.FLIR import ...' resolves)
# Run:       cd ROOT/SCRIPTS && python3 FPS_DIAG.py

# Does NOT touch any production service. Reuses DEPENDANT/FLIR.py exactly as-is
# so it measures YOUR real camera configuration, not a clean-room one.

# It runs 4 stages against the same camera:
#     A) grab only            -> what the camera + link + driver can actually deliver
#     B) grab + convert       -> cost of Bayer->GRAY cvtColor
#     C) grab + convert + write -> cost of cv2.imwrite (the suspected bottleneck)
#     D) same as C, but write to /dev/shm -> separates CPU encode cost from disk cost

# The key output is FRAME ID GAPS:
#     gaps == 0  -> you consumed every frame the camera sent. Camera-side limit.
#     gaps > 0   -> camera sent more than you consumed. Host-side limit (your loop).
# """

# import os
# import sys
# import time
# import statistics
# import traceback
# from datetime import datetime

# import cv2

# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# from DEPENDANT.FLIR import FlirCamera, PySpin

# # ----------------------------------------------------------------------------
# # EDIT THESE TO MATCH YOUR PRODUCTION CAPTURE.py
# # ----------------------------------------------------------------------------
# CAM_SERIAL = "24521939"
# CAM_CONFIG = {
#     "EXPOSURE": 300.0,
#     "FRAME_RATE": 70.0,
#     "PIXEL_FORMAT": "Mono8",
#     "PACKET_SIZE": 7000,
#     "PACKET_DELAY": 10,
#     "THROUGHPUT": 700000000,
# }

# BASE_FILE_PATH = os.environ.get(
#     "BASE_FILE_PATH",
#     "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/",
# )
# DISK_OUT = os.path.join(BASE_FILE_PATH, "DEBUGS", "FPS_DIAG")
# SHM_OUT = "/dev/shm/FPS_DIAG"

# FRAMES_PER_STAGE = 400          # ~6 s per stage at 70 fps
# JPEG_QUALITY = 95               # match whatever cv2.imwrite default you rely on
# GRAB_TIMEOUT_MS = 1000


# # ----------------------------------------------------------------------------
# def dump_camera_nodes(cam):
#     """Read back what the camera ACTUALLY applied, after all configure() calls."""
#     nm = cam.GetNodeMap()
#     tl = cam.GetTLDeviceNodeMap()

#     def rd_float(node_map, name):
#         try:
#             n = PySpin.CFloatPtr(node_map.GetNode(name))
#             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#                 return n.GetValue()
#         except Exception:
#             pass
#         return None

#     def rd_int(node_map, name):
#         try:
#             n = PySpin.CIntegerPtr(node_map.GetNode(name))
#             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#                 return n.GetValue()
#         except Exception:
#             pass
#         return None

#     def rd_enum(node_map, name):
#         try:
#             n = PySpin.CEnumerationPtr(node_map.GetNode(name))
#             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#                 return n.GetCurrentEntry().GetSymbolic()
#         except Exception:
#             pass
#         return None

#     def rd_bool(node_map, name):
#         try:
#             n = PySpin.CBooleanPtr(node_map.GetNode(name))
#             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#                 return n.GetValue()
#         except Exception:
#             pass
#         return None

#     print("\n" + "=" * 70)
#     print("CAMERA STATE AS ACTUALLY APPLIED")
#     print("=" * 70)

#     rows = [
#         ("Width",                       rd_int(nm, "Width")),
#         ("Height",                      rd_int(nm, "Height")),
#         ("PixelFormat",                 rd_enum(nm, "PixelFormat")),
#         ("PixelSize",                   rd_enum(nm, "PixelSize")),
#         ("BinningHorizontal",           rd_int(nm, "BinningHorizontal")),
#         ("BinningVertical",             rd_int(nm, "BinningVertical")),
#         ("---",                         "---"),
#         ("ExposureAuto",                rd_enum(nm, "ExposureAuto")),
#         ("ExposureTime (us)",           rd_float(nm, "ExposureTime")),
#         ("GainAuto",                    rd_enum(nm, "GainAuto")),
#         ("Gain",                        rd_float(nm, "Gain")),
#         ("AcquisitionFrameRateEnable",  rd_bool(nm, "AcquisitionFrameRateEnable")),
#         ("AcquisitionFrameRate",        rd_float(nm, "AcquisitionFrameRate")),
#         # >>> THE MOST IMPORTANT LINE IN THIS ENTIRE SCRIPT <<<
#         ("AcquisitionResultingFrameRate", rd_float(nm, "AcquisitionResultingFrameRate")),
#         ("---",                         "---"),
#         ("DeviceLinkSpeed (Bps)",       rd_int(nm, "DeviceLinkSpeed")),
#         ("DeviceLinkThroughputLimit",   rd_int(nm, "DeviceLinkThroughputLimit")),
#         ("DeviceLinkCurrentThroughput", rd_int(nm, "DeviceLinkCurrentThroughput")),
#         ("DeviceMaxThroughput",         rd_int(nm, "DeviceMaxThroughput")),
#         ("GevSCPSPacketSize",           rd_int(nm, "GevSCPSPacketSize")),
#         ("GevSCPD (packet delay)",      rd_int(nm, "GevSCPD")),
#         ("DeviceTemperature (C)",       rd_float(nm, "DeviceTemperature")),
#         ("DeviceCurrentSpeed",          rd_enum(tl, "DeviceCurrentSpeed")),
#     ]
#     for k, v in rows:
#         if k == "---":
#             print("-" * 70)
#         else:
#             print(f"  {k:<32} : {v}")

#     w = rd_int(nm, "Width") or 0
#     h = rd_int(nm, "Height") or 0
#     resulting = rd_float(nm, "AcquisitionResultingFrameRate")
#     if w and h:
#         bpp = 1  # BayerRG8 / Mono8
#         need = w * h * bpp * 70
#         print("-" * 70)
#         print(f"  Frame size            : {w}x{h} = {w*h/1e6:.2f} MP ({w*h*bpp/1e6:.2f} MB/frame @8bit)")
#         print(f"  Bandwidth req @70fps  : {need/1e6:.1f} MB/s")
#         print(f"  1GigE ceiling         : ~118 MB/s   {'>>> EXCEEDED <<<' if need > 118e6 else 'OK'}")
#         print(f"  Disk write req @70fps : ~{need/1e6*0.25:.0f} MB/s of JPEG (assuming 4:1)")
#     if resulting is not None and resulting < 69.0:
#         print("-" * 70)
#         print(f"  !!! CAMERA IS CLAMPED. AcquisitionResultingFrameRate = {resulting:.2f}")
#         print(f"  !!! The camera will NEVER send 70 fps with this config.")
#         print(f"  !!! Cause is exposure / ROI / pixel format / throughput limit - NOT your code.")
#     print("=" * 70 + "\n")


# def dump_stream_stats(cam, label):
#     """Spinnaker transport-layer counters: the ground truth on dropped frames."""
#     try:
#         s = cam.GetTLStreamNodeMap()
#     except Exception:
#         return

#     def rd(name):
#         try:
#             n = PySpin.CIntegerPtr(s.GetNode(name))
#             if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
#                 return n.GetValue()
#         except Exception:
#             pass
#         return None

#     print(f"  [TL stream counters after {label}]")
#     for name in [
#         "StreamDeliveredFrameCount",
#         "StreamLostFrameCount",
#         "StreamDroppedFrameCount",
#         "StreamInputBufferCount",
#         "StreamOutputBufferCount",
#         "StreamFailedBufferCount",
#         "StreamPacketResendReceivedCount",
#         "StreamPacketResendRequestCount",
#     ]:
#         v = rd(name)
#         if v is not None:
#             print(f"     {name:<34} : {v}")


# def pct(vals, p):
#     if not vals:
#         return 0.0
#     vs = sorted(vals)
#     i = min(len(vs) - 1, int(round((p / 100.0) * (len(vs) - 1))))
#     return vs[i]


# def run_stage(cam, flir, label, do_convert, write_dir):
#     """Time one stage and report where the milliseconds go."""
#     if write_dir:
#         os.makedirs(write_dir, exist_ok=True)

#     grab_ms, conv_ms, write_ms, loop_ms = [], [], [], []
#     frame_ids = []
#     incomplete = 0

#     # warm up - discard first 30
#     for _ in range(30):
#         try:
#             im = cam.GetNextImage(GRAB_TIMEOUT_MS)
#             im.Release()
#         except Exception:
#             pass

#     t_stage = time.perf_counter()
#     t_prev = t_stage

#     for i in range(FRAMES_PER_STAGE):
#         t0 = time.perf_counter()
#         try:
#             image_result = cam.GetNextImage(GRAB_TIMEOUT_MS)
#         except Exception as e:
#             print(f"    GetNextImage failed at {i}: {e}")
#             break
#         t1 = time.perf_counter()

#         try:
#             if image_result.IsIncomplete():
#                 incomplete += 1
#                 image_result.Release()
#                 continue

#             try:
#                 frame_ids.append(image_result.GetFrameID())
#             except Exception:
#                 pass

#             img = None
#             if do_convert:
#                 img = flir.converter(image_result)
#             t2 = time.perf_counter()

#             if write_dir and img is not None:
#                 fname = os.path.join(
#                     write_dir,
#                     f"diag_{i:06d}_{datetime.now():%H%M%S_%f}.jpg",
#                 )
#                 cv2.imwrite(fname, img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
#             t3 = time.perf_counter()
#         finally:
#             try:
#                 image_result.Release()
#             except Exception:
#                 pass

#         grab_ms.append((t1 - t0) * 1000.0)
#         conv_ms.append((t2 - t1) * 1000.0)
#         write_ms.append((t3 - t2) * 1000.0)
#         loop_ms.append((t3 - t_prev) * 1000.0)
#         t_prev = t3

#     elapsed = time.perf_counter() - t_stage
#     n = len(grab_ms)
#     fps = n / elapsed if elapsed > 0 else 0.0

#     # frame id gaps = frames the camera produced that we never received
#     gaps = 0
#     if len(frame_ids) > 1:
#         for a, b in zip(frame_ids, frame_ids[1:]):
#             d = b - a
#             if d > 1:
#                 gaps += (d - 1)

#     print(f"\n  --- STAGE: {label} ---")
#     print(f"    frames ok          : {n}   incomplete: {incomplete}")
#     print(f"    elapsed            : {elapsed:.2f} s")
#     print(f"    ACHIEVED FPS       : {fps:.2f}")
#     print(f"    FRAME ID GAPS      : {gaps}   <-- frames camera sent that we dropped")
#     if n:
#         print(f"    grab  ms  p50/p95/p99/max : {pct(grab_ms,50):6.2f} / {pct(grab_ms,95):6.2f} / {pct(grab_ms,99):6.2f} / {max(grab_ms):6.2f}")
#         print(f"    conv  ms  p50/p95/p99/max : {pct(conv_ms,50):6.2f} / {pct(conv_ms,95):6.2f} / {pct(conv_ms,99):6.2f} / {max(conv_ms):6.2f}")
#         print(f"    write ms  p50/p95/p99/max : {pct(write_ms,50):6.2f} / {pct(write_ms,95):6.2f} / {pct(write_ms,99):6.2f} / {max(write_ms):6.2f}")
#         print(f"    LOOP  ms  p50/p95/p99/max : {pct(loop_ms,50):6.2f} / {pct(loop_ms,95):6.2f} / {pct(loop_ms,99):6.2f} / {max(loop_ms):6.2f}")
#         print(f"    (budget for 70 fps = 14.28 ms)")
#         slow = sum(1 for v in loop_ms if v > 14.28)
#         print(f"    iterations over budget    : {slow}/{n} ({100.0*slow/n:.1f}%)")

#     dump_stream_stats(cam, label)
#     return fps, gaps


# def main():
#     print(f"FPS_DIAG  -  {datetime.now()}")
#     print(f"BASE_FILE_PATH = {BASE_FILE_PATH}")
#     print(f"Target: {CAM_SERIAL}  requested FPS = {CAM_CONFIG['FRAME_RATE']}")

#     flir = FlirCamera()
#     flir.set_config(CAM_SERIAL, CAM_CONFIG)

#     status, err = flir.initialize(CAM_SERIAL)
#     if not status:
#         print(f"initialize failed: {err}")
#         return

#     cam, err = flir.start_acquisition(CAM_SERIAL)
#     if err or cam is None:
#         print(f"start_acquisition failed: {err}")
#         flir.release_all()
#         return

#     try:
#         dump_camera_nodes(cam)

#         results = {}
#         results["A grab only"] = run_stage(cam, flir, "A: grab only (no convert, no write)",
#                                            do_convert=False, write_dir=None)
#         results["B +convert"] = run_stage(cam, flir, "B: grab + Bayer convert",
#                                           do_convert=True, write_dir=None)
#         results["C +write disk"] = run_stage(cam, flir, f"C: grab + convert + imwrite -> {DISK_OUT}",
#                                              do_convert=True, write_dir=DISK_OUT)
#         results["D +write tmpfs"] = run_stage(cam, flir, f"D: grab + convert + imwrite -> {SHM_OUT} (RAM)",
#                                               do_convert=True, write_dir=SHM_OUT)

#         print("\n" + "=" * 70)
#         print("SUMMARY")
#         print("=" * 70)
#         for k, (fps, gaps) in results.items():
#             print(f"  {k:<18} : {fps:7.2f} fps   (dropped {gaps} frames)")
#         print("-" * 70)
#         print("  READ IT LIKE THIS:")
#         print("   A already < 70          -> camera/link/config limit. Fix FLIR.py + network.")
#         print("   A ~70 but C ~30         -> host limit. imwrite is the bottleneck.")
#         print("   C ~30 but D ~70         -> DISK is the bottleneck, not CPU encode.")
#         print("   C ~= D ~= 30            -> CPU JPEG encode is the bottleneck.")
#         print("   gaps > 0 anywhere       -> NewestOnly is silently discarding your frames.")
#         print("=" * 70)

#     except Exception:
#         print(traceback.format_exc())
#     finally:
#         try:
#             flir.end_acquisition(CAM_SERIAL)
#         except Exception:
#             pass
#         try:
#             flir.release_all()
#         except Exception:
#             pass
#         print("\nCleanup: remove test images with:")
#         print(f"  rm -rf {DISK_OUT} {SHM_OUT}")


# if __name__ == "__main__":
#     main()


# import PySpin
# system = PySpin.System.GetInstance()
# cam = system.GetCameras().GetBySerial("24521939")
# cam.Init()
# nm = cam.GetNodeMap()

# isp = PySpin.CBooleanPtr(nm.GetNode("IspEnable"))
# print("IspEnable available:", PySpin.IsAvailable(isp),
#       "writable:", PySpin.IsWritable(isp))
# if PySpin.IsAvailable(isp):
#     print("IspEnable currently:", isp.GetValue())
#     if PySpin.IsWritable(isp):
#         isp.SetValue(True)
#         pf = PySpin.CEnumerationPtr(nm.GetNode("PixelFormat"))
#         fmts = [PySpin.CEnumEntryPtr(e).GetSymbolic()
#                 for e in pf.GetEntries()
#                 if PySpin.IsReadable(PySpin.CEnumEntryPtr(e))]
#         print("Formats WITH Isp:", fmts)
#         fr = PySpin.CFloatPtr(nm.GetNode("AcquisitionFrameRate"))
#         print("Max FPS with Isp on:", fr.GetMax())
#         isp.SetValue(False)   # restore

# cam.DeInit()
# del cam
# system.ReleaseInstance()#!/usr/bin/env python3









#!/usr/bin/env python3
"""
FLIR streaming diagnostic - finds WHY no frames arrive.

Run with the Capture service stopped and SpinView closed:

    python3 SCRIPTS/STREAM_DIAG.py

Checks, in order:
  1. Trigger mode      - TriggerMode=On with no trigger signal = 0 fps, black
  2. Packet size / MTU - camera packet > NIC MTU = 0 fps (silent)
  3. Acquisition mode
  4. Actually streams and reports per-frame status + stream statistics
"""
import os
import subprocess
import sys
import time

SERIAL = "24521939"
NIC = "ens10f0np0"

try:
    import PySpin
except ImportError:
    print("PySpin not importable.")
    sys.exit(1)


def ip_str(v):
    v = int(v)
    return "%d.%d.%d.%d" % ((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255)


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as e:
        return f"<{e}>"


def enum_get(nm, name):
    try:
        n = PySpin.CEnumerationPtr(nm.GetNode(name))
        if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
            return n.GetCurrentEntry().GetSymbolic()
    except Exception:
        pass
    return None


def enum_set(nm, name, value):
    try:
        n = PySpin.CEnumerationPtr(nm.GetNode(name))
        if PySpin.IsAvailable(n) and PySpin.IsWritable(n):
            e = n.GetEntryByName(value)
            if PySpin.IsAvailable(e):
                n.SetIntValue(e.GetValue())
                return True
    except Exception:
        pass
    return False


def int_get(nm, name):
    try:
        n = PySpin.CIntegerPtr(nm.GetNode(name))
        if PySpin.IsAvailable(n) and PySpin.IsReadable(n):
            return int(n.GetValue())
    except Exception:
        pass
    return None


def int_set(nm, name, value):
    try:
        n = PySpin.CIntegerPtr(nm.GetNode(name))
        if PySpin.IsAvailable(n) and PySpin.IsWritable(n):
            v = max(int(n.GetMin()), min(int(value), int(n.GetMax())))
            n.SetValue(v)
            return v
    except Exception:
        pass
    return None


def main():
    print("=" * 72)
    print("HOST NETWORK")
    print("=" * 72)
    mtu = sh(f"cat /sys/class/net/{NIC}/mtu")
    print(f"{NIC} MTU        : {mtu}")
    print(f"{NIC} link       : {sh(f'cat /sys/class/net/{NIC}/operstate')}")
    print(f"rmem_max         : {sh('sysctl -n net.core.rmem_max')}")
    print(f"firewall         : {sh('sudo -n ufw status 2>/dev/null || echo (needs sudo - check manually)')}")
    print(f"ping camera      : {sh('ping -c2 -W2 192.168.3.1 | tail -2')}")

    print()
    print("=" * 72)
    print("OPEN CAMERA")
    print("=" * 72)
    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()

    target = None
    for c in cam_list:
        try:
            tl = c.GetTLDeviceNodeMap()
            sn = PySpin.CStringPtr(tl.GetNode("DeviceSerialNumber"))
            cam_ip = int_get(tl, "GevDeviceIPAddress")
            if PySpin.IsReadable(sn) and sn.GetValue() == SERIAL:
                print(f"  candidate: cam ip {ip_str(cam_ip) if cam_ip else '-'}")
                if target is None:
                    target = c
        except Exception:
            continue
    del c, tl

    if target is None:
        print("Camera not found.")
        cam_list.Clear(); system.ReleaseInstance(); return 1

    target.Init()
    nm = target.GetNodeMap()
    print("Init() OK")

    print()
    print("=" * 72)
    print("SUSPECT 1 - TRIGGER MODE")
    print("=" * 72)
    tm = enum_get(nm, "TriggerMode")
    print(f"  TriggerMode      : {tm}")
    print(f"  TriggerSource    : {enum_get(nm, 'TriggerSource')}")
    print(f"  AcquisitionMode  : {enum_get(nm, 'AcquisitionMode')}")
    if tm == "On":
        print("  >>> TriggerMode is ON. The camera waits for a trigger and sends")
        print("      NOTHING on its own. This alone gives 0 fps and a black view.")
    print(f"  forcing TriggerMode Off : {enum_set(nm, 'TriggerMode', 'Off')}")
    print(f"  forcing AcquisitionMode Continuous : {enum_set(nm, 'AcquisitionMode', 'Continuous')}")

    print()
    print("=" * 72)
    print("SUSPECT 2 - PACKET SIZE vs MTU")
    print("=" * 72)
    pkt = int_get(nm, "GevSCPSPacketSize")
    print(f"  camera packet size : {pkt}")
    print(f"  NIC MTU            : {mtu}")
    try:
        if pkt and mtu.isdigit() and pkt > int(mtu) - 36:
            print("  >>> PACKET SIZE EXCEEDS MTU. Every image packet is dropped by the")
            print("      kernel. Control still works, so you get 0 fps and no error.")
    except Exception:
        pass
    print(f"  forcing packet size 1500 -> {int_set(nm, 'GevSCPSPacketSize', 1500)}")
    print(f"  packet delay             -> {int_set(nm, 'GevSCPD', 1000)}")

    print()
    print("=" * 72)
    print("SAFE EXPOSURE (so a real frame is visible)")
    print("=" * 72)
    enum_set(nm, "ExposureAuto", "Off")
    enum_set(nm, "GainSelector", "All")
    enum_set(nm, "GainAuto", "Off")
    try:
        et = PySpin.CFloatPtr(nm.GetNode("ExposureTime"))
        if PySpin.IsWritable(et):
            et.SetValue(20000.0)
            print(f"  ExposureTime -> {et.GetValue():.0f} us (long, for visibility only)")
        gn = PySpin.CFloatPtr(nm.GetNode("Gain"))
        if PySpin.IsWritable(gn):
            gn.SetValue(10.0)
            print(f"  Gain         -> {gn.GetValue():.2f} dB")
    except Exception as e:
        print(f"  exposure/gain set failed: {e}")

    # host-side stream buffers
    tls = target.GetTLStreamNodeMap()
    enum_set(tls, "StreamBufferHandlingMode", "OldestFirst")

    print()
    print("=" * 72)
    print("STREAM TEST - 10 frames")
    print("=" * 72)
    rc = 1
    try:
        target.BeginAcquisition()
        print("BeginAcquisition() OK")
        good = incomplete = timeout = 0
        for i in range(10):
            try:
                img = target.GetNextImage(3000)
                if img.IsIncomplete():
                    incomplete += 1
                    print(f"  frame {i}: INCOMPLETE status={img.GetImageStatus()}")
                else:
                    a = img.GetNDArray()
                    good += 1
                    print(f"  frame {i}: OK {a.shape} min={a.min()} max={a.max()} mean={a.mean():.1f}")
                img.Release()
            except Exception as e:
                timeout += 1
                print(f"  frame {i}: NO IMAGE - {e}")
        print(f"\n  good={good}  incomplete={incomplete}  timeout={timeout}")

        for stat in ["StreamDeliveredFrameCount", "StreamLostFrameCount",
                     "StreamFailedBufferCount", "StreamIncompleteFrameCount"]:
            v = int_get(tls, stat)
            if v is not None:
                print(f"  {stat:30s} = {v}")

        target.EndAcquisition()
        rc = 0 if good else 1
    except Exception as e:
        print(f"BeginAcquisition/stream FAILED: {e}")

    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    if rc == 0:
        print("Frames ARE arriving at packet size 1500.")
        print("If max==0 the sensor sees no light; if max>0 the pipeline is fine.")
        print("Next: raise packet size only AFTER setting NIC MTU 9000.")
    else:
        print("No frames even at packet size 1500 with trigger off.")
        print("Check: firewall (sudo ufw disable), and that no other client holds")
        print("the camera. Report the per-frame lines above.")

    try:
        target.DeInit()
    except Exception:
        pass
    del target, nm, tls
    cam_list.Clear()
    system.ReleaseInstance()
    time.sleep(0.2)
    return rc


if __name__ == "__main__":
    sys.exit(main())