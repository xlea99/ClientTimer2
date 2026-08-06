import copy
import dataclasses
import json
import os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from ct.common.logger import log
from ct.common.setup import PATHS
from ct.util import now_iso


_SCHEMA_VERSION = 1
_STATE_PATH = PATHS.current / "state.json"

# ---------------------------------------------------------------------------
# Settings — the dataclass is the single source of truth for keys/defaults
# ---------------------------------------------------------------------------

# Coerces a raw settings value to the type of its default, honoring reasonable
# hand-edits (0/1 or "true"/"false" for bools, numeric strings for ints)
# instead of discarding them. Values that can't be sensibly interpreted fall
# back to the default. Anything other than a clean pass is logged as a plain-
# English warning — users do read these logs. Type-checking only: string
# values are never validated against THEMES/SIZES/etc., so settings from
# other app versions survive a load/save round trip.
# Returns (value, changed) — changed is True for any coercion or reset.
def _coerce_setting(key, value, default):
    target = type(default)
    if target is bool:
        if isinstance(value, bool):
            return value, False
        if isinstance(value, int) and value in (0, 1):
            log.warning(f"Setting '{key}' was {value} — read as {bool(value)}.")
            return bool(value), True
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
            result = value.strip().lower() in ("true", "1")
            log.warning(f"Setting '{key}' was '{value}' (text) — read as {result}.")
            return result, True
    elif target is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value, False
        if isinstance(value, (str, float)):
            try:
                result = int(float(value))
                log.warning(f"Setting '{key}' was {value!r} — read as {result}.")
                return result, True
            except (ValueError, OverflowError):
                pass
    elif target is str:
        if isinstance(value, str):
            return value, False
    log.warning(f"Setting '{key}' was {value!r}, which couldn't be interpreted — reset to default {default!r}.")
    return default, True


@dataclass
class Settings:
    """All user-configurable settings as a typed, dot-accessible object."""
    theme:                str  = "E-Ink (Default)"
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
    show_adjust_buttons:  bool = True
    recover_running_time: bool = True
    # ISO timestamp of the last time the user was SHOWN an update prompt —
    # not the last check, and not per launch. Six restarts in a morning must
    # not mean six prompts, and an app left open for a week must still get
    # one. Written when the toast appears, whether or not they act on it.
    last_update_prompt:   str  = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        d = cls._migrate(d)
        values = {}
        coerced = []
        for k, default in _SETTINGS_DEFAULTS.items():
            if k in d:
                values[k], changed = _coerce_setting(k, d[k], default)
                if changed:
                    coerced.append(k)
            else:
                values[k] = default
        obj = cls(**values)
        # Plain attribute, not a field — never serialized by to_dict().
        # MainWindow reads this at startup to toast the user about it.
        obj.coerced_keys = coerced
        return obj

    @staticmethod
    def _migrate(d: dict) -> dict:
        """Rewrite settings saved by an older version into current keys."""
        if "button_visibility" not in d:
            return d
        # The X button used to be user-configurable ("All" / "Adjust Only" /
        # "None"); it now appears only in edit mode, so all that survives is
        # whether the +5/-5 pair is shown.
        d = dict(d)
        d.setdefault("show_adjust_buttons", d.pop("button_visibility") != "None")
        d.pop("button_visibility", None)
        return d

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# Derived from the dataclass so defaults live in exactly one place.
_SETTINGS_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Settings)}


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
                "window_height": 0,
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
        self.window_height    = 0                 # user's height ceiling; 0 = auto-fit

    # Helper to build the full state dict from current live data.
    def _serialize(self, timers: dict) -> dict:
        tracked = {}
        for rid, ts in timers.items():
            ts.freeze()
            entry = {"elapsed": ts.elapsed}
            if ts.running:
                # freeze() above makes elapsed current as of this save, so the
                # recovery baseline is the save moment — using started_at here
                # would double-count everything between start and last save.
                entry["running_since"] = now_iso()
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
                "window_height":    int(self.window_height),
            },
            "settings": self.settings.to_dict(),
            "session": {
                "start":         self.session_start.isoformat(),
                "tracked_times": tracked,
            },
        }

    # Loads the current unified state from PATHS.current / state.json, ensuring the schema is valid and handling
    # default fallbacks. Loading the default state.json never fails (falls back to a fresh state); loading an
    # explicit path (e.g. a snapshot restore) raises on a missing/unreadable file so callers can bail out safely.
    @classmethod
    def load(cls, path: Path = _STATE_PATH) -> "AppState":
        is_default_path = (path == _STATE_PATH)
        try:
            # No state file yet: build a fresh default.
            if not path.exists():
                if not is_default_path:
                    raise FileNotFoundError(f"State file not found: {path}")
                state = cls._build_default_state()
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
                    state["layout"] = {"rows": [], "collapsed_groups": [],
                                       "window_height": 0}
                    defaulted_values.add("layout")
                # Validate rows and collapsed groups in layout dict.
                else:
                    if not isinstance(state["layout"].get("rows"), list):
                        state["layout"]["rows"] = []
                        defaulted_values.add("layout.rows")
                    if not isinstance(state["layout"].get("collapsed_groups"), list):
                        state["layout"]["collapsed_groups"] = []
                        defaulted_values.add("layout.collapsed_groups")
                    # Absent on states written before window sizing existed.
                    wh = state["layout"].get("window_height", 0)
                    if not isinstance(wh, int) or isinstance(wh, bool) or wh < 0:
                        state["layout"]["window_height"] = 0
                        if "window_height" in state["layout"]:
                            defaulted_values.add("layout.window_height")

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
            if not is_default_path:
                log.exception(f"Failed to load state from '{path}'.")
                raise
            log.warning("Error loading state.json; falling back to fresh state.", exc_info=True)
            state = cls._build_default_state()

        # Hydrate the validated dict into typed fields
        settings  = Settings.from_dict(state["settings"])
        # No theme-rename map. A saved theme that no longer exists falls
        # back to the default at render time; the cost is one person
        # re-picking a theme, which is not worth carrying retired names in
        # the source forever.
        rows      = list(state["layout"]["rows"])
        collapsed = set(state["layout"]["collapsed_groups"])
        try:
            start = datetime.fromisoformat(state["session"].get("start", now_iso()))
        except (ValueError, TypeError):
            start = datetime.now().astimezone()
        tracked = state["session"]["tracked_times"]
        obj = cls(settings, rows, collapsed, start, tracked)
        obj.window_height = state["layout"].get("window_height", 0)
        return obj
    # Serialize and write state to disk. Returns the state dict.
    def save(self, timers: dict) -> dict:
        state = self._serialize(timers)
        # Write to a temp file and atomically replace, so a crash mid-write
        # can't corrupt state.json.
        tmp_path = _STATE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, _STATE_PATH)
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



