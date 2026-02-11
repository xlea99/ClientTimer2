"""JSON-based configuration and save file management.

No eval(). No arbitrary code execution. Just JSON like a normal person.
"""

import json
import os
from datetime import date

CONFIG_DIR = os.path.join(
    os.getenv("APPDATA") or os.path.expanduser("~"),
    "ICOMM Client Timer",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SAVE_PATH = os.path.join(CONFIG_DIR, "recent_save.json")
BACKUP_DIR = os.path.join(CONFIG_DIR, "Backups")

_DEFAULTS = {
    "clients": [],
    "theme": "Cupertino Light",
    "size": "Regular",
    "font": "Calibri",
    "label_align": "Left",
    "client_separators": False,
    "show_group_count": True,
    "show_group_time": True,
    "collapsed_groups": [],
    "always_on_top": True,
    "backup_frequency": 15,
    "max_backups": 5,
    "confirm_delete": True,
    "confirm_reset": True,
    "daily_reset_enabled": False,
    "daily_reset_time": "00:00",
}


def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _migrate_clients(data):
    """Migrate old string-list client format to new row-dict format.

    Old: ["#Sysco", "Sysco Calls", "Sysco Tickets"]
    New: [{"rowid": 0, "name": "Sysco", "type": "separator", "bg": null}, ...]
    """
    clients = data.get("clients", [])
    if not clients or not isinstance(clients[0], str):
        return  # already new format or empty

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
    data["clients"] = new_clients

    # Migrate collapsed_groups from old #name strings to rowids
    old_collapsed = set(data.get("collapsed_groups", []))
    new_collapsed = []
    for row in new_clients:
        if row["type"] == "separator" and f"#{row['name']}" in old_collapsed:
            new_collapsed.append(row["rowid"])
    data["collapsed_groups"] = new_collapsed

    save_config(data)


def load_config():
    """Load config from disk, creating defaults if missing or corrupt."""
    _ensure_dirs()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, default in _DEFAULTS.items():
            data.setdefault(key, default)
        _migrate_clients(data)
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        save_config(dict(_DEFAULTS))
        return dict(_DEFAULTS)


def save_config(data):
    """Write config to disk."""
    _ensure_dirs()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_times():
    """Load saved times if they're from today.

    Returns:
        dict of {key: elapsed_seconds} or None.
        Keys may be rowid strings (new) or client names (old).
    """
    try:
        with open(SAVE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == date.today().isoformat():
            return data.get("clients", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return None


def save_times(client_times):
    """Save current times. client_times: {rowid_str: elapsed_seconds}."""
    _ensure_dirs()
    with open(SAVE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"date": date.today().isoformat(), "clients": client_times},
            f,
            indent=2,
        )
