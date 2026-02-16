import copy
import json
from datetime import datetime
from ct.common.logger import log
from ct.util import now_iso, read_old_config
from ct.common.setup import PATHS


_SCHEMA_VERSION = 1

#region === Helpers and Paths ===

STATE_PATH = PATHS.current / "state.json"
SNAPSHOT_DIR = PATHS.snapshots
COMPLETED_DIR = PATHS.sessions

# Old path, used only for migration detection
_OLD_CONFIG = PATHS.old / "config.txt"

# Default values just for the settings section of the state dict.
_SETTINGS_DEFAULTS = {
    "theme": "Cupertino Light",
    "size": "Regular",
    "font": "Calibri",
    "label_align": "Left",
    "client_separators": True,
    "show_group_count": True,
    "show_group_time": True,
    "always_on_top": True,
    "confirm_delete": True,
    "confirm_reset": True,
    "daily_reset_enabled": False,
    "daily_reset_time": "00:00",
    "snapshot_min_minutes": 5,
}
# Helper to return a truly fresh, default state.
def build_default_state():
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

#endregion === Helpers and Paths ===

#region === Saving and Loading State ===

# Loads the current unified state from PATHS.current / state.json, ensuring the schema is valid and handling
# default fallbacks.
def load_state():
    try:
        # If the save doesn't yet exist, we check if there's an old ClientTimer1 install to migrate from. If so,
        # it gets built using those clients/sizing. Otherwise, a full fresh default state is built.
        if not STATE_PATH.exists():
            state = build_default_state()
            if _OLD_CONFIG.exists():
                migration_dict = read_old_config(_OLD_CONFIG)
                for i,timer in enumerate(migration_dict["Timers"]):
                    state["layout"]["rows"].append({
                        "rowid": i,
                        "name": timer,
                        "type": "timer",
                        "bg": None
                    })
                state["settings"]["size"] = migration_dict["Size"]
                state["settings"]["theme"] = migration_dict["Theme"]
                log.info("No existing state.json found in `current`, but a ClientTimer1 config.txt was detected - loading and migrating state dict from previous user configuration.")
            else:
                log.info("No existing state.json found in `current`, loading fresh state dict.")
        else:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            defaulted_values = set()

            # Validate the meta dict
            if "meta" not in state or not isinstance(state["meta"], dict):
                defaulted_values.add("meta")
                state["meta"] = {}
            if "schema_version" not in state["meta"] or not isinstance(state["meta"]["schema_version"], int):
                defaulted_values.add("meta.schema_version")
                state["meta"]["schema_version"] = _SCHEMA_VERSION
            if "is_completed_session" not in state["meta"] or not isinstance(state["meta"]["is_completed_session"], bool):
                defaulted_values.add("meta.is_completed_session")
                state["meta"]["is_completed_session"] = False

            # Validate the layout dict, default to empty if its missing and treat as an error
            if "layout" not in state or not isinstance(state["layout"],dict):
                defaulted_values.add("layout")
                state["layout"] = {"rows": [], "collapsed_groups": []}
            # Validate rows and collapsed groups in layout dict.
            else:
                if "rows" not in state["layout"] or not isinstance(state["layout"]["rows"],list):
                    defaulted_values.add("layout.rows")
                    state["layout"]["rows"] = []
                if "collapsed_groups" not in state["layout"] or not isinstance(state["layout"]["collapsed_groups"],list):
                    defaulted_values.add("layout.collapsed_groups")
                    state["layout"]["collapsed_groups"] = []

            # Validate the settings dict, fill in any necessary defaults
            if "settings" not in state or not isinstance(state["settings"],dict):
                defaulted_values.add("settings")
                state["settings"] = dict(_SETTINGS_DEFAULTS)
            else:
                for key, default in _SETTINGS_DEFAULTS.items():
                    if key not in state["settings"]:
                        defaulted_values.add(f"settings.{key}")
                        state["settings"][key] = default

            # Validate the session dict
            if "session" not in state or not isinstance(state["session"],dict):
                defaulted_values.add("session")
                state["session"] = {"start": now_iso(), "tracked_times": {}}
            # Validate that the tracked_times dict exists within sessions
            else:
                if "tracked_times" not in state["session"] or not isinstance(state["session"]["tracked_times"],dict):
                    defaulted_values.add("session.tracked_times")
                    state["session"]["tracked_times"] = {}

            # Log results
            if defaulted_values:
                log.warning(f"Successfully loaded current state dict from '{STATE_PATH}', but with missing values that were defaulted: {", ".join(sorted(defaulted_values))}")
            else:
                log.info(f"Successfully loaded current state dict from '{STATE_PATH}'.")
        return state
    # Fall back to a fresh state dict in case of error, but warn in log
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        log.warning("Ran into an error while trying to load state.json, falling back to loading a fresh state dict.",exc_info=True)
        return build_default_state()
# Write the given state to disk under PATHS.current / state.json
def save_state(state):
    state["meta"]["saved_at"] = now_iso()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    log.info(f"Successfully saved state to '{STATE_PATH}'")

# Saves the given state dict as a completed session, marking it as fully completed, in the PATHS.sessions folder.
def save_completed_session(state, boundary_dt):
    completed = copy.deepcopy(state)
    completed["meta"]["is_completed_session"] = True
    completed["meta"]["saved_at"] = now_iso()
    completed["session"]["end"] = boundary_dt.isoformat()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    final_path = COMPLETED_DIR / f"session_{ts}.json"
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(completed, f, indent=2)
    log.info(f"Saved completed session to '{final_path}'")
    return str(final_path)

#endregion === Saving and Loading State ===