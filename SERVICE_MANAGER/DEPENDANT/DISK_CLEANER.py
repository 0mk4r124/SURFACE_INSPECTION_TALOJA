import os
import time
import threading
import traceback
import psutil
import logging

from logging.handlers import TimedRotatingFileHandler

from pathlib import Path
# from LOGGING import initializeLogger

def initializeLogger(logger_name, LOGS_PATH="LOGS"):
    logger = None

    try:
        LOG_BACKUP_COUNT = 2
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        log_file = os.path.join(LOGS_PATH, f"{logger_name}.log")
        file_handler = TimedRotatingFileHandler(log_file, when='midnight', backupCount=LOG_BACKUP_COUNT)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.debug(f"Logger initialized for {logger_name}")
    except Exception as e:
        print(f"initializeLogger() Exception for {logger_name}: {e}")
        print(traceback.format_exc())

    return logger

BASE_FILE_PATH = os.environ.get("BASE_FILE_PATH","/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

DISK_CHECK_INTERVAL = 60   # seconds between routine checks
TRIGGER_PCT         = 95   # start cleaning above this %
TARGET_PCT          = 80   # stop cleaning once below this %
RETENTION_DAYS      = 1    # NEVER delete files newer than this

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}

# Directories to clean, in deletion priority order (oldest-first within each)
CLEAN_DIRS = [
    os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG"),    # raw captures next
    os.path.join(BASE_FILE_PATH, "DEBUGS", "RESULT"),    # result images last (JSON kept)
    os.path.join(BASE_FILE_PATH, "DEBUGS", "TRASH"),      # trash last
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def disk_percent() -> float:
    return psutil.disk_usage("/").percent


def collect_images(paths: list[str]) -> list[tuple[float, str]]:
    files  = []
    cutoff = time.time() - (RETENTION_DAYS * 86400)   # ← NEW

    for base in paths:
        if not os.path.isdir(base):
            continue

        for root, _, fnames in os.walk(base):
            for fname in fnames:
                fp = os.path.join(root, fname)
                ext = Path(fname).suffix.lower()

                try:
                    if ext == ".json":
                        continue

                    if ext in IMAGE_EXTS:
                        mtime = os.path.getmtime(fp)
                        if mtime < cutoff:            # ← NEW: only files older than 7 days
                            files.append((mtime, fp))

                except OSError:
                    pass

    files.sort()  # oldest first
    return files


def prune_empty_dirs(paths: list[str], logger):
    """Remove empty subdirectories left after image deletion."""
    for base in paths:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base, topdown=False):
            if root == base:
                continue
            if not os.listdir(root):
                try:
                    os.rmdir(root)
                    logger.debug(f"Removed empty dir: {root}")
                except OSError:
                    pass


# ── Cleaner loop ──────────────────────────────────────────────────────────────

def _cleaner_loop(logger):
    logger.info(
        f"DiskCleaner started | trigger={TRIGGER_PCT}% target={TARGET_PCT}% "
        f"interval={DISK_CHECK_INTERVAL}s"
    )

    while True:
        try:
            pct = disk_percent()

            if pct >= TRIGGER_PCT:
                logger.warning(
                    f"[DISK] {pct:.1f}% used — CLEANUP TRIGGERED (target: {TARGET_PCT}%)"
                )

                files = collect_images(CLEAN_DIRS)
                logger.info(f"[DISK] Found {len(files)} image files to consider")

                deleted_count = 0
                freed_bytes   = 0

                for _mtime, fp in files:
                    if disk_percent() < TARGET_PCT:
                        break
                    try:
                        size = os.path.getsize(fp)
                        os.remove(fp)
                        freed_bytes   += size
                        deleted_count += 1
                        logger.info(f"[DISK] Deleted: {fp}")
                    except Exception:
                        logger.error(f"[DISK] Could not delete {fp}:\n{traceback.format_exc()}")

                prune_empty_dirs(CLEAN_DIRS, logger)

                logger.warning(
                    f"[DISK] Cleanup done | deleted={deleted_count} files | "
                    f"freed={freed_bytes / (1024**3):.2f} GB | "
                    f"disk now={disk_percent():.1f}%"
                )

                # NEW: retention guard notice
                if disk_percent() >= TRIGGER_PCT:
                    logger.warning(
                        f"[DISK] Still at {disk_percent():.1f}% but all remaining files "
                        f"are within the {RETENTION_DAYS}-day retention window — "
                        "no further deletion. Manual intervention may be required."
                    )

        except Exception:
            logger.error(f"[DISK] Cleaner loop error:\n{traceback.format_exc()}")

        time.sleep(DISK_CHECK_INTERVAL)


# ── Public API ────────────────────────────────────────────────────────────────

def start(logger=None):
    """Call once from any entry point to launch the cleaner as a daemon thread."""
    if logger is None:
        logger = initializeLogger("DISK_CLEANER", LOGS_PATH=LOGS_PATH)

    t = threading.Thread(
        target=_cleaner_loop,
        args=(logger,),
        daemon=True,
        name="disk-cleaner"
    )
    t.start()
    return t


# if __name__ == "__main__":
#     # Start disk auto-cleaner daemon
#     # start()
#     logger = initializeLogger("DISK_CLEANER", LOGS_PATH=LOGS_PATH)
#     _cleaner_loop(logger)