"""
CYCLE_CLAIM - cross-process cycle ownership for LOGIC_LIVE / LOGIC_ORPHAN.

LOGIC_LIVE.py and LOGIC_ORPHAN.py run as SEPARATE processes, so the old
in-process locks (_live_uid_lock / _active_orphans) can no longer stop the
two services from picking up the same cycle folder. This module provides a
tiny file-based claim system instead:

    DEBUGS/TEMP_JSON/CYCLE_CLAIMS/<cycle_uid>.live
    DEBUGS/TEMP_JSON/CYCLE_CLAIMS/<cycle_uid>.orphan

- A claim is "fresh" if its mtime is within CLAIM_FRESH_SECS. Owners must
  keep touching (heartbeating) their claim while working on the cycle.
- If a process crashes, its heartbeat stops, the claim goes stale, and the
  other service is free to recover the cycle. This preserves the existing
  self-healing behavior (crashed live coil -> recovered as orphan).
- Claims live OUTSIDE the RAW_IMG cycle folders on purpose: touching a file
  inside a cycle folder would make it look "actively written" and confuse
  find_active_cycle / find_orphan_cycles idle detection.

No existing file depends on this module; it is only used by the two new
entry scripts. LOGIC.py is untouched.
"""

import os
import time

BASE_FILE_PATH = os.environ.get(
    'BASE_FILE_PATH',
    '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/'
)

CLAIMS_DIR = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_JSON", "CYCLE_CLAIMS")

# A claim older than this is considered dead (owner crashed / stopped).
# Owners heartbeat far more often than this (live: ~5s, orphan: ~15s).
CLAIM_FRESH_SECS = float(os.environ.get('CLAIM_FRESH_SECS', '60'))


def _claim_path(cycle_uid, kind):
    return os.path.join(CLAIMS_DIR, f"{cycle_uid}.{kind}")


def touch_claim(cycle_uid, kind):
    """Create/refresh a claim ('live' or 'orphan') for a cycle."""
    try:
        os.makedirs(CLAIMS_DIR, exist_ok=True)
        p = _claim_path(cycle_uid, kind)
        with open(p, "a"):
            pass
        os.utime(p, None)
    except OSError:
        pass


def release_claim(cycle_uid, kind):
    """Remove a claim once the cycle is finalized (or on failure)."""
    try:
        os.remove(_claim_path(cycle_uid, kind))
    except OSError:
        pass


def is_claimed(cycle_uid, kind, fresh_secs=None):
    """True if a FRESH claim of this kind exists for the cycle."""
    fresh = CLAIM_FRESH_SECS if fresh_secs is None else fresh_secs
    try:
        return (time.time() - os.path.getmtime(_claim_path(cycle_uid, kind))) < fresh
    except OSError:
        return False


def claimed_uids(kind, fresh_secs=None):
    """Set of cycle_uids that currently hold a FRESH claim of this kind."""
    fresh = CLAIM_FRESH_SECS if fresh_secs is None else fresh_secs
    out = set()
    suffix = "." + kind
    try:
        now = time.time()
        for fname in os.listdir(CLAIMS_DIR):
            if not fname.endswith(suffix):
                continue
            uid = fname[: -len(suffix)]
            try:
                if (now - os.path.getmtime(os.path.join(CLAIMS_DIR, fname))) < fresh:
                    out.add(uid)
            except OSError:
                continue
    except OSError:
        pass
    return out


def cleanup_stale_claims(older_than_secs=3600):
    """Housekeeping: delete claim files untouched for a long time."""
    try:
        now = time.time()
        for fname in os.listdir(CLAIMS_DIR):
            p = os.path.join(CLAIMS_DIR, fname)
            try:
                if (now - os.path.getmtime(p)) > older_than_secs:
                    os.remove(p)
            except OSError:
                continue
    except OSError:
        pass