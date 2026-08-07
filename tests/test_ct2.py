"""
Comprehensive test suite for ClientTimer2.
Tests core logic, config/state persistence, theme data integrity,
snapshot system, timer state, and utility functions.

Run:  python -m pytest tests/test_ct2.py -v
  or: python tests/test_ct2.py
"""

import copy
import json
import os
import re
import shutil
import tempfile
import traceback
import time
import unittest

from ct.util import now_iso
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


# =========================================================================== #
#  Module-level state.json isolation                                            #
# =========================================================================== #
# The whole suite runs against a throwaway state.json, redirected here before
# a single test is collected. Per-test StatePathMixin still layers on top of
# this; the module-level patch is what makes a leak IMPOSSIBLE rather than
# merely detected.
#
# The previous version did the opposite: it let tests hit the real file and
# restored from a backup afterwards. That made the safety net itself the only
# thing that ever wrote to the user's live session — and because the check was
# a byte comparison run at teardown, an intermittent false positive meant it
# would overwrite their real state for no reason. A net that performs the
# damage it is guarding against is worse than no net.
#
# What remains is a READ-ONLY tripwire: if the real file's stat changed while
# the suite ran, something bypassed the redirect (a hardcoded path), and that
# is reported loudly and never "fixed" by writing.

_real_state_path = None
_suite_tmp = None
_saved_load_defaults = None
_leak_traces = []
_write_guards = []


def _install_write_guards():
    """Record any write THIS process aims at the real state.json.

    Attribution, not detection-by-mtime: the user's own app autosaves every
    20s, so comparing the file's stat across a run reports their running app
    as a test leak. (The previous net did exactly that, and then "restored" a
    stale backup over their live session, corrupting the thing it guarded.)
    """
    import os
    real = str(_real_state_path)

    def guard_method(cls, name):
        orig = getattr(cls, name)

        def inner(self, *a, **k):
            if str(self) == real:
                _leak_traces.append((name, "".join(traceback.format_stack(limit=8))))
            return orig(self, *a, **k)
        setattr(cls, name, inner)
        _write_guards.append((cls, name, orig))

    for name in ("write_text", "write_bytes", "unlink"):
        guard_method(Path, name)

    _orig_replace = os.replace

    def guarded_replace(src, dst, *a, **k):
        if str(dst) == real:
            _leak_traces.append(("os.replace",
                                 "".join(traceback.format_stack(limit=8))))
        return _orig_replace(src, dst, *a, **k)
    os.replace = guarded_replace
    _write_guards.append((os, "replace", _orig_replace))


def _remove_write_guards():
    for target, name, orig in _write_guards:
        setattr(target, name, orig)
    _write_guards.clear()


def setUpModule():
    global _real_state_path, _suite_tmp, _saved_load_defaults
    import ct.core.config as cfgmod
    _real_state_path = cfgmod._STATE_PATH

    _suite_tmp = Path(tempfile.mkdtemp(prefix="ct2-suite-"))
    tmp_state = _suite_tmp / "current" / "state.json"
    tmp_state.parent.mkdir(parents=True, exist_ok=True)
    cfgmod._STATE_PATH = tmp_state
    # save() reads the module global at call time, but load()'s default
    # argument was bound at def time — patch both or reads still go home.
    _saved_load_defaults = cfgmod.AppState.load.__func__.__defaults__
    cfgmod.AppState.load.__func__.__defaults__ = (tmp_state,)
    _install_write_guards()


def tearDownModule():
    import ct.core.config as cfgmod
    cfgmod._STATE_PATH = _real_state_path
    cfgmod.AppState.load.__func__.__defaults__ = _saved_load_defaults
    if _suite_tmp is not None:
        shutil.rmtree(_suite_tmp, ignore_errors=True)
    _remove_write_guards()
    # Only writes made by THIS process count. Comparing the file's mtime
    # instead would report the user's own running app — it autosaves every
    # 20s — as a test leak, which is what the previous net did before it
    # "restored" a stale backup over their live session.
    if _leak_traces:
        print(f"\n[WARNING] {len(_leak_traces)} write(s) from this process "
              f"reached the real state.json — something bypassed the "
              f"module-level redirect. NOT restoring; fix the caller:")
        print(_leak_traces[0][1])


# =========================================================================== #
#  Test utilities                                                               #
# =========================================================================== #

class TempDirMixin:
    """Mixin that creates a temp dir for each test and cleans up after."""
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ct2_test_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def tmp(self, name):
        return Path(self._tmpdir) / name


class StatePathMixin(TempDirMixin):
    """Point the real state.json at a temp file for the duration of a test.

    Several tests write through AppState.save() or hand-write a corrupt
    state.json. Without this they hit the user's live session and lean on the
    module-level backup above to put it back — which is no help at all if the
    run is killed part-way through.
    """

    def setUp(self):
        super().setUp()
        import ct.core.config as cfgmod
        self._real_state_path = cfgmod._STATE_PATH
        tmp_state = Path(self._tmpdir) / "current" / "state.json"
        tmp_state.parent.mkdir(parents=True, exist_ok=True)
        cfgmod._STATE_PATH = tmp_state
        # save() reads the module global at call time, but load()'s default
        # argument was bound at def time — patching one without the other
        # still leaves reads pointed at the user's real file.
        self._real_load_defaults = cfgmod.AppState.load.__func__.__defaults__
        cfgmod.AppState.load.__func__.__defaults__ = (tmp_state,)

    def tearDown(self):
        import ct.core.config as cfgmod
        cfgmod._STATE_PATH = self._real_state_path
        cfgmod.AppState.load.__func__.__defaults__ = self._real_load_defaults
        super().tearDown()


def _minimal_state(**overrides):
    """Build a minimal valid state.json dict."""
    from ct.util import now_iso
    state = {
        "meta": {
            "schema_version": 1,
            "saved_at": now_iso(),
            "is_completed_session": False,
        },
        "layout": {
            "rows": [],
            "collapsed_groups": [],
        },
        "settings": {
            "theme": "E-Ink (Default)",
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
            "show_adjust_buttons": True,
        },
        "session": {
            "start": now_iso(),
            "tracked_times": {},
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in state:
            state[k].update(v)
        else:
            state[k] = v
    return state


def _write_state(path, state_dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state_dict, f, indent=2)


# =========================================================================== #
#  1. TIMER STATE                                                               #
# =========================================================================== #

class TestTimerState(unittest.TestCase):
    """Tests for ct.core.timer_state.TimerState"""

    def _make(self, name="Test", elapsed=0.0, running_since=None):
        from ct.core.timer_state import TimerState
        return TimerState(name, elapsed, running_since)

    # --- Basic lifecycle ---

    def test_initial_state(self):
        ts = self._make()
        self.assertEqual(ts.name, "Test")
        self.assertAlmostEqual(ts.elapsed, 0.0)
        self.assertFalse(ts.running)
        self.assertIsNone(ts.started_at)
        self.assertIsNone(ts._mono)

    def test_start_sets_running(self):
        ts = self._make()
        ts.start()
        self.assertTrue(ts.running)
        self.assertIsNotNone(ts._mono)
        self.assertIsNotNone(ts.started_at)

    def test_start_is_idempotent(self):
        ts = self._make()
        ts.start()
        mono1 = ts._mono
        started1 = ts.started_at
        ts.start()  # second call should be no-op
        self.assertEqual(ts._mono, mono1)
        self.assertEqual(ts.started_at, started1)

    def test_stop_accumulates_elapsed(self):
        ts = self._make()
        ts.start()
        time.sleep(0.05)
        ts.stop()
        self.assertFalse(ts.running)
        self.assertGreater(ts.elapsed, 0.0)
        self.assertIsNone(ts._mono)
        self.assertIsNone(ts.started_at)

    def test_stop_when_not_running_is_noop(self):
        ts = self._make(elapsed=100.0)
        ts.stop()
        self.assertAlmostEqual(ts.elapsed, 100.0)

    def test_current_elapsed_while_running(self):
        ts = self._make(elapsed=10.0)
        ts.start()
        time.sleep(0.05)
        ce = ts.current_elapsed
        self.assertGreater(ce, 10.0)

    def test_current_elapsed_while_stopped(self):
        ts = self._make(elapsed=42.5)
        self.assertAlmostEqual(ts.current_elapsed, 42.5)

    # --- Reset ---

    def test_reset_zeros_everything(self):
        ts = self._make(elapsed=999.0)
        ts.start()
        ts.reset()
        self.assertFalse(ts.running)
        self.assertAlmostEqual(ts.elapsed, 0.0)
        self.assertIsNone(ts._mono)
        self.assertIsNone(ts.started_at)

    # --- Freeze ---

    def test_freeze_captures_running_time(self):
        ts = self._make()
        ts.start()
        time.sleep(0.05)
        ts.freeze()
        self.assertTrue(ts.running)  # still running
        self.assertGreater(ts.elapsed, 0.0)

    def test_freeze_when_stopped_is_noop(self):
        ts = self._make(elapsed=50.0)
        ts.freeze()
        self.assertAlmostEqual(ts.elapsed, 50.0)

    # --- Adjust ---

    def test_adjust_positive(self):
        ts = self._make(elapsed=100.0)
        ts.adjust(60)
        self.assertAlmostEqual(ts.elapsed, 160.0)

    def test_adjust_negative(self):
        ts = self._make(elapsed=100.0)
        ts.adjust(-60)
        self.assertAlmostEqual(ts.elapsed, 40.0)

    def test_adjust_clamps_to_zero(self):
        ts = self._make(elapsed=10.0)
        ts.adjust(-999)
        self.assertAlmostEqual(ts.elapsed, 0.0)

    def test_adjust_while_running(self):
        ts = self._make(elapsed=100.0)
        ts.start()
        time.sleep(0.05)
        ts.adjust(50)
        # Should be > 150 because freeze captures running time too
        self.assertGreater(ts.elapsed, 150.0)
        self.assertTrue(ts.running)

    # --- Restore from running_since ---

    def test_running_since_restores_running(self):
        iso = datetime.now().astimezone().isoformat()
        ts = self._make(elapsed=300.0, running_since=iso)
        self.assertTrue(ts.running)
        self.assertIsNotNone(ts._mono)
        self.assertAlmostEqual(ts.elapsed, 300.0, places=0)

    def test_running_since_none_starts_stopped(self):
        ts = self._make(elapsed=300.0, running_since=None)
        self.assertFalse(ts.running)

    # --- Edge cases ---

    def test_negative_elapsed_init_kept(self):
        # TimerState doesn't clamp on init, only on adjust
        ts = self._make(elapsed=-5.0)
        self.assertAlmostEqual(ts.elapsed, -5.0)

    def test_string_elapsed_converted_to_float(self):
        ts = self._make(elapsed="123")
        self.assertAlmostEqual(ts.elapsed, 123.0)
        self.assertIsInstance(ts.elapsed, float)


# =========================================================================== #
#  2. SETTINGS DATACLASS                                                        #
# =========================================================================== #

class TestSettings(unittest.TestCase):
    """Tests for ct.core.config.Settings"""

    def _cls(self):
        from ct.core.config import Settings
        return Settings

    def test_defaults(self):
        s = self._cls()()
        self.assertEqual(s.theme, "E-Ink (Default)")
        self.assertEqual(s.size, "Regular")
        self.assertEqual(s.font, "Calibri")
        self.assertTrue(s.always_on_top)
        self.assertTrue(s.daily_reset_enabled)
        self.assertTrue(s.show_adjust_buttons)

    def test_from_dict_full(self):
        d = {"theme": "Galaxy Dark", "size": "Compact", "font": "Arial",
             "label_align": "Center", "client_separators": False,
             "show_group_count": False, "show_group_time": False,
             "always_on_top": False, "confirm_delete": False,
             "confirm_reset": False, "daily_reset_enabled": True,
             "daily_reset_time": "17:00",
             "show_adjust_buttons": False}
        s = self._cls().from_dict(d)
        self.assertEqual(s.theme, "Galaxy Dark")
        self.assertEqual(s.size, "Compact")
        self.assertFalse(s.client_separators)
        self.assertTrue(s.daily_reset_enabled)
        self.assertEqual(s.daily_reset_time, "17:00")
        self.assertFalse(s.show_adjust_buttons)

    def test_from_dict_empty_uses_defaults(self):
        s = self._cls().from_dict({})
        self.assertEqual(s.theme, "E-Ink (Default)")
        self.assertEqual(s.size, "Regular")

    def test_legacy_button_visibility_migrates(self):
        """The old 3-way setting collapses onto show_adjust_buttons."""
        for legacy, expected in (("All", True), ("Adjust Only", True),
                                 ("None", False)):
            s = self._cls().from_dict({"button_visibility": legacy})
            self.assertIs(s.show_adjust_buttons, expected, legacy)
            # And the dead key never survives into the saved settings.
            self.assertNotIn("button_visibility", s.to_dict())

    def test_legacy_button_visibility_does_not_override_new_key(self):
        s = self._cls().from_dict(
            {"button_visibility": "None", "show_adjust_buttons": True})
        self.assertTrue(s.show_adjust_buttons)

    def test_migrate_does_not_mutate_caller_dict(self):
        d = {"button_visibility": "None"}
        self._cls().from_dict(d)
        self.assertEqual(d, {"button_visibility": "None"})

    def test_from_dict_partial(self):
        s = self._cls().from_dict({"theme": "A Way"})
        self.assertEqual(s.theme, "A Way")
        self.assertEqual(s.size, "Regular")  # default

    def test_from_dict_ignores_unknown_keys(self):
        s = self._cls().from_dict({"theme": "Galaxy Dark", "bogus_key": 42})
        self.assertEqual(s.theme, "Galaxy Dark")
        self.assertFalse(hasattr(s, "bogus_key"))

    def test_to_dict_roundtrip(self):
        s = self._cls()(theme="Telecomm Blues", size="Bulky")
        d = s.to_dict()
        s2 = self._cls().from_dict(d)
        self.assertEqual(s.theme, s2.theme)
        self.assertEqual(s.size, s2.size)
        self.assertEqual(s.font, s2.font)

    def test_to_dict_has_all_keys(self):
        from ct.core.config import _SETTINGS_DEFAULTS
        s = self._cls()()
        d = s.to_dict()
        for key in _SETTINGS_DEFAULTS:
            self.assertIn(key, d)

    def test_settings_field_count_matches_defaults(self):
        from ct.core.config import _SETTINGS_DEFAULTS
        import dataclasses
        fields = dataclasses.fields(self._cls())
        self.assertEqual(len(fields), len(_SETTINGS_DEFAULTS))


# =========================================================================== #
#  3. APP STATE — load / save / serialize                                       #
# =========================================================================== #

class TestAppStateLoad(StatePathMixin, unittest.TestCase):
    """Tests for AppState.load() with various state.json scenarios."""

    def _load(self, path):
        from ct.core.config import AppState
        return AppState.load(path)

    # --- Fresh state (no file) ---

    def test_load_nonexistent_explicit_path_raises(self):
        # AppState.load() with an explicit non-default path that doesn't exist
        # must raise, so a snapshot restore can't silently wipe current state.
        fake = self.tmp("nope.json")
        with self.assertRaises(FileNotFoundError):
            self._load(fake)

    # --- Valid state file ---

    def test_load_valid_state(self):
        path = self.tmp("state.json")
        rows = [{"rowid": 0, "name": "Client A", "type": "timer", "bg": None}]
        state = _minimal_state()
        state["layout"]["rows"] = rows
        state["session"]["tracked_times"] = {"0": {"elapsed": 123.0}}
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(len(app.rows), 1)
        self.assertEqual(app.rows[0]["name"], "Client A")
        self.assertEqual(app.tracked_times["0"]["elapsed"], 123.0)
        self.assertEqual(app.settings.theme, "E-Ink (Default)")

    def test_load_preserves_collapsed_groups(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        state["layout"]["collapsed_groups"] = [0, 3, 7]
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.collapsed_groups, {0, 3, 7})

    def test_load_preserves_session_start(self):
        path = self.tmp("state.json")
        start = "2025-06-15T14:30:00-05:00"
        state = _minimal_state()
        state["session"]["start"] = start
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.session_start.year, 2025)
        self.assertEqual(app.session_start.month, 6)

    # --- Corrupted / partial state files ---

    def test_load_invalid_json_explicit_path_raises(self):
        # Same contract as a missing file: corrupt snapshots must not fall
        # back to defaults when loaded via an explicit path.
        path = self.tmp("state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT JSON!!!", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            self._load(path)

    def test_load_invalid_json_default_path_falls_back(self):
        # The default state.json keeps the lenient behavior: corrupt file
        # means fresh state, never a startup crash.
        from ct.core.config import AppState, _STATE_PATH
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text("NOT JSON!!!", encoding="utf-8")

        app = AppState.load()
        self.assertEqual(app.settings.theme, "E-Ink (Default)")
        self.assertEqual(len(app.rows), 0)

    def test_load_empty_json_object_falls_back(self):
        path = self.tmp("state.json")
        _write_state(path, {})

        app = self._load(path)
        self.assertEqual(app.settings.theme, "E-Ink (Default)")
        self.assertEqual(len(app.rows), 0)

    def test_load_missing_meta_gets_defaulted(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        del state["meta"]
        _write_state(path, state)

        app = self._load(path)
        self.assertIsNotNone(app.settings)

    def test_load_missing_layout_gets_defaulted(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        del state["layout"]
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(len(app.rows), 0)

    def test_load_missing_settings_gets_defaulted(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        del state["settings"]
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.settings.theme, "E-Ink (Default)")

    def test_load_missing_session_gets_defaulted(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        del state["session"]
        _write_state(path, state)

        app = self._load(path)
        self.assertIsNotNone(app.session_start)
        self.assertEqual(app.tracked_times, {})

    def test_load_partial_settings_fills_defaults(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        state["settings"] = {"theme": "Galaxy Dark"}  # only one key
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.settings.theme, "Galaxy Dark")
        self.assertEqual(app.settings.size, "Regular")  # default filled

    def test_load_wrong_types_get_defaulted(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        state["meta"] = "garbage"
        state["layout"] = 42
        state["settings"] = [1, 2, 3]
        state["session"] = True
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.settings.theme, "E-Ink (Default)")
        self.assertEqual(len(app.rows), 0)

    def test_load_invalid_session_start_uses_now(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        state["session"]["start"] = "not-a-date"
        _write_state(path, state)

        before = datetime.now().astimezone()
        app = self._load(path)
        after = datetime.now().astimezone()
        self.assertGreaterEqual(app.session_start, before - timedelta(seconds=1))
        self.assertLessEqual(app.session_start, after + timedelta(seconds=1))

    def test_load_rows_with_invalid_tracked_times(self):
        path = self.tmp("state.json")
        state = _minimal_state()
        state["session"]["tracked_times"] = "not a dict"
        _write_state(path, state)

        app = self._load(path)
        self.assertEqual(app.tracked_times, {})


class TestAppStateSave(StatePathMixin, unittest.TestCase):
    """Tests for AppState.save() and _serialize()."""

    def test_serialize_stopped_timer(self):
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState

        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        ts = TimerState("A", elapsed=300.0)
        result = state._serialize({0: ts})

        self.assertIn("0", result["session"]["tracked_times"])
        self.assertAlmostEqual(result["session"]["tracked_times"]["0"]["elapsed"], 300.0)
        self.assertNotIn("running_since", result["session"]["tracked_times"]["0"])

    def test_serialize_running_timer(self):
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState

        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        ts = TimerState("B", elapsed=100.0)
        ts.start()
        result = state._serialize({0: ts})

        entry = result["session"]["tracked_times"]["0"]
        self.assertIn("running_since", entry)
        self.assertGreaterEqual(entry["elapsed"], 100.0)

    def test_serialize_running_since_is_save_moment_not_start(self):
        # Regression: serialize freezes elapsed up to the save moment, so
        # running_since must be the save moment. Recording the original
        # started_at made startup recovery double-count the span between
        # start and last save.
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState

        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        one_hour_ago = (datetime.now().astimezone() - timedelta(hours=1))
        ts = TimerState("C", elapsed=600.0, running_since=one_hour_ago.isoformat())
        result = state._serialize({0: ts})

        entry = result["session"]["tracked_times"]["0"]
        running_since = datetime.fromisoformat(entry["running_since"])
        gap = abs((datetime.now().astimezone() - running_since).total_seconds())
        self.assertLess(gap, 5.0, "running_since should be ~now (the save moment)")

    def test_serialize_preserves_rows(self):
        from ct.core.config import AppState, Settings
        rows = [{"rowid": 0, "name": "X", "type": "timer", "bg": None}]
        state = AppState(Settings(), rows, set(), datetime.now().astimezone(), {})
        result = state._serialize({})
        self.assertEqual(result["layout"]["rows"], rows)

    def test_serialize_preserves_collapsed(self):
        from ct.core.config import AppState, Settings
        state = AppState(Settings(), [], {1, 5}, datetime.now().astimezone(), {})
        result = state._serialize({})
        self.assertEqual(set(result["layout"]["collapsed_groups"]), {1, 5})

    def test_serialize_preserves_settings(self):
        from ct.core.config import AppState, Settings
        s = Settings(theme="Galaxy Dark", size="Compact")
        state = AppState(s, [], set(), datetime.now().astimezone(), {})
        result = state._serialize({})
        self.assertEqual(result["settings"]["theme"], "Galaxy Dark")
        self.assertEqual(result["settings"]["size"], "Compact")

    def test_serialize_meta_fields(self):
        from ct.core.config import AppState, Settings
        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        result = state._serialize({})
        self.assertEqual(result["meta"]["schema_version"], 1)
        self.assertFalse(result["meta"]["is_completed_session"])
        self.assertIn("saved_at", result["meta"])

    def test_save_writes_json_to_disk(self):
        from ct.core.config import AppState, Settings, _STATE_PATH

        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        result = state.save({})

        self.assertTrue(_STATE_PATH.exists())
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["meta"]["schema_version"], 1)

    def test_save_roundtrip(self):
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState

        rows = [
            {"rowid": 0, "name": "Sep", "type": "separator", "bg": None},
            {"rowid": 1, "name": "Alpha", "type": "timer", "bg": "#FF0000"},
            {"rowid": 2, "name": "Beta", "type": "timer", "bg": None},
        ]
        s = Settings(theme="Telecomm Blues", size="Bulky", daily_reset_enabled=True)
        start = datetime.now().astimezone()
        state = AppState(s, rows, {0}, start, {})

        timers = {
            1: TimerState("Alpha", elapsed=600.0),
            2: TimerState("Beta", elapsed=0.0),
        }
        timers[1].start()

        saved = state.save(timers)

        # Reload from disk
        from ct.core.config import _STATE_PATH
        loaded = AppState.load(_STATE_PATH)
        self.assertEqual(len(loaded.rows), 3)
        self.assertEqual(loaded.settings.theme, "Telecomm Blues")
        self.assertEqual(loaded.collapsed_groups, {0})
        self.assertIn("1", loaded.tracked_times)
        self.assertIn("running_since", loaded.tracked_times["1"])


# =========================================================================== #
#  4. COMPLETED SESSIONS                                                        #
# =========================================================================== #

class TestCompletedSession(TempDirMixin, unittest.TestCase):
    """Tests for save_completed_session()."""

    def setUp(self):
        super().setUp()
        self._created_sessions = []

    def tearDown(self):
        for p in self._created_sessions:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        super().tearDown()

    def test_saves_session_file(self):
        from ct.core.config import save_completed_session
        state = _minimal_state()
        state["session"]["tracked_times"] = {"0": {"elapsed": 500.0}}
        boundary = datetime.now().astimezone()

        path_str = save_completed_session(state, boundary)
        self._created_sessions.append(path_str)
        path = Path(path_str)
        self.assertTrue(path.exists())

        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertTrue(saved["meta"]["is_completed_session"])
        self.assertIn("end", saved["session"])

    def test_does_not_mutate_original(self):
        from ct.core.config import save_completed_session
        state = _minimal_state()
        original_meta = copy.deepcopy(state["meta"])
        path_str = save_completed_session(state, datetime.now().astimezone())
        self._created_sessions.append(path_str)
        self.assertEqual(state["meta"], original_meta)


# =========================================================================== #
#  5. SNAPSHOT SYSTEM                                                           #
# =========================================================================== #

class SnapshotDirMixin(TempDirMixin):
    """Point PATHS.snapshots at a temp dir for the duration of a test.

    Without this, these tests write into — and then delete the entire
    contents of — the user's real snapshots folder, destroying their backup
    history every time the suite runs.
    """

    def setUp(self):
        super().setUp()
        from ct.common.setup import PATHS
        self._real_snapshots = PATHS.snapshots
        PATHS.snapshots = Path(self._tmpdir) / "snapshots"
        PATHS.snapshots.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        from ct.common.setup import PATHS
        PATHS.snapshots = self._real_snapshots
        super().tearDown()

    def _snap(self, seconds_ago, priority="normal", corrupt=False):
        """Write a fake snapshot dated `seconds_ago` in the past."""
        from ct.common.setup import PATHS
        t = datetime.now() - timedelta(seconds=seconds_ago)
        p = PATHS.snapshots / f"state_{t.strftime('%Y%m%d_%H%M%S_%f')}.json"
        if corrupt:
            p.write_text("{ not json", encoding="utf-8")
        else:
            state = _minimal_state()
            state["meta"]["snapshot_priority"] = priority
            _write_state(p, state)
        return p

    def _alive(self, paths):
        return [p for p in paths if p.exists()]


class TestSnapshot(SnapshotDirMixin, unittest.TestCase):
    """Tests for ct.core.snapshot create/prune."""

    def setUp(self):
        super().setUp()
        self._created_snapshots = []

    def test_create_snapshot_writes_file(self):
        from ct.core.snapshot import create_snapshot
        state = _minimal_state()
        path = create_snapshot(state, "test")
        self._created_snapshots.append(path)
        self.assertTrue(Path(path).exists())

        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual(snap["meta"]["snapshot_reason"], "test")
        self.assertEqual(snap["meta"]["snapshot_priority"], "normal")

    def test_create_snapshot_custom_priority(self):
        from ct.core.snapshot import create_snapshot
        state = _minimal_state()
        path = create_snapshot(state, "important", priority="high")
        self._created_snapshots.append(path)

        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual(snap["meta"]["snapshot_priority"], "high")

    def test_create_snapshot_does_not_mutate_original(self):
        from ct.core.snapshot import create_snapshot
        state = _minimal_state()
        original = copy.deepcopy(state)
        path = create_snapshot(state, "test")
        self._created_snapshots.append(path)
        # The original should not have snapshot_reason added
        self.assertNotIn("snapshot_reason", state["meta"])


class TestSnapshotParsing(unittest.TestCase):
    """Tests for _parse_snapshot_time."""

    def test_valid_filename(self):
        from ct.core.snapshot import _parse_snapshot_time
        dt = _parse_snapshot_time("state_20260115_143022_123456.json")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.hour, 14)

    def test_invalid_filename_returns_none(self):
        from ct.core.snapshot import _parse_snapshot_time
        self.assertIsNone(_parse_snapshot_time("garbage.json"))
        self.assertIsNone(_parse_snapshot_time("state_.json"))

    def test_no_underscore_returns_none(self):
        from ct.core.snapshot import _parse_snapshot_time
        self.assertIsNone(_parse_snapshot_time("state.json"))


class TestSnapshotPrune(SnapshotDirMixin, unittest.TestCase):
    """Tests for prune_snapshots()."""

    def _create_fake_snapshots(self, count, base_time=None):
        """Create count fake snapshot files with timestamps minutes apart."""
        return [self._snap(i * 120) for i in range(count)]

    def test_prune_keeps_newest(self):
        from ct.core.snapshot import prune_snapshots
        paths = self._create_fake_snapshots(20)
        prune_snapshots()
        # Newest should always survive
        self.assertTrue(paths[0].exists())

    def test_prune_with_zero_snapshots_is_noop(self):
        from ct.core.snapshot import prune_snapshots
        prune_snapshots()  # should not raise

    def test_prune_with_one_snapshot_is_noop(self):
        from ct.core.snapshot import prune_snapshots
        self._create_fake_snapshots(1)
        prune_snapshots()  # should not raise

    def test_burst_of_edits_all_survive(self):
        """The whole point: rapid destructive edits stay individually
        recoverable. The tier ladder alone kept only the first and last."""
        from ct.core.snapshot import prune_snapshots
        paths = [self._snap(90 - i * 10) for i in range(10)]
        prune_snapshots()
        self.assertEqual(len(self._alive(paths)), 10)

    def test_recent_buffer_is_bounded(self):
        """Being forgiving must not mean unbounded."""
        from ct.core.snapshot import prune_snapshots, RECENT_KEEP, TIERS
        paths = [self._snap(600 - i * 15) for i in range(40)]
        prune_snapshots()
        alive = self._alive(paths)
        self.assertGreaterEqual(len(alive), RECENT_KEEP)
        self.assertLessEqual(len(alive), RECENT_KEEP + len(TIERS) + 1)

    def test_high_priority_outlives_the_ladder(self):
        from ct.core.snapshot import prune_snapshots
        old_high = self._snap(3 * 86400, priority="high")
        for i in range(1, 40):
            self._snap(i * 60)
        self._snap(0)
        prune_snapshots()
        self.assertTrue(old_high.exists(),
                        "a 3-day-old high-priority snapshot was pruned")

    def test_high_priority_expires_eventually(self):
        from ct.core.snapshot import (prune_snapshots, HIGH_PRIORITY_KEEP_SECS,
                                      RECENT_KEEP, TIERS)
        stale = self._snap(HIGH_PRIORITY_KEEP_SECS + 86400, priority="high")
        # Enough newer snapshots to push it out of the recent buffer...
        for i in range(RECENT_KEEP + 5):
            self._snap(i * 10)
        # ...and a closer match for every tier so the ladder doesn't claim it.
        for secs in TIERS:
            self._snap(secs)
        prune_snapshots()
        self.assertFalse(stale.exists(),
                         "high-priority retention has no upper bound")

    def test_hard_ceiling_holds(self):
        from ct.core.snapshot import prune_snapshots, MAX_SNAPSHOTS
        paths = [self._snap(i * 30, priority="high") for i in range(300)]
        prune_snapshots()
        alive = self._alive(paths)
        self.assertLessEqual(len(alive), MAX_SNAPSHOTS)
        self.assertTrue(paths[0].exists(), "newest must always survive")

    def test_ceiling_drops_routine_before_high_priority(self):
        from ct.core.snapshot import prune_snapshots, MAX_SNAPSHOTS
        highs = [self._snap(i * 30 + 15, priority="high") for i in range(1, 40)]
        routine = [self._snap(i * 30) for i in range(1, 200)]
        prune_snapshots()
        self.assertLessEqual(len(self._alive(highs + routine)), MAX_SNAPSHOTS)
        self.assertEqual(len(self._alive(highs)), len(highs),
                         "high-priority was dropped before routine snapshots")

    def test_long_tail_still_kept(self):
        from ct.core.snapshot import prune_snapshots
        old = self._snap(4 * 86400)
        for secs in (0, 60, 300, 900, 3600, 6 * 3600, 24 * 3600):
            self._snap(secs)
        prune_snapshots()
        self.assertTrue(old.exists(), "lost the multi-day tail")

    def test_corrupt_snapshot_cannot_pin_itself(self):
        """An unreadable file must not read as high-priority and stick."""
        from ct.core.snapshot import prune_snapshots, RECENT_KEEP, TIERS
        paths = [self._snap((60 - i) * 60, corrupt=True) for i in range(60)]
        prune_snapshots()   # must not raise
        self.assertLessEqual(len(self._alive(paths)),
                             RECENT_KEEP + len(TIERS) + 1)

    def test_prune_leaves_unrelated_files_alone(self):
        from ct.common.setup import PATHS
        from ct.core.snapshot import prune_snapshots
        keeper = PATHS.snapshots / "notes.txt"
        keeper.write_text("do not delete me", encoding="utf-8")
        self._create_fake_snapshots(40)
        prune_snapshots()
        self.assertTrue(keeper.exists())


# =========================================================================== #
#  5b. UNDO STACK                                                               #
# =========================================================================== #

class _FakeState:
    """Just the parts of AppState the undo commands touch."""

    def __init__(self, rows, collapsed=None):
        self.rows = rows
        self.collapsed_groups = set(collapsed or ())


class UndoTestBase(unittest.TestCase):

    def setUp(self):
        from ct.core.timer_state import TimerState
        self.rows = [
            {"rowid": 10, "name": "Group", "type": "separator", "bg": None},
            {"rowid": 11, "name": "Alpha", "type": "timer", "bg": None},
            {"rowid": 12, "name": "Bravo", "type": "timer", "bg": None},
        ]
        self.state = _FakeState(self.rows)
        self.timers = {11: TimerState("Alpha", elapsed=3600),
                       12: TimerState("Bravo", elapsed=1800)}

    def order(self):
        return [r["rowid"] for r in self.state.rows]


class TestUndoStack(unittest.TestCase):

    def test_push_pop_peek(self):
        from ct.core.undo import UndoStack, RenameRow
        s = UndoStack()
        self.assertIsNone(s.pop())
        self.assertIsNone(s.peek())
        a, b = RenameRow("a", 1, "x"), RenameRow("b", 2, "y")
        s.push(a)
        s.push(b)
        self.assertEqual(len(s), 2)
        self.assertIs(s.peek(), b)      # peek does not consume
        self.assertEqual(len(s), 2)
        self.assertIs(s.pop(), b)
        self.assertIs(s.pop(), a)
        self.assertEqual(len(s), 0)

    def test_depth_is_bounded_and_drops_oldest(self):
        from ct.core.undo import UndoStack, RenameRow
        s = UndoStack(depth=3)
        for i in range(10):
            s.push(RenameRow(f"cmd{i}", i, str(i)))
        self.assertEqual(len(s), 3)
        self.assertEqual([c.rowid for c in s._items], [7, 8, 9])

    def test_clear(self):
        from ct.core.undo import UndoStack, RenameRow
        s = UndoStack()
        s.push(RenameRow("a", 1, "x"))
        s.clear()
        self.assertEqual(len(s), 0)
        self.assertIsNone(s.peek())


class TestUndoDeleteRow(UndoTestBase):

    def test_timer_comes_back_with_its_time_and_position(self):
        from ct.core.undo import DeleteRow
        cmd = DeleteRow("x", row=dict(self.rows[1]), index=1, elapsed=3600)
        self.state.rows = [r for r in self.state.rows if r["rowid"] != 11]
        del self.timers[11]
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.order(), [10, 11, 12])
        self.assertEqual(self.timers[11].current_elapsed, 3600)
        self.assertFalse(self.timers[11].running)

    def test_restored_row_is_a_copy(self):
        """Undoing twice must not alias the same dict into the layout."""
        from ct.core.undo import DeleteRow
        original = dict(self.rows[1])
        cmd = DeleteRow("x", row=original, index=1, elapsed=0)
        self.state.rows = []
        cmd.undo(self.state, self.timers)
        self.state.rows[0]["name"] = "MUTATED"
        self.assertEqual(original["name"], "Alpha")

    def test_collapsed_group_returns_collapsed(self):
        from ct.core.undo import DeleteRow
        cmd = DeleteRow("x", row=dict(self.rows[0]), index=0,
                        was_collapsed=True)
        self.state.rows = [r for r in self.state.rows if r["rowid"] != 10]
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.order(), [10, 11, 12])
        self.assertIn(10, self.state.collapsed_groups)

    def test_index_past_the_end_is_clamped(self):
        from ct.core.undo import DeleteRow
        cmd = DeleteRow("x", row=dict(self.rows[1]), index=99, elapsed=0)
        self.state.rows = []
        cmd.undo(self.state, self.timers)     # must not raise
        self.assertEqual(self.order(), [11])


class TestUndoResetTimes(UndoTestBase):

    def _reset(self, rids):
        from ct.core.undo import ResetTimes
        cmd = ResetTimes("x", {r: self.timers[r].current_elapsed
                               for r in rids})
        for r in rids:
            self.timers[r].reset()
        return cmd

    def test_no_accrual_means_no_conflict_and_a_clean_undo(self):
        cmd = self._reset([11, 12])
        self.assertEqual(cmd.conflicts(self.timers), [])
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.timers[11].current_elapsed, 3600)
        self.assertEqual(self.timers[12].current_elapsed, 1800)

    def test_accrual_is_reported_as_a_conflict(self):
        cmd = self._reset([11, 12])
        self.timers[11].elapsed = 300          # ran for 5 min since
        names = [n for n, _ in cmd.conflicts(self.timers)]
        self.assertEqual(names, ["Alpha"])
        self.assertEqual(cmd.conflicts(self.timers)[0][1], 300)

    def test_revert_discards_time_accrued_since(self):
        cmd = self._reset([11])
        self.timers[11].elapsed = 300
        cmd.undo(self.state, self.timers, mode="revert")
        self.assertEqual(self.timers[11].current_elapsed, 3600)

    def test_add_keeps_time_accrued_since(self):
        cmd = self._reset([11])
        self.timers[11].elapsed = 300
        cmd.undo(self.state, self.timers, mode="add")
        self.assertEqual(self.timers[11].current_elapsed, 3900)

    def test_a_running_timer_is_not_double_counted(self):
        """Writing elapsed while running would add the live segment twice."""
        cmd = self._reset([11])
        self.timers[11].start()
        self.timers[11].elapsed = 300          # pretend 5 min already banked
        cmd.undo(self.state, self.timers, mode="add")
        self.assertTrue(self.timers[11].running)
        # 3600 restored + 300 accrued, plus at most a hair of live time.
        self.assertAlmostEqual(self.timers[11].current_elapsed, 3900, delta=2)

    def test_a_running_timer_stays_running(self):
        cmd = self._reset([11])
        self.timers[11].start()
        cmd.undo(self.state, self.timers, mode="revert")
        self.assertTrue(self.timers[11].running)

    def test_a_timer_deleted_after_the_reset_is_skipped(self):
        cmd = self._reset([11, 12])
        del self.timers[11]
        cmd.undo(self.state, self.timers)      # must not raise
        self.assertEqual(self.timers[12].current_elapsed, 1800)

    def test_conflicts_ignores_a_deleted_timer(self):
        cmd = self._reset([11])
        del self.timers[11]
        self.assertEqual(cmd.conflicts(self.timers), [])


class TestUndoReorderAndRename(UndoTestBase):

    def test_reorder_restores_the_order(self):
        from ct.core.undo import ReorderRows
        cmd = ReorderRows("x", [dict(r) for r in self.state.rows], set())
        self.state.rows = [self.rows[2], self.rows[0], self.rows[1]]
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.order(), [10, 11, 12])

    def test_reorder_leaves_times_alone(self):
        from ct.core.undo import ReorderRows
        cmd = ReorderRows("x", [dict(r) for r in self.state.rows], set())
        self.timers[11].elapsed = 9999
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.timers[11].current_elapsed, 9999)

    def test_reorder_restores_collapsed_groups(self):
        from ct.core.undo import ReorderRows
        cmd = ReorderRows("x", [dict(r) for r in self.state.rows], {10})
        self.state.collapsed_groups = set()
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.state.collapsed_groups, {10})

    def test_reorder_mutates_the_list_in_place(self):
        """Callers hold a reference to state.rows; rebinding would strand it."""
        from ct.core.undo import ReorderRows
        held = self.state.rows
        cmd = ReorderRows("x", [dict(r) for r in self.state.rows], set())
        self.state.rows.reverse()
        cmd.undo(self.state, self.timers)
        self.assertIs(self.state.rows, held)
        self.assertEqual([r["rowid"] for r in held], [10, 11, 12])

    def test_rename_restores_row_and_timer_name(self):
        from ct.core.undo import RenameRow
        cmd = RenameRow("x", 11, "Alpha")
        self.rows[1]["name"] = "Renamed"
        self.timers[11].name = "Renamed"
        cmd.undo(self.state, self.timers)
        self.assertEqual(self.rows[1]["name"], "Alpha")
        self.assertEqual(self.timers[11].name, "Alpha")

    def test_rename_of_a_missing_row_is_a_noop(self):
        from ct.core.undo import RenameRow
        RenameRow("x", 999, "Nope").undo(self.state, self.timers)


# =========================================================================== #
#  5c. LIVE UI (Qt)                                                             #
# =========================================================================== #

_QT_APP = None


def _qt_app():
    """One QApplication for the whole run. None if Qt can't start."""
    global _QT_APP
    if _QT_APP is None:
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            return None
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


class QtWindowTestBase(StatePathMixin, SnapshotDirMixin, unittest.TestCase):
    """Builds a real MainWindow against throwaway state.

    Both state.json and the snapshots directory are redirected first. A
    MainWindow reads and writes real user data otherwise, and the suite has
    already destroyed a user's backups once by not doing this.
    """

    ROWS = [
        {"rowid": 10, "name": "Group", "type": "separator", "bg": None},
        {"rowid": 11, "name": "Alpha", "type": "timer", "bg": None},
        {"rowid": 12, "name": "Bravo", "type": "timer", "bg": None},
        {"rowid": 13, "name": "Charlie", "type": "timer", "bg": None},
    ]

    def setUp(self):
        app = _qt_app()
        if app is None:
            self.skipTest("PySide6 unavailable")
        # Redirects both state.json and the snapshots dir before anything
        # constructs a MainWindow, which reads and writes both.
        super().setUp()
        self.app = app

        from ct.core.timer_state import TimerState
        from ct.ui.app import MainWindow
        self._real_checks = MainWindow._startup_checks
        MainWindow._startup_checks = lambda s: setattr(s, "_pending_toast", None)

        self.win = MainWindow()
        self.toasts = []
        # **kw so it survives show_toast growing parameters; tests that
        # need the real widget delete this stub in their own setUp.
        self.win.show_toast = lambda m, s=5, **kw: self.toasts.append(m)
        self.win._try_snapshot = lambda *a, **k: None
        self.win._state.rows = [dict(r) for r in self.ROWS]
        self.win.timers = {r["rowid"]: TimerState(r["name"])
                           for r in self.ROWS if r["type"] == "timer"}
        self.win._state.collapsed_groups = set()
        self.win._state.settings.confirm_delete = False
        self.win._state.settings.confirm_reset = False
        self.win._state.window_height = 0
        self.win.show()
        self.rebuild()

    def tearDown(self):
        from ct.ui.app import MainWindow
        MainWindow._startup_checks = self._real_checks
        # Kill anything still pending before the state path is put back: a
        # settle timer that fires afterwards saves to the user's REAL
        # state.json, which the module-level detector then has to undo.
        for timer in ("_resize_settle", "_toast_timer", "_timer", "_hover_poll"):
            t = getattr(self.win, timer, None)
            if t is not None:
                t.stop()
        self.win.close()
        self.win.deleteLater()
        self.settle()
        super().tearDown()

    def settle(self, n=5):
        """Drain the event loop the way a running app would.

        processEvents() alone does NOT deliver DeferredDelete, so widgets
        that _rebuild_rows discarded stay alive for the whole test and any
        use-after-free is invisible here while crashing in the real app.
        Flush those explicitly.
        """
        from PySide6.QtCore import QCoreApplication, QEvent
        for _ in range(n):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def rebuild(self):
        self.win._rebuild_rows()
        self.win._shrink_to_fit()
        self.settle()

    def footer(self):
        return self.win._cfg_btn.parentWidget()

    def row_heights(self):
        grid = self.win._grid
        return [grid.itemAt(i).widget().height()
                for i in range(grid.count())
                if grid.itemAt(i).widget() is not None]

    def order(self):
        return [r["rowid"] for r in self.win._state.rows]


class TestQtRowHover(QtWindowTestBase):

    def rows_with_gap(self):
        """Rowids that have a layout gap above them (i.e. not the first)."""
        return [r["rowid"] for r in self.win._state.rows][1:]

    def test_hover_tints_the_row_and_clears_on_leave(self):
        rid = self.rows_with_gap()[0]
        rc = self.win._widgets[rid]["container"]
        self.win._on_row_hover(rid, True)
        self.assertEqual(rc.property("hov"), "1")
        self.win._on_row_hover(rid, False)
        self.assertNotEqual(rc.property("hov"), "1")

    def assertStripCovers(self, strip, rc, gap):
        """The gap fill starts one gap up and leaves no seam at the row.

        The bottom edge overlaps the row's top by a pixel ONLY when the row
        carries a QGraphicsEffect (i.e. it is being dragged). Such a row is
        composited from an offscreen pixmap, and under fractional display
        scaling the rounding drops its top device pixel, showing the parent
        through as a hard line; the overlap covers that.

        A hovered row has no effect and so must NOT get the overlap — the
        strip is raised above the row, so an overlap there clips the top
        pixel off the row's buttons, which on a bordered button eats the
        border and looks like the button has been sliced.
        """
        self.assertEqual(strip.y(), rc.y() - gap,
                         "the strip no longer starts one gap above the row")
        self.assertGreaterEqual(
            strip.geometry().bottom() + 1, rc.y(),
            "a gap between the strip and the row renders as a seam")
        lifted = rc.graphicsEffect() is not None
        self.assertEqual(strip.height(), gap + (1 if lifted else 0),
                         "overlap must appear only on a row with an effect")

    def test_hover_strip_fills_the_gap_above_the_row(self):
        rid = self.rows_with_gap()[0]
        rc = self.win._widgets[rid]["container"]
        gap = self.win._grid.spacing()
        if gap <= 0:
            self.skipTest("no v_spacing in this size preset")
        self.win._on_row_hover(rid, True)
        strip = self.win._hover_strip
        self.assertTrue(strip.isVisible())
        # Fills the gap and meets the row, and is aligned to the row's PAINTED
        # left edge (indented children inset their bg).
        self.assertStripCovers(strip, rc, gap)
        self.assertEqual(strip.x(), rc.x() + self.win._widgets[rid]["bg_left"])

    def test_first_row_draws_no_strip(self):
        rid = self.win._state.rows[0]["rowid"]
        self.win._on_row_hover(rid, True)
        strip = self.win._hover_strip
        self.assertTrue(strip is None or not strip.isVisible())

    def test_hovering_after_a_rebuild_does_not_touch_a_dead_widget(self):
        """The strip lives inside the content tree, which _rebuild_rows
        deletes wholesale. Reaching for the old one raised RuntimeError on
        every mouse move — including from the check meant to detect it."""
        rid = self.rows_with_gap()[0]
        self.win._on_row_hover(rid, True)
        self.win._on_rearrange_toggle()      # unlock -> full rebuild
        self.settle()
        rid = self.rows_with_gap()[0]
        self.win._on_row_hover(rid, True)    # raised RuntimeError before
        self.win._on_row_hover(rid, False)
        self.win._on_rearrange_toggle()
        self.settle()

    def test_dragged_row_fills_the_gap_too(self):
        """The drag colour is painted by the row's own rule, so it stopped at
        the row rect — the same partial fill hover used to have."""
        self.win._on_rearrange_toggle()
        self.settle()
        rid = self.rows_with_gap()[0]
        gap = self.win._grid.spacing()
        if gap <= 0:
            self.skipTest("no v_spacing in this size preset")
        self.win._drag.start(rid)
        self.settle()
        rc = self.win._widgets[rid]["container"]
        strip = self.win._hover_strip
        self.assertIsNotNone(strip)
        self.assertTrue(strip.isVisible())
        self.assertStripCovers(strip, rc, gap)
        from ct.ui.theme.colors import THEMES
        theme = THEMES[self.win._state.settings.theme]
        self.assertIn(theme["row_drag_bg"].lower(), strip.styleSheet().lower())
        self.win._drag.end()
        self.settle()

    def test_gap_fill_travels_with_the_dragged_row(self):
        """_reorder_visual reuses the existing containers instead of
        rebuilding, so nothing moved the strip — it stayed at the position
        the row started from, decorating whichever row took its place."""
        self.win._on_rearrange_toggle()
        self.settle()
        gap = self.win._grid.spacing()
        if gap <= 0:
            self.skipTest("no v_spacing in this size preset")
        rid = self.win._state.rows[-1]["rowid"]        # bottom row
        self.win._drag.start(rid)
        self.settle()
        start_y = self.win._hover_strip.y()
        # Walk it one slot up, the way a real drag drives the reorder.
        order = list(self.win._state.rows)
        pos = next(i for i, r in enumerate(order) if r["rowid"] == rid)
        order.insert(pos - 1, order.pop(pos))
        self.win._state.rows = order
        self.win._drag._reorder_visual()
        self.settle()
        rc = self.win._widgets[rid]["container"]
        strip = self.win._hover_strip
        self.assertNotEqual(strip.y(), start_y, "the fill did not move")
        self.assertStripCovers(strip, rc, gap)
        self.win._drag.end()
        self.settle()

    def test_group_state_colours_are_wired_independently(self):
        """All four group state keys reach the header, and none leak to rows.

        Injects values, rather than reading the shipped palette, because
        every theme currently seeds these EQUAL to their row/base
        counterparts — so a test comparing real values passes no matter how
        the code is wired, and the sibling tests below skip themselves
        entirely. These four hexes appear nowhere else in any theme, so a
        match cannot be coincidence.
        """
        from ct.ui.theme.colors import THEMES
        theme = THEMES["E-Ink (Default)"]
        original = dict(theme)
        self.addCleanup(lambda: (theme.clear(), theme.update(original)))
        theme["group_hover_bg"]   = "#111111"
        theme["group_hover_line"] = "#222222"
        theme["group_drag_bg"]    = "#333333"
        theme["group_drag_line"]  = "#444444"

        self.win._state.settings.theme = "E-Ink (Default)"
        self.win._rebuild_rows()
        css = lambda rid: self.win._widgets[rid]["container"].styleSheet().lower()
        sep = next(r["rowid"] for r in self.win._state.rows
                   if r["type"] == "separator")
        timer = next(r["rowid"] for r in self.win._state.rows
                     if r["type"] == "timer")

        self.assertIn("#111111", css(sep), "group_hover_bg not applied")
        self.assertIn("#222222", css(sep), "group_hover_line not applied")

        self.win._drag.start(sep)
        built = css(sep)
        self.win._drag._reorder_visual()
        moved = css(sep)
        self.win._drag.end()
        for label, sheet in (("on build", built), ("after a reorder", moved)):
            self.assertIn("#333333", sheet, f"group_drag_bg lost {label}")
            self.assertIn("#444444", sheet, f"group_drag_line lost {label}")

        # None of the four may appear on an ordinary timer row.
        self.win._drag.start(timer)
        row_css = css(timer)
        self.win._drag.end()
        for hexv in ("#111111", "#222222", "#333333", "#444444"):
            self.assertNotIn(hexv, row_css,
                             f"group color {hexv} leaked onto a timer row")

    def test_group_headers_hover_with_their_own_colour(self):
        """A bordered box reads very differently from an open row, so the
        tint that works for one is rarely the one that works for the other."""
        from ct.ui.theme.colors import THEMES
        for name in THEMES:
            t = THEMES[name]
            if t["group_hover_bg"] == t["row_hover_bg"]:
                continue          # seeded identical; nothing to tell apart
            with self.subTest(theme=name):
                self.win._state.settings.theme = name
                self.win._rebuild_rows()
                sep = next(r["rowid"] for r in self.win._state.rows
                           if r["type"] == "separator")
                css = self.win._widgets[sep]["container"].styleSheet().lower()
                self.assertIn(t["group_hover_bg"].lower(), css,
                              "group header does not use group_hover_bg")

    def test_group_headers_drag_with_their_own_colour(self):
        """Same reasoning as the hover colour: a bordered box on group_bg
        starts somewhere completely different from an open row on app_bg."""
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            if t["group_drag_bg"] == t["row_drag_bg"]:
                continue          # seeded identical; nothing to tell apart
            with self.subTest(theme=name):
                self.win._state.settings.theme = name
                self.win._rebuild_rows()
                sep = next(r["rowid"] for r in self.win._state.rows
                           if r["type"] == "separator")
                self.win._drag.start(sep)
                built = self.win._widgets[sep]["container"].styleSheet().lower()
                self.win._drag._reorder_visual()
                reordered = self.win._widgets[sep]["container"].styleSheet().lower()
                self.win._drag.end()
                want = t["group_drag_bg"].lower()
                self.assertIn(want, built, "group header ignores group_drag_bg")
                self.assertIn(want, reordered,
                              "group_drag_bg lost when the row was moved")

    def test_group_hover_colour_survives_a_reorder(self):
        """_reorder_visual rewrites every stylesheet and rebuilds the hover
        rule from scratch — it has to pick the group colour again."""
        from ct.ui.theme.colors import THEMES
        name = next((n for n, t in THEMES.items()
                     if t["group_hover_bg"] != t["row_hover_bg"]), None)
        if name is None:
            self.skipTest("every theme still seeds group_hover_bg = row_hover_bg")
        self.win._state.settings.theme = name
        self.win._rebuild_rows()
        sep = next(r["rowid"] for r in self.win._state.rows
                   if r["type"] == "separator")
        self.win._drag.start(sep)
        self.win._drag._reorder_visual()
        css = self.win._widgets[sep]["container"].styleSheet().lower()
        self.win._drag.end()
        self.assertIn(THEMES[name]["group_hover_bg"].lower(), css)

    def test_reorder_keeps_the_hover_rule_on_every_row(self):
        """_reorder_visual rewrites each container's stylesheet wholesale and
        has to re-append the rule RowFactory baked in.

        Asserted DURING the drag on purpose: drag.end() rebuilds the rows and
        would hand the rule back, so checking afterwards passes either way and
        proves nothing. Today the loss is invisible — hover is suppressed
        while dragging — but it leaves the rule dependent on a rebuild that
        happens to follow, which is not a property worth relying on.
        """
        self.win._on_rearrange_toggle()
        self.settle()
        rid = self.win._state.rows[-1]["rowid"]
        self.win._drag.start(rid)
        self.win._drag._reorder_visual()
        self.settle()
        try:
            for other in self.rows_with_gap():
                rc = self.win._widgets[other]["container"]
                self.assertIn("hov", rc.styleSheet(),
                              f"row {other} lost its hover rule mid-drag")
        finally:
            self.win._drag.end()
            self.settle()

    def group_rid(self):
        return next(r["rowid"] for r in self.win._state.rows
                    if r["type"] == "separator")

    def test_group_headers_never_take_the_gap_fill(self):
        """A bordered header with the fill above it reads as a tab sticking
        out of the box. Timer rows keep it; separators don't."""
        # Give the group a row above it so it HAS a gap to fill.
        self.win._state.rows.append(
            {"rowid": 99, "name": "Later", "type": "separator", "bg": None})
        self.rebuild()
        rid = 99
        self.assertTrue(self.win._widgets[rid].get("is_group"))
        self.win._on_row_hover(rid, True)
        strip = self.win._hover_strip
        self.assertTrue(strip is None or not strip.isVisible())

    def test_group_headers_take_no_fill_while_dragged_either(self):
        self.win._on_rearrange_toggle()
        self.settle()
        rid = self.group_rid()
        self.win._drag.start(rid)
        self.settle()
        strip = self.win._hover_strip
        self.assertTrue(strip is None or not strip.isVisible())
        self.win._drag.end()
        self.settle()

    def test_leaving_a_timer_for_a_group_clears_the_fill(self):
        """The strip must not linger from the previously hovered row."""
        gap = self.win._grid.spacing()
        if gap <= 0:
            self.skipTest("no v_spacing in this size preset")
        timer_rid = self.rows_with_gap()[0]
        self.win._on_row_hover(timer_rid, True)
        self.assertTrue(self.win._hover_strip.isVisible())
        self.win._on_row_hover(timer_rid, False)
        self.win._on_row_hover(self.group_rid(), True)
        self.assertFalse(self.win._hover_strip.isVisible())

    def test_a_missed_leave_does_not_strand_the_tint(self):
        """Qt gives no Leave guarantee when the pointer exits fast, and this
        window is usually unfocused — so the tint used to sit there until the
        user came back and hovered something else. A poll runs only while a
        row is tinted and clears it on its own."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor
        rid = self.rows_with_gap()[0]
        rc = self.win._widgets[rid]["container"]
        QCursor.setPos(rc.mapToGlobal(QPoint(rc.width() // 2,
                                             rc.height() // 2)))
        self.settle()
        self.win._sync_hover_to_cursor()
        self.settle()
        if rc.property("hov") != "1":
            self.skipTest("cursor could not be placed over the row")
        self.assertTrue(self.win._hover_poll.isActive(),
                        "poll must run while a row is tinted")
        # Leave the row without the app ever seeing a Leave event.
        QCursor.setPos(QPoint(2, 2))
        self.win._sync_hover_to_cursor()      # what the poll tick does
        self.settle()
        self.assertNotEqual(rc.property("hov"), "1")
        self.assertFalse(self.win._hover_poll.isActive(),
                         "poll must idle once nothing is tinted")

    def test_hovering_a_deleted_row_is_a_no_op(self):
        rid = self.rows_with_gap()[0]
        self.win._on_row_hover(rid, True)
        self.win._on_remove(rid)
        self.settle()
        self.win._on_row_hover(rid, False)


class TestQtLayoutInvariants(QtWindowTestBase):

    def test_all_rows_are_one_uniform_height(self):
        """Row snapping — and therefore every window size — depends on it."""
        self.assertEqual(len(set(self.row_heights())), 1)

    def test_footer_is_one_line_in_both_lock_modes(self):
        """HARD POLICY. Footer height feeds `chrome`, and chrome decides how
        many whole rows fit — a taller edit footer would drop a row."""
        locked = self.footer().height()
        self.win._on_rearrange_toggle()
        self.settle()
        unlocked = self.footer().height()
        self.win._on_rearrange_toggle()
        self.settle()
        self.assertEqual(locked, unlocked)
        self.assertEqual(locked, self.footer().height())

    def test_toggling_the_lock_does_not_change_window_height(self):
        before = self.win.height()
        self.win._on_rearrange_toggle()
        self.settle()
        mid = self.win.height()
        self.win._on_rearrange_toggle()
        self.settle()
        self.assertEqual((before, before), (mid, self.win.height()))

    def test_x_buttons_appear_only_while_unlocked(self):
        def xs():
            return {w["x"].isVisible()
                    for w in self.win._widgets.values() if "x" in w}
        self.assertEqual(xs(), {False})
        self.win._on_rearrange_toggle()
        self.settle()
        self.assertEqual(xs(), {True})

    def test_bottom_most_row_drops_its_separator(self):
        """Otherwise it stacks with the footer rule a few pixels below."""
        self.win._state.settings.client_separators = True
        self.rebuild()
        grid = self.win._grid
        sa = self.win._scroll_area
        vp_bottom = (sa.verticalScrollBar().value() + sa.viewport().height())
        tol = max(grid.spacing(), 2)
        flush = [grid.itemAt(i).widget() for i in range(grid.count())
                 if grid.itemAt(i).widget() is not None
                 and 0 <= vp_bottom - (grid.itemAt(i).widget().y()
                                       + grid.itemAt(i).widget().height()) <= tol]
        for w in flush:
            # The base rule may draw a separator; if it does, the row must be
            # flagged nosep. (The nosep rule itself always contains the words
            # "border-bottom: none", so grepping the stylesheet proves nothing.)
            base = w.styleSheet().split("#rowBg[")[0]
            if "border-bottom" in base:
                self.assertEqual(w.property("nosep"), "1",
                                 "bottom-flush row still paints its separator")

    def test_hidden_separator_is_the_bottom_most_row(self):
        """It must be the row genuinely flush with the viewport bottom.

        The tolerance equals the inter-row gap, so when the rows fill the
        viewport exactly the SECOND-to-last row is also within tolerance —
        and the old loop broke on the first match. Worse, callers run mid-fit
        where the rows still carry the previous pitch, so the hit test ran on
        stale positions. Both hid the separator one row too high, and nothing
        corrected it: at launch the line between two rows was just missing.
        """
        from ct.core.timer_state import TimerState
        rows = [{"rowid": 300 + i, "name": f"Row {i}", "type": "timer",
                 "bg": None} for i in range(10)]
        self.win._state.rows = rows
        self.win.timers = {r["rowid"]: TimerState(r["name"]) for r in rows}
        self.win._state.settings.client_separators = True
        self.rebuild()
        pitch = self.row_heights()[0] + self.win._grid.spacing()
        # A ceiling that fits a whole number of rows exactly — the case that
        # puts two rows inside the tolerance at once.
        self.win._state.window_height = (self.win._last_chrome + 6 * pitch
                                         - self.win._grid.spacing())
        self.win._shrink_to_fit()
        self.settle()
        hidden = self.win._hidden_line
        self.assertIsNotNone(hidden, "no row was picked at all")
        sa = self.win._scroll_area
        vp_bottom = sa.verticalScrollBar().value() + sa.viewport().height()
        # The bottom-most row whose bottom edge is at or above the cut.
        best, best_d = None, None
        for i in range(self.win._grid.count()):
            w = self.win._grid.itemAt(i).widget()
            if w is None:
                continue
            d = vp_bottom - (w.y() + w.height())
            if d >= 0 and (best_d is None or d < best_d):
                best, best_d = w, d
        self.assertIs(hidden, best,
                      "hid a separator on the wrong row — the visible symptom "
                      "is a missing line between two rows mid-list")

    def test_hidden_separator_survives_a_stylesheet_rewrite(self):
        """The old version stored the exact string it had replaced and only
        restored on an exact match, so any rewrite in between (a drag
        reorder) made the restore silently no-op — one separator gone at
        random until the next rebuild."""
        self.win._state.settings.client_separators = True
        self.rebuild()
        hidden = self.win._hidden_line
        if hidden is None:
            self.skipTest("no row is flush with the viewport bottom here")
        rid = next(r for r, w in self.win._widgets.items()
                   if w.get("container") is hidden)
        self.assertEqual(hidden.property("nosep"), "1")
        # Something else rewrites the row wholesale, mid-drag.
        self.win._on_rearrange_toggle()
        self.settle()
        rid2 = self.win._state.rows[-1]["rowid"]
        self.win._drag.start(rid2)
        self.win._drag._reorder_visual()
        self.win._drag.end()
        self.settle()
        # Every row either paints its separator or is flagged; none may be
        # left permanently stripped with nothing tracking it.
        for r, w in self.win._widgets.items():
            rc = w.get("container")
            if rc is None or rc is self.win._hidden_line:
                continue
            self.assertNotEqual(rc.property("nosep"), "1",
                                f"row {r} left stripped with nothing to restore it")
        self.win._on_rearrange_toggle()
        self.settle()


class TestQtStatusLine(QtWindowTestBase):

    def plain(self):
        import re as _re
        return _re.sub(r"<[^>]+>", "", self.win._status_lbl.text())

    def test_nothing_running_shows_only_the_total(self):
        self.win._update_status()
        self.assertNotIn("running", self.plain())

    def test_running_count_is_shown_and_never_one_dot_per_timer(self):
        self.win.timers[11].start()
        self.win.timers[12].start()
        self.win._update_status()
        self.assertIn("2 running", self.plain())
        self.assertEqual(self.plain().count("●"), 1)

    def test_total_equals_the_sum_of_the_rows(self):
        from ct.util import format_time
        self.win.timers[11].elapsed = 3600
        self.win.timers[12].elapsed = 1800
        self.win._update_status()
        total = sum(t.current_elapsed for t in self.win.timers.values())
        self.assertIn(format_time(total), self.plain())

    def test_period_word_follows_daily_reset(self):
        self.win._state.settings.daily_reset_enabled = True
        self.win._update_status()
        self.assertIn("Today", self.plain())
        self.win._state.settings.daily_reset_enabled = False
        self.win._update_status()
        self.assertIn("Total", self.plain())
        self.assertNotIn("Today", self.plain())

    def test_tooltip_only_claims_a_period_when_one_exists(self):
        self.win._state.settings.daily_reset_enabled = False
        self.win._update_status()
        self.assertEqual(self.win._status_lbl.toolTip(), "")


class TestQtUpdateThreading(QtWindowTestBase):
    """The result must cross from the worker thread into the GUI thread.

    This is the test for the bug that cost the most time: the check ran on a
    plain threading.Thread and handed its result back via
    QTimer.singleShot. A QTimer created on a thread with no Qt event loop
    NEVER FIRES — so the check succeeded, logged "Update available", and the
    result silently evaporated. Everything reported success; nothing happened.

    The probe that missed it called the handler directly on the main thread,
    exercising the UI and never the hop. These drive the real worker, so they
    fail if a QTimer is ever put back there.
    """

    MANIFEST = {"version": "99.0.0", "url": "https://example.com/x.exe",
                "notes": "n"}

    def setUp(self):
        super().setUp()
        del self.win.show_toast          # exercise the real widget

    def pump(self, predicate, seconds=3.0):
        """Spin the event loop until predicate() or the deadline.

        settle() is a fixed number of iterations; a worker thread needs
        wall-clock time, and how much depends on the machine.
        """
        end = time.time() + seconds
        while time.time() < end:
            self.settle(2)
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def toast_says(self, fragment):
        return lambda: fragment.lower() in self.win._toast.text().lower()

    def test_check_result_reaches_the_ui_from_a_worker_thread(self):
        self.win._state.settings.last_update_prompt = ""
        with patch("ct.core.update.check",
                   return_value=("update", self.MANIFEST)):
            self.win._start_update_check()
            got = self.pump(self.toast_says("99.0.0"))
        self.assertTrue(got, "the worker's result never reached the GUI thread")
        self.assertTrue(self.win._toast_action.isVisible())

    def test_download_result_reaches_the_ui_from_a_worker_thread(self):
        """The download hop had the identical bug waiting behind it."""
        with patch("ct.core.update.download", return_value=None):
            self.win._do_update(self.MANIFEST)
            got = self.pump(self.toast_says("failed"))
        self.assertTrue(got, "the download result never reached the GUI thread")

    def test_a_forced_check_answers_even_when_up_to_date(self):
        """A button that does nothing visible reads as broken."""
        with patch("ct.core.update.check", return_value=("current", {})):
            self.win._start_update_check(forced=True)
            self.assertTrue(self.pump(self.toast_says("up to date")))

    def test_a_forced_check_says_so_when_it_cannot_reach_the_manifest(self):
        """'Up to date' and 'I couldn't tell' are different answers."""
        with patch("ct.core.update.check", return_value=("failed", None)):
            self.win._start_update_check(forced=True)
            self.assertTrue(self.pump(self.toast_says("couldn't check")))

    def test_an_automatic_check_stays_silent_when_up_to_date(self):
        with patch("ct.core.update.check", return_value=("current", {})):
            self.win._start_update_check(forced=False)
            shown = self.pump(lambda: self.win._toast_container.isVisible(), 1.0)
        self.assertFalse(shown, "an automatic check must not toast when current")

    def test_a_forced_check_ignores_the_once_a_day_gate(self):
        """Being told yesterday is no reason to withhold an answer from
        someone who just clicked the button."""
        self.win._state.settings.last_update_prompt = now_iso()
        self.assertFalse(self.win._due_for_update_prompt())
        with patch("ct.core.update.check",
                   return_value=("update", self.MANIFEST)):
            self.win._start_update_check(forced=True)
            self.assertTrue(self.pump(self.toast_says("99.0.0")))



class TestQtDragLift(QtWindowTestBase):
    """The dragged row reads as picked up off the page.

    Before this existed, 20 of 21 themes used the same value for
    row_drag_bg and row_hover_bg — and the cursor is necessarily ON the row
    being dragged, so it would be hovered anyway. The dragged row rendered
    pixel-identical to a merely hovered one. The elevation is what carries
    the signal on its own.
    """

    def effect_on(self, rid):
        w = self.win._widgets.get(rid)
        container = w.get("container") if w else None
        return None if container is None else container.graphicsEffect()

    def test_drag_lifts_the_row(self):
        self.win._drag.start(12)
        self.assertIsNotNone(self.effect_on(12), "the dragged row has no lift")
        self.win._drag.end()

    def test_only_the_dragged_row_is_lifted(self):
        self.win._drag.start(12)
        others = [r for r in (10, 11, 13) if self.effect_on(r) is not None]
        self.win._drag.end()
        self.assertEqual(others, [], f"rows {others} were lifted too")

    def test_the_lift_survives_a_reorder(self):
        """_reorder_visual removes and re-inserts every container on every
        mouse move. The effect must not be a casualty of that."""
        self.win._drag.start(12)
        self.win._drag._reorder_visual()
        lifted = self.effect_on(12)
        self.win._drag.end()
        self.assertIsNotNone(lifted, "the lift was lost on the first reorder")

    def test_the_lift_survives_a_full_rebuild_mid_drag(self):
        """_reorder_visual falls back to _rebuild_rows when a row is missing,
        which builds NEW containers and drops the effect with the old ones."""
        self.win._drag.start(12)
        self.win._rebuild_rows()
        self.win._drag._lift()
        lifted = self.effect_on(12)
        self.win._drag.end()
        self.assertIsNotNone(lifted)

    def test_dropping_removes_the_lift(self):
        self.win._drag.start(12)
        self.win._drag.end()
        self.assertIsNone(self.effect_on(12),
                          "the row is still elevated after being dropped")

    def test_the_gap_fill_strip_stays_above_the_lifted_row(self):
        """Otherwise the shadow blurs across the strip and the join reads as
        a hard seam between the row and its extension.

        The strip fills only the inter-row spacing — about four pixels — so a
        blurred shadow laid over it looks exactly like a drawn black line.
        """
        self.win._state.settings.client_separators = True
        self.win._rebuild_rows()
        self.win._drag.start(12)
        container = self.win._widgets[12]["container"]
        strip = self.win._live_strip()
        self.assertIsNotNone(strip, "no gap-fill strip during the drag")
        siblings = container.parentWidget().children()
        self.assertIn(strip, siblings)
        row_z, strip_z = siblings.index(container), siblings.index(strip)
        self.win._drag.end()
        # Later in children() == painted on top.
        self.assertGreater(strip_z, row_z,
                           "the lifted row is above its own gap fill, so the "
                           "shadow paints over the fill")

    def test_the_strip_stays_on_top_across_a_reorder(self):
        """_reorder_visual re-raises the row on every mouse move."""
        self.win._state.settings.client_separators = True
        self.win._rebuild_rows()
        self.win._drag.start(12)
        self.win._drag._reorder_visual()
        container = self.win._widgets[12]["container"]
        strip = self.win._live_strip()
        siblings = container.parentWidget().children()
        ok = (strip is not None and strip in siblings
              and siblings.index(strip) > siblings.index(container))
        self.win._drag.end()
        self.assertTrue(ok, "the strip fell behind the row after a reorder")

    def sep_css(self, rid):
        """Is a separator line actually DRAWN on this row?

        Not a bare "border-bottom" search: every row also carries the
        `#rowBg[nosep="1"] { border-bottom: none; }` rule that
        _update_bottom_line switches on, so that substring is always
        present. Only a width means a line is painted.
        """
        css = self.win._widgets[rid]["container"].styleSheet()
        return "border-bottom: 1px" in css or "border-bottom: 2px" in css

    def test_a_lifted_row_drops_its_separator(self):
        """The line divides this row from the next one — the dragged row has
        left the list, so it should not carry one.

        Left on, it paints a hard row_line edge along the bottom of a row
        that has otherwise gone to row_drag_bg. On a high-contrast theme
        (95 Windows: teal drag on grey chrome) that reads as the bottom
        pixel of the row failing to tint.
        """
        self.win._state.settings.client_separators = True
        self.win._rebuild_rows()
        self.assertTrue(self.sep_css(11), "row 11 should have a separator")
        self.win._drag.start(11)
        dragged, neighbour = self.sep_css(11), self.sep_css(12)
        self.win._drag.end()
        self.assertFalse(dragged, "the lifted row still draws a separator")
        self.assertTrue(neighbour, "a row that is NOT dragged lost its line")

    def test_the_separator_stays_gone_across_a_reorder(self):
        """_reorder_visual rebuilds every stylesheet on each mouse move, so
        the rule has to exist there too or the line returns the instant the
        row is moved."""
        self.win._state.settings.client_separators = True
        self.win._rebuild_rows()
        self.win._drag.start(11)
        self.win._drag._reorder_visual()
        dragged = self.sep_css(11)
        self.win._drag.end()
        self.assertFalse(dragged, "the separator came back during the drag")

    def test_the_separator_returns_after_the_drop(self):
        self.win._state.settings.client_separators = True
        self.win._rebuild_rows()
        self.win._drag.start(11)
        self.win._drag.end()
        self.settle()
        self.assertTrue(self.sep_css(11),
                        "the row never got its separator back")

    def test_dark_themes_get_a_halo_and_light_ones_a_shadow(self):
        """Black on near-black is invisible at any alpha, so dark themes
        signal elevation with a light rim instead of a cast shadow."""
        from ct.ui.theme import THEMES
        from ct.ui.drag import _luma
        for name in ("Galaxy Dark", "NOCturnal", "Manila Memories", "95 Windows"):
            with self.subTest(theme=name):
                self.win._state.settings.theme = name
                self.win._rebuild_rows()
                self.win._drag.start(12)
                effect = self.effect_on(12)
                self.assertIsNotNone(effect)
                if _luma(THEMES[name]["app_bg"]) < 128:
                    self.assertEqual(effect.yOffset(), 0,
                                     "a halo must not have a light source")
                    self.assertGreater(effect.color().lightness(), 100,
                                       "a dark theme needs a LIGHT rim")
                else:
                    self.assertGreater(effect.yOffset(), 0,
                                       "a cast shadow needs an offset")
                    self.assertLess(effect.color().lightness(), 100)
                self.win._drag.end()

    def test_every_theme_produces_a_usable_lift(self):
        """A theme with a malformed app_bg must not crash a drag."""
        from ct.ui.theme import THEMES
        for name in THEMES:
            with self.subTest(theme=name):
                self.win._state.settings.theme = name
                self.win._rebuild_rows()
                self.win._drag.start(12)
                effect = self.effect_on(12)
                self.assertIsNotNone(effect)
                self.assertGreater(effect.blurRadius(), 0)
                self.assertGreater(effect.color().alpha(), 0)
                self.win._drag.end()


class TestQtSettingsDialog(QtWindowTestBase):
    """The settings dialog builds and its preview survives every combination.

    This class exists because 291 passing tests once shipped a settings
    dialog that raised on construction: a widget was removed from the
    preview rows but its name was left in the tuple the styling loop
    unpacks. Nothing opened the dialog, so nothing noticed. Opening it at
    all is most of the value here.
    """

    def build(self):
        # No addCleanup: cleanups run AFTER tearDown, which destroys the
        # window this dialog is parented to — touching it there raises
        # "C++ object already deleted". Qt frees it with its parent.
        from ct.ui.dialogs.settings import ConfigDialog
        return ConfigDialog(self.win, self.win._state.settings.to_dict(),
                            lambda: None)

    def test_the_dialog_builds(self):
        self.assertIsNotNone(self.build())

    def test_every_page_is_present(self):
        dlg = self.build()
        self.assertEqual(dlg._stack.count(), dlg._tab_list.count())

    def test_general_is_preselected_not_about(self):
        """About is listed first but must not be what opens."""
        dlg = self.build()
        self.assertEqual(dlg._stack.currentIndex(), dlg._tab_list.currentRow())
        self.assertNotEqual(dlg._tab_list.currentRow(), 0)

    def test_the_preview_shows_one_toggle_per_row(self):
        """Two buttons per row was the pre-toggle layout. Row 1 is the
        running sample, row 2 the stopped one."""
        dlg = self.build()
        self.assertEqual(dlg._p1_start.text(), "Stop")
        self.assertEqual(dlg._p2_start.text(), "Start")
        self.assertFalse(hasattr(dlg, "_p1_stop"))
        self.assertFalse(hasattr(dlg, "_p2_stop"))

    def test_the_tip_strip_has_actual_width(self):
        """It rendered zero pixels wide once: the label has an Ignored width
        policy, so a sibling addStretch() took the entire row and left it
        nothing. The strip looked absent rather than broken."""
        dlg = self.build()
        dlg.show()
        self.app.processEvents()
        self.assertGreater(dlg._tip_lbl.width(), 0,
                           "the tip strip is collapsed to nothing")

    def test_the_tip_strip_shows_on_every_page(self):
        """It lives below the page stack, not on any page, so a new settings
        page cannot forget to include it."""
        dlg = self.build()
        dlg.show()
        for i in range(dlg._stack.count()):
            with self.subTest(page=i):
                dlg._tab_list.setCurrentRow(i)
                self.assertTrue(dlg._tip_lbl.isVisible())

    def test_tips_cycle_through_all_of_them_and_wrap(self):
        from ct.ui.dialogs.settings import TIPS, TIP_PREFIX
        dlg = self.build()
        seen = set()
        for _ in range(len(TIPS) * 2):
            seen.add(dlg._tip_lbl.toolTip())   # tooltip is the unelided text
            dlg._next_tip()
        self.assertEqual(seen, {TIP_PREFIX + t for t in TIPS})

    def test_the_prefix_is_permanent_and_never_elided(self):
        """A lone italic sentence in the corner reads as a status message
        about the page. The prefix is held out of the elision so a narrow
        dialog eats the tip, not the label telling you what it is."""
        from ct.ui.dialogs.settings import TIPS, TIP_PREFIX
        dlg = self.build()
        dlg.show()
        for i in range(len(TIPS)):
            with self.subTest(tip=i):
                dlg._tip_index = i
                dlg._show_tip()
                self.assertTrue(dlg._tip_lbl.text().startswith(TIP_PREFIX))
        # Even squeezed to almost nothing.
        dlg._tip_lbl.resize(40, dlg._tip_lbl.height())
        dlg._show_tip()
        self.assertTrue(dlg._tip_lbl.text().startswith(TIP_PREFIX))

    def test_the_strip_uses_the_themes_muted_foreground(self):
        """The dialog's background IS app_bg — build_stylesheet paints
        QDialog with it — so app_fg_muted is the designed pairing. It must
        follow the SAVED theme, since _apply_style only runs after this
        dialog closes and the background does not move until then."""
        from ct.ui.theme import THEMES
        for name in ("NOCturnal", "Manila Memories", "Galaxy Dark"):
            with self.subTest(theme=name):
                self.win._state.settings.theme = name
                dlg = self.build()
                self.assertIn(THEMES[name]["app_fg_muted"].lower(),
                              dlg._tip_lbl.styleSheet().lower())
                # Changing the dropdown must NOT move it: that would put the
                # new theme's muted colour on the old theme's background.
                other = "95 Windows" if name != "95 Windows" else "NOCturnal"
                dlg._theme.setCurrentText(other)
                dlg._refresh_preview()
                self.assertIn(THEMES[name]["app_fg_muted"].lower(),
                              dlg._tip_lbl.styleSheet().lower())

    def test_the_strip_height_never_changes(self):
        """A tip that wrapped would resize the strip mid-cycle and jostle the
        whole dialog every few seconds while you were reading it."""
        from ct.ui.dialogs.settings import TIPS
        dlg = self.build()
        heights = set()
        for i in range(len(TIPS)):
            dlg._tip_index = i
            dlg._show_tip()
            heights.add(dlg._tip_lbl.sizeHint().height())
        self.assertEqual(len(heights), 1, f"strip height varies: {heights}")

    def test_tips_are_single_line_and_unique(self):
        from ct.ui.dialogs.settings import TIPS
        self.assertTrue(TIPS, "no tips defined")
        self.assertEqual(len(set(TIPS)), len(TIPS), "a tip is duplicated")
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertNotIn("\n", tip, "tips must be one line")
                self.assertTrue(tip.strip(), "empty tip")
                # Not a pixel budget — the test font is not the shipped font,
                # so characters are the only stable unit here. Generous, and
                # elision covers anything that still overflows.
                self.assertLessEqual(len(tip), 75, "too long for the strip")

    def test_the_tip_timer_is_running(self):
        dlg = self.build()
        self.assertTrue(dlg._tip_timer.isActive())
        self.assertGreaterEqual(dlg._tip_timer.interval(), 3000,
                                "cycling this fast is a distraction")

    def test_the_preview_refreshes_for_every_theme_and_size(self):
        """_refresh_preview restyles every preview widget, so a widget added
        or removed without updating that loop raises here."""
        from ct.ui.theme import THEMES, SIZES
        dlg = self.build()
        for name in THEMES:
            dlg._theme.setCurrentText(name)
            for size in SIZES:
                with self.subTest(theme=name, size=size):
                    dlg._size.setCurrentText(size)
                    dlg._refresh_preview()


class TestQtStopAll(QtWindowTestBase):
    """'Stop All Timers' in the row context menu.

    Deliberately NOT a footer button. Starting is exclusive by default, so
    0 or 1 timers run and the row's own toggle already covers that; a second
    running-related control beside the status line would split an affordance
    that currently has one meaning.
    """

    def menu_labels(self):
        """Every action the row menu would offer, without opening it."""
        from unittest.mock import patch, MagicMock
        labels = []

        def add(text):
            # Must return an action: callers immediately setEnabled() on it.
            labels.append(text)
            return MagicMock()

        with patch("ct.ui.app.QMenu") as fake:
            menu = fake.return_value
            menu.addAction.side_effect = add
            menu.exec.return_value = None
            self.win._on_row_context_menu(11, self.win.rect().center())
        return labels

    def test_absent_when_nothing_is_running(self):
        """A permanently-greyed entry is clutter on every right-click."""
        self.assertNotIn("Stop All Timers", self.menu_labels())

    def test_present_while_a_timer_runs(self):
        self.win._start_exclusive(11)
        labels = self.menu_labels()
        self.win._stop_all()
        self.assertIn("Stop All Timers", labels)

    def test_it_stops_every_running_timer(self):
        self.win._start_additional(11)
        self.win._start_additional(12)
        self.assertEqual(len(self.win._running_rids()), 2)
        self.win._stop_all()
        self.assertEqual(self.win._running_rids(), [])

    def test_stopping_keeps_the_elapsed_time(self):
        """Stop is not reset — the whole point of the separate entry."""
        self.win.timers[11].elapsed = 500.0
        self.win._start_exclusive(11)
        self.win._stop_all()
        self.assertGreaterEqual(self.win.timers[11].current_elapsed, 500.0)

    def test_the_toggle_button_label_follows(self):
        """_stop_all goes through _set_bold, which owns the button's text.
        Without that the row would sit stopped with a 'Stop' button on it."""
        self.win._start_exclusive(11)
        self.assertEqual(self.win._widgets[11]["toggle"].text(), "Stop")
        self.win._stop_all()
        self.assertEqual(self.win._widgets[11]["toggle"].text(), "Start")


class TestQtStatusCopy(QtWindowTestBase):
    """The footer click copies every time, whatever is running."""

    def setUp(self):
        super().setUp()
        self.win.timers[11].elapsed = 3725.0
        self.win.timers[12].elapsed = 612.0
        self.rebuild()

    def copied(self):
        """What a footer click would put on the clipboard.

        Deliberately NOT read back from the real clipboard: it is a global
        OS resource, so whenever another app on the machine held it the
        read came back empty and this class failed at random.
        """
        self.win._on_status_click()
        self.settle()
        return "\n".join(self.win._session_lines())

    def test_copies_with_nothing_running(self):
        # HH:MM is the default copy format; the row on screen still reads
        # HH:MM:SS. TestCopyFormat covers the other options.
        text = self.copied()
        self.assertIn("Alpha: 01:02", text)
        self.assertIn("Bravo: 00:10", text)

    def test_copies_while_one_is_running(self):
        """This used to jump to the row instead — copy-all was unreachable
        the moment anything was running, which is exactly when you want it."""
        self.win._start_exclusive(11)
        self.settle()
        text = self.copied()
        self.assertIn("Alpha:", text)
        self.assertIn("Bravo: 00:10", text)
        self.win._stop_all()

    def test_the_copy_format_setting_reaches_the_session_copy(self):
        """Copy-all is the highest-volume copy in the app; a format that
        applied only to single rows would be the one people notice."""
        from ct.util import format_copy_time
        cases = {"HH:MM": "Alpha: 01:02", "HH:MM:SS": "Alpha: 01:02:05",
                 "Decimal": "Alpha: 1.03", "Raw Minutes": "Alpha: 62"}
        for fmt, expected in cases.items():
            with self.subTest(fmt=fmt):
                self.win._state.settings.copy_format = fmt
                self.assertIn(expected, "\n".join(self.win._session_lines()))
        # And the helper agrees with what the window produced.
        self.assertEqual(format_copy_time(3725, "Decimal"), "1.03")

    def test_copies_while_several_are_running(self):
        self.win._start_additional(11)
        self.win._start_additional(12)
        self.settle()
        text = self.copied()
        self.assertIn("Alpha:", text)
        self.assertIn("Bravo:", text)
        self.win._stop_all()

    def test_a_running_timer_copies_its_live_time(self):
        self.win._start_exclusive(11)
        self.win.timers[11].elapsed = 3725.0 + 30    # advance without waiting
        self.settle()
        line = [l for l in self.copied().splitlines()
                if l.startswith("Alpha")][0]
        self.assertNotIn("01:02:05", line)
        self.win._stop_all()

    def test_right_click_still_jumps_to_a_running_row(self):
        """Jump wasn't dropped, it moved to right-click — where every other
        secondary action in this app already lives."""
        self.win._start_exclusive(12)
        self.settle()
        jumped = []
        self.win._scroll_to_row = lambda rid: jumped.append(rid)
        self.win._on_status_context()
        self.assertEqual(jumped, [12])
        self.win._stop_all()

    def test_right_click_with_nothing_running_does_nothing(self):
        jumped = []
        self.win._scroll_to_row = lambda rid: jumped.append(rid)
        self.win._on_status_context()
        self.assertEqual(jumped, [])


class TestQtInlineRename(QtWindowTestBase):

    def test_editor_overlays_the_label_without_moving_anything(self):
        heights, h = self.row_heights(), self.win.height()
        lbl = self.win._widgets[11]["name"]
        geo = lbl.geometry()
        self.win._begin_inline_rename(11)
        self.settle()
        editor = self.win._inline_editor[0]
        self.assertEqual(editor.geometry(), geo)
        self.assertIs(editor.parentWidget(), self.win._widgets[11]["container"])
        self.assertEqual(self.row_heights(), heights)
        self.assertEqual(self.win.height(), h)

    def test_enter_commits_to_row_and_timer(self):
        self.win._begin_inline_rename(11)
        self.win._inline_editor[0].setText("Renamed")
        self.win._end_inline_rename(commit=True)
        self.settle()
        self.assertEqual(self.win._state.rows[1]["name"], "Renamed")
        self.assertEqual(self.win.timers[11].name, "Renamed")

    def test_cancel_keeps_the_old_name(self):
        self.win._begin_inline_rename(11)
        self.win._inline_editor[0].setText("Nope")
        self.win._end_inline_rename(commit=False)
        self.settle()
        self.assertEqual(self.win._state.rows[1]["name"], "Alpha")

    def test_blank_name_is_rejected(self):
        for junk in ("", "   ", "\x01\x02"):
            self.win._begin_inline_rename(11)
            self.win._inline_editor[0].setText(junk)
            self.win._end_inline_rename(commit=True)
            self.settle()
            self.assertEqual(self.win._state.rows[1]["name"], "Alpha")

    def test_a_second_editor_replaces_the_first(self):
        self.win._begin_inline_rename(11)
        first = self.win._inline_editor[0]
        self.win._begin_inline_rename(12)
        self.assertIsNot(self.win._inline_editor[0], first)
        self.settle()

    def test_no_inline_editing_while_unlocked(self):
        """The name label goes mouse-transparent so drags still work."""
        self.win._on_rearrange_toggle()
        self.settle()
        self.assertEqual(self.win._name_labels, {})


class TestQtUndoWiring(QtWindowTestBase):

    def test_delete_then_undo_restores_row_time_and_position(self):
        self.win.timers[12].elapsed = 1800
        self.win._on_remove(12)
        self.settle()
        self.assertEqual(self.order(), [10, 11, 13])
        self.win._undo_last()
        self.settle()
        self.assertEqual(self.order(), [10, 11, 12, 13])
        self.assertEqual(self.win.timers[12].current_elapsed, 1800)

    def test_deleting_a_row_toasts_with_the_undo_hint(self):
        self.win._on_remove(12)
        self.settle()
        self.assertIn("Deleted 'Bravo'", self.toasts[-1])
        self.assertIn("Ctrl+Z", self.toasts[-1])

    def test_deleting_a_group_toasts_too(self):
        self.win._on_remove_group(10)
        self.settle()
        self.assertIn("Deleted group 'Group'", self.toasts[-1])
        self.assertIn("Ctrl+Z", self.toasts[-1])

    def test_reset_then_undo_with_no_accrual_needs_no_dialog(self):
        self.win.timers[11].elapsed = 3600
        self.win._reset_one(11)
        self.settle()
        self.assertEqual(self.win.timers[11].current_elapsed, 0)
        self.win._undo_last()          # would block if a dialog opened
        self.settle()
        self.assertEqual(self.win.timers[11].current_elapsed, 3600)

    def test_reset_all_is_a_single_undo_entry(self):
        self.win.timers[11].elapsed = 3600
        self.win.timers[12].elapsed = 1800
        self.win._reset_all()
        self.settle()
        self.assertEqual(len(self.win._undo), 1)
        self.win._undo_last()
        self.settle()
        self.assertEqual(self.win.timers[11].current_elapsed, 3600)
        self.assertEqual(self.win.timers[12].current_elapsed, 1800)

    def test_undo_on_an_empty_stack_says_so(self):
        self.win._undo.clear()
        self.win._undo_last()
        self.assertIn("Nothing to undo", self.toasts[-1])

    def test_a_snapshot_restore_clears_the_stack(self):
        self.win._on_remove(12)
        self.settle()
        self.assertEqual(len(self.win._undo), 1)
        self.win._undo.clear()         # stand-in for the restore path
        self.assertEqual(len(self.win._undo), 0)


class TestQtToast(QtWindowTestBase):

    def setUp(self):
        super().setUp()
        del self.win.show_toast        # exercise the real one

    def test_duration_is_honoured(self):
        for secs in (1, 7, 12):
            self.win.show_toast("hi", secs)
            self.assertEqual(self.win._toast_timer.interval(), secs * 1000)

    def test_the_x_dismisses_and_stops_the_countdown(self):
        self.win.show_toast("hi", 30)
        self.settle()
        self.assertTrue(self.win._toast_container.isVisible())
        self.win._toast_close.click()
        self.settle()
        self.assertFalse(self.win._toast_container.isVisible())
        self.assertFalse(self.win._toast_timer.isActive())

    def test_dismissing_mid_fade_leaves_opacity_restored(self):
        self.win.show_toast("hi", 1)
        self.settle()
        self.win._fade_toast()
        self.win._toast_close.click()
        self.settle()
        self.assertEqual(self.win._toast_opacity.opacity(), 1.0)

    def test_the_x_sits_left_of_the_message(self):
        self.win.show_toast("hi", 30)
        self.settle()
        self.assertLess(self.win._toast_close.x(), self.win._toast.x())

    # -- a toast is ADDITIVE: it grows the window, it never costs a row ----

    def _scrollable(self, visible_rows=4):
        """Enough rows to scroll, with a ceiling showing `visible_rows`."""
        from ct.core.timer_state import TimerState
        rows = [{"rowid": 200 + i, "name": f"Row {i}", "type": "timer",
                 "bg": None} for i in range(12)]
        self.win._state.rows = rows
        self.win.timers = {r["rowid"]: TimerState(r["name"]) for r in rows}
        self.rebuild()
        pitch = self.row_heights()[0] + self.win._grid.spacing()
        self.win._state.window_height = (self.win._last_chrome
                                         + visible_rows * pitch
                                         - self.win._grid.spacing())
        self.win._shrink_to_fit()
        self.settle()
        return pitch

    def test_toast_height_is_what_the_layout_actually_gives_it(self):
        """The message label word-wraps, so its sizeHint is Qt's guess at an
        unwrapped shape — twice the truth for a one-liner. Believing it made
        the chrome too small and handed the viewport a whole extra row."""
        self._scrollable()
        # A message that fits on one line but is long enough for Qt's hint to
        # reach for a squarish block is the case that broke: it reports two
        # lines for a toast the layout draws as one.
        for message in ("hi", "Reset 'Row 3'  (Ctrl+Z to undo)",
                        "a much longer message " * 8):
            with self.subTest(message=message[:12]):
                self.win.show_toast(message, 30)
                self.settle()
                self.assertEqual(self.win._toast_height(),
                                 self.win._toast_container.height())
                self.win._dismiss_toast()
                self.settle()

    def test_a_toast_grows_the_window_and_leaves_the_rows_alone(self):
        self._scrollable()
        before_vp = self.win._scroll_area.viewport().height()
        before_h = self.win.height()
        self.win.show_toast("Reset 'Row 3'  (Ctrl+Z to undo)", 30)
        self.settle()
        toast_h = self.win._toast_container.height()
        self.assertGreater(toast_h, 0)
        self.assertEqual(self.win._scroll_area.viewport().height(), before_vp)
        self.assertEqual(self.win.height(), before_h + toast_h)
        self.win._dismiss_toast()
        self.settle()
        self.assertEqual(self.win.height(), before_h)
        self.assertEqual(self.win._scroll_area.viewport().height(), before_vp)

    def test_a_toast_does_not_move_the_scroll_position(self):
        """The window and the toast's layout land in different turns, so the
        scroll range briefly shrinks and Qt clamps the position into it. That
        left the list stranded a toast-height off a row boundary for good."""
        pitch = self._scrollable()
        bar = self.win._scroll_area.verticalScrollBar()
        for start in (0, pitch, bar.maximum()):
            with self.subTest(scroll=start):
                bar.setValue(start)
                self.settle()
                keep = bar.value()
                self.win.show_toast("Reset 'Row 3'  (Ctrl+Z to undo)", 30)
                self.settle()
                self.assertEqual(bar.value(), keep)
                self.win._dismiss_toast()
                self.settle()
                self.assertEqual(bar.value(), keep)
                # And still flush on a row, which is what the user sees.
                self.assertIn(bar.value(), self.win._row_offsets())


# =========================================================================== #
#  6. THEME DATA INTEGRITY                                                      #
# =========================================================================== #

class TestUpdateCheck(unittest.TestCase):
    """The update check. Every failure mode must be a silent no-op."""

    def test_version_comparison_is_numeric_not_lexical(self):
        """'2.10.0' < '2.9.0' as text. That bug hides until release ten."""
        from ct.core.update import is_newer
        self.assertTrue(is_newer("2.10.0", "2.9.0"))
        self.assertTrue(is_newer("3.0.0", "2.99.99"))
        self.assertTrue(is_newer("2.3.1", "2.3.0"))
        self.assertFalse(is_newer("2.3.0", "2.3.0"))
        self.assertFalse(is_newer("2.2.9", "2.3.0"))

    def test_unparseable_versions_never_offer_an_update(self):
        """A typo'd manifest must be a non-event, not a push to download."""
        from ct.core.update import is_newer
        for bad in ("garbage", "2.3", "", None, "2.3.0-beta", "v2.4.0", 3):
            with self.subTest(version=bad):
                self.assertFalse(is_newer(bad, "2.3.0"))

    def _with_response(self, payload, status=200):
        """Fake urlopen returning payload, as a context manager."""
        import contextlib, io, json as _json

        @contextlib.contextmanager
        def fake(req, timeout=None):
            body = payload if isinstance(payload, bytes) else _json.dumps(payload).encode()
            yield io.BytesIO(body)
        return patch("urllib.request.urlopen", fake)

    def test_fetch_returns_none_on_every_network_failure(self):
        """No network, proxy, DNS, 404 — all silent. The user can act on
        none of them and the app works fine without an update check."""
        import urllib.error
        from ct.core.update import fetch_manifest
        for exc in (urllib.error.URLError("no route"),
                    urllib.error.HTTPError("u", 404, "Not Found", None, None),
                    OSError("proxy refused"),
                    TimeoutError("slow")):
            with self.subTest(error=type(exc).__name__):
                with patch("urllib.request.urlopen", side_effect=exc):
                    self.assertIsNone(fetch_manifest())

    def test_malformed_json_is_none_not_a_crash(self):
        from ct.core.update import fetch_manifest
        with self._with_response(b"<html>404</html>"):
            self.assertIsNone(fetch_manifest())

    def test_json_that_is_not_an_object_is_rejected(self):
        from ct.core.update import fetch_manifest
        with self._with_response([1, 2, 3]):
            self.assertIsNone(fetch_manifest())

    def test_check_reports_current_when_up_to_date(self):
        from ct.core.update import check, CURRENT
        from ct.common.version import __version__
        with self._with_response({"version": __version__,
                                  "url": "https://example.com/x.exe"}):
            status, manifest = check()
        self.assertEqual(status, CURRENT)
        self.assertIsNotNone(manifest)

    def test_check_reports_update_when_newer(self):
        from ct.core.update import check, UPDATE
        payload = {"version": "99.0.0", "url": "https://example.com/x.exe",
                   "notes": "hi"}
        with self._with_response(payload):
            status, manifest = check()
        self.assertEqual(status, UPDATE)
        self.assertEqual(manifest["version"], "99.0.0")

    def test_unreachable_manifest_is_failed_not_current(self):
        """A manual check must distinguish 'you're up to date' from 'I
        couldn't tell'. Collapsing them would tell the user something false."""
        import urllib.error
        from ct.core.update import check, FAILED
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            status, manifest = check()
        self.assertEqual(status, FAILED)
        self.assertIsNone(manifest)

    def test_newer_version_with_no_usable_url_is_not_offered(self):
        """Advertising an update the user cannot install is worse than
        staying quiet — that is a publishing mistake, not their problem."""
        from ct.core.update import check, UPDATE
        for bad_url in ("", "ftp://x/y.exe", "http://insecure/x.exe", None):
            with self.subTest(url=bad_url):
                with self._with_response({"version": "99.0.0", "url": bad_url}):
                    status, _ = check()
                self.assertNotEqual(status, UPDATE)


class TestUpdateDownload(unittest.TestCase):
    """Downloading the installer — the last checks before we EXECUTE a file.

    The checksum's job here is narrow and worth stating: TLS already
    guarantees the bytes arrived intact (a corrupted response fails the
    record MAC rather than arriving quietly), so the digest is really
    guarding the WRITE — a full disk, AV touching the file mid-write. That is
    why verification reads back off disk instead of hashing the buffer.
    """

    def setUp(self):
        self.dest = Path(tempfile.mkdtemp(prefix="ct2_dl_"))
        self.addCleanup(shutil.rmtree, self.dest, True)
        self.payload = b"MZ" + b"x" * 1_500_000        # plausible installer size
        import hashlib
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def _serving(self, body, content_length="auto"):
        """Fake urlopen returning `body` with a Content-Length header."""
        import contextlib, io

        class FakeResponse(io.BytesIO):
            pass

        length = str(len(body)) if content_length == "auto" else content_length

        @contextlib.contextmanager
        def fake(req, timeout=None):
            response = FakeResponse(body)
            response.headers = {} if length is None else {"Content-Length": length}
            # urllib's headers object is dict-like via .get
            yield response
        return patch("urllib.request.urlopen", fake)

    def test_download_without_a_digest_still_works(self):
        """Absent sha256 means no claim was made — not a reason to refuse.

        A manifest published before checksums existed, or hand-edited in a
        hurry, must not break updates for everyone at once."""
        from ct.core.update import download
        with self._serving(self.payload):
            path = download("https://x/ClientTimer2_Setup_9.9.9.exe",
                            dest_dir=self.dest)
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_matching_digest_is_accepted(self):
        from ct.core.update import download
        with self._serving(self.payload):
            path = download("https://x/setup.exe", dest_dir=self.dest,
                            sha256=self.digest)
        self.assertIsNotNone(path)

    def test_digest_comparison_ignores_case_and_whitespace(self):
        """Get-FileHash yields uppercase; a hand-pasted value carries spaces.
        Neither is a corrupt download."""
        from ct.core.update import download
        with self._serving(self.payload):
            path = download("https://x/setup.exe", dest_dir=self.dest,
                            sha256="  " + self.digest.upper() + "\n")
        self.assertIsNotNone(path)

    def test_mismatched_digest_is_refused(self):
        from ct.core.update import download
        with self._serving(self.payload):
            path = download("https://x/setup.exe", dest_dir=self.dest,
                            sha256="0" * 64)
        self.assertIsNone(path, "a file failing its checksum was accepted")

    def test_a_refused_file_is_not_left_on_disk(self):
        """It would sit in temp with an installer's name, already declined."""
        from ct.core.update import download
        with self._serving(self.payload):
            download("https://x/setup.exe", dest_dir=self.dest,
                     sha256="0" * 64)
        leftovers = list(self.dest.iterdir())
        self.assertEqual(leftovers, [],
                         f"untrusted download left behind: {leftovers}")

    def test_truncated_download_is_refused(self):
        """Content-Length disagreeing with what arrived, checked with no
        digest present so this stays an independent guard."""
        from ct.core.update import download
        with self._serving(self.payload, content_length=str(len(self.payload) + 99)):
            path = download("https://x/setup.exe", dest_dir=self.dest)
        self.assertIsNone(path)

    def test_a_tiny_response_is_never_an_installer(self):
        """An HTML error page served with a 200 is the realistic case."""
        from ct.core.update import download
        with self._serving(b"<html>Not Found</html>"):
            path = download("https://x/setup.exe", dest_dir=self.dest)
        self.assertIsNone(path)

    def test_file_sha256_matches_hashlib(self):
        """The chunked read must agree with the one-shot hash, or every
        release would publish a digest the client rejects."""
        import hashlib
        from ct.core.update import file_sha256
        target = self.dest / "blob.bin"
        target.write_bytes(self.payload)
        self.assertEqual(file_sha256(target, chunk=4096),
                         hashlib.sha256(self.payload).hexdigest())


class TestReleaseAutomation(unittest.TestCase):
    """The version number must have exactly one home.

    It used to be hand-typed in five places across three files. These tests
    assert the derivation still holds, because the failure is silent: a URL
    naming a version the exe does not have 404s for every user, and nothing
    on the publishing end looks wrong.
    """

    def repo_file(self, *parts):
        return Path(__file__).resolve().parent.parent.joinpath(*parts)

    def test_release_script_exists(self):
        self.assertTrue(self.repo_file("release.py").exists(),
                        "release.py is gone — the version has five homes again")

    def test_setup_iss_does_not_hardcode_a_version(self):
        """It must #include the generated file, not define its own."""
        text = self.repo_file("installer", "clienttimer2_setup.iss").read_text(
            encoding="utf-8")
        self.assertNotRegex(
            text, r'(?m)^\s*#define\s+MyAppVersion\s+"',
            "clienttimer2_setup.iss defines MyAppVersion itself; it must "
            "#include version.iss so the number stays in one place")
        self.assertIn('#include "version.iss"', text)

    def test_generated_iss_version_matches_version_py(self):
        """If these drift, About and Add/Remove Programs disagree, and the
        built exe's filename stops matching the manifest URL."""
        from ct.common.version import __version__
        text = self.repo_file("installer", "version.iss").read_text(
            encoding="utf-8")
        match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', text)
        self.assertIsNotNone(match, "version.iss has no MyAppVersion define")
        self.assertEqual(match.group(1), __version__,
                         "installer/version.iss is out of sync with "
                         "ct/common/version.py — re-run release.py")


class TestVersionAndManifest(unittest.TestCase):
    """The build's identity, and the manifest that advertises it.

    Deliberately NOT asserting that latest.json equals the installed version:
    between cutting a release and uploading it, version.py is legitimately
    ahead. What must never drift is the manifest's internal consistency — a
    version bumped in one field and forgotten in the URL ships an update that
    404s for every user.
    """

    def manifest(self):
        path = Path(__file__).resolve().parent.parent / "latest.json"
        self.assertTrue(path.exists(), "latest.json missing from the repo root")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_installed_version_is_a_real_version(self):
        from ct.common.version import __version__, version_tuple
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(version_tuple()), 3)

    def test_version_tuple_orders_numerically(self):
        """String comparison breaks at double digits: '2.10.0' < '2.9.0'."""
        from ct.common.version import version_tuple
        self.assertIsInstance(version_tuple()[0], int)
        self.assertLess((2, 9, 0), (2, 10, 0))

    def test_release_date_is_iso_and_parseable(self):
        from ct.common.version import RELEASE_DATE
        datetime.strptime(RELEASE_DATE, "%Y-%m-%d")

    def test_manifest_has_every_field_the_updater_reads(self):
        m = self.manifest()
        for key in ("version", "released", "url", "notes"):
            self.assertIn(key, m, f"latest.json is missing '{key}'")

    def test_manifest_version_and_date_are_well_formed(self):
        m = self.manifest()
        self.assertRegex(m["version"], r"^\d+\.\d+\.\d+$")
        datetime.strptime(m["released"], "%Y-%m-%d")

    def test_manifest_url_points_at_its_own_version(self):
        """The bump-one-field-forget-the-other bug ships a 404 to everyone."""
        m = self.manifest()
        self.assertIn(m["version"], m["url"],
                      "latest.json advertises a version its download URL "
                      "does not match")
        self.assertTrue(m["url"].startswith("https://"))

    def test_manifest_sha256_is_well_formed_if_present(self):
        """Optional by design — a manifest predating checksums has no claim
        to make, and download() skips the check rather than refusing. But a
        digest that IS there must be a real one, because a malformed value
        fails every download instead of none."""
        m = self.manifest()
        if "sha256" not in m:
            self.skipTest("this manifest predates checksums")
        self.assertRegex(m["sha256"], r"^[0-9a-fA-F]{64}$")


class TestThemeColors(unittest.TestCase):
    """Verify every theme has all required color keys."""

    REQUIRED_KEYS = [
        "app_bg", "app_fg", "app_fg_muted",
        "control_bg", "control_fg", "control_hover_bg", "control_hover_fg",
        "control_line", "control_border_px",
        "row_running_fg", "row_drag_bg", "row_hover_bg", "row_line",
        "group_bg", "group_hover_bg", "group_drag_bg", "group_fg",
        "group_running_fg", "group_line", "group_hover_line", "group_drag_line",
        "chrome_line",
        "toast_bg", "toast_fg",
    ]

    def test_all_themes_have_required_keys(self):
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            for key in self.REQUIRED_KEYS:
                self.assertIn(key, t, f"Theme '{name}' missing key '{key}'")

    def test_no_unknown_theme_keys(self):
        """A typo'd key would silently do nothing — catch it here instead."""
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            extra = set(t) - set(self.REQUIRED_KEYS)
            self.assertFalse(extra, f"Theme '{name}' has unknown key(s): {extra}")

    def test_no_empty_theme_values(self):
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            for key, val in t.items():
                self.assertIsNotNone(val, f"Theme '{name}' has None for '{key}'")
                if isinstance(val, str):
                    self.assertTrue(len(val) > 0, f"Theme '{name}' has empty string for '{key}'")

    def test_border_is_numeric(self):
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            self.assertIsInstance(t["control_border_px"], (int, float),
                                 f"Theme '{name}' border is not numeric: {t['control_border_px']}")

    def test_color_values_look_like_colors(self):
        from ct.ui.theme.colors import THEMES
        for name, t in THEMES.items():
            for key, val in t.items():
                if key == "control_border_px":
                    continue
                self.assertTrue(
                    val.startswith("#") or val.startswith("rgb"),
                    f"Theme '{name}' key '{key}' doesn't look like a color: {val}"
                )

    def test_themes_not_empty(self):
        from ct.ui.theme.colors import THEMES
        self.assertGreater(len(THEMES), 0)

    def test_default_theme_exists(self):
        from ct.ui.theme.colors import THEMES
        self.assertIn("E-Ink (Default)", THEMES)


class TestThemeSizes(unittest.TestCase):
    """Verify every size preset has required keys."""

    REQUIRED_KEYS = ["label", "time", "action", "padding", "frame_pad",
                     "h_spacing", "v_spacing", "line_gap", "footer_gap",
                     "scrollbar_gap", "footer_line_gap"]

    def test_all_sizes_have_required_keys(self):
        from ct.ui.theme.sizes import SIZES
        for name, s in SIZES.items():
            for key in self.REQUIRED_KEYS:
                self.assertIn(key, s, f"Size '{name}' missing key '{key}'")

    def test_all_size_values_are_numeric(self):
        from ct.ui.theme.sizes import SIZES
        for name, s in SIZES.items():
            for key, val in s.items():
                self.assertIsInstance(val, (int, float),
                                     f"Size '{name}' key '{key}' is not numeric: {val}")

    def test_all_size_values_non_negative(self):
        from ct.ui.theme.sizes import SIZES
        for name, s in SIZES.items():
            for key, val in s.items():
                self.assertGreaterEqual(val, 0,
                                        f"Size '{name}' key '{key}' is negative: {val}")

    def test_default_size_exists(self):
        from ct.ui.theme.sizes import SIZES
        self.assertIn("Regular", SIZES)

    def test_sizes_are_ordered_by_label(self):
        from ct.ui.theme.sizes import SIZES
        labels = [s["label"] for s in SIZES.values()]
        self.assertEqual(labels, sorted(labels))


class TestThemeFonts(unittest.TestCase):

    def test_fonts_is_nonempty_list(self):
        from ct.ui.theme.fonts import FONTS
        self.assertIsInstance(FONTS, list)
        self.assertGreater(len(FONTS), 0)

    def test_default_font_in_list(self):
        from ct.ui.theme.fonts import FONTS
        self.assertIn("Calibri", FONTS)

    def test_all_fonts_are_strings(self):
        from ct.ui.theme.fonts import FONTS
        for f in FONTS:
            self.assertIsInstance(f, str)
            self.assertGreater(len(f), 0)


# =========================================================================== #
#  7. STYLESHEET GENERATION                                                     #
# =========================================================================== #

class TestStylesheet(unittest.TestCase):
    """Tests for build_stylesheet and build_menu_stylesheet."""

    def test_build_stylesheet_returns_string(self):
        from ct.ui.theme.stylesheet import build_stylesheet
        result = build_stylesheet("E-Ink (Default)")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 100)

    def test_build_stylesheet_contains_widget_selectors(self):
        from ct.ui.theme.stylesheet import build_stylesheet
        result = build_stylesheet("Galaxy Dark")
        for selector in ["QMainWindow", "QLabel", "QPushButton", "QLineEdit",
                         "QComboBox", "QSpinBox", "QScrollBar", "QTableWidget"]:
            self.assertIn(selector, result, f"Missing selector: {selector}")

    def test_build_stylesheet_all_themes(self):
        from ct.ui.theme.stylesheet import build_stylesheet
        from ct.ui.theme.colors import THEMES
        for name in THEMES:
            result = build_stylesheet(name)
            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0, f"Empty stylesheet for {name}")

    def test_build_stylesheet_unknown_theme_falls_back(self):
        from ct.ui.theme.stylesheet import build_stylesheet
        result = build_stylesheet("NonexistentTheme")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_build_menu_stylesheet_returns_string(self):
        from ct.ui.theme.stylesheet import build_menu_stylesheet
        result = build_menu_stylesheet("E-Ink (Default)")
        self.assertIsInstance(result, str)
        self.assertIn("QMenu", result)

    def test_build_menu_stylesheet_all_themes(self):
        from ct.ui.theme.stylesheet import build_menu_stylesheet
        from ct.ui.theme.colors import THEMES
        for name in THEMES:
            result = build_menu_stylesheet(name)
            self.assertIn("QMenu", result)

    def test_build_menu_stylesheet_unknown_theme_falls_back(self):
        from ct.ui.theme.stylesheet import build_menu_stylesheet
        result = build_menu_stylesheet("BogusTheme")
        self.assertIn("QMenu", result)


# =========================================================================== #
#  8. THEME __init__ RE-EXPORTS                                                 #
# =========================================================================== #

class TestThemePackage(unittest.TestCase):
    """Verify ct.ui.theme re-exports everything."""

    def test_themes_reexported(self):
        from ct.ui.theme import THEMES
        self.assertIsInstance(THEMES, dict)

    def test_sizes_reexported(self):
        from ct.ui.theme import SIZES
        self.assertIsInstance(SIZES, dict)

    def test_build_stylesheet_reexported(self):
        from ct.ui.theme import build_stylesheet
        self.assertTrue(callable(build_stylesheet))

    def test_build_menu_stylesheet_reexported(self):
        from ct.ui.theme import build_menu_stylesheet
        self.assertTrue(callable(build_menu_stylesheet))


# =========================================================================== #
#  9. UTILITY FUNCTIONS                                                         #
# =========================================================================== #

class TestFormatTime(unittest.TestCase):
    """Tests for ct.util.format_time."""

    def test_zero(self):
        from ct.util import format_time
        self.assertEqual(format_time(0), "00:00:00")

    def test_seconds_only(self):
        from ct.util import format_time
        self.assertEqual(format_time(45), "00:00:45")

    def test_minutes_and_seconds(self):
        from ct.util import format_time
        self.assertEqual(format_time(125), "00:02:05")

    def test_hours(self):
        from ct.util import format_time
        self.assertEqual(format_time(3661), "01:01:01")

    def test_large_value(self):
        from ct.util import format_time
        result = format_time(86400)  # 24 hours
        self.assertEqual(result, "24:00:00")

    def test_negative_clamped_to_zero(self):
        from ct.util import format_time
        self.assertEqual(format_time(-100), "00:00:00")

    def test_float_truncated(self):
        from ct.util import format_time
        self.assertEqual(format_time(59.9), "00:00:59")

    def test_very_large(self):
        from ct.util import format_time
        result = format_time(360000)  # 100 hours
        self.assertEqual(result, "100:00:00")


class TestThemeRenames(unittest.TestCase):
    """Renamed themes survive the rename; retired ones still fall back."""

    def migrate(self, **settings):
        from ct.core.config import Settings
        base = Settings().to_dict()
        base.update(settings)
        return Settings.from_dict(base)

    def test_a_renamed_theme_follows_its_new_name(self):
        """T-Magentle shipped from 2026-02-14, so it is in real users'
        state.json. Without this they silently land on the default."""
        self.assertEqual(self.migrate(theme="T-Magentle").theme, "T-Magenta")

    def test_every_rename_target_actually_exists(self):
        """Renaming a theme twice and forgetting to update the map would
        migrate users onto a name that is gone — worse than not migrating,
        because it looks handled."""
        from ct.core.config import Settings
        from ct.ui.theme.colors import THEMES
        for old, new in Settings._THEME_RENAMES.items():
            with self.subTest(rename=f"{old} -> {new}"):
                self.assertIn(new, THEMES, f"'{new}' is not a real theme")
                self.assertNotIn(old, THEMES,
                                 f"'{old}' still exists — that is not a rename")

    def test_a_current_theme_is_untouched(self):
        self.assertEqual(self.migrate(theme="Galaxy Dark").theme, "Galaxy Dark")

    def test_a_retired_theme_is_left_alone(self):
        """Deliberately NOT mapped — it falls back at render time instead."""
        self.assertEqual(self.migrate(theme="Herizons").theme, "Herizons")

    def test_a_rename_and_the_legacy_button_key_migrate_together(self):
        """_migrate used to bail early when button_visibility was absent, so
        a theme rename on its own would have been skipped entirely."""
        from ct.core.config import Settings
        d = Settings().to_dict()
        d["theme"] = "T-Magentle"
        d.pop("show_adjust_buttons")
        d["button_visibility"] = "None"
        s = Settings.from_dict(d)
        self.assertEqual(s.theme, "T-Magenta")
        self.assertFalse(s.show_adjust_buttons)

    def test_migration_does_not_mutate_the_caller_dict(self):
        """AppState.load hands in the dict it parsed from disk."""
        from ct.core.config import Settings
        d = Settings().to_dict()
        d["theme"] = "T-Magentle"
        Settings.from_dict(d)
        self.assertEqual(d["theme"], "T-Magentle")


class TestCopyFormat(unittest.TestCase):
    """What a copy puts on the clipboard.

    The row on screen is always HH:MM:SS. This is only the copy, because a
    time is read on screen and pasted somewhere else, and those two want
    different things — the timesheet on the other end wants HH:MM.
    """

    def test_the_four_formats_of_five_fifteen(self):
        """5h15m, the example the whole feature was specified against."""
        from ct.util import format_copy_time
        secs = 5 * 3600 + 15 * 60
        self.assertEqual(format_copy_time(secs, "HH:MM"), "05:15")
        self.assertEqual(format_copy_time(secs, "HH:MM:SS"), "05:15:00")
        self.assertEqual(format_copy_time(secs, "Decimal"), "5.25")
        self.assertEqual(format_copy_time(secs, "Raw Minutes"), "315")

    def test_the_default_is_hh_mm(self):
        """The timesheet system on the other end expects it, and the seconds
        were being deleted by hand on every paste."""
        from ct.util import format_copy_time, DEFAULT_COPY_FORMAT
        from ct.core.config import Settings
        self.assertEqual(DEFAULT_COPY_FORMAT, "HH:MM")
        self.assertEqual(Settings().copy_format, "HH:MM")
        self.assertEqual(format_copy_time(3725), "01:02")

    def test_leftover_seconds_are_dropped_never_rounded_up(self):
        """A copied time must never exceed the one on screen — the app would
        be silently overstating billable time."""
        from ct.util import format_copy_time
        secs = 5 * 3600 + 15 * 60 + 59      # 05:15:59
        self.assertEqual(format_copy_time(secs, "HH:MM"), "05:15")
        self.assertEqual(format_copy_time(secs, "Raw Minutes"), "315")

    def test_hh_mm_and_raw_minutes_always_agree(self):
        """Both floor to the whole minute, so they can never disagree about
        which minute a time is in."""
        from ct.util import format_copy_time
        for secs in (0, 59, 60, 3599, 3600, 3725, 86399, 123456):
            with self.subTest(seconds=secs):
                hh, mm = format_copy_time(secs, "HH:MM").split(":")
                self.assertEqual(int(hh) * 60 + int(mm),
                                 int(format_copy_time(secs, "Raw Minutes")))

    def test_decimal_keeps_two_places(self):
        """The timesheet convention, and it carries the sub-minute precision
        the other formats drop."""
        from ct.util import format_copy_time
        self.assertEqual(format_copy_time(3600, "Decimal"), "1.00")
        self.assertEqual(format_copy_time(1800, "Decimal"), "0.50")
        self.assertEqual(format_copy_time(0, "Decimal"), "0.00")
        self.assertEqual(format_copy_time(5 * 3600 + 15 * 60 + 45, "Decimal"),
                         "5.26")

    def test_hours_are_never_wrapped_at_24(self):
        """A long-running timer must not silently restart at zero."""
        from ct.util import format_copy_time
        secs = 30 * 3600 + 5 * 60
        self.assertEqual(format_copy_time(secs, "HH:MM"), "30:05")
        self.assertEqual(format_copy_time(secs, "Raw Minutes"), "1805")

    def test_negative_and_zero_clamp(self):
        from ct.util import format_copy_time
        for fmt, zero in (("HH:MM", "00:00"), ("HH:MM:SS", "00:00:00"),
                          ("Decimal", "0.00"), ("Raw Minutes", "0")):
            with self.subTest(fmt=fmt):
                self.assertEqual(format_copy_time(-500, fmt), zero)
                self.assertEqual(format_copy_time(0, fmt), zero)

    def test_an_unknown_format_falls_back_instead_of_raising(self):
        """A hand-edited state.json must not be able to break copying."""
        from ct.util import format_copy_time
        for bad in ("", "hh:mm", "Nonsense", None, 7):
            with self.subTest(fmt=bad):
                self.assertEqual(format_copy_time(3725, bad), "01:02")

    def test_every_offered_option_is_implemented(self):
        """A name in the dropdown with no branch behind it would silently
        fall back to HH:MM, which looks like the setting being ignored."""
        from ct.util import format_copy_time, COPY_FORMATS
        secs = 5 * 3600 + 15 * 60
        produced = {fmt: format_copy_time(secs, fmt) for fmt in COPY_FORMATS}
        self.assertEqual(len(set(produced.values())), len(COPY_FORMATS),
                         f"two options produce the same string: {produced}")

    def test_the_setting_survives_a_save_load_round_trip(self):
        from ct.core.config import Settings
        for fmt in ("HH:MM", "HH:MM:SS", "Decimal", "Raw Minutes"):
            with self.subTest(fmt=fmt):
                s = Settings.from_dict(Settings(copy_format=fmt).to_dict())
                self.assertEqual(s.copy_format, fmt)

    def test_a_state_file_predating_the_setting_gets_the_default(self):
        from ct.core.config import Settings
        legacy = Settings().to_dict()
        legacy.pop("copy_format")
        self.assertEqual(Settings.from_dict(legacy).copy_format, "HH:MM")


class TestNowIso(unittest.TestCase):
    """Tests for ct.util.now_iso."""

    def test_returns_string(self):
        from ct.util import now_iso
        result = now_iso()
        self.assertIsInstance(result, str)

    def test_is_parseable_iso(self):
        from ct.util import now_iso
        result = now_iso()
        dt = datetime.fromisoformat(result)
        self.assertIsInstance(dt, datetime)

    def test_has_timezone(self):
        from ct.util import now_iso
        result = now_iso()
        dt = datetime.fromisoformat(result)
        self.assertIsNotNone(dt.tzinfo)

    def test_is_recent(self):
        from ct.util import now_iso
        before = datetime.now().astimezone()
        result = datetime.fromisoformat(now_iso())
        after = datetime.now().astimezone()
        self.assertGreaterEqual(result, before - timedelta(seconds=1))
        self.assertLessEqual(result, after + timedelta(seconds=1))


class TestSettingsDefaultsConsistency(unittest.TestCase):
    """Verify _SETTINGS_DEFAULTS, Settings dataclass, and _build_default_state
    are all in sync."""

    def test_defaults_dict_matches_dataclass(self):
        from ct.core.config import _SETTINGS_DEFAULTS, Settings
        s = Settings()
        for key, default_val in _SETTINGS_DEFAULTS.items():
            self.assertEqual(getattr(s, key), default_val,
                             f"Settings.{key} default doesn't match _SETTINGS_DEFAULTS")

    def test_build_default_state_has_all_settings(self):
        from ct.core.config import AppState, _SETTINGS_DEFAULTS
        state = AppState._build_default_state()
        for key in _SETTINGS_DEFAULTS:
            self.assertIn(key, state["settings"],
                          f"_build_default_state missing settings key: {key}")

    def test_default_theme_exists_in_themes(self):
        from ct.core.config import _SETTINGS_DEFAULTS
        from ct.ui.theme.colors import THEMES
        self.assertIn(_SETTINGS_DEFAULTS["theme"], THEMES)

    def test_default_size_exists_in_sizes(self):
        from ct.core.config import _SETTINGS_DEFAULTS
        from ct.ui.theme.sizes import SIZES
        self.assertIn(_SETTINGS_DEFAULTS["size"], SIZES)

    def test_default_font_exists_in_fonts(self):
        from ct.core.config import _SETTINGS_DEFAULTS
        from ct.ui.theme.fonts import FONTS
        self.assertIn(_SETTINGS_DEFAULTS["font"], FONTS)

    def test_all_settings_themes_exist(self):
        """Every theme referenced in settings must exist in THEMES."""
        from ct.ui.theme.colors import THEMES
        from ct.ui.theme.sizes import SIZES
        self.assertIn("E-Ink (Default)", THEMES)
        self.assertIn("Regular", SIZES)


# =========================================================================== #
#  11. CROSS-MODULE INTEGRATION                                                 #
# =========================================================================== #

class TestCrossModuleIntegration(StatePathMixin, unittest.TestCase):
    """Tests that verify modules work together correctly."""

    def test_timer_freeze_then_serialize(self):
        """Timer freeze + AppState serialize = correct elapsed."""
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState

        ts = TimerState("X", elapsed=0.0)
        ts.start()
        time.sleep(0.1)

        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        result = state._serialize({0: ts})

        elapsed = result["session"]["tracked_times"]["0"]["elapsed"]
        self.assertGreater(elapsed, 0.05)

    def test_settings_to_dict_to_stylesheet(self):
        """Settings.to_dict() theme value works with build_stylesheet()."""
        from ct.core.config import Settings
        from ct.ui.theme.stylesheet import build_stylesheet

        for theme in ["E-Ink (Default)", "Galaxy Dark", "Telecomm Blues"]:
            s = Settings(theme=theme)
            d = s.to_dict()
            result = build_stylesheet(d["theme"])
            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0)

    def test_full_save_load_cycle_with_timers(self):
        """Full save → load → verify cycle."""
        from ct.core.config import AppState, Settings, _STATE_PATH
        from ct.core.timer_state import TimerState

        # Build state with multiple timers
        rows = [
            {"rowid": 0, "name": "Group", "type": "separator", "bg": "#123456"},
            {"rowid": 1, "name": "Client1", "type": "timer", "bg": None},
            {"rowid": 2, "name": "Client2", "type": "timer", "bg": None},
            {"rowid": 3, "name": "Client3", "type": "timer", "bg": None},
        ]
        settings = Settings(
            theme="A Way", size="Compact", font="Consolas",
            daily_reset_enabled=True, daily_reset_time="17:30",
            show_adjust_buttons=False,
        )
        collapsed = {0}
        start = datetime.now().astimezone() - timedelta(hours=3)

        state = AppState(settings, rows, collapsed, start, {})
        timers = {
            1: TimerState("Client1", elapsed=1800.0),
            2: TimerState("Client2", elapsed=0.0),
            3: TimerState("Client3", elapsed=3600.0),
        }
        timers[1].start()  # Client1 is running

        state.save(timers)

        # Reload
        loaded = AppState.load(_STATE_PATH)
        self.assertEqual(len(loaded.rows), 4)
        self.assertEqual(loaded.rows[0]["bg"], "#123456")
        self.assertEqual(loaded.settings.theme, "A Way")
        self.assertEqual(loaded.settings.font, "Consolas")
        self.assertTrue(loaded.settings.daily_reset_enabled)
        self.assertEqual(loaded.settings.daily_reset_time, "17:30")
        self.assertFalse(loaded.settings.show_adjust_buttons)
        self.assertEqual(loaded.collapsed_groups, {0})
        self.assertIn("1", loaded.tracked_times)
        self.assertIn("running_since", loaded.tracked_times["1"])
        self.assertGreaterEqual(loaded.tracked_times["1"]["elapsed"], 1800.0)


# =========================================================================== #
#  12. PATHS AND SETUP                                                          #
# =========================================================================== #

class TestNameSanitizer(unittest.TestCase):
    """Tests for the client-name sanitizer (permissive denylist)."""

    def _sanitize(self, raw):
        from ct.ui.app import _SANITIZE
        return _SANITIZE.sub("", raw).strip()

    def test_real_world_names_untouched(self):
        for name in ("Müller & Sons - Tickets, LLC", "O'Brien (West)",
                     "AT&T", "Über-Client #2", "日本クライアント"):
            self.assertEqual(self._sanitize(name), name)

    def test_control_characters_stripped(self):
        self.assertEqual(self._sanitize("bad\x00name\x1f\x7f\x85"), "badname")

    def test_whitespace_only_becomes_empty(self):
        self.assertEqual(self._sanitize("   "), "")


class TestPaths(unittest.TestCase):
    """Tests for ct.common.setup.PATHS."""

    def test_paths_exist(self):
        from ct.common.setup import PATHS
        self.assertTrue(PATHS.data.exists())
        self.assertTrue(PATHS.logs.exists())
        self.assertTrue(PATHS.current.exists())
        self.assertTrue(PATHS.snapshots.exists())
        self.assertTrue(PATHS.sessions.exists())

    def test_paths_are_directories(self):
        from ct.common.setup import PATHS
        self.assertTrue(PATHS.data.is_dir())
        self.assertTrue(PATHS.logs.is_dir())
        self.assertTrue(PATHS.current.is_dir())
        self.assertTrue(PATHS.snapshots.is_dir())
        self.assertTrue(PATHS.sessions.is_dir())

    def test_root_contains_ct_package(self):
        from ct.common.setup import PATHS
        self.assertTrue((PATHS.root / "ct").is_dir())

    def test_assets_exists(self):
        from ct.common.setup import PATHS
        self.assertTrue(PATHS.assets.exists())


class TestEnsureDirectory(TempDirMixin, unittest.TestCase):
    """Tests for ensure_directory."""

    def test_creates_missing_directory(self):
        from ct.common.setup import ensure_directory
        p = self.tmp("new_dir")
        self.assertFalse(p.exists())
        ensure_directory(p)
        self.assertTrue(p.exists())
        self.assertTrue(p.is_dir())

    def test_creates_nested_directories(self):
        from ct.common.setup import ensure_directory
        p = self.tmp("a/b/c/d")
        ensure_directory(p)
        self.assertTrue(p.exists())

    def test_existing_directory_is_noop(self):
        from ct.common.setup import ensure_directory
        p = self.tmp("existing")
        p.mkdir()
        ensure_directory(p)  # should not raise
        self.assertTrue(p.exists())

    def test_must_exist_raises_on_missing(self):
        from ct.common.setup import ensure_directory
        p = self.tmp("nonexistent")
        with self.assertRaises(FileNotFoundError):
            ensure_directory(p, must_exist=True)

    def test_must_exist_passes_on_existing(self):
        from ct.common.setup import ensure_directory
        p = self.tmp("exists")
        p.mkdir()
        result = ensure_directory(p, must_exist=True)
        self.assertEqual(result, p)


# =========================================================================== #
#  13. EDGE CASES & STRESS                                                      #
# =========================================================================== #

class TestEdgeCases(StatePathMixin, unittest.TestCase):
    """Weird inputs, boundary conditions, and stress tests."""

    def test_timer_rapid_start_stop(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Rapid")
        for _ in range(100):
            ts.start()
            ts.stop()
        self.assertGreaterEqual(ts.elapsed, 0.0)
        self.assertFalse(ts.running)

    def test_timer_rapid_freeze(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("FreezeBurn")
        ts.start()
        for _ in range(50):
            ts.freeze()
        ts.stop()
        self.assertGreaterEqual(ts.elapsed, 0.0)

    def test_timer_adjust_spam(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Spam", elapsed=1000.0)
        for _ in range(100):
            ts.adjust(-10)
        self.assertAlmostEqual(ts.elapsed, 0.0)

    def test_settings_from_dict_with_wrong_types(self):
        """Settings.from_dict coerces or defaults mistyped values so a
        hand-edited state.json can never poison runtime behavior."""
        from ct.core.config import Settings
        s = Settings.from_dict({"theme": 12345, "size": None})
        self.assertEqual(s.theme, "E-Ink (Default)")
        self.assertEqual(s.size, "Regular")

    def test_settings_coerces_int_bools(self):
        # Hand-editors write 1/0 for booleans; honor the intent.
        from ct.core.config import Settings
        s = Settings.from_dict({"confirm_delete": 0, "always_on_top": 1})
        self.assertIs(s.confirm_delete, False)
        self.assertIs(s.always_on_top, True)

    def test_settings_coerces_string_bools(self):
        from ct.core.config import Settings
        s = Settings.from_dict({"confirm_delete": "false", "confirm_reset": "True"})
        self.assertIs(s.confirm_delete, False)
        self.assertIs(s.confirm_reset, True)

    def test_settings_coerces_numeric_string_int(self):
        """Against _coerce_setting directly: Settings currently has no int
        field (snapshot_min_minutes was the last, and it was removed), but
        the int branch still has to work for the next one."""
        from ct.core.config import _coerce_setting
        self.assertEqual(_coerce_setting("n", "30", 5), (30, True))
        self.assertEqual(_coerce_setting("n", 7, 5), (7, False))
        self.assertEqual(_coerce_setting("n", 2.9, 5), (2, True))

    def test_settings_bool_for_int_field_defaults(self):
        # JSON `true` is not a sensible number — reset to the default.
        from ct.core.config import _coerce_setting
        self.assertEqual(_coerce_setting("n", True, 5), (5, True))

    def test_settings_uninterpretable_values_default(self):
        from ct.core.config import Settings
        s = Settings.from_dict({
            "confirm_delete": "maybe",
            "daily_reset_time": 300,
        })
        self.assertIs(s.confirm_delete, True)
        self.assertEqual(s.daily_reset_time, "03:00")

    def test_settings_unknown_string_values_preserved(self):
        # Type-check only — never domain-check. A theme from another app
        # version must survive a load/save round trip untouched.
        from ct.core.config import Settings
        s = Settings.from_dict({"theme": "Some Future Theme"})
        self.assertEqual(s.theme, "Some Future Theme")

    def test_serialize_empty_timers(self):
        from ct.core.config import AppState, Settings
        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        result = state._serialize({})
        self.assertEqual(result["session"]["tracked_times"], {})

    def test_serialize_many_timers(self):
        from ct.core.config import AppState, Settings
        from ct.core.timer_state import TimerState
        state = AppState(Settings(), [], set(), datetime.now().astimezone(), {})
        timers = {i: TimerState(f"T{i}", elapsed=float(i * 100)) for i in range(50)}
        result = state._serialize(timers)
        self.assertEqual(len(result["session"]["tracked_times"]), 50)

    def test_format_time_huge_number(self):
        from ct.util import format_time
        result = format_time(999999)
        self.assertIn(":", result)
        parts = result.split(":")
        self.assertEqual(len(parts), 3)

    def test_many_rows_save_load(self):
        from ct.core.config import AppState, Settings, _STATE_PATH
        rows = [{"rowid": i, "name": f"Client{i}", "type": "timer", "bg": None}
                for i in range(200)]
        state = AppState(Settings(), rows, set(), datetime.now().astimezone(), {})
        state.save({})
        loaded = AppState.load(_STATE_PATH)
        self.assertEqual(len(loaded.rows), 200)

    def test_unicode_client_names(self):
        from ct.core.config import AppState, Settings, _STATE_PATH
        from ct.core.timer_state import TimerState
        rows = [
            {"rowid": 0, "name": "日本語クライアント", "type": "timer", "bg": None},
            {"rowid": 1, "name": "Ünïcödé Çlient", "type": "timer", "bg": None},
            {"rowid": 2, "name": "🎉 Emoji Client", "type": "timer", "bg": None},
        ]
        timers = {i: TimerState(rows[i]["name"], elapsed=float(i * 60))
                  for i in range(3)}
        state = AppState(Settings(), rows, set(), datetime.now().astimezone(), {})
        state.save(timers)
        loaded = AppState.load(_STATE_PATH)
        self.assertEqual(loaded.rows[0]["name"], "日本語クライアント")
        self.assertEqual(loaded.rows[2]["name"], "🎉 Emoji Client")

    def test_special_chars_in_names(self):
        from ct.core.config import AppState, Settings, _STATE_PATH
        rows = [
            {"rowid": 0, "name": 'He said "hello"', "type": "timer", "bg": None},
            {"rowid": 1, "name": "Path\\To\\Thing", "type": "timer", "bg": None},
            {"rowid": 2, "name": "Line\nBreak", "type": "timer", "bg": None},
        ]
        state = AppState(Settings(), rows, set(), datetime.now().astimezone(), {})
        state.save({})
        loaded = AppState.load(_STATE_PATH)
        self.assertEqual(loaded.rows[0]["name"], 'He said "hello"')
        self.assertEqual(loaded.rows[1]["name"], "Path\\To\\Thing")


# =========================================================================== #
#  Runner                                                                       #
# =========================================================================== #

if __name__ == "__main__":
    import sys
    # Ensure project root is on path so ct imports work
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Discover all test classes in this module
    import tests.test_ct2 as this_module
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(this_module)

    # Count tests
    def count_tests(s):
        count = 0
        for test in s:
            if isinstance(test, unittest.TestSuite):
                count += count_tests(test)
            else:
                count += 1
        return count

    total = count_tests(suite)
    print(f"\n{'='*70}")
    print(f"  ClientTimer2 Test Suite — {total} tests")
    print(f"{'='*70}\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print(f"\n{'='*70}")
    passed = total - len(result.failures) - len(result.errors)
    print(f"  PASSED: {passed}/{total}")
    if result.failures:
        print(f"  FAILED: {len(result.failures)}")
        for test, _ in result.failures:
            print(f"    - {test}")
    if result.errors:
        print(f"  ERRORS: {len(result.errors)}")
        for test, _ in result.errors:
            print(f"    - {test}")
    if not result.failures and not result.errors:
        print("  ALL TESTS PASSED!")
    print(f"{'='*70}\n")
