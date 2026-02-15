"""Snapshot system — time-tiered retention replacing old count-based backups.

Snapshots are full copies of state.json stored in a snapshots/ directory.
Retention uses exponential-ish time tiers so you always have a few recent
snapshots and older ones become progressively sparser.
"""

import copy
import json
import os
import time as _time
from datetime import datetime


# Time-tier targets in seconds.  For each tier we keep the snapshot whose
# timestamp is closest to (now - tier).
TIERS = [
    5 * 60,       # ~5 minutes ago
    10 * 60,      # ~10 minutes ago
    20 * 60,      # ~20 minutes ago
    60 * 60,      # ~1 hour ago
    6 * 3600,     # ~6 hours ago
    24 * 3600,    # ~1 day ago
    2 * 86400,    # ~2 days ago
    4 * 86400,    # ~4 days ago
]

# Writes a full copy of the state_dict as a snapshot (backupish thing)
def create_snapshot(state_dict, snapshot_dir, reason, priority="normal"):
    os.makedirs(snapshot_dir, exist_ok=True)
    snap = copy.deepcopy(state_dict)
    snap["meta"]["snapshot_reason"] = reason
    snap["meta"]["snapshot_priority"] = priority

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(snapshot_dir, f"state_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    return path

# Extracts and returns the datetime from a given snapshot's filename, such as state_20260212_140311_123456.json ->
# 2/12/2026, 2:03PM, 11.123456 seconds
def _parse_snapshot_time(filename):
    base = os.path.splitext(filename)[0]  # state_20260212_140311_123456
    parts = base.split("_", 1)
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(parts[1], "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None


def prune_snapshots(snapshot_dir):
    """Apply time-tier retention, removing snapshots that don't fit any tier.

    Algorithm:
    1. Always keep the newest snapshot.
    2. For each tier, keep the snapshot closest to (now - tier_seconds).
    3. Delete everything else.
    """
    if not os.path.isdir(snapshot_dir):
        return

    # Gather snapshots with parsed timestamps
    entries = []
    for fname in os.listdir(snapshot_dir):
        if not fname.startswith("state_") or not fname.endswith(".json"):
            continue
        ts = _parse_snapshot_time(fname)
        if ts is not None:
            entries.append((fname, ts))

    if len(entries) <= 1:
        return  # nothing to prune

    # Sort newest first
    entries.sort(key=lambda e: e[1], reverse=True)
    now = datetime.now()

    keep = set()
    # Always keep newest
    keep.add(entries[0][0])

    # For each tier, find closest snapshot
    for tier_secs in TIERS:
        target = now.timestamp() - tier_secs
        best = None
        best_dist = float("inf")
        for fname, ts in entries:
            dist = abs(ts.timestamp() - target)
            if dist < best_dist:
                best_dist = dist
                best = fname
        if best is not None:
            keep.add(best)

    # Delete everything not in the keep set
    for fname, _ in entries:
        if fname not in keep:
            try:
                os.remove(os.path.join(snapshot_dir, fname))
            except OSError:
                pass


class SnapshotScheduler:
    """Tracks when snapshots should be created.  Does NOT create them.

    Normal-priority requests are debounced (coalesced over a short window)
    and gated by a minimum interval.  High-priority requests return True
    from ``request()`` so the caller can snapshot immediately.
    """

    def __init__(self, min_interval=300, debounce=30):
        self._min_interval = min_interval  # seconds between normal snapshots
        self._debounce = debounce          # seconds to wait after last action
        self._dirty = False
        self._dirty_reason = None
        self._last_action_time = None      # monotonic
        self._last_snapshot_time = 0.0     # monotonic

    def request(self, reason, priority="normal"):
        """Request a snapshot.

        Returns True if the snapshot should happen immediately (high priority).
        For normal priority, marks dirty and returns False.
        """
        if priority == "high":
            return True
        self._dirty = True
        self._dirty_reason = reason
        self._last_action_time = _time.monotonic()
        return False

    def check(self):
        """Called from tick loop.  Returns (should_fire, reason).

        Fires when:
        - Dirty AND debounce expired AND min_interval elapsed, OR
        - Not dirty but min_interval elapsed (periodic snapshot).
        """
        now = _time.monotonic()

        if self._dirty and self._last_action_time is not None:
            debounce_ok = (now - self._last_action_time) >= self._debounce
            interval_ok = (now - self._last_snapshot_time) >= self._min_interval
            if debounce_ok and interval_ok:
                return True, self._dirty_reason or "periodic"

        # Periodic: even if not dirty, snapshot every min_interval
        if (now - self._last_snapshot_time) >= self._min_interval:
            return True, "periodic"

        return False, None

    def mark_done(self):
        """Called after a snapshot was successfully created."""
        self._dirty = False
        self._dirty_reason = None
        self._last_action_time = None
        self._last_snapshot_time = _time.monotonic()
