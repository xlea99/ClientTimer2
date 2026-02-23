"""Comprehensive tests for the Save Update v1 — unified state.json system.

Covers: ct.core.config, ct.core.timer_state, ct.core.snapshot
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


# ──────────────────────────────────────────────────────────────────────────
# config.py tests
# ──────────────────────────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    """Tests for the unified state management in config.py."""

    def setUp(self):
        self.tmpdir  = tempfile.mkdtemp()
        self._tmppath = Path(self.tmpdir)

        # Monkey-patch config paths to use temp dir
        from ct.core import config
        self._orig_state_path     = config.STATE_PATH
        self._orig_snapshot_dir   = config.SNAPSHOT_DIR
        self._orig_completed_dir  = config.COMPLETED_DIR
        self._orig_old_config     = config._OLD_CONFIG

        config.STATE_PATH    = self._tmppath / "state.json"
        config.SNAPSHOT_DIR  = self._tmppath / "snapshots"
        config.COMPLETED_DIR = self._tmppath / "completed_sessions"
        config._OLD_CONFIG   = self._tmppath / "config.txt"

    def tearDown(self):
        from ct.core import config
        config.STATE_PATH    = self._orig_state_path
        config.SNAPSHOT_DIR  = self._orig_snapshot_dir
        config.COMPLETED_DIR = self._orig_completed_dir
        config._OLD_CONFIG   = self._orig_old_config
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fresh_start_returns_default_state(self):
        """No existing files → fresh default state."""
        from ct.core.config import load_state
        state = load_state()
        self.assertEqual(state["meta"]["schema_version"], 1)
        self.assertFalse(state["meta"]["is_completed_session"])
        self.assertEqual(state["layout"]["rows"], [])
        self.assertEqual(state["settings"]["theme"], "Cupertino Light")
        self.assertEqual(state["settings"]["snapshot_min_minutes"], 5)
        self.assertIn("session", state)
        self.assertIn("start", state["session"])

    def test_fresh_start_creates_state_file(self):
        """load_state() on fresh start writes state.json to disk."""
        from ct.core import config
        config.load_state()
        self.assertTrue(os.path.exists(config.STATE_PATH))

    def test_save_and_load_roundtrip(self):
        """Saved state is faithfully loaded back."""
        from ct.core.config import load_state, save_state, STATE_PATH

        state = load_state()
        state["layout"]["rows"] = [
            {"rowid": 0, "name": "ACME", "type": "timer", "bg": None},
            {"rowid": 1, "name": "Ops", "type": "separator", "bg": "#ff0000"},
        ]
        state["session"]["tracked_times"] = {
            "0": {"elapsed": 123.4}
        }
        state["settings"]["theme"] = "Galaxy Dark"
        save_state(state)

        loaded = load_state()
        self.assertEqual(len(loaded["layout"]["rows"]), 2)
        self.assertEqual(loaded["layout"]["rows"][0]["name"], "ACME")
        self.assertEqual(loaded["layout"]["rows"][1]["bg"], "#ff0000")
        self.assertAlmostEqual(
            loaded["session"]["tracked_times"]["0"]["elapsed"], 123.4)
        self.assertEqual(loaded["settings"]["theme"], "Galaxy Dark")

    def test_save_updates_saved_at(self):
        """save_state() always refreshes meta.saved_at."""
        from ct.core.config import load_state, save_state

        state = load_state()
        old_ts = state["meta"]["saved_at"]
        time.sleep(0.05)
        save_state(state)
        new_ts = state["meta"]["saved_at"]
        self.assertNotEqual(old_ts, new_ts)

    def test_load_fills_missing_settings_defaults(self):
        """If state.json is missing some settings keys, they get filled."""
        from ct.core import config

        minimal = {
            "meta": {"schema_version": 1, "saved_at": "x"},
            "layout": {"rows": [], "collapsed_groups": []},
            "settings": {"theme": "Galaxy Dark"},  # missing most keys
            "session": {"start": "x", "tracked_times": {}},
        }
        with open(config.STATE_PATH, "w") as f:
            json.dump(minimal, f)

        loaded = config.load_state()
        self.assertEqual(loaded["settings"]["theme"], "Galaxy Dark")
        self.assertEqual(loaded["settings"]["snapshot_min_minutes"], 5)
        self.assertEqual(loaded["settings"]["font"], "Calibri")
        self.assertTrue(loaded["settings"]["always_on_top"])

    def test_load_fills_missing_session(self):
        """If state.json has no session key, one is created."""
        from ct.core import config

        minimal = {
            "meta": {"schema_version": 1, "saved_at": "x"},
            "layout": {"rows": [], "collapsed_groups": []},
            "settings": {},
        }
        with open(config.STATE_PATH, "w") as f:
            json.dump(minimal, f)

        loaded = config.load_state()
        self.assertIn("session", loaded)
        self.assertIn("start", loaded["session"])
        self.assertIn("tracked_times", loaded["session"])

    def test_corrupted_state_json_triggers_migration_or_fresh(self):
        """Corrupted state.json falls through to migration or fresh start."""
        from ct.core import config

        with open(config.STATE_PATH, "w") as f:
            f.write("{invalid json!!")

        state = config.load_state()
        self.assertEqual(state["meta"]["schema_version"], 1)
        self.assertEqual(state["layout"]["rows"], [])

    def test_now_iso_returns_aware_datetime(self):
        """now_iso() includes timezone offset."""
        from ct.core.config import now_iso
        iso    = now_iso()
        parsed = datetime.fromisoformat(iso)
        self.assertIsNotNone(parsed.tzinfo)


# ──────────────────────────────────────────────────────────────────────────
# Settings dataclass tests
# ──────────────────────────────────────────────────────────────────────────

class TestSettings(unittest.TestCase):
    """Tests for the Settings dataclass."""

    def test_defaults(self):
        from ct.core.config import Settings
        s = Settings()
        self.assertEqual(s.theme, "Cupertino Light")
        self.assertEqual(s.size, "Regular")
        self.assertEqual(s.font, "Calibri")
        self.assertTrue(s.always_on_top)
        self.assertEqual(s.snapshot_min_minutes, 5)

    def test_from_dict_full(self):
        from ct.core.config import Settings
        d = {
            "theme": "Galaxy Dark", "size": "Large", "font": "Arial",
            "label_align": "Right", "client_separators": False,
            "show_group_count": False, "show_group_time": False,
            "always_on_top": False, "confirm_delete": False,
            "confirm_reset": False, "daily_reset_enabled": True,
            "daily_reset_time": "08:00", "snapshot_min_minutes": 10,
        }
        s = Settings.from_dict(d)
        self.assertEqual(s.theme, "Galaxy Dark")
        self.assertEqual(s.size, "Large")
        self.assertFalse(s.always_on_top)
        self.assertEqual(s.snapshot_min_minutes, 10)

    def test_from_dict_partial_fills_defaults(self):
        from ct.core.config import Settings
        s = Settings.from_dict({"theme": "Galaxy Dark"})
        self.assertEqual(s.theme, "Galaxy Dark")
        self.assertEqual(s.size, "Regular")   # defaulted
        self.assertTrue(s.always_on_top)      # defaulted

    def test_to_dict_roundtrip(self):
        from ct.core.config import Settings
        s = Settings(theme="Galaxy Dark", size="Large", snapshot_min_minutes=15)
        d = s.to_dict()
        self.assertEqual(d["theme"], "Galaxy Dark")
        self.assertEqual(d["size"], "Large")
        self.assertEqual(d["snapshot_min_minutes"], 15)
        s2 = Settings.from_dict(d)
        self.assertEqual(s2.theme, s.theme)
        self.assertEqual(s2.size, s.size)
        self.assertEqual(s2.snapshot_min_minutes, s.snapshot_min_minutes)


# ──────────────────────────────────────────────────────────────────────────
# timer_state.py tests
# ──────────────────────────────────────────────────────────────────────────

class TestTimerState(unittest.TestCase):
    """Tests for TimerState including running_since recovery."""

    def test_basic_start_stop(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test")
        self.assertFalse(ts.running)
        self.assertEqual(ts.elapsed, 0.0)

        ts.start()
        self.assertTrue(ts.running)
        self.assertIsNotNone(ts.started_at)
        time.sleep(0.05)
        ts.stop()
        self.assertFalse(ts.running)
        self.assertGreater(ts.elapsed, 0.0)
        self.assertIsNone(ts.started_at)

    def test_started_at_set_on_start(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test")
        before = datetime.now().astimezone()
        ts.start()
        after = datetime.now().astimezone()
        self.assertIsNotNone(ts.started_at)
        self.assertGreaterEqual(ts.started_at, before)
        self.assertLessEqual(ts.started_at, after)

    def test_started_at_preserved_on_restart(self):
        """Calling start() when already running doesn't reset started_at."""
        from ct.core.timer_state import TimerState
        ts = TimerState("Test")
        ts.start()
        original_started = ts.started_at
        time.sleep(0.02)
        ts.start()  # no-op (already running)
        self.assertEqual(ts.started_at, original_started)

    def test_running_since_recovery(self):
        """Timer started with running_since resumes running immediately."""
        from ct.core.timer_state import TimerState
        ten_secs_ago = (datetime.now().astimezone()
                        - timedelta(seconds=10)).isoformat()
        ts = TimerState("Test", elapsed=100.0, running_since=ten_secs_ago)
        self.assertTrue(ts.running)
        self.assertIsNotNone(ts.started_at)
        self.assertGreaterEqual(ts.current_elapsed, 100.0)
        time.sleep(0.05)
        self.assertGreater(ts.current_elapsed, 100.0)

    def test_running_since_preserves_started_at(self):
        """running_since sets started_at to the original wall-clock time."""
        from ct.core.timer_state import TimerState
        iso = "2026-02-12T14:30:00-05:00"
        ts  = TimerState("Test", running_since=iso)
        self.assertEqual(ts.started_at, datetime.fromisoformat(iso))

    def test_freeze_keeps_running(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test")
        ts.start()
        time.sleep(0.05)
        ts.freeze()
        self.assertTrue(ts.running)
        self.assertGreater(ts.elapsed, 0.0)
        self.assertIsNotNone(ts.started_at)

    def test_reset_clears_everything(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test", elapsed=500.0)
        ts.start()
        ts.reset()
        self.assertFalse(ts.running)
        self.assertEqual(ts.elapsed, 0.0)
        self.assertIsNone(ts.started_at)
        self.assertIsNone(ts._mono)

    def test_adjust_positive(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test", elapsed=100.0)
        ts.adjust(300)
        self.assertAlmostEqual(ts.elapsed, 400.0, places=1)

    def test_adjust_negative_clamps_to_zero(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test", elapsed=10.0)
        ts.adjust(-9999)
        self.assertEqual(ts.elapsed, 0.0)

    def test_current_elapsed_while_stopped(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test", elapsed=42.0)
        self.assertEqual(ts.current_elapsed, 42.0)

    def test_current_elapsed_while_running(self):
        from ct.core.timer_state import TimerState
        ts = TimerState("Test", elapsed=100.0)
        ts.start()
        time.sleep(0.05)
        self.assertGreater(ts.current_elapsed, 100.0)


# ──────────────────────────────────────────────────────────────────────────
# snapshot.py tests
# ──────────────────────────────────────────────────────────────────────────

class TestSnapshot(unittest.TestCase):
    """Tests for snapshot creation and pruning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Redirect PATHS.snapshots to tmpdir so snapshots land there
        from ct.common.setup import PATHS
        self._orig_snapshots = PATHS.snapshots
        PATHS.snapshots = Path(self.tmpdir)

    def tearDown(self):
        from ct.common.setup import PATHS
        PATHS.snapshots = self._orig_snapshots
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _sample_state(self):
        return {
            "meta": {"schema_version": 1, "saved_at": "x"},
            "layout": {"rows": [{"rowid": 0, "name": "A", "type": "timer",
                                  "bg": None}]},
            "settings": {"theme": "Galaxy Dark"},
            "session": {"start": "x", "tracked_times": {"0": {"elapsed": 5}}},
        }

    def test_create_snapshot_writes_file(self):
        from ct.core.snapshot import create_snapshot
        path = create_snapshot(self._sample_state(), "test")
        self.assertTrue(os.path.exists(path))

    def test_create_snapshot_content(self):
        from ct.core.snapshot import create_snapshot
        path = create_snapshot(self._sample_state(), "layout_change", "high")
        with open(path) as f:
            snap = json.load(f)
        self.assertEqual(snap["meta"]["snapshot_reason"], "layout_change")
        self.assertEqual(snap["meta"]["snapshot_priority"], "high")
        self.assertEqual(snap["layout"]["rows"][0]["name"], "A")

    def test_create_snapshot_does_not_mutate_original(self):
        from ct.core.snapshot import create_snapshot
        state = self._sample_state()
        create_snapshot(state, "test")
        self.assertNotIn("snapshot_reason", state["meta"])

    def test_snapshot_filename_format(self):
        from ct.core.snapshot import create_snapshot
        path  = create_snapshot(self._sample_state(), "test")
        fname = os.path.basename(path)
        self.assertTrue(fname.startswith("state_"))
        self.assertTrue(fname.endswith(".json"))

    def test_parse_snapshot_time(self):
        from ct.core.snapshot import _parse_snapshot_time
        dt = _parse_snapshot_time("state_20260212_143011_123456.json")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 2)
        self.assertEqual(dt.day, 12)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)
        self.assertEqual(dt.second, 11)

    def test_parse_snapshot_time_bad_name(self):
        from ct.core.snapshot import _parse_snapshot_time
        self.assertIsNone(_parse_snapshot_time("garbage.json"))
        self.assertIsNone(_parse_snapshot_time("state_notadate.json"))

    def test_prune_keeps_newest(self):
        """Pruning always preserves the most recent snapshot."""
        from ct.core.snapshot import create_snapshot, prune_snapshots

        for _ in range(20):
            create_snapshot(self._sample_state(), "test")
            time.sleep(0.01)

        prune_snapshots()

        remaining = [f for f in os.listdir(self.tmpdir)
                     if f.startswith("state_")]
        self.assertGreater(len(remaining), 0)
        self.assertLessEqual(len(remaining), len(_snapshot_tiers()) + 1)

    def test_prune_single_snapshot_safe(self):
        """Pruning with only one snapshot does nothing."""
        from ct.core.snapshot import create_snapshot, prune_snapshots

        create_snapshot(self._sample_state(), "test")
        prune_snapshots()
        remaining = os.listdir(self.tmpdir)
        self.assertEqual(len(remaining), 1)

    def test_prune_empty_dir_safe(self):
        """Pruning an empty directory doesn't crash."""
        from ct.core.snapshot import prune_snapshots
        prune_snapshots()  # should not raise


    def test_prune_ignores_non_snapshot_files(self):
        """Files that don't match the snapshot pattern are left alone."""
        from ct.core.snapshot import create_snapshot, prune_snapshots

        other = os.path.join(self.tmpdir, "notes.txt")
        with open(other, "w") as f:
            f.write("hello")

        for _ in range(15):
            create_snapshot(self._sample_state(), "test")
            time.sleep(0.01)

        prune_snapshots()
        self.assertTrue(os.path.exists(other))

    def test_snapshot_tiered_retention(self):
        """Create snapshots across a simulated time range and verify tier logic."""
        from ct.core.snapshot import prune_snapshots, TIERS

        now = datetime.now()
        offsets_minutes = [
            0, 1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 60, 120, 360,
            720, 1440, 2880, 4320, 5760,
        ]
        for offset in offsets_minutes:
            ts    = now - timedelta(minutes=offset)
            fname = f"state_{ts.strftime('%Y%m%d_%H%M%S_%f')}.json"
            with open(os.path.join(self.tmpdir, fname), "w") as f:
                json.dump({"meta": {}}, f)

        prune_snapshots()

        remaining = [f for f in os.listdir(self.tmpdir)
                     if f.startswith("state_")]
        self.assertLessEqual(len(remaining), len(TIERS) + 1)
        self.assertGreaterEqual(len(remaining), 2)


def _snapshot_tiers():
    from ct.core.snapshot import TIERS
    return TIERS


# ──────────────────────────────────────────────────────────────────────────
# Integration tests
# ──────────────────────────────────────────────────────────────────────────

class TestIntegration(unittest.TestCase):
    """End-to-end tests: state round-trips through config + snapshot."""

    def setUp(self):
        self.tmpdir  = tempfile.mkdtemp()
        self._tmppath = Path(self.tmpdir)

        from ct.core import config
        from ct.common.setup import PATHS

        self._orig_state_path    = config.STATE_PATH
        self._orig_snapshot_dir  = config.SNAPSHOT_DIR
        self._orig_completed_dir = config.COMPLETED_DIR
        self._orig_old_config    = config._OLD_CONFIG
        self._orig_paths_snaps   = PATHS.snapshots

        config.STATE_PATH    = self._tmppath / "state.json"
        config.SNAPSHOT_DIR  = self._tmppath / "snapshots"
        config.COMPLETED_DIR = self._tmppath / "completed_sessions"
        config._OLD_CONFIG   = self._tmppath / "config.txt"
        PATHS.snapshots      = self._tmppath / "snapshots"
        PATHS.snapshots.mkdir(exist_ok=True)

    def tearDown(self):
        from ct.core import config
        from ct.common.setup import PATHS
        config.STATE_PATH    = self._orig_state_path
        config.SNAPSHOT_DIR  = self._orig_snapshot_dir
        config.COMPLETED_DIR = self._orig_completed_dir
        config._OLD_CONFIG   = self._orig_old_config
        PATHS.snapshots      = self._orig_paths_snaps
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_lifecycle(self):
        """Simulate: fresh start → add clients → save → snapshot → reload."""
        from ct.core.config import load_state, save_state, now_iso
        from ct.core.snapshot import create_snapshot, prune_snapshots
        from ct.core.timer_state import TimerState

        state = load_state()
        self.assertEqual(state["layout"]["rows"], [])

        rows = [
            {"rowid": 0, "name": "Sysco",   "type": "separator", "bg": None},
            {"rowid": 1, "name": "Calls",   "type": "timer",     "bg": None},
            {"rowid": 2, "name": "Tickets", "type": "timer",     "bg": None},
        ]
        state["layout"]["rows"] = rows

        ts = TimerState("Calls", elapsed=0.0)
        ts.start()
        time.sleep(0.05)
        ts.freeze()
        state["session"]["tracked_times"]["1"] = {
            "elapsed": ts.elapsed,
            "running_since": ts.started_at.isoformat(),
        }
        state["session"]["tracked_times"]["2"] = {"elapsed": 300.0}

        save_state(state)

        snap_path = create_snapshot(state, "layout_change")
        self.assertTrue(os.path.exists(snap_path))

        loaded = load_state()
        self.assertEqual(len(loaded["layout"]["rows"]), 3)
        tt = loaded["session"]["tracked_times"]
        self.assertIn("running_since", tt["1"])
        self.assertAlmostEqual(tt["2"]["elapsed"], 300.0)

        ts2 = TimerState(
            "Calls",
            elapsed=tt["1"]["elapsed"],
            running_since=tt["1"]["running_since"],
        )
        self.assertTrue(ts2.running)
        self.assertGreater(ts2.current_elapsed, 0.0)

    def test_snapshot_then_prune_cycle(self):
        """Create several snapshots and prune — verify no crash and files remain."""
        from ct.core.config import load_state
        from ct.core.snapshot import create_snapshot, prune_snapshots

        state = load_state()
        for i in range(10):
            state["meta"]["saved_at"] = f"iter_{i}"
            create_snapshot(state, f"test_{i}")
            time.sleep(0.01)

        prune_snapshots()
        from ct.common.setup import PATHS
        remaining = os.listdir(PATHS.snapshots)
        self.assertGreater(len(remaining), 0)

    def test_save_completed_session(self):
        """save_completed_session writes a self-contained finalized file."""
        from ct.core.config import load_state, save_completed_session, COMPLETED_DIR
        import os
        os.makedirs(COMPLETED_DIR, exist_ok=True)

        state = load_state()
        state["layout"]["rows"] = [
            {"rowid": 0, "name": "A", "type": "timer", "bg": None},
        ]
        state["session"]["tracked_times"] = {"0": {"elapsed": 500.0}}

        boundary = datetime.now().astimezone()
        path     = save_completed_session(state, boundary)

        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.basename(path).startswith("session_"))

        with open(path) as f:
            data = json.load(f)

        self.assertIn("meta", data)
        self.assertIn("layout", data)
        self.assertIn("settings", data)
        self.assertIn("session", data)

        self.assertTrue(data["meta"]["is_completed_session"])
        self.assertEqual(data["session"]["end"], boundary.isoformat())

        self.assertEqual(len(data["layout"]["rows"]), 1)
        self.assertAlmostEqual(
            data["session"]["tracked_times"]["0"]["elapsed"], 500.0)

    def test_completed_session_does_not_mutate_source(self):
        """save_completed_session deep-copies — original state unchanged."""
        from ct.core.config import load_state, save_completed_session, COMPLETED_DIR
        import os
        os.makedirs(COMPLETED_DIR, exist_ok=True)

        state    = load_state()
        boundary = datetime.now().astimezone()
        save_completed_session(state, boundary)

        self.assertFalse(state["meta"]["is_completed_session"])
        self.assertNotIn("end", state["session"])

    def test_completed_session_dir_created(self):
        """completed_sessions/ dir is created automatically if missing."""
        from ct.core import config

        completed_dir        = self._tmppath / "completed_sessions"
        config.COMPLETED_DIR = completed_dir
        # Don't pre-create it — save_completed_session should do that

        state    = config.load_state()
        boundary = datetime.now().astimezone()
        config.save_completed_session(state, boundary)

        self.assertTrue(os.path.isdir(completed_dir))
        files = os.listdir(completed_dir)
        self.assertEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
