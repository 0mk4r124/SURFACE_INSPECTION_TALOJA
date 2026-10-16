#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DAILY_DEFECT_REPORT.py
SURFACE_INSPECTION_TALOJA -- daily defect report, one row per processed coil.

HOW TO USE
  1. Edit the three settings below (RESULT_DIR, REPORT_DATE, OUT_DIR).
  2. Run:   python3 DAILY_DEFECT_REPORT.py
  3. Open  OUT_DIR/DAILY_DEFECT_REPORT_<REPORT_DATE>.xlsx

WHAT IT READS
  Every *.json file in RESULT_DIR/<REPORT_DATE>/ - one coil per file, in the
  history format:
     {"cycle_uid": "ROLL_...", "coil_id": "...",
      "result": [ {"W": [{"c": <accuracy %>, ...}, ...], "O": [...], "D": [...],
                   "t": {"W": n, ...}, ...}, ... ] }
  Every entry in a class list is one defect (nothing merged); "c" is the
  model's detection accuracy for it.

WHAT IT WRITES  (values only - no formulas, opens fast)
  REPORT : SR.NO | COIL ID | CYCLE ID | for each class: AVG ACCURACY %, TOTAL |
           for each class: >= 90 %, 75 % to < 90 %, < 75 %  (the three bands
           add up to the class total) | TOTAL row for the day.
  FILES  : every file looked at, with its status and any warning.

STANDALONE OFFLINE TOOL. Not a service, not started by MANAGE.py, no DB,
imports nothing from SCRIPTS/DEPENDANT. Takes no command-line arguments.
Requires: openpyxl
"""

import os
import re
import sys
import json
import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ============================================================================
# >>> SETTINGS - EDIT BEFORE RUNNING <<<
# ============================================================================
RESULT_DIR = "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/DEBUGS/RESULT"
REPORT_DATE = "20260927"                                    # YYYYMMDD = folder in RESULT_DIR
OUT_DIR = "/home/hindalco_surface_inspection/Downloads/XL_REPORT"

# Defect classes, in report order: (JSON key, name in "AVG .. ACCURACY %", name in totals/bands)
CLASSES = [
    ("W", "WHITE SPOT", "WHITE SPOTS"),
    ("S", "SCORING", "SCORING"),
    ("D", "DENTS", "DENTS"),
    ("O", "OIL STAIN", "OIL STAINS"),
    ("C", "SCRATCH", "SCRATCHES"),
    ("F", "FOLD", "FOLDS"),
]
ACC_KEY = "c"             # accuracy of one defect, in %
BAND_HIGH = 90.0          # band 1: accuracy >= BAND_HIGH
BAND_LOW = 75.0           # band 2: BAND_LOW <= accuracy < BAND_HIGH ; band 3: < BAND_LOW
RECURSIVE = False         # True = also read JSON files in sub-folders of the date folder
FILE_SUFFIX = "_defect_data.json"   # coil files: RESULT/<date>/ROLL_x/ROLL_x_defect_data.json

# ============================================================================
# STYLE
# ============================================================================
FONT = "Arial"
F_TITLE = Font(name=FONT, size=13, bold=True, color="FFFFFF")
F_HDR = Font(name=FONT, size=9, bold=True, color="FFFFFF")
F_BODY = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_NOTE = Font(name=FONT, size=9, italic=True, color="595959")
FILL_TITLE = PatternFill("solid", fgColor="1F3864")
FILL_BASE = PatternFill("solid", fgColor="44546A")
CLASS_FILLS = ["2F5597", "7030A0", "C55A11", "548235", "BF8F00"]
FILL_TOTAL = PatternFill("solid", fgColor="D9E1F2")
FILL_WARN = PatternFill("solid", fgColor="FFC7CE")
FILL_OK = PatternFill("solid", fgColor="C6EFCE")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CTR = Alignment(horizontal="center", vertical="center", wrap_text=True)


# ============================================================================
# READ ONE COIL FILE
# ============================================================================
def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def read_coil(path):
    """Return (row dict or None, status, warning text)."""
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "SKIPPED", "not readable as JSON: %s" % e
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        return None, "SKIPPED", "no 'result' list - not a coil history file"

    m = re.search(r"ROLL_\d{14}", os.path.basename(path))
    cycle = data.get("cycle_uid") or (m.group(0) if m else None)
    coil = data.get("coil_id")
    warns = []
    if not cycle:
        warns.append("no cycle_uid")
    if not coil:
        warns.append("no coil_id")

    stats = {k: dict(n=0, acc_sum=0.0, acc_n=0, hi=0, mid=0, lo=0, t=0) for k, _, _ in CLASSES}
    for rec in data["result"]:
        if not isinstance(rec, dict):
            continue
        t = rec.get("t") if isinstance(rec.get("t"), dict) else {}
        for key, _, _ in CLASSES:
            st = stats[key]
            st["t"] += int(_num(t.get(key)) or 0)
            items = rec.get(key)
            if not isinstance(items, list):
                continue
            for it in items:
                st["n"] += 1
                acc = _num(it.get(ACC_KEY)) if isinstance(it, dict) else None
                if acc is None:
                    continue
                st["acc_sum"] += acc
                st["acc_n"] += 1
                if acc >= BAND_HIGH:
                    st["hi"] += 1
                elif acc >= BAND_LOW:
                    st["mid"] += 1
                else:
                    st["lo"] += 1

    for key, _, plural in CLASSES:
        st = stats[key]
        if st["t"] != st["n"]:
            warns.append("%s: %d in lists but t-count says %d" % (plural, st["n"], st["t"]))
        if st["acc_n"] != st["n"]:
            warns.append("%s: %d without accuracy - not in average or bands"
                         % (plural, st["n"] - st["acc_n"]))
        if st["acc_n"] and st["acc_sum"] / st["acc_n"] <= 1.0:
            warns.append("%s: accuracy looks like 0-1, not %% - check ACC_KEY" % plural)
    row = dict(cycle=cycle or "-", coil=coil or "-", file=os.path.basename(path), stats=stats)
    return row, ("OK" if not warns else "OK WITH WARNINGS"), "; ".join(warns)


def list_files(folder):
    """Every file in the date folder and its sub-folders (one per coil)."""
    out = []
    for root, _, files in os.walk(folder):
        out += [os.path.join(root, f) for f in files]
    return sorted(out)


# ============================================================================
# MAIN
# ============================================================================
def main():
    if not re.fullmatch(r"\d{8}", str(REPORT_DATE)):
        sys.exit("REPORT_DATE must be YYYYMMDD, got %r" % REPORT_DATE)
    try:
        day = datetime.datetime.strptime(REPORT_DATE, "%Y%m%d").date()
    except ValueError:
        sys.exit("REPORT_DATE %r is not a real date" % REPORT_DATE)
    folder = os.path.join(RESULT_DIR, REPORT_DATE)
    if not os.path.isdir(folder):
        sys.exit("Folder not found: %s" % folder)

    rows, files_log = [], []
    if not any(p.endswith(FILE_SUFFIX) for p in list_files(folder)):
        sys.exit("No *%s files found under %s" % (FILE_SUFFIX, folder))
    for path in list_files(folder):
        if not path.endswith(FILE_SUFFIX):
            continue                                  # images, other JSONs: not coil files
        row, status, warn = read_coil(path)
        files_log.append([os.path.relpath(path, folder), row["cycle"] if row else "-",
                          row["coil"] if row else "-", status, warn])
        if row:
            rows.append(row)

    # the same cycle in two files would be counted twice - keep one, flag both
    seen = {}
    for r in rows:
        seen.setdefault(r["cycle"], []).append(r)
    dupes = {c for c, lst in seen.items() if len(lst) > 1 and c != "-"}
    if dupes:
        keep = []
        for r in rows:
            if r["cycle"] in dupes:
                if seen[r["cycle"]][0] is not r:
                    for fl in files_log:
                        if fl[0].endswith(r["file"]):
                            fl[3], fl[4] = "SKIPPED", "duplicate of cycle %s" % r["cycle"]
                    continue
            keep.append(r)
        rows = keep
    rows.sort(key=lambda r: r["cycle"])

    # ---------------------------------------------------------------- workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "REPORT"
    ws.sheet_view.showGridLines = False

    head = ["SR.NO.", "COIL ID", "CYCLE ID"]
    fills = [FILL_BASE] * 3
    for i, (_, name, plural) in enumerate(CLASSES):
        head += ["AVG %s ACCURACY %%" % name, "TOTAL %s" % plural]
        fills += [PatternFill("solid", fgColor=CLASS_FILLS[i % len(CLASS_FILLS)])] * 2
    band_names = ["%s %g%% AND ABOVE" % ("{}", BAND_HIGH),
                  "%s %g%% TO BELOW %g%%" % ("{}", BAND_LOW, BAND_HIGH),
                  "%s BELOW %g%%" % ("{}", BAND_LOW)]
    for i, (_, _, plural) in enumerate(CLASSES):
        head += [b.format(plural) for b in band_names]
        fills += [PatternFill("solid", fgColor=CLASS_FILLS[i % len(CLASS_FILLS)])] * 3
    ncol = len(head)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.cell(1, 1, "DAILY DEFECT REPORT  -  %s  -  %d coil(s)" % (day.strftime("%d-%m-%Y"), len(rows)))
    ws.cell(1, 1).font = F_TITLE
    ws.cell(1, 1).fill = FILL_TITLE
    ws.row_dimensions[1].height = 24
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    ws.cell(2, 1, "Accuracy = model detection accuracy of each defect. Bands: %g%% and above | "
                  "%g%% to below %g%% | below %g%% - the three bands add up to the total. "
                  "Source: %s" % (BAND_HIGH, BAND_LOW, BAND_HIGH, BAND_LOW, folder))
    ws.cell(2, 1).font = F_NOTE

    HR = 4
    for c, (h, f) in enumerate(zip(head, fills), start=1):
        cell = ws.cell(HR, c, h)
        cell.font = F_HDR
        cell.fill = f
        cell.alignment = CTR
        cell.border = BOX
    ws.row_dimensions[HR].height = 48

    def values(stats_list):
        out = []
        for key, _, _ in CLASSES:
            n = sum(s[key]["n"] for s in stats_list)
            an = sum(s[key]["acc_n"] for s in stats_list)
            asum = sum(s[key]["acc_sum"] for s in stats_list)
            out += [round(asum / an, 2) if an else "-", n]
        for key, _, _ in CLASSES:
            out += [sum(s[key]["hi"] for s in stats_list), sum(s[key]["mid"] for s in stats_list),
                    sum(s[key]["lo"] for s in stats_list)]
        return out

    r = HR + 1
    for i, row in enumerate(rows, start=1):
        vals = [i, row["coil"], row["cycle"]] + values([row["stats"]])
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(r, c, v)
            cell.font = F_BODY
            cell.border = BOX
            cell.alignment = CTR
            if isinstance(v, float):
                cell.number_format = "0.00"
        r += 1
    # TOTAL row: counts summed, averages weighted by defect count
    tot = ["", "TOTAL", "%d coil(s)" % len(rows)] + values([row["stats"] for row in rows])
    for c, v in enumerate(tot, start=1):
        cell = ws.cell(r, c, v)
        cell.font = F_BOLD
        cell.fill = FILL_TOTAL
        cell.border = BOX
        cell.alignment = CTR
        if isinstance(v, float):
            cell.number_format = "0.00"

    for c in range(1, ncol + 1):
        ws.column_dimensions[get_column_letter(c)].width = 7 if c == 1 else (
            16 if c in (2, 3) else 12)
    ws.column_dimensions["C"].width = 22
    ws.freeze_panes = ws.cell(HR + 1, 4)
    if rows:
        ws.auto_filter.ref = "A%d:%s%d" % (HR, get_column_letter(ncol), HR + len(rows))

    fl = wb.create_sheet("FILES")
    fl.sheet_view.showGridLines = False
    for c, h in enumerate(["FILE", "CYCLE ID", "COIL ID", "STATUS", "WARNING"], start=1):
        cell = fl.cell(1, c, h)
        cell.font = F_HDR
        cell.fill = FILL_BASE
        cell.alignment = CTR
        cell.border = BOX
    for i, v in enumerate(files_log, start=2):
        for c, x in enumerate(v, start=1):
            cell = fl.cell(i, c, x)
            cell.font = F_BODY
            cell.border = BOX
        fl.cell(i, 4).fill = FILL_OK if v[3] == "OK" else FILL_WARN
    for c, w in zip("ABCDE", (48, 22, 16, 18, 90)):
        fl.column_dimensions[c].width = w
    fl.freeze_panes = "A2"

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "DAILY_DEFECT_REPORT_%s.xlsx" % REPORT_DATE)
    wb.save(out)

    # ---------------------------------------------------------------- console
    bad = [f for f in files_log if f[3] != "OK"]
    print("date %s | files %d | coils in report %d | files with issues %d"
          % (day.strftime("%d-%m-%Y"), len(files_log), len(rows), len(bad)))
    for (key, _, plural), i in zip(CLASSES, range(len(CLASSES))):
        n = tot[4 + 2 * i]
        if n:
            print("  %-12s total %7d | avg accuracy %s %%" % (plural, n, tot[3 + 2 * i]))
    for f in bad:
        print("  %s: %s - %s" % (f[0], f[3], f[4]))
    print("written: %s" % out)


if __name__ == "__main__":
    main()