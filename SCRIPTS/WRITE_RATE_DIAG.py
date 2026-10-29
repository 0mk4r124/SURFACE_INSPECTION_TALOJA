"""
WRITE_RATE_DIAG.py - standalone, read-only. Not a service; no DEPENDANT imports.

Answers two questions from files already on disk:

1) Is CAPTURE really saving 30 fps, and how late are the writes?
       python3 WRITE_RATE_DIAG.py <cam folder>
   <cam folder> = DEBUGS/RAW_IMG/TALOJA/<ROLL_uid>/<serial>   (live coil: capture write lag)
               or DEBUGS/TRASH/<ROLL_uid>/<serial>             (finished coil)
   File name carries the GRAB time (CAPTURE: ...__<counter>__<YYYYmmdd_HHMMSS_ffffff>.jpg);
   file mtime is the WRITE time. For a TRASH folder written before the hard-link
   patch, mtime is when LOGIC re-saved it, so "write lag" there means logic lag.

2) How many days of TRASH fit on this disk?
       python3 WRITE_RATE_DIAG.py --storage
"""
import os
import sys
import shutil
from collections import defaultdict
from datetime import datetime

BASE_FILE_PATH = os.environ.get("BASE_FILE_PATH",
                                "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/")
DEBUGS = os.path.join(BASE_FILE_PATH, "DEBUGS")
TARGET_FPS = 30.0
CLEANER_TRIGGER_PCT = 90   # DISK_CLEANER.TRIGGER_PCT


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def parse(name):
    """-> (counter, grab_epoch) or None"""
    try:
        parts = name.rsplit(".", 1)[0].split("__")
        counter = int(parts[2])
        ts = datetime.strptime(parts[3], "%Y%m%d_%H%M%S_%f").timestamp()
        return counter, ts
    except Exception:
        return None


def folder_report(folder):
    rows = []
    total_bytes = 0
    for n in os.listdir(folder):
        if not n.lower().endswith((".jpg", ".jpeg")):
            continue
        p = parse(n)
        if p is None:
            continue
        st = os.stat(os.path.join(folder, n))
        total_bytes += st.st_size
        rows.append((p[1], p[0], st.st_mtime))
    if len(rows) < 2:
        print(f"Not enough frames in {folder}")
        return
    rows.sort()
    grabs = [r[0] for r in rows]
    counters = [r[1] for r in rows]
    mtimes = [r[2] for r in rows]
    n = len(rows)

    dur = grabs[-1] - grabs[0]
    gaps = [b - a for a, b in zip(grabs, grabs[1:])]

    # stale-buffer burst at START: frames handed out back-to-back (<5 ms apart)
    burst = 0
    for g in gaps:
        if g < 0.005:
            burst += 1
        else:
            break

    live = grabs[burst:]                      # rate of real frames, burst excluded
    live_fps = (len(live) - 1) / max(live[-1] - live[0], 1e-6) if len(live) > 1 else 0.0

    span = max(counters) - min(counters) + 1
    lost = span - n
    big_holes = sorted(((b - a - 1, a) for a, b in zip(sorted(counters), sorted(counters)[1:]) if b - a > 1),
                       reverse=True)[:5]

    lag = [m - g for g, m in zip(grabs, mtimes)]
    wdur = max(mtimes) - min(mtimes)
    third = max(1, n // 3)

    print(f"\nFolder        : {folder}")
    print(f"Frames        : {n}   ({total_bytes / 1e9:.2f} GB, {total_bytes / n / 1e6:.2f} MB/frame)")
    print(f"Grab span     : {dur:.1f} s  -> capture rate {live_fps:.2f} fps excluding start burst "
          f"(target {TARGET_FPS})")
    print(f"Grab interval : p50 {pct(gaps, 50) * 1000:.1f} ms  p99 {pct(gaps, 99) * 1000:.1f} ms  "
          f"max {max(gaps) * 1000:.0f} ms  (33.3 ms = 30 fps)")
    print(f"Start burst   : {burst} frames grabbed <5 ms apart at the start "
          f"{'<-- STALE IDLE BUFFER (fixed by flush_stream)' if burst > 20 else ''}")
    print(f"Counter       : span {span}, missing {lost} ({100.0 * lost / span:.2f}%)  "
          f"<- incomplete grabs or SAVE QUEUE FULL drops")
    if big_holes:
        print(f"Largest holes : " + ", ".join(f"{h} frames after #{c}" for h, c in big_holes))
    print(f"Write rate    : {n / max(wdur, 1e-6):.2f} fps over {wdur:.1f} s of writes")
    print(f"Write lag     : p50 {pct(lag, 50):.2f} s  p95 {pct(lag, 95):.2f} s  max {max(lag):.2f} s")
    print(f"Lag by third  : start {pct(lag[:third], 50):.2f} s | middle {pct(lag[third:2 * third], 50):.2f} s "
          f"| end {pct(lag[2 * third:], 50):.2f} s   (rising = writes slower than capture)")
    print(f"Storage rate  : {total_bytes / max(dur, 1e-6) / 1e6:.1f} MB/s while capturing")


def storage_report():
    trash = os.path.join(DEBUGS, "TRASH")
    per_day = defaultdict(lambda: [0, 0, 0])   # date -> [bytes, frames, coils]
    for uid in os.listdir(trash):
        up = os.path.join(trash, uid)
        if not os.path.isdir(up):
            continue
        try:
            day = uid.split("_")[1][:8]
            datetime.strptime(day, "%Y%m%d")
        except Exception:
            day = "unknown"
        per_day[day][2] += 1
        for root, _, files in os.walk(up):
            for f in files:
                try:
                    per_day[day][0] += os.path.getsize(os.path.join(root, f))
                    per_day[day][1] += 1
                except OSError:
                    pass

    print(f"\nTRASH by day ({trash})")
    print(f"{'day':10} {'coils':>6} {'frames':>9} {'GB':>8} {'MB/frame':>9}")
    tot = [0, 0, 0]
    for d in sorted(per_day):
        b, fr, c = per_day[d]
        tot = [tot[0] + b, tot[1] + fr, tot[2] + c]
        print(f"{d:10} {c:6d} {fr:9d} {b / 1e9:8.1f} {b / max(fr, 1) / 1e6:9.2f}")

    du = shutil.disk_usage(DEBUGS)
    print(f"\nDisk holding DEBUGS: {du.total / 1e12:.2f} TB total, {du.used / 1e12:.2f} TB used, "
          f"{du.free / 1e12:.2f} TB free")
    full_days = [d for d in per_day if d != "unknown"]
    if tot[2] and full_days:
        per_coil = tot[0] / tot[2]
        budget = du.total * CLEANER_TRIGGER_PCT / 100.0 - (du.used - tot[0])   # space TRASH may use
        print(f"Avg per coil : {per_coil / 1e9:.1f} GB ({tot[1] / tot[2]:.0f} frames)")
        print(f"TRASH budget : {budget / 1e12:.2f} TB before the cleaner fires at {CLEANER_TRIGGER_PCT}%")
        print(f"Coils that fit: {budget / per_coil:.0f}  -> divide by your coils/day for retention days")
        print(f"With hard-linked TRASH (~0.6 MB/frame): about {budget / (0.6e6 * tot[1] / tot[2]):.0f} coils")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--storage":
        storage_report()
    elif len(sys.argv) == 2 and os.path.isdir(sys.argv[1]):
        folder_report(sys.argv[1])
    else:
        print(__doc__)
