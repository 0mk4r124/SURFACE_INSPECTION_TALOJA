"""
LOGIC_ORPHAN - ORPHAN-cycle recovery service (split out of LOGIC.py).

Runs ONLY background orphan recovery:
  - 2 dedicated models: taloja3, taloja4
  - Up to ORPHAN_MAX (default 2) orphan cycles recovered SIMULTANEOUSLY,
    each on the least-busy model pair via the existing LOGIC.ModelAllocator
  - Uses LOGIC.process_cycle(..., bg_orphan=True), so this process NEVER
    writes live.json and never publishes ui/logic - exactly like the old
    in-process orphan workers. live.json stays owned by LOGIC_LIVE.

Design notes:
  - LOGIC.py is imported as a LIBRARY and is completely unchanged
    (process_cycle, ModelAllocator, _run_orphan_bg, find_orphan_cycles are
    all reused as-is).
  - Cross-process safety via DEPENDANT.CYCLE_CLAIM:
      * cycles with a FRESH 'live' claim (LOGIC_LIVE streaming or just
        finalizing them) are never picked up here;
      * every orphan being recovered holds an 'orphan' claim, heartbeated
        each scheduler pass, so LOGIC_LIVE's fallback adoption skips it;
      * if this process crashes, its claims go stale and the cycles are
        simply found as orphans again on next start - same recovery
        semantics as today.

Run:  python3 LOGIC_ORPHAN.py     (from SCRIPTS/, same as LOGIC.py today)
"""

import os
import time
import threading
import traceback

from threading import Lock
from concurrent.futures import ThreadPoolExecutor

import LOGIC as L                      # reuse ALL existing logic, unchanged

from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger
from DEPENDANT.CYCLE_CLAIM import (touch_claim, release_claim, claimed_uids, cleanup_stale_claims)

logger = initializeLogger("LOGIC_ORPHAN", LOGS_PATH=L.LOGS_PATH)

# Orphan cycles recovered in parallel (2 models -> 2 concurrent cycles,
# shared safely through each model's infer_lock + least-busy allocation,
# exactly like the existing orphan pool behavior).
ORPHAN_MAX = int(os.environ.get('ORPHAN_MAX', '3'))
ORPHAN_NUM_MODELS = int(os.environ.get('ORPHAN_NUM_MODELS', '6'))
ORPHAN_IDLE_SECS = float(os.environ.get('ORPHAN_IDLE_SECS', '5'))
ORPHAN_SCAN_INTERVAL = float(os.environ.get('ORPHAN_SCAN_INTERVAL', '60'))

def _run_orphan(cycle_uid, orphan_alloc, mq_obj):
    """Claim wrapper around the existing LOGIC._run_orphan_bg worker.
    _run_orphan_bg already handles model acquire/release, logging, and
    removing the uid from LOGIC._active_orphans in its finally block."""
    touch_claim(cycle_uid, "orphan")
    try:
        L._run_orphan_bg(cycle_uid, orphan_alloc, mq_obj)
    finally:
        release_claim(cycle_uid, "orphan")


def main():

    # ---- 2 models DEDICATED to orphan recovery ----
    # model_3 = L._build_model("taloja3")
    # model_4 = L._build_model("taloja4")
    # orphan_models = [model_3, model_4]

    orphan_models = []
    for i in range(3, 3 + ORPHAN_NUM_MODELS):
        try:
            m = L._build_model(f"taloja{i}")
            orphan_models.append(m)
            logger.info(f"Loaded orphan model taloja{i}")
        except Exception:
            logger.error(f"Could not load taloja{i} (likely GPU memory): {traceback.format_exc()}")
            break
    if not orphan_models:
        raise RuntimeError("No orphan models could be loaded")


    for m in orphan_models:
        m.infer_lock = Lock()
        m.use_amp = L.INFER_FP16

    # Least-busy shared allocation: with 2 models, both ORPHAN_MAX cycles
    # run at the same time (each model's infer_lock keeps sharing safe).
    orphan_alloc = L.ModelAllocator(orphan_models)

    logger.info(f"LOGIC_ORPHAN model pool ready: taloja3, taloja4 | "
                f"ORPHAN_MAX={ORPHAN_MAX} FP16={L.INFER_FP16}")
    print(f"LOGIC_ORPHAN model pool ready: taloja3, taloja4 | "
          f"ORPHAN_MAX={ORPHAN_MAX} FP16={L.INFER_FP16} "
          f"DEBUG_PARALLEL={L.DEBUG_PARALLEL}", flush=True)

    mq_obj = MQTT("logic_orphan")

    base = os.path.join(L.RAW_PATH, L.LOCATION)

    orphan_pool = ThreadPoolExecutor(max_workers=ORPHAN_MAX,
                                     thread_name_prefix="orphan")

    cleanup_stale_claims()

    print("LOGIC_ORPHAN service started, scanning for orphan cycles...")
    logger.info("LOGIC_ORPHAN service started, scanning for orphan cycles")

    while True:
        try:
            # ---- keep our orphan claims fresh (cross-process heartbeat) ----
            with L._orphan_lock:
                active = list(L._active_orphans)
            for uid in active:
                touch_claim(uid, "orphan")

            # health heartbeat: same service name as before so
            # HEALTH_MONITOR keeps working with zero changes
            mq_obj.publish("taloja/health", {"service": "LOGIC"})

            # ---- STATUS heartbeat (debug only) ----
            if L.DEBUG_PARALLEL:
                try:
                    raw_cycles = [d for d in os.listdir(base)
                                  if os.path.isdir(os.path.join(base, d))]
                except OSError:
                    raw_cycles = []
                L.dbg(f"[STATUS] orphans_running={len(active)} {active} "
                      f"orphan_model_load={orphan_alloc.snapshot()} "
                      f"live_claims={sorted(claimed_uids('live'))} "
                      f"raw_cycles={len(raw_cycles)} {raw_cycles[:5]}")

            # ---- schedule orphans ----
            with L._orphan_lock:
                free_slots = ORPHAN_MAX - len(L._active_orphans)
                busy = set(L._active_orphans)

            # never touch the coil LOGIC_LIVE is streaming/finalizing
            busy |= claimed_uids("live")

            if free_slots > 0:
                for uid in L.find_orphan_cycles(base,
                                                idle_secs=ORPHAN_IDLE_SECS,
                                                exclude=busy)[:free_slots]:
                    with L._orphan_lock:
                        if uid in L._active_orphans:
                            continue
                        L._active_orphans.add(uid)
                    # claim BEFORE submitting so LOGIC_LIVE's fallback
                    # can never adopt it in the tiny scheduling gap
                    touch_claim(uid, "orphan")
                    print(f"Recovering orphan cycle in background: {uid}")
                    logger.info(f"Recovering orphan cycle in background: {uid}")
                    orphan_pool.submit(_run_orphan, uid, orphan_alloc, mq_obj)

        except Exception:
            logger.error(f"orphan scheduler error: {traceback.format_exc()}")

        time.sleep(ORPHAN_SCAN_INTERVAL)


if __name__ == "__main__":

    while True:
        try:
            main()
        except Exception:
            logger.critical(f"main() crashed:\n{traceback.format_exc()}")
            print("Error in LOGIC_ORPHAN main() ! Restarting in 5 secs")
            time.sleep(5)