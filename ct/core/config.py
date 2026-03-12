import copy
import dataclasses
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from ct.common.logger import log
from ct.common.setup import PATHS
from ct.util import now_iso, read_old_config


_SCHEMA_VERSION = 1
_STATE_PATH = PATHS.current / "state.json"
_OLD_CONFIG = PATHS.old / "config.txt"

# ---------------------------------------------------------------------------
# Settings defaults — single source of truth for all setting keys/values
# ---------------------------------------------------------------------------

_SETTINGS_DEFAULTS = {
    "theme":                "Cupertino Light",
    "size":                 "Regular",
    "font":                 "Calibri",
    "label_align":          "Left",
    "client_separators":    True,
    "show_group_count":     True,
    "show_group_time":      True,
    "always_on_top":        True,
    "confirm_delete":       True,
    "confirm_reset":        True,
    "daily_reset_enabled":  True,
    "daily_reset_time":     "03:00",
    "snapshot_min_minutes": 5,
    "button_visibility":    "All",
    "recover_running_time": True,
}


@dataclass
class Settings:
    """All user-configurable settings as a typed, dot-accessible object."""
    theme:                str  = "Cupertino Light"
    size:                 str  = "Regular"
    font:                 str  = "Calibri"
    label_align:          str  = "Left"
    client_separators:    bool = True
    show_group_count:     bool = True
    show_group_time:      bool = True
    always_on_top:        bool = True
    confirm_delete:       bool = True
    confirm_reset:        bool = True
    daily_reset_enabled:  bool = True
    daily_reset_time:     str  = "03:00"
    snapshot_min_minutes: int  = 5
    button_visibility:    str  = "All"
    recover_running_time: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(**{k: d.get(k, v) for k, v in _SETTINGS_DEFAULTS.items()})

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# AppState — runtime holder for the full session state
# ---------------------------------------------------------------------------

class AppState:
    """All runtime app state loaded from state.json.

    Owns settings (typed), layout rows (live list), collapsed groups (live
    set), session start, and the raw tracked_times needed to reconstruct
    TimerState objects in MainWindow.

    TimerState objects themselves live in MainWindow.timers — pass them to
    serialize() / save() when persisting.
    """

    # Helper to construct a truly fresh, default state.
    @staticmethod
    def _build_default_state() -> dict:
        return {
            "meta": {
                "schema_version": _SCHEMA_VERSION,
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


    def __init__(self, settings: Settings, rows: list, collapsed_groups: set,
                 session_start: datetime, tracked_times: dict):
        self.settings         = settings
        self.rows             = rows              # live list — mutated in place by MainWindow
        self.collapsed_groups = collapsed_groups  # live set — mutated in place by MainWindow
        self.session_start    = session_start
        self.tracked_times    = tracked_times     # used only during MainWindow.__init__
        self.migrated_from_ct1 = None             # set by load() if CT1 migration occurred

    # Helper to build the full state dict from current live data.
    def _serialize(self, timers: dict) -> dict:
        tracked = {}
        for rid, ts in timers.items():
            ts.freeze()
            entry = {"elapsed": ts.elapsed}
            if ts.running and ts.started_at:
                entry["running_since"] = ts.started_at.isoformat()
            tracked[str(rid)] = entry
        return {
            "meta": {
                "schema_version":      _SCHEMA_VERSION,
                "saved_at":            now_iso(),
                "is_completed_session": False,
            },
            "layout": {
                "rows":             list(self.rows),
                "collapsed_groups": list(self.collapsed_groups),
            },
            "settings": self.settings.to_dict(),
            "session": {
                "start":         self.session_start.isoformat(),
                "tracked_times": tracked,
            },
        }

    # Loads the current unified state from PATHS.current / state.json, ensuring the schema is valid and handling
    # default fallbacks.
    @classmethod
    def load(cls, path: Path = _STATE_PATH) -> "AppState":
        try:
            # If the save doesn't yet exist, we check if there's an old ClientTimer1 install to migrate from. If so,
            # it gets built using those clients/sizing. Otherwise, a full fresh default state is built.
            if not path.exists():
                try:
                    if path != _STATE_PATH:
                        raise FileNotFoundError
                except FileNotFoundError:
                    log.exception(f"Tried to load a specified a file other than the current state.json, but file was not found: {path}")
                    raise
                state = cls._build_default_state()
                if _OLD_CONFIG.exists():
                    migration = read_old_config(_OLD_CONFIG)
                    for i, timer in enumerate(migration["Timers"]):
                        state["layout"]["rows"].append({
                            "rowid": i, "name": timer, "type": "timer", "bg": None,
                        })
                    state["settings"]["size"]  = migration["Size"]
                    state["settings"]["theme"] = migration["Theme"]
                    state["_migrated_from_ct1"] = migration
                    log.info("Migrated state from ClientTimer1 config.txt.")
                else:
                    log.info("No existing state.json; loading fresh state.")
            else:
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                defaulted_values = set()

                # Validate the meta dict
                if not isinstance(state.get("meta"), dict):
                    state["meta"] = {}
                    defaulted_values.add("meta")
                if not isinstance(state["meta"].get("schema_version"), int):
                    state["meta"]["schema_version"] = _SCHEMA_VERSION
                    defaulted_values.add("meta.schema_version")
                if not isinstance(state["meta"].get("is_completed_session"), bool):
                    state["meta"]["is_completed_session"] = False
                    defaulted_values.add("meta.is_completed_session")

                # Validate the layout dict, default to empty if its missing and treat as an error
                if not isinstance(state.get("layout"), dict):
                    state["layout"] = {"rows": [], "collapsed_groups": []}
                    defaulted_values.add("layout")
                # Validate rows and collapsed groups in layout dict.
                else:
                    if not isinstance(state["layout"].get("rows"), list):
                        state["layout"]["rows"] = []
                        defaulted_values.add("layout.rows")
                    if not isinstance(state["layout"].get("collapsed_groups"), list):
                        state["layout"]["collapsed_groups"] = []
                        defaulted_values.add("layout.collapsed_groups")

                # Validate the settings dict, fill in any necessary defaults
                if not isinstance(state.get("settings"), dict):
                    state["settings"] = dict(_SETTINGS_DEFAULTS)
                    defaulted_values.add("settings")
                else:
                    for key, default in _SETTINGS_DEFAULTS.items():
                        if key not in state["settings"]:
                            state["settings"][key] = default
                            defaulted_values.add(f"settings.{key}")

                # Validate the session dict
                if not isinstance(state.get("session"), dict):
                    state["session"] = {"start": now_iso(), "tracked_times": {}}
                    defaulted_values.add("session")
                # Validate that the tracked_times dict exists within sessions
                else:
                    if not isinstance(state["session"].get("tracked_times"), dict):
                        state["session"]["tracked_times"] = {}
                        defaulted_values.add("session.tracked_times")

                # Log results
                if defaulted_values:
                    log.warning(
                        f"Loaded '{path}' with missing values defaulted: "
                        f"{', '.join(sorted(defaulted_values))}"
                    )
                else:
                    log.info(f"Loaded state from '{path}'.")
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            log.warning("Error loading state.json; falling back to fresh state.", exc_info=True)
            state = cls._build_default_state()

        # Hydrate the validated dict into typed fields
        settings  = Settings.from_dict(state["settings"])
        rows      = list(state["layout"]["rows"])
        collapsed = set(state["layout"]["collapsed_groups"])
        try:
            start = datetime.fromisoformat(state["session"].get("start", now_iso()))
        except (ValueError, TypeError):
            start = datetime.now().astimezone()
        tracked = state["session"]["tracked_times"]
        obj = cls(settings, rows, collapsed, start, tracked)
        obj.migrated_from_ct1 = state.get("_migrated_from_ct1")
        return obj
    # Serialize and write state to disk. Returns the state dict.
    def save(self, timers: dict) -> dict:
        state = self._serialize(timers)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        log.info(f"Saved state to '{_STATE_PATH}'.")
        return state







#region === Helpers and Paths ===


# Archives a state dict as a completed session to PATHS.sessions, and returns the file path.
def save_completed_session(state: dict, boundary_dt: datetime) -> str:
    completed = copy.deepcopy(state)
    completed["meta"]["is_completed_session"] = True
    completed["meta"]["saved_at"] = now_iso()
    completed["session"]["end"] = boundary_dt.isoformat()
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = PATHS.sessions / f"session_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(completed, f, indent=2)
    log.info(f"Saved completed session to '{path}'.")
    return str(path)


#endregion === Helpers and Paths ===



