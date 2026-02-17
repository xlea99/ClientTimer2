import copy
import json
import os
from datetime import datetime
from ct.common.setup import PATHS
from ct.common.logger import log

# Exponential-ish time-tier targets in seconds.  For each tier we keep the snapshot whose
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
def create_snapshot(state_dict, reason, priority="normal"):
    snap = copy.deepcopy(state_dict)
    snap["meta"]["snapshot_reason"] = reason
    snap["meta"]["snapshot_priority"] = priority

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target_path = PATHS.snapshots / f"state_{timestamp}.json"
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    log.debug(f"Saved snapshot for reason '{reason}', priority '{priority}' to {target_path}")
    return target_path

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
# Use time-tier retention to remove all snapshots that don't best fit any tier. The newest snapshot is always kept.
# We then calculate which snapshot is closest to each tier in TIERS, and delete everything else.
def prune_snapshots():
    # Gather snapshots with parsed timestamps
    entries = []
    for path in PATHS.snapshots.iterdir():
        filename = path.name
        if not filename.startswith("state_") or not filename.endswith(".json"):
            continue
        ts = _parse_snapshot_time(filename)
        if ts is not None:
            entries.append((filename, ts))

    # This means there isn't anything to prune yet.
    if len(entries) <= 1:
        return

    # Sort by newest first
    entries.sort(key=lambda e: e[1], reverse=True)
    now = datetime.now()

    # Always keep newest
    keep = set()
    keep.add(entries[0][0])

    # For each tier, find closest snapshot
    for tier_secs in TIERS:
        target = now.timestamp() - tier_secs
        best = None
        best_distance = float("inf")
        for filename, ts in entries:
            distance = abs(ts.timestamp() - target)
            if distance < best_distance:
                best_distance = distance
                best = filename
        if best is not None:
            keep.add(best)

    # Delete everything not in the keep set
    pruned_count = 0
    for filename, _ in entries:
        if filename not in keep:
            try:
                os.remove(PATHS.snapshots / filename)
                pruned_count += 1
            except OSError:
                pass
    if pruned_count > 0:
        log.info(f"Pruned {pruned_count} files from '{PATHS.snapshots}'")