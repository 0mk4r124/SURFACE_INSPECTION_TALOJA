"""
WS_SIZE_STATS - pick WS_MAX_AREA_MM2 from a WS_FILTER_TEST run, and preview
what the new production white-spot logic will do on that coil.

Not a service. Set CSV_PATH below, then from SCRIPTS/:
    python3 WS_SIZE_STATS.py

Reads the *_white_spots.csv written by WS_FILTER_TEST.py (no DB, no model).
"""

# =========================================================================
# SET THESE
# =========================================================================
CSV_PATH = ("/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/DEBUGS/RESULT/TEST/"
            "TEST_ROLL_20261026000000/TEST_ROLL_20261026000000_white_spots.csv")
SIZE_LIMIT_X_MEDIAN = 3.0      # "really big" = this many times the median true-spot area
THRESHOLD = 0.0                # same as WS_SCORE_THRESHOLD
MAP_MIN_CONF_W = 95            # web-app map / count rule for white spot
# =========================================================================

import csv
import sys
import statistics

MM_PER_PX = 400.0 / 2448.0     # FRAME_WIDTH_MM / native frame width
SHAPE_OK = {"OVAL", "CIRCULAR"}
SHAPE_SCORED_REJECT = {"OIL_LIKE", "OVERSIZE"}   # set by the new WS_SHAPE - not a shape-gate reject


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pct(v, p):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    try:
        rows = list(csv.DictReader(open(CSV_PATH, newline="")))
    except OSError as e:
        sys.exit(f"Cannot read {CSV_PATH}: {e}")
    if not rows:
        sys.exit("CSV has no white spots")
    if "a_mm2" not in rows[0]:
        sys.exit("CSV has no a_mm2 column - re-run WS_FILTER_TEST.py")

    for r in rows:
        r["_a"] = f(r.get("a_mm2"))
        r["_s"] = f(r.get("s_new"))
        r["_c"] = f(r.get("conf"))
        sh = (r.get("shape") or "").upper()
        # shape verdict BEFORE the score/size gate (new WS_SHAPE may already write OIL_LIKE/OVERSIZE)
        r["_shape_ok"] = sh in SHAPE_OK or sh in SHAPE_SCORED_REJECT
        r["_unknown"] = sh in ("UNKNOWN", "")

    kept = [r for r in rows if r["_s"] is not None and r["_s"] > THRESHOLD and r["_shape_ok"]]
    areas_true = [r["_a"] for r in kept if r["_a"] is not None]
    areas_all = [r["_a"] for r in rows if r["_a"] is not None]
    if not areas_true:
        sys.exit("No spots pass the score + shape gates - cannot size the limit")

    print(f"\n{len(rows)} white spots in {CSV_PATH.rsplit('/', 1)[-1]}")
    print("\nArea, mm^2 (px = mm^2 / %.4f)" % (MM_PER_PX ** 2))
    print(f"{'':28s}{'n':>6s}{'median':>9s}{'mean':>9s}{'P90':>9s}{'P95':>9s}{'P99':>9s}{'max':>9s}")
    for name, v in (("all detections", areas_all), ("pass score + shape (true)", areas_true)):
        print(f"{name:28s}{len(v):6d}{statistics.median(v):9.1f}{statistics.mean(v):9.1f}"
              f"{pct(v, 90):9.1f}{pct(v, 95):9.1f}{pct(v, 99):9.1f}{max(v):9.1f}")

    med = statistics.median(areas_true)
    print("\nCandidate limits (spots that pass score + shape, then rejected by size):")
    for x in (2.0, 3.0, 4.0, 5.0):
        lim = med * x
        n = sum(1 for a in areas_true if a > lim)
        print(f"  {x:.0f} x median = {lim:7.1f} mm^2 ({lim / MM_PER_PX ** 2:8.0f} px)  rejects {n} of {len(areas_true)}")

    limit = round(med * SIZE_LIMIT_X_MEDIAN, 1)

    # preview of production verdicts with this limit
    # same order as production: shape gates, then size (any score), then score
    out = {"WHITE_SPOT": 0, "OVERSIZE (checked before score)": 0, "OIL_LIKE": 0,
           "SHAPE (asym/stretch/irregular)": 0, "kept: no panel data / UNKNOWN": 0}
    vis_w = 0
    for r in rows:
        if r["_unknown"] or r["_s"] is None and r["_shape_ok"]:
            out["kept: no panel data / UNKNOWN"] += 1
            v = "W"
        elif not r["_shape_ok"]:
            out["SHAPE (asym/stretch/irregular)"] += 1
            v = "O"
        elif r["_a"] is not None and r["_a"] > limit:
            out["OVERSIZE (checked before score)"] += 1
            v = "O"
        elif r["_s"] <= THRESHOLD:
            out["OIL_LIKE"] += 1
            v = "O"
        else:
            out["WHITE_SPOT"] += 1
            v = "W"
        if v == "W" and r["_c"] is not None and r["_c"] >= MAP_MIN_CONF_W:
            vis_w += 1

    print(f"\nPreview with WS_MAX_AREA_MM2 = {limit}  (threshold {THRESHOLD})")
    for k, n in out.items():
        print(f"  {k:32s} {n}")
    print(f"  white spots on the map (conf >= {MAP_MIN_CONF_W}%): {vis_w}")
    print(f"\nSet in SERVICE_MANAGER/MANAGE.py env block:   env.insert(\"WS_MAX_AREA_MM2\", \"{limit}\")")
    print("Check a few crops in crops/WHITE_SPOT that are near the limit before deploying.\n")


if __name__ == "__main__":
    main()
