import os
import time
import threading
import traceback
import pymysql

from datetime import datetime
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger


BASE_FILE_PATH = os.environ.get(
    "BASE_FILE_PATH",
    "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/"
)

LOGS_PATH          = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")
MQTT_TOPIC         = "taloja/health"
CHECK_INTERVAL     = 3    # seconds between each thread's health check
INACTIVE_THRESHOLD = 10   # seconds without a message → INACTIVE

db_user = os.environ.get("db_user", "root")
db_pass = os.environ.get("db_pass", "insightzz@123")
db_host = os.environ.get("db_host", "127.0.0.1")
db_name = os.environ.get("db_name", "SURFACE_INSPECTION_TALOJA")

logger = initializeLogger("HEALTH_MONITOR", LOGS_PATH=LOGS_PATH)

# Shared dict: service_name (str) → last seen timestamp (float, monotonic)
# Updated by the MQTT listener thread, read by each row's checker thread
last_seen: dict[str, float] = {}
last_seen_lock = threading.Lock()


# ── DB ────────────────────────────────────────────────────────────────────────

def get_db_conn():
    return pymysql.connect(
        host=db_host, user=db_user,
        password=db_pass, database=db_name,
        connect_timeout=5
    )


def fetch_rows():
    """Return all HEALTH_STATUS rows (we skip DISABLED inside each thread)."""
    conn = get_db_conn()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM HEALTH_STATUS")
            return cur.fetchall()
    finally:
        conn.close()


def set_status(row_id, status):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE HEALTH_STATUS SET STATUS = %s, LAST_PING = %s WHERE id = %s",
                (status, datetime.now(), row_id)
            )
        conn.commit()
    except Exception:
        logger.error(f"DB update failed (id={row_id}):\n{traceback.format_exc()}")
        conn.rollback()
    finally:
        conn.close()


# ── Service name resolution ───────────────────────────────────────────────────
# Each script publishes {"service": "LOGIC"} etc. to taloja/health.
# We match that against the DB row by IP (most stable column).

SERVICE_BY_IP = {
    "0.0.0.0":       "LOGIC",
    "192.168.3.1":   "CAPTURE",
    "192.168.2.5":   "WRITER",
    "192.168.1.13":  "TRIGGER",
}

def row_service_name(row):
    ip = (row.get("IP") or "").strip()
    return SERVICE_BY_IP.get(ip, ip)


# ── Per-row checker thread ────────────────────────────────────────────────────

def row_checker(row):
    """
    One thread per DB row.
    Every CHECK_INTERVAL seconds:
      - Skip if DB status is DISABLED
      - If last heartbeat was within INACTIVE_THRESHOLD → ACTIVE
      - Otherwise → INACTIVE
    """
    row_id   = row["ID"]
    svc_name = row_service_name(row)

    logger.info(f"Checker started → id={row_id} service={svc_name}")

    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            # Re-read status from DB to respect live DISABLED changes
            conn = get_db_conn()
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cur:
                    cur.execute("SELECT STATUS FROM HEALTH_STATUS WHERE id = %s", (row_id,))
                    live = cur.fetchone()
            finally:
                conn.close()

            if not live:
                continue

            if (live["STATUS"] or "").upper() == "DISABLED":
                logger.debug(f"[{svc_name}] DISABLED — skipping")
                continue

            with last_seen_lock:
                ts = last_seen.get(svc_name)

            elapsed = (time.monotonic() - ts) if ts is not None else float("inf")
            new_status = "ACTIVE" if elapsed <= INACTIVE_THRESHOLD else "INACTIVE"

            if new_status != (live["STATUS"] or "").upper():
                logger.info(f"[{svc_name}] {live['STATUS']} → {new_status} (elapsed={elapsed:.1f}s)")

            set_status(row_id, new_status)

        except Exception:
            logger.error(f"row_checker error (id={row_id}):\n{traceback.format_exc()}")


# ── MQTT listener thread ──────────────────────────────────────────────────────

def mqtt_listener(mq):
    logger.info("MQTT listener started")
    while True:
        try:
            data = mq.pop(MQTT_TOPIC)
            if not data:
                time.sleep(0.05)
                continue

            service = (data.get("service") or "").strip().upper()
            if not service:
                continue

            with last_seen_lock:
                last_seen[service] = time.monotonic()

            print(f"Heartbeat: {service}")
            logger.debug(f"Heartbeat: {service}")

        except Exception:
            logger.error(f"MQTT listener error:\n{traceback.format_exc()}")
            time.sleep(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    mq = MQTT("HEALTH_MONITOR")
    mq.subscribe(MQTT_TOPIC)

    rows = fetch_rows()
    logger.info(f"Loaded {len(rows)} rows from HEALTH_STATUS")

    # One checker thread per DB row
    for row in rows:
        t = threading.Thread(
            target=row_checker,
            args=(row,),
            daemon=True,
            name=f"checker-{row['ID']}-{row_service_name(row)}"
        )
        t.start()

    # Single MQTT listener — updates shared last_seen dict
    threading.Thread(
        target=mqtt_listener,
        args=(mq,),
        daemon=True,
        name="mqtt-listener"
    ).start()

    logger.info("All threads running")

    # Keep alive
    while True:
        time.sleep(10)


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception:
            logger.critical(f"main() crashed:\n{traceback.format_exc()}")
            print("Restarting in 5 seconds...")
            time.sleep(5)