"""
WS_SHAPE - White spot shape analysis (symmetric egg / oval matcher).

Goal
----
A white spot is ACCEPTED only when its segmentation polygon looks like a
symmetric egg / oval:

  1. NOT OVERSTRETCHED : major/minor axis ratio must stay under a ceiling.
                         The ceiling is TIERED - a blob with a very clean
                         elliptical outline AND strong symmetry on both axes
                         is allowed to be longer (WS_AR_MAX_CLEAN), while a
                         merely acceptable blob is held to WS_AR_MAX. This
                         stops a ragged smear and a well-formed long egg from
                         being judged by the same number.
  2. SMOOTH ELLIPTICAL OUTLINE : blob-vs-fitted-ellipse IoU >= WS_ELLIPSE_IOU_MIN
                                 and solidity >= WS_SOLIDITY_MIN
  3. SYMMETRIC : the blob, rotated so its major axis is horizontal, must
                 overlap its own mirror image
                    - across the major axis (top/bottom)  >= WS_SYM_MAJOR_MIN
                    - across the minor axis (left/right)  >= WS_SYM_MINOR_MIN
                 (the minor-axis threshold is slightly looser so a gently
                  tapered egg still passes, while a lopsided blob does not)

Verdicts (result["shape"])
--------------------------
  "OVAL"          accepted, egg/oval and symmetric        -> result["ok"] = True
  "CIRCULAR"      accepted, near-circle (ar<=WS_AR_CIRCLE)-> result["ok"] = True
  "OVERSTRETCHED" rejected, too elongated for its quality tier
  "IRREGULAR"     rejected, ragged / non-elliptical outline
  "ASYMMETRIC"    rejected, lopsided
  "FAINT"         rejected, not brighter than the strip around it
                  (only when WS_MIN_CONTRAST > 0; see apply_contrast)
  "UNKNOWN"       polygon unusable (too few points / too small)

Usage from LOGIC.py (only lines LOGIC.py needs)
-----------------------------------------------
  from DEPENDANT.WS_SHAPE import analyze_polygon_shape, scale_shape

  # inside process_labels(), for cls == "W":
  #   poly = label[9] if len(label) > 9 else None
  #   shp = analyze_polygon_shape(poly, mm_per_px=300.0 / float(w))
  #   if shp: item["shape"] = shp
  #
  # inside scale_defect_boxes():
  #   scaled_item["shape"] = scale_shape(scaled_item.get("shape"), scale)

All thresholds are environment-tunable; no code change needed on the line.
This module is pure numpy/cv2 - no torch, no DB, no file IO.
"""

import os
import math
import traceback

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Tunables (env-overridable)
# ---------------------------------------------------------------------------
WS_SHAPE_ENABLED   = os.environ.get("WS_SHAPE_ENABLED", "1") == "1"

# "not overstretched" - BASE ceiling, applied to normal-quality blobs
WS_AR_MAX          = float(os.environ.get("WS_AR_MAX", "2.2"))

# RELAXED ceiling, applied only to blobs that clear the "clean" bar below.
# A long but perfectly formed, symmetric egg is a real white spot; a ragged
# smear of the same length is not.
WS_AR_MAX_CLEAN    = float(os.environ.get("WS_AR_MAX_CLEAN", "3.0"))
WS_CLEAN_IOU_MIN   = float(os.environ.get("WS_CLEAN_IOU_MIN", "0.94"))
WS_CLEAN_SYM_MIN   = float(os.environ.get("WS_CLEAN_SYM_MIN", "0.90"))

# below this aspect ratio the spot is labelled CIRCULAR instead of OVAL
WS_AR_CIRCLE       = float(os.environ.get("WS_AR_CIRCLE", "1.15"))

# orientation gate: max tilt of the MAJOR axis away from the rolling
# direction (vertical in the frame), in degrees. 0 = disabled (default,
# preserves current verdicts). Real white spots are elongated along the
# rolling direction; recommended 30 once tilt distributions are checked.
WS_TILT_MAX        = float(os.environ.get("WS_TILT_MAX", "0"))

# elliptical-outline gates
WS_ELLIPSE_IOU_MIN = float(os.environ.get("WS_ELLIPSE_IOU_MIN", "0.85"))
WS_SOLIDITY_MIN    = float(os.environ.get("WS_SOLIDITY_MIN", "0.92"))

# symmetry gates ("symmetric egg")
WS_SYM_MAJOR_MIN   = float(os.environ.get("WS_SYM_MAJOR_MIN", "0.90"))
WS_SYM_MINOR_MIN   = float(os.environ.get("WS_SYM_MINOR_MIN", "0.82"))

# sanity limits
WS_MIN_AREA_PX     = float(os.environ.get("WS_MIN_AREA_PX", "25"))
_MAX_CANVAS        = 4000   # px; guards against pathological fits

# brightness / contrast gate (needs the frame; see analyze_contrast).
# A white spot is BRIGHTER than the strip around it. A faint smear that the
# model calls white spot (oil film, roll mark) barely differs from its
# surroundings. ct = [mean_in, mean_ring, delta] in 0-255 grey levels is
# ALWAYS recorded; the gate only acts when WS_MIN_CONTRAST > 0.
# 0 = disabled (default, preserves current verdicts).
WS_MIN_CONTRAST    = float(os.environ.get("WS_MIN_CONTRAST", "0"))
WS_RING_PX         = int(os.environ.get("WS_RING_PX", "15"))   # ring width around the blob


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ellipse_iou(cnt, center, axes, angle):
    """Overlap between the real blob and its best-fit ellipse.
    The strongest single indicator of 'is this actually elliptical'."""
    cx, cy = center
    major = max(axes) / 2.0
    x, y, bw, bh = cv2.boundingRect(cnt)

    ox = int(math.floor(min(x, cx - major))) - 2
    oy = int(math.floor(min(y, cy - major))) - 2
    ex = int(math.ceil(max(x + bw, cx + major))) + 2
    ey = int(math.ceil(max(y + bh, cy + major))) + 2

    W, H = ex - ox, ey - oy
    if W <= 0 or H <= 0 or W > _MAX_CANVAS or H > _MAX_CANVAS:
        return 0.0

    m_blob = np.zeros((H, W), np.uint8)
    m_ell  = np.zeros((H, W), np.uint8)

    shifted = cnt - np.array([[[ox, oy]]], dtype=cnt.dtype)
    cv2.drawContours(m_blob, [shifted], -1, 1, -1)

    cv2.ellipse(
        m_ell,
        (int(round(cx - ox)), int(round(cy - oy))),
        (max(1, int(round(axes[0] / 2.0))), max(1, int(round(axes[1] / 2.0)))),
        angle, 0, 360, 1, -1
    )

    union = int(np.count_nonzero(m_blob | m_ell))
    if not union:
        return 0.0
    return float(np.count_nonzero(m_blob & m_ell)) / union


def _aligned_mask(cnt, center, angle_deg, half_extent):
    """Rasterize the blob onto a square canvas centred on the ellipse centre,
    then rotate by the ellipse angle so the fitted axes align with the image
    axes (d1 -> horizontal, d2 -> vertical)."""
    size = int(2 * half_extent) + 5
    if size <= 0 or size > _MAX_CANVAS:
        return None
    half = size // 2

    ox = int(round(center[0])) - half
    oy = int(round(center[1])) - half

    canvas = np.zeros((size, size), np.uint8)
    shifted = cnt - np.array([[[ox, oy]]], dtype=cnt.dtype)
    cv2.drawContours(canvas, [shifted], -1, 1, -1)

    M = cv2.getRotationMatrix2D((half, half), angle_deg, 1.0)
    return cv2.warpAffine(canvas, M, (size, size), flags=cv2.INTER_NEAREST)


def _flip_iou(mask, flipped):
    union = int(np.count_nonzero(mask | flipped))
    if not union:
        return 0.0
    return float(np.count_nonzero(mask & flipped)) / union


def _symmetry_scores(cnt, center, axes, angle_deg):
    """Symmetry of the blob about its own fitted axes.

    Returns (sym_major, sym_minor):
      sym_major : mirror across the MAJOR axis (an egg is ~perfect here)
      sym_minor : mirror across the MINOR axis (a tapered egg dips a little,
                  a lopsided blob dips a lot)
    """
    d1, d2 = axes
    half_extent = max(d1, d2) / 2.0 + 4

    m = _aligned_mask(cnt, center, angle_deg, half_extent)
    if m is None or not m.any():
        return 0.0, 0.0

    # after rotation: d1 lies along x (horizontal), d2 along y (vertical)
    sym_across_horizontal = _flip_iou(m, np.flipud(m))  # mirror across d1 axis
    sym_across_vertical   = _flip_iou(m, np.fliplr(m))  # mirror across d2 axis

    if d1 >= d2:   # major axis is horizontal
        return sym_across_horizontal, sym_across_vertical
    else:          # major axis is vertical
        return sym_across_vertical, sym_across_horizontal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyze_polygon_shape(polygon, mm_per_px=None):
    """
    Classify a white-spot segmentation polygon.

    Parameters
    ----------
    polygon   : list of [x, y] points in full-frame pixel coords (label[9]),
                or None.
    mm_per_px : optional pixel->mm factor (e.g. 300.0 / frame_width).

    Returns
    -------
    dict with compact JSON-friendly keys, or None when disabled / no polygon.

        shape   verdict string (see module docstring)
        ok      True only for OVAL / CIRCULAR (symmetric, not overstretched)
        ell     [cx, cy, major_px, minor_px, angle_deg] fitted ellipse
        ar      aspect ratio major/minor
        ar_max  the ceiling that was actually applied (shows which tier hit)
        clean   True if the blob qualified for the relaxed ceiling
        iou     blob-vs-ellipse IoU
        sol     solidity
        sym     [sym_major, sym_minor]
        a_px    polygon area in px^2
        maj_mm / min_mm / a_mm2   physical size (when mm_per_px given)
        why     short reject reason (only on rejects)
    """
    try:
        if not WS_SHAPE_ENABLED or polygon is None:
            return None

        cnt = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
        if cnt.shape[0] < 5:
            return {"shape": "UNKNOWN", "ok": False, "why": "few_pts"}

        area = float(cv2.contourArea(cnt))
        if area < WS_MIN_AREA_PX:
            return {"shape": "UNKNOWN", "ok": False, "why": "tiny"}

        perim = float(cv2.arcLength(cnt, True))
        if perim <= 0:
            return {"shape": "UNKNOWN", "ok": False, "why": "no_perim"}

        hull_area = float(cv2.contourArea(cv2.convexHull(cnt))) or area
        solidity = area / hull_area

        (ecx, ecy), (d1, d2), angle = cv2.fitEllipse(cnt)
        major, minor = max(d1, d2), min(d1, d2)
        # fitEllipse's angle belongs to d1, NOT the major axis. Re-express it
        # as the MAJOR-axis angle so (major, minor, maj_angle) is consistent
        # for drawing. iou/symmetry below keep using the raw (d1, d2, angle).
        maj_angle = (angle if d1 >= d2 else angle + 90.0) % 180.0
        tilt_v = abs(90.0 - maj_angle)   # 0 = vertical (rolling dir), 90 = across strip
        if minor < 1.0:
            return {"shape": "IRREGULAR", "ok": False, "why": "degenerate"}

        ar  = major / minor
        iou = _ellipse_iou(cnt, (ecx, ecy), (d1, d2), angle)
        sym_major, sym_minor = _symmetry_scores(cnt, (ecx, ecy), (d1, d2), angle)

        # ---- tiered aspect-ratio ceiling ----
        # A blob that is genuinely well-formed (tight ellipse fit + symmetric
        # on BOTH axes) is permitted to be longer than a merely acceptable one.
        is_clean = (iou >= WS_CLEAN_IOU_MIN
                    and sym_major >= WS_CLEAN_SYM_MIN
                    and sym_minor >= WS_CLEAN_SYM_MIN)
        ar_ceiling = WS_AR_MAX_CLEAN if is_clean else WS_AR_MAX

        out = {
            "ell":    [round(ecx, 1), round(ecy, 1), round(major, 1), round(minor, 1), round(maj_angle, 1)],
            "tilt":   round(tilt_v, 1),
            "ar":     round(ar, 2),
            "ar_max": round(ar_ceiling, 2),
            "clean":  bool(is_clean),
            "iou":    round(iou, 3),
            "sol":    round(solidity, 3),
            "sym":    [round(sym_major, 3), round(sym_minor, 3)],
            "a_px":   round(area, 1),
        }
        if mm_per_px:
            out["maj_mm"] = round(major * mm_per_px, 2)
            out["min_mm"] = round(minor * mm_per_px, 2)
            out["a_mm2"]  = round(area * mm_per_px * mm_per_px, 2)

        # ---- verdict, in priority order ----
        if ar > ar_ceiling:
            out.update(shape="OVERSTRETCHED", ok=False, why="ar")
        elif iou < WS_ELLIPSE_IOU_MIN or solidity < WS_SOLIDITY_MIN:
            out.update(shape="IRREGULAR", ok=False, why="outline")
        elif sym_major < WS_SYM_MAJOR_MIN or sym_minor < WS_SYM_MINOR_MIN:
            out.update(shape="ASYMMETRIC", ok=False, why="sym")
        elif WS_TILT_MAX > 0 and ar > WS_AR_CIRCLE and tilt_v > WS_TILT_MAX:
            out.update(shape="HORIZONTAL", ok=False, why="tilt")
        elif ar <= WS_AR_CIRCLE:
            out.update(shape="CIRCULAR", ok=True)
        else:
            out.update(shape="OVAL", ok=True)

        return out

    except Exception:
        print(f"analyze_polygon_shape failed: {traceback.format_exc()}")
        return None


def scale_shape(shp, scale):
    """Rescale the pixel-space fields of a shape dict to a resized image
    (SAVE_SCALE). mm fields are physical and stay untouched. Safe on None."""
    try:
        if not shp or not scale or scale == 1.0:
            return shp
        ell = shp.get("ell")
        if not ell:
            return shp
        ecx, ecy, maj, minr, ang = ell
        shp = dict(shp)
        shp["ell"] = [round(ecx * scale, 1), round(ecy * scale, 1),
                      round(maj * scale, 1), round(minr * scale, 1), ang]
        if "a_px" in shp:
            shp["a_px"] = round(shp["a_px"] * scale * scale, 1)
        return shp
    except Exception:
        return shp


def analyze_contrast(img, polygon, ring_px=None):
    """Brightness of the blob vs a ring of strip around it.

    Works on a small crop around the polygon only (cost ~ the blob size,
    not the frame). Returns [mean_in, mean_ring, delta] (grey levels, delta =
    in - ring, positive = brighter than surroundings) or None.
    """
    try:
        if img is None or polygon is None:
            return None
        ring_px = WS_RING_PX if ring_px is None else ring_px
        cnt = np.asarray(polygon, dtype=np.int32).reshape(-1, 1, 2)
        if cnt.shape[0] < 3:
            return None
        H, W = img.shape[:2]
        x, y, bw, bh = cv2.boundingRect(cnt)
        x1 = max(0, x - ring_px); y1 = max(0, y - ring_px)
        x2 = min(W, x + bw + ring_px); y2 = min(H, y + bh + ring_px)
        if x2 <= x1 or y2 <= y1:
            return None

        crop = img[y1:y2, x1:x2]
        if crop.ndim == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        inner = np.zeros(crop.shape, np.uint8)
        cv2.drawContours(inner, [cnt - np.array([[[x1, y1]]], dtype=np.int32)], -1, 1, -1)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ring_px + 1, 2 * ring_px + 1))
        ring = cv2.dilate(inner, k) & (1 - inner)

        if not inner.any() or not ring.any():
            return None
        m_in = float(cv2.mean(crop, mask=inner)[0])
        m_ring = float(cv2.mean(crop, mask=ring)[0])
        return [round(m_in, 1), round(m_ring, 1), round(m_in - m_ring, 1)]
    except Exception:
        print(f"analyze_contrast failed: {traceback.format_exc()}")
        return None


def apply_contrast(shp, ct):
    """Attach ct to a shape dict and, when WS_MIN_CONTRAST > 0, reject an
    otherwise-accepted spot that is not brighter than its surroundings by at
    least WS_MIN_CONTRAST. Earlier rejects keep their reason. Safe on None."""
    if not shp or ct is None:
        return shp
    shp["ct"] = ct
    if WS_MIN_CONTRAST > 0 and shp.get("ok") and ct[2] < WS_MIN_CONTRAST:
        shp.update(shape="FAINT", ok=False, why="contrast")
    return shp


def draw_shape(img, shp, ok_color=(0, 255, 255), bad_color=(0, 0, 255)):
    """Optional debug overlay: draws the fitted ellipse on img (in-place).
    Yellow = accepted symmetric oval/egg, red = rejected."""
    try:
        if not shp or not shp.get("ell"):
            return img
        ecx, ecy, maj, minr, ang = shp["ell"]
        cv2.ellipse(
            img, (int(ecx), int(ecy)),
            (max(1, int(maj / 2)), max(1, int(minr / 2))),
            ang, 0, 360,
            ok_color if shp.get("ok") else bad_color, 2
        )
    except Exception:
        pass
    return img