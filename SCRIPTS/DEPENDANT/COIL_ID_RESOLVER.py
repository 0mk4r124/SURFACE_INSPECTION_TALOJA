"""
COIL_ID_RESOLVER.py  —  SCRIPTS/DEPENDANT/

Late coil-ID resolution for TRIGGER.py.

Problem (from DB_COIL_ID_SYNC_LOG): the coil ID for a new coil reaches
DB_COIL_ID_SYNC a few minutes AFTER the coil starts, so a single fetch at
coil start returns either NOT FOUND or the PREVIOUS coil's ID.

Rule implemented:
  * From coil start until coil end, poll DB_COIL_ID_SYNC every POLL_SEC.
  * NOT FOUND / empty  -> keep polling.
  * Same as previous coil's ID -> new ID not arrived yet, keep polling.
  * Different from previous coil's ID -> new coil ID: update DB, stop polling.
  * Coil ends without a new ID -> coil stays NOT_FOUND.

"Previous coil ID" is persisted in DEBUGS/TEMP_JSON/LAST_COIL_ID.json so it
survives TRIGGER restarts by MANAGE.py.

Non-blocking: no threads, no sleeps. TRIGGER.py calls tick() from its existing
loop; each tick runs at most one short query, only when POLL_SEC has elapsed.

A fresh short-lived DB connection is opened per poll so a dropped MySQL
connection (the issue being monitored) can never wedge the resolver.

Env-tunable (set in MANAGE.py QProcess env block if needed):
  COIL_ID_POLL_SEC     default 5
  COIL_ID_SYNC_TABLE   default DB_COIL_ID_SYNC
"""

import os
import json
import time
import logging

import pymysql

POLL_SEC = float(os.getenv("COIL_ID_POLL_SEC", "5"))
SYNC_TABLE = os.getenv("COIL_ID_SYNC_TABLE", "DB_COIL_ID_SYNC")
NOT_FOUND = "NOT_FOUND"

# Values in the sync table that mean "no coil ID yet"
_NOT_FOUND_TOKENS = {"", "NONE", "NULL", "NA", "N/A", "NOT FOUND", "NOT_FOUND", "NOTFOUND"}

# Latest row of the sync table. Assumes the same ID column as DB_COIL_ID_SYNC_LOG.
FETCH_SQL = f"SELECT COIL_ID FROM {SYNC_TABLE} ORDER BY ID DESC LIMIT 1"

# TRIGGER.py inserts the MILL_RUNS row (COIL_ID = NOT_FOUND) before start()
UPDATE_SQL = "UPDATE MILL_RUNS SET COIL_ID = %s WHERE CYCLE_UID = %s"

# Same BASE_FILE_PATH convention and default as TRIGGER.py (project root; DEBUGS joined below)
BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')
STATE_FILE = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_JSON", "LAST_COIL_ID.json")

LOG_EVERY_N_POLLS = 12  # "still waiting" log roughly once a minute at 5 s polling


def _connect():
    # Same credentials/defaults as TRIGGER.py. Short timeouts: this runs inside
    # TRIGGER's PLC loop, so a hung DB must not stall stop detection for long.
    return pymysql.connect(
        host=os.environ.get('db_host', "127.0.0.1"),
        user=os.environ.get('db_user', "root"),
        password=os.environ.get('db_pass', "insightzz@123"),
        database=os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA"),
        connect_timeout=3,
        read_timeout=3,
        write_timeout=3,
        autocommit=True,
    )


def _normalise(raw):
    """Return a clean coil ID string, or None if it means 'not found'."""
    if raw is None:
        return None
    cid = str(raw).strip()
    if cid.upper() in _NOT_FOUND_TOKENS:
        return None
    return cid


class CoilIdResolver:

    def __init__(self, logger=None, poll_sec=POLL_SEC):
        self.log = logger or logging.getLogger("COIL_ID_RESOLVER")
        self.poll_sec = poll_sec
        self.active = False
        self.cycle_uid = None
        self.coil_id = NOT_FOUND
        self._last_poll = 0.0
        self._polls = 0
        self._t0 = 0.0
        self.prev_coil_id = self._load_prev()

    # ---------------- public API (called from TRIGGER.py) ----------------

    def start(self, cycle_uid):
        """Call once when a new coil starts and its cycle UID is created."""
        if self.active:
            self.log.warning(f"[COIL_ID] start({cycle_uid}) while {self.cycle_uid} "
                             f"still resolving - closing previous as {self.coil_id}")
            self.finish()
        self.active = True
        self.cycle_uid = cycle_uid
        self.coil_id = NOT_FOUND
        self._last_poll = 0.0
        self._polls = 0
        self._t0 = time.time()
        self.log.info(f"[COIL_ID] Polling {SYNC_TABLE} for {cycle_uid} "
                      f"(prev coil = {self.prev_coil_id}, every {self.poll_sec}s)")
        return self.tick()

    def tick(self, force=False):
        """Call every iteration of TRIGGER.py's loop. Cheap when not due."""
        if not self.active:
            return self.coil_id
        now = time.time()
        if not force and (now - self._last_poll) < self.poll_sec:
            return self.coil_id
        self._last_poll = now
        self._polls += 1

        try:
            cid = _normalise(self._fetch_latest())
        except Exception as e:
            self.log.error(f"[COIL_ID] Fetch failed for {self.cycle_uid} "
                           f"(poll {self._polls}): {e}")
            return self.coil_id

        if cid is None or cid == self.prev_coil_id:
            if self._polls % LOG_EVERY_N_POLLS == 1:
                state = "NOT FOUND" if cid is None else f"still previous coil {cid}"
                self.log.info(f"[COIL_ID] {self.cycle_uid}: {state} "
                              f"after {now - self._t0:.0f}s, polling")
            return self.coil_id

        # New coil ID - write to DB first; only stop polling if the write landed.
        try:
            self._update_db(self.cycle_uid, cid)
        except Exception as e:
            self.log.error(f"[COIL_ID] Got {cid} for {self.cycle_uid} but DB update "
                           f"failed, will retry next poll: {e}")
            return self.coil_id

        self.coil_id = cid
        self.prev_coil_id = cid
        self._save_prev(cid)
        self.active = False
        self.log.info(f"[COIL_ID] {self.cycle_uid} -> {cid} "
                      f"(resolved after {time.time() - self._t0:.0f}s, {self._polls} polls)")
        return cid

    def finish(self):
        """Call when the coil ends. One last poll, then give up as NOT_FOUND."""
        if not self.active:
            return self.coil_id
        self.tick(force=True)
        if self.active:
            self.active = False
            self.log.warning(f"[COIL_ID] {self.cycle_uid}: no new coil ID by coil end "
                             f"({time.time() - self._t0:.0f}s, {self._polls} polls) "
                             f"- left as {NOT_FOUND}")
        return self.coil_id

    # ---------------- DB ----------------

    def _fetch_latest(self):
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(FETCH_SQL)
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            conn.close()

    def _update_db(self, cycle_uid, coil_id):
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(UPDATE_SQL, (coil_id, cycle_uid))
            n = cur.rowcount
            cur.close()
        finally:
            conn.close()
        if n == 0:
            # Loud on purpose: row may not be inserted yet, or the column names are wrong.
            raise RuntimeError(f"UPDATE matched 0 rows for cycle {cycle_uid}")

    # ---------------- previous coil ID persistence ----------------

    def _load_prev(self):
        try:
            with open(STATE_FILE) as f:
                prev = _normalise(json.load(f).get("coil_id"))
            self.log.info(f"[COIL_ID] Previous coil ID from state file: {prev}")
            return prev
        except FileNotFoundError:
            pass
        except Exception as e:
            self.log.error(f"[COIL_ID] Could not read {STATE_FILE}: {e}")

        # First run: whatever is in the sync table now belongs to an earlier coil.
        try:
            prev = _normalise(self._fetch_latest())
            self.log.info(f"[COIL_ID] No state file - seeded previous coil ID from "
                          f"{SYNC_TABLE}: {prev}")
            if prev:
                self._save_prev(prev)
            return prev
        except Exception as e:
            self.log.error(f"[COIL_ID] Could not seed previous coil ID: {e}")
            return None

    def _save_prev(self, coil_id):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"coil_id": coil_id, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            self.log.error(f"[COIL_ID] Could not save {STATE_FILE}: {e}")
