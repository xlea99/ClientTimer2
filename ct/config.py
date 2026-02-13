"""Unified state management — single state.json source of truth.

Replaces the old split-brain approach (config.json + recent_save.json).
Migration from old format happens automatically on first load.
"""

import json
import os
from datetime import date, datetime, timezone


CONFIG_DIR = os.path.join(
    os.getenv("APPDATA") or os.path.expanduser("~"),
    "ICOMM Client Timer",
)
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
SNAPSHOT_DIR = os.path.join(CONFIG_DIR, "snapshots")
COMPLETED_DIR = os.path.join(CONFIG_DIR, "completed_sessions")

# Old paths — used only for migration detection
_OLD_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
_OLD_SAVE_PATH = os.path.join(CONFIG_DIR, "recent_save.json")

_SETTINGS_DEFAULTS = {
    "theme": "Cupertino Light",
    "size": "Regular",
    "font": "Calibri",
    "label_align": "Left",
    "client_separators": False,
    "show_group_count": True,
    "show_group_time": True,
    "always_on_top": True,
    "confirm_delete": True,
    "confirm_reset": True,
    "daily_reset_enabled": False,
    "daily_reset_time": "00:00",
    "snapshot_min_minutes": 5,
}


def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(COMPLETED_DIR, exist_ok=True)


def now_iso():
    """Return current local time as an ISO 8601 string with timezone offset."""
    return datetime.now().astimezone().isoformat()


def build_default_state():
    """Construct a fresh empty state dict."""
    return {
        "meta": {
            "schema_version": 1,
            "saved_at": now_iso(),
            "is_completed_session": False,
        },
        "layout": {
            "rows": [],
            "collapsed_groups": [],
        },
        "settings": dict(_SETTINGS_DEFAULTS),
        "session": {
            "start": now_iso(),
            "tracked_times": {},
        },
    }


def load_state():
    """Load the unified state from state.json.

    If state.json doesn't exist, attempts migration from old config.json
    and recent_save.json.  If neither exists, returns a fresh default state.
    """
    _ensure_dirs()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        # Fill in any missing settings with defaults
        settings = state.setdefault("settings", {})
        for key, default in _SETTINGS_DEFAULTS.items():
            settings.setdefault(key, default)
        state.setdefault("session", {
            "start": now_iso(), "tracked_times": {}
        })
        state["session"].setdefault("tracked_times", {})
        return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Try migrating from old format
    state = _migrate_from_old()
    if state is not None:
        save_state(state)
        return state

    # Fresh start
    state = build_default_state()
    save_state(state)
    return state


def save_state(state):
    """Write state to disk, updating the saved_at timestamp."""
    _ensure_dirs()
    state["meta"]["saved_at"] = now_iso()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def save_completed_session(state_dict, boundary_dt):
    """Write a finalized session file to completed_sessions/.

    Takes the current full state, marks it as completed, stamps session.end,
    and writes it as a timestamped file.  The result is a fully self-contained
    state file that could be restored on its own.
    """
    import copy
    _ensure_dirs()
    completed = copy.deepcopy(state_dict)
    completed["meta"]["is_completed_session"] = True
    completed["meta"]["saved_at"] = now_iso()
    completed["session"]["end"] = boundary_dt.isoformat()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(COMPLETED_DIR, f"session_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(completed, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# Migration from old config.json + recent_save.json
# ---------------------------------------------------------------------------

def _migrate_clients(clients):
    """Convert old string-list client format to row-dict format.

    Old: ["#Sysco", "Sysco Calls", "Sysco Tickets"]
    New: [{"rowid": 0, "name": "Sysco", "type": "separator", "bg": null}, ...]
    Returns (rows, collapsed_groups).
    """
    if not clients or not isinstance(clients[0], str):
        return clients, []  # already new format or empty

    new_clients = []
    for i, name in enumerate(clients):
        if name.startswith("#"):
            new_clients.append({
                "rowid": i, "name": name[1:],
                "type": "separator", "bg": None,
            })
        else:
            new_clients.append({
                "rowid": i, "name": name,
                "type": "timer", "bg": None,
            })
    return new_clients, []


def _migrate_from_old():
    """Read old config.json + recent_save.json, construct state.json."""
    try:
        with open(_OLD_CONFIG_PATH, "r", encoding="utf-8") as f:
            old_cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None  # no old config to migrate from

    # Migrate client list format if needed
    raw_clients = old_cfg.get("clients", [])
    rows, _ = _migrate_clients(raw_clients)
    if rows is raw_clients:
        rows = list(raw_clients)  # already new format, copy it

    # Handle old collapsed_groups (might be #name strings)
    old_collapsed = old_cfg.get("collapsed_groups", [])
    if old_collapsed and isinstance(old_collapsed[0], str):
        collapsed = []
        for row in rows:
            if (row["type"] == "separator"
                    and f"#{row['name']}" in set(old_collapsed)):
                collapsed.append(row["rowid"])
    else:
        collapsed = list(old_collapsed)

    # Build settings from old config
    settings = {}
    for key, default in _SETTINGS_DEFAULTS.items():
        if key == "snapshot_min_minutes":
            # Migrate from old backup_frequency if present
            old_freq = old_cfg.get("backup_frequency", 15)
            settings[key] = max(1, old_freq)
        else:
            settings[key] = old_cfg.get(key, default)

    # Load times from old save file (if today's)
    tracked_times = {}
    try:
        with open(_OLD_SAVE_PATH, "r", encoding="utf-8") as f:
            old_save = json.load(f)
        if old_save.get("date") == date.today().isoformat():
            old_times = old_save.get("clients", {})
            # Handle both rowid-string keys and name-based keys
            name_to_rid = {}
            for row in rows:
                if row["type"] == "timer":
                    name_to_rid[row["name"]] = row["rowid"]

            for key, elapsed in old_times.items():
                try:
                    rid = int(key)
                    tracked_times[str(rid)] = {"elapsed": float(elapsed)}
                except ValueError:
                    # Old name-based key
                    if key in name_to_rid:
                        rid = name_to_rid[key]
                        tracked_times[str(rid)] = {"elapsed": float(elapsed)}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Ensure all timers have an entry
    for row in rows:
        if row["type"] == "timer":
            tracked_times.setdefault(str(row["rowid"]), {"elapsed": 0.0})

    state = {
        "meta": {
            "schema_version": 1,
            "saved_at": now_iso(),
            "is_completed_session": False,
        },
        "layout": {
            "rows": rows,
            "collapsed_groups": collapsed,
        },
        "settings": settings,
        "session": {
            "start": now_iso(),
            "tracked_times": tracked_times,
        },
    }
    return state
