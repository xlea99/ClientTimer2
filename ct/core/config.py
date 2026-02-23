import copy
import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime

from ct.common.logger import log
from ct.common.setup import PATHS
from ct.util import now_iso, read_old_config


_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

STATE_PATH    = PATHS.current / "state.json"
SNAPSHOT_DIR  = PATHS.snapshots
COMPLETED_DIR = PATHS.sessions

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
    "daily_reset_enabled":  False,
    "daily_reset_time":     "00:00",
    "snapshot_min_minutes": 5,
    "button_visibility":    "All",
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
    daily_reset_enabled:  bool = False
    daily_reset_time:     str  = "00:00"
    snapshot_min_minutes: int  = 5
    button_visibility:    str  = "All"

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

    def __init__(
        self,
        settings: Settings,
        rows: list,
        collapsed_groups: set,
        session_start: datetime,
        tracked_times: dict,
    ):
        self.settings        = settings
        self.rows            = rows            # live list — mutated in place by MainWindow
        self.collapsed_groups = collapsed_groups  # live set
        self.session_start   = session_start
        self.tracked_times   = tracked_times  # used only during MainWindow.__init__

    @classmethod
    def load(cls) -> "AppState":
        """Load from disk and return a populated AppState."""
        raw      = load_state()
        settings = Settings.from_dict(raw.get("settings", {}))
        layout   = raw.get("layout", {})
        rows     = list(layout.get("rows", []))
        collapsed = set(layout.get("collapsed_groups", []))
        session  = raw.get("session", {})
        try:
            start = datetime.fromisoformat(session.get("start", now_iso()))
        except (ValueError, TypeError):
            start = datetime.now().astimezone()
        tracked = session.get("tracked_times", {})
        return cls(settings, rows, collapsed, start, tracked)

    def serialize(self, timers: dict) -> dict:
        """Build the full state dict from current live data."""
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

    def save(self, timers: dict) -> dict:
        """Serialize and write state to disk. Returns the state dict."""
        state = self.serialize(timers)
        save_state(state)
        return state


# ---------------------------------------------------------------------------
# Low-level load / save functions (also used by AppState internally)
# ---------------------------------------------------------------------------

def build_default_state() -> dict:
    """Construct a fresh empty state dict."""
    return {
        "meta": {
            "schema_version":       _SCHEMA_VERSION,
            "saved_at":             now_iso(),
            "is_completed_session": False,
        },
        "layout": {
            "rows":             [],
            "collapsed_groups": [],
        },
        "settings": dict(_SETTINGS_DEFAULTS),
        "session": {
            "start":         now_iso(),
            "tracked_times": {},
        },
    }


def load_state() -> dict:
    """Load unified state from state.json, migrating or defaulting as needed."""
    try:
        if not STATE_PATH.exists():
            state = build_default_state()
            if _OLD_CONFIG.exists():
                migration = read_old_config(_OLD_CONFIG)
                for i, timer in enumerate(migration["Timers"]):
                    state["layout"]["rows"].append({
                        "rowid": i, "name": timer, "type": "timer", "bg": None,
                    })
                state["settings"]["size"]  = migration["Size"]
                state["settings"]["theme"] = migration["Theme"]
                log.info("Migrated state from ClientTimer1 config.txt.")
            else:
                log.info("No existing state.json; loading fresh state.")
            save_state(state)
            return state

        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)

        defaulted = set()

        # meta
        if not isinstance(state.get("meta"), dict):
            state["meta"] = {}
            defaulted.add("meta")
        m = state["meta"]
        if not isinstance(m.get("schema_version"), int):
            m["schema_version"] = _SCHEMA_VERSION
            defaulted.add("meta.schema_version")
        if not isinstance(m.get("is_completed_session"), bool):
            m["is_completed_session"] = False
            defaulted.add("meta.is_completed_session")

        # layout
        if not isinstance(state.get("layout"), dict):
            state["layout"] = {"rows": [], "collapsed_groups": []}
            defaulted.add("layout")
        else:
            lay = state["layout"]
            if not isinstance(lay.get("rows"), list):
                lay["rows"] = []
                defaulted.add("layout.rows")
            if not isinstance(lay.get("collapsed_groups"), list):
                lay["collapsed_groups"] = []
                defaulted.add("layout.collapsed_groups")

        # settings — fill any missing keys
        if not isinstance(state.get("settings"), dict):
            state["settings"] = dict(_SETTINGS_DEFAULTS)
            defaulted.add("settings")
        else:
            for key, default in _SETTINGS_DEFAULTS.items():
                if key not in state["settings"]:
                    state["settings"][key] = default
                    defaulted.add(f"settings.{key}")

        # session
        if not isinstance(state.get("session"), dict):
            state["session"] = {"start": now_iso(), "tracked_times": {}}
            defaulted.add("session")
        else:
            if not isinstance(state["session"].get("tracked_times"), dict):
                state["session"]["tracked_times"] = {}
                defaulted.add("session.tracked_times")

        if defaulted:
            log.warning(
                f"Loaded '{STATE_PATH}' with missing values defaulted: "
                f"{', '.join(sorted(defaulted))}"
            )
        else:
            log.info(f"Loaded state from '{STATE_PATH}'.")
        return state

    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        log.warning("Error loading state.json; falling back to fresh state.", exc_info=True)
        return build_default_state()


def save_state(state: dict) -> None:
    """Write state dict to disk, refreshing saved_at timestamp."""
    state["meta"]["saved_at"] = now_iso()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    log.info(f"Saved state to '{STATE_PATH}'.")


def save_completed_session(state: dict, boundary_dt: datetime) -> str:
    """Archive a completed session to COMPLETED_DIR. Returns the file path."""
    import os
    os.makedirs(COMPLETED_DIR, exist_ok=True)
    completed = copy.deepcopy(state)
    completed["meta"]["is_completed_session"] = True
    completed["meta"]["saved_at"] = now_iso()
    completed["session"]["end"] = boundary_dt.isoformat()
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = COMPLETED_DIR / f"session_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(completed, f, indent=2)
    log.info(f"Saved completed session to '{path}'.")
    return str(path)
