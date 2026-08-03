"""
LOGIC_LIVE - LIVE-cycle service (split out of LOGIC.py).

Runs ONLY the live-coil streaming path:
  - 2 dedicated models: taloja1, taloja2
  - MQTT 'start'/'stop' driven streaming via LOGIC.process_cycle_stream
  - LIVE_FALLBACK adoption of actively-written folders (missed START)
  - Writes live.json (this process OWNS live.json; LOGIC_ORPHAN never touches it)

Orphan recovery is handled by the separate LOGIC_ORPHAN.py process.

Design notes:
  - LOGIC.py is imported as a LIBRARY and is completely unchanged. All
    processing, DB, live.json, saver/loader and MQTT-UI behavior is reused
    from it, so results are byte-identical to the current live path.
  - Cross-process safety: while streaming a coil, this process heartbeats a
    'live' claim file (DEPENDANT.CYCLE_CLAIM). LOGIC_ORPHAN skips any cycle
    with a fresh live claim. Conversely, fallback adoption here skips any
    cycle with a fresh 'orphan' claim. If this process crashes mid-coil the
    claim goes stale and LOGIC_ORPHAN recovers the folder - same
    self-healing behavior as before, now across processes.

Run:  python3 LOGIC_LIVE.py     (from SCRIPTS/, same as LOGIC.py today)
"""

import os
import time
import threading
import traceback

from threading import Lock

import LOGIC as L                      # reuse ALL existing logic, unchanged

from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger
from DEPENDANT.CYCLE_CLAIM import (touch_claim, release_claim, claimed_uids, cleanup_stale_claims)

logger = initializeLogger("LOGIC_LIVE", LOGS_PATH=L.LOGS_PATH)

# How often the live claim heartbeat file is refreshed while streaming.
LIVE_CLAIM_HEARTBEAT_SECS = float(os.environ.get('LIVE_CLAIM_HEARTBEAT_SECS', '5'))


# =========================================================================
# Live claim heartbeat
# =========================================================================
def _claim_heartbeat():
    """
    Daemon thread: while a coil is streaming (LOGIC._current_live_uid set),
    keep its 'live' claim fresh so LOGIC_ORPHAN never touches that cycle.
    process_cycle_stream (in LOGIC.py, unchanged) maintains _current_live_uid
    for us, so no changes to LOGIC.py are needed.
    """
    while True:
        try:
            with L._live_uid_lock:
                uid = L._current_live_uid
            if uid:
                touch_claim(uid, "live")
        except Exception:
            logger.error(f"live claim heartbeat error: {traceback.format_exc()}")
        time.sleep(LIVE_CLAIM_HEARTBEAT_SECS)


def _run_live(cycle_uid, model_1, model_2, mq_obj, mqtt_triggered=True):
    """Claim -> stream -> release. Claim is released even on failure so a
    crashed cycle can be recovered by LOGIC_ORPHAN once it goes idle."""
    touch_claim(cycle_uid, "live")
    try:
        L.process_cycle_stream(cycle_uid, model_1, model_2, mq_obj,
                               mqtt_triggered=mqtt_triggered)
    finally:
        release_claim(cycle_uid, "live")


# =========================================================================
# Main service loop (live half of the original LOGIC.main)
# =========================================================================
def main():

    # ---- 2 models DEDICATED to the live cycle ----
    model_1 = L._build_model("taloja1")
    model_2 = L._build_model("taloja2")
    for m in (model_1, model_2):
        m.infer_lock = Lock()
        m.use_amp = L.INFER_FP16

    logger.info(f"LOGIC_LIVE model pool ready: taloja1, taloja2 | FP16={L.INFER_FP16}")
    print(f"LOGIC_LIVE model pool ready: taloja1, taloja2 | "
          f"FP16={L.INFER_FP16} DEBUG_PARALLEL={L.DEBUG_PARALLEL}", flush=True)

    mq_obj = MQTT("logic_live")
    mq_obj.subscribe("taloja/capture")          # TRIGGER start/stop

    base = os.path.join(L.RAW_PATH, L.LOCATION)

    threading.Thread(target=_claim_heartbeat,
                     name="live_claim_hb", daemon=True).start()

    cleanup_stale_claims()

    print("LOGIC_LIVE service started, waiting for triggers...")
    logger.info("LOGIC_LIVE service started, waiting for triggers")

    last_idle_pub = 0.0

    while True:
        now_t = time.time()

        # idle heartbeats (same topics/payloads as the original LOGIC main
        # loop so HEALTH_MONITOR and the UI keep working unchanged)
        if now_t - last_idle_pub > 1.0:
            mq_obj.publish("taloja/health", {"service": "LOGIC"})
            mq_obj.publish("main/logic", {"roll_num": "--"})
            last_idle_pub = now_t

        # ---- 1) Trigger-driven streaming (primary path) ----
        data = mq_obj.data
        if data and data.get("_consumed") is not True:
            action   = data.get("action", "")
            roll_num = data.get("roll_num", "")
            if action == "start" and roll_num:
                mq_obj.data = {**data, "_consumed": True}
                print(f"START received for {roll_num}")
                logger.info(f"START received for {roll_num}")
                try:
                    _run_live(roll_num, model_1, model_2, mq_obj,
                              mqtt_triggered=True)
                except Exception:
                    logger.critical(f"process_cycle_stream error:\n{traceback.format_exc()}")
                continue

        # ---- 1b) FALLBACK live adoption (missed START protection) ----
        # Skip anything LOGIC_ORPHAN is currently recovering (fresh orphan
        # claim). An actively-written folder can never be a valid orphan
        # anyway, so this exclusion is a pure safety net.
        if L.LIVE_FALLBACK and not L.LIVE_ACTIVE.is_set():
            busy = claimed_uids("orphan")
            active_uid = L.find_active_cycle(base, exclude=busy)
            if active_uid:
                logger.warning(f"No MQTT start seen but {active_uid} is being written - "
                               f"adopting as LIVE (fallback)")
                print(f"Adopting actively-written cycle as LIVE (fallback): {active_uid}")
                try:
                    _run_live(active_uid, model_1, model_2, mq_obj,
                              mqtt_triggered=False)
                except Exception:
                    logger.critical(f"fallback stream error:\n{traceback.format_exc()}")
                continue

        time.sleep(0.2)


if __name__ == "__main__":

    while True:
        try:
            main()
        except Exception:
            logger.critical(f"main() crashed:\n{traceback.format_exc()}")
            print("Error in LOGIC_LIVE main() ! Restarting in 5 secs")
            time.sleep(5)