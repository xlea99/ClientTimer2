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

# The tier ladder has no resolution below its smallest tier, so a burst of
# destructive edits used to collapse to just its first and last snapshot —
# every tier from 5 minutes to 4 days resolved to the same (oldest) file.
# These most-recent snapshots are kept whatever their age, which is what makes
# individual actions inside a burst recoverable.
RECENT_KEEP = 20

# Snapshots taken for a reason worth going back to (a full reset, the daily
# rollover, app exit) outlive the ladder for this long.
HIGH_PRIORITY_KEEP_SECS = 7 * 86400

# Absolute ceiling, so nothing above can inflate without bound. A snapshot is
# ~2.5 KB, so this is a few hundred KB at worst.
MAX_SNAPSHOTS = 100

# Writes a full copy of the state_dict as a snapshot (backupish thing)
def create_snapshot(state_dict, reason, priority="normal"):
    snap = copy.deepcopy(state_dict)
    snap["meta"]["snapshot_reason"] = reason
    snap["meta"]["snapshot_priority"] = priority

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target_path = PATHS.snapshots / f"state_{timestamp}.json"
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    # Seed the cache from the writer: this file never needs reading back.
    _PRIORITY_CACHE[target_path.name] = priority
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


# filename -> priority. A snapshot is immutable once written, so a cached
# answer can never go stale; only the FILE can disappear, and prune drops
# those entries itself.
#
# Exists because prune_snapshots read and JSON-parsed EVERY snapshot to
# recover one string from meta. At the 100-file cap that measured ~350ms,
# and it ran on every snapshot creation — drag end, layout change, reset,
# undo, app exit. It was quietly taxing most of the app, not just dragging.
#
# Bounded by the directory: prune evicts entries whose file is gone, and
# MAX_SNAPSHOTS caps the directory at 100. A filename plus a short string is
# well under a KB, so this is kilobytes, not megabytes.
_PRIORITY_CACHE = {}


# Reads back the priority create_snapshot recorded. Anything unreadable is
# treated as routine, so a corrupt file can never pin itself in the keep set.
def _snapshot_priority(path):
    name = path.name
    hit = _PRIORITY_CACHE.get(name)
    if hit is not None:
        return hit
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f).get("meta", {}).get("snapshot_priority", "normal")
    except (OSError, ValueError):
        # NOT cached: an unreadable file may be one that is still being
        # written, and pinning "normal" here would outlive the reason.
        return "normal"
    _PRIORITY_CACHE[name] = value
    return value
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

    # Keep the cache bounded to what is actually on disk. Anything deleted by
    # a previous prune (or by hand) drops out here, so this can never grow
    # past the directory — which MAX_SNAPSHOTS already caps.
    present = {name for name, _ in entries}
    for gone in [k for k in _PRIORITY_CACHE if k not in present]:
        del _PRIORITY_CACHE[gone]

    # Priority lives inside the file, so only read the ones we have to.
    priorities = {}

    def priority_of(filename):
        if filename not in priorities:
            priorities[filename] = _snapshot_priority(PATHS.snapshots / filename)
        return priorities[filename]

    # Always keep newest
    keep = set()
    keep.add(entries[0][0])

    # Recent buffer — the last RECENT_KEEP, regardless of age. Covers the
    # short timespans the tier ladder can't see into.
    for filename, _ in entries[:RECENT_KEEP]:
        keep.add(filename)

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

    # High-priority snapshots survive the ladder while they're recent enough.
    cutoff = now.timestamp() - HIGH_PRIORITY_KEEP_SECS
    for filename, ts in entries:
        if filename in keep or ts.timestamp() < cutoff:
            continue
        if priority_of(filename) == "high":
            keep.add(filename)

    # Ceiling. Drop from the oldest end, sparing high-priority until last and
    # the newest snapshot always.
    if len(keep) > MAX_SNAPSHOTS:
        newest = entries[0][0]
        oldest_first = [f for f, _ in reversed(entries) if f in keep]
        for spare_high in (True, False):
            for filename in oldest_first:
                if len(keep) <= MAX_SNAPSHOTS:
                    break
                if filename == newest or filename not in keep:
                    continue
                if spare_high and priority_of(filename) == "high":
                    continue
                keep.discard(filename)

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