"""Comprehensive tests for the Save Update v1 — unified state.json system.

Covers: ct.core.config, ct.core.state, ct.core.snapshot
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
        self.tmpdir = tempfile.mkdtemp()
        self._tmppath = Path(self.tmpdir)

        # Monkey-patch config paths to use temp dir
        from ct.core import config
        self._orig_state_path = config.STATE_PATH
        self._orig_snapshot_dir = config.SNAPSHOT_DIR
        self._orig_completed_dir = config.COMPLETED_DIR
        self._orig_old_config = config._OLD_CONFIG_PATH
        self._orig_old_save = config._OLD_SAVE_PATH

        config.STATE_PATH = self._tmppath / "state.json"
        config.SNAPSHOT_DIR = self._tmppath / "snapshots"
        config.COMPLETED_DIR = self._tmppath / "completed_sessions"
        config._OLD_CONFIG_PATH = self._tmppath / "config.json"
        config._OLD_SAVE_PATH = self._tmppath / "recent_save.json"

    def tearDown(self):
        from ct.core import config
        config.STATE_PATH = self._orig_state_path
        config.SNAPSHOT_DIR = self._orig_snapshot_dir
        config.COMPLETED_DIR = self._orig_completed_dir
        config._OLD_CONFIG_PATH = self._orig_old_config
        config._OLD_SAVE_PATH = self._orig_old_save
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

        # Write a state.json with incomplete settings
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
        # Should get a valid default state
        self.assertEqual(state["meta"]["schema_version"], 1)
        self.assertEqual(state["layout"]["rows"], [])

    # ── Migration tests ──

    def test_migrate_from_old_config_only(self):
        """Old config.json without recent_save.json → migrated state."""
        from ct.core import config

        old_cfg = {
            "theme": "Galaxy Dark",
            "size": "Large",
            "clients": [
                {"rowid": 0, "name": "Sysco", "type": "separator", "bg": None},
                {"rowid": 1, "name": "Calls", "type": "timer", "bg": None},
            ],
            "collapsed_groups": [],
            "backup_frequency": 10,
            "always_on_top": False,
        }
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        state = config.load_state()
        self.assertEqual(state["settings"]["theme"], "Galaxy Dark")
        self.assertEqual(state["settings"]["size"], "Large")
        self.assertFalse(state["settings"]["always_on_top"])
        # backup_frequency → snapshot_min_minutes
        self.assertEqual(state["settings"]["snapshot_min_minutes"], 10)
        self.assertEqual(len(state["layout"]["rows"]), 2)
        self.assertEqual(state["layout"]["rows"][0]["type"], "separator")

    def test_migrate_old_string_clients(self):
        """Old #-prefixed string clients → proper row dicts."""
        from ct.core import config

        old_cfg = {
            "clients": ["#Sysco", "Sysco Calls", "Sysco Tickets"],
        }
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        state = config.load_state()
        rows = state["layout"]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["type"], "separator")
        self.assertEqual(rows[0]["name"], "Sysco")
        self.assertEqual(rows[1]["type"], "timer")
        self.assertEqual(rows[1]["name"], "Sysco Calls")

    def test_migrate_old_string_clients_with_times(self):
        """Old name-based times in recent_save.json map to rowids."""
        from ct.core import config

        old_cfg = {
            "clients": ["#Ops", "Alice", "Bob"],
        }
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        old_save = {
            "date": date.today().isoformat(),
            "clients": {"Alice": 300.0, "Bob": 600.0},
        }
        with open(config._OLD_SAVE_PATH, "w") as f:
            json.dump(old_save, f)

        state = config.load_state()
        tt = state["session"]["tracked_times"]
        # Alice is rowid 1, Bob is rowid 2
        self.assertAlmostEqual(tt["1"]["elapsed"], 300.0)
        self.assertAlmostEqual(tt["2"]["elapsed"], 600.0)

    def test_migrate_stale_save_ignored(self):
        """Old recent_save.json from a different day is ignored."""
        from ct.core import config

        old_cfg = {"clients": ["Timer1"]}
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        old_save = {
            "date": "2020-01-01",  # ancient date
            "clients": {"Timer1": 9999.0},
        }
        with open(config._OLD_SAVE_PATH, "w") as f:
            json.dump(old_save, f)

        state = config.load_state()
        tt = state["session"]["tracked_times"]
        self.assertAlmostEqual(tt["0"]["elapsed"], 0.0)

    def test_migrate_collapsed_string_groups(self):
        """Old #-name collapsed_groups → rowid-based."""
        from ct.core import config

        old_cfg = {
            "clients": ["#Sysco", "Calls", "#ACME", "Tickets"],
            "collapsed_groups": ["#Sysco"],
        }
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        state = config.load_state()
        collapsed = state["layout"]["collapsed_groups"]
        # Sysco is rowid 0
        self.assertIn(0, collapsed)
        # ACME (rowid 2) is NOT collapsed
        self.assertNotIn(2, collapsed)

    def test_now_iso_returns_aware_datetime(self):
        """now_iso() includes timezone offset."""
        from ct.core.config import now_iso
        iso = now_iso()
        parsed = datetime.fromisoformat(iso)
        self.assertIsNotNone(parsed.tzinfo)


# ──────────────────────────────────────────────────────────────────────────
# state.py tests
# ──────────────────────────────────────────────────────────────────────────

class TestClientState(unittest.TestCase):
    """Tests for ClientState including running_since recovery."""

    def test_basic_start_stop(self):
        from ct.core.state import ClientState
        cs = ClientState("Test")
        self.assertFalse(cs.running)
        self.assertEqual(cs.elapsed, 0.0)

        cs.start()
        self.assertTrue(cs.running)
        self.assertIsNotNone(cs.started_at)
        time.sleep(0.05)
        cs.stop()
        self.assertFalse(cs.running)
        self.assertGreater(cs.elapsed, 0.0)
        self.assertIsNone(cs.started_at)

    def test_started_at_set_on_start(self):
        from ct.core.state import ClientState
        cs = ClientState("Test")
        before = datetime.now().astimezone()
        cs.start()
        after = datetime.now().astimezone()
        self.assertIsNotNone(cs.started_at)
        self.assertGreaterEqual(cs.started_at, before)
        self.assertLessEqual(cs.started_at, after)

    def test_started_at_preserved_on_restart(self):
        """Calling start() when already running doesn't reset started_at."""
        from ct.core.state import ClientState
        cs = ClientState("Test")
        cs.start()
        original_started = cs.started_at
        time.sleep(0.02)
        cs.start()  # no-op (already running)
        self.assertEqual(cs.started_at, original_started)

    def test_running_since_recovery(self):
        """Timer started with running_since resumes running immediately.

        Note: running_since restores the wall-clock started_at and restarts
        the timer, but does NOT add the gap since last save.  freeze() already
        captured elapsed up to save time, so current_elapsed starts from the
        saved elapsed value.
        """
        from ct.core.state import ClientState
        ten_secs_ago = (datetime.now().astimezone()
                        - timedelta(seconds=10)).isoformat()
        cs = ClientState("Test", elapsed=100.0, running_since=ten_secs_ago)
        self.assertTrue(cs.running)
        self.assertIsNotNone(cs.started_at)
        # Elapsed grows from 100.0 onward (monotonic time since constructor)
        self.assertGreaterEqual(cs.current_elapsed, 100.0)
        time.sleep(0.05)
        self.assertGreater(cs.current_elapsed, 100.0)

    def test_running_since_preserves_started_at(self):
        """running_since sets started_at to the original wall-clock time."""
        from ct.core.state import ClientState
        iso = "2026-02-12T14:30:00-05:00"
        cs = ClientState("Test", running_since=iso)
        self.assertEqual(cs.started_at, datetime.fromisoformat(iso))

    def test_freeze_keeps_running(self):
        from ct.core.state import ClientState
        cs = ClientState("Test")
        cs.start()
        time.sleep(0.05)
        cs.freeze()
        self.assertTrue(cs.running)
        self.assertGreater(cs.elapsed, 0.0)
        self.assertIsNotNone(cs.started_at)  # freeze preserves started_at

    def test_reset_clears_everything(self):
        from ct.core.state import ClientState
        cs = ClientState("Test", elapsed=500.0)
        cs.start()
        cs.reset()
        self.assertFalse(cs.running)
        self.assertEqual(cs.elapsed, 0.0)
        self.assertIsNone(cs.started_at)
        self.assertIsNone(cs._mono)

    def test_adjust_positive(self):
        from ct.core.state import ClientState
        cs = ClientState("Test", elapsed=100.0)
        cs.adjust(300)
        self.assertAlmostEqual(cs.elapsed, 400.0, places=1)

    def test_adjust_negative_clamps_to_zero(self):
        from ct.core.state import ClientState
        cs = ClientState("Test", elapsed=10.0)
        cs.adjust(-9999)
        self.assertEqual(cs.elapsed, 0.0)

    def test_current_elapsed_while_stopped(self):
        from ct.core.state import ClientState
        cs = ClientState("Test", elapsed=42.0)
        self.assertEqual(cs.current_elapsed, 42.0)

    def test_current_elapsed_while_running(self):
        from ct.core.state import ClientState
        cs = ClientState("Test", elapsed=100.0)
        cs.start()
        time.sleep(0.05)
        self.assertGreater(cs.current_elapsed, 100.0)


# ──────────────────────────────────────────────────────────────────────────
# snapshot.py tests
# ──────────────────────────────────────────────────────────────────────────

class TestSnapshot(unittest.TestCase):
    """Tests for snapshot creation and pruning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
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
        path = create_snapshot(self._sample_state(), self.tmpdir, "test")
        self.assertTrue(os.path.exists(path))

    def test_create_snapshot_content(self):
        from ct.core.snapshot import create_snapshot
        path = create_snapshot(
            self._sample_state(), self.tmpdir, "layout_change", "high")
        with open(path) as f:
            snap = json.load(f)
        self.assertEqual(snap["meta"]["snapshot_reason"], "layout_change")
        self.assertEqual(snap["meta"]["snapshot_priority"], "high")
        self.assertEqual(snap["layout"]["rows"][0]["name"], "A")

    def test_create_snapshot_does_not_mutate_original(self):
        from ct.core.snapshot import create_snapshot
        state = self._sample_state()
        create_snapshot(state, self.tmpdir, "test")
        self.assertNotIn("snapshot_reason", state["meta"])

    def test_snapshot_filename_format(self):
        from ct.core.snapshot import create_snapshot
        path = create_snapshot(self._sample_state(), self.tmpdir, "test")
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

        # Create many snapshots
        for _ in range(20):
            create_snapshot(self._sample_state(), self.tmpdir, "test")
            time.sleep(0.01)

        prune_snapshots(self.tmpdir)

        remaining = [f for f in os.listdir(self.tmpdir)
                     if f.startswith("state_")]
        self.assertGreater(len(remaining), 0)
        self.assertLessEqual(len(remaining), len(from_backup_tiers()) + 1)

    def test_prune_single_snapshot_safe(self):
        """Pruning with only one snapshot does nothing."""
        from ct.core.snapshot import create_snapshot, prune_snapshots

        create_snapshot(self._sample_state(), self.tmpdir, "test")
        prune_snapshots(self.tmpdir)
        remaining = os.listdir(self.tmpdir)
        self.assertEqual(len(remaining), 1)

    def test_prune_empty_dir_safe(self):
        """Pruning an empty directory doesn't crash."""
        from ct.core.snapshot import prune_snapshots
        prune_snapshots(self.tmpdir)  # should not raise

    def test_prune_nonexistent_dir_safe(self):
        """Pruning a nonexistent directory doesn't crash."""
        from ct.core.snapshot import prune_snapshots
        prune_snapshots(os.path.join(self.tmpdir, "nope"))

    def test_prune_ignores_non_snapshot_files(self):
        """Files that don't match the snapshot pattern are left alone."""
        from ct.core.snapshot import create_snapshot, prune_snapshots

        # Create a non-snapshot file
        other = os.path.join(self.tmpdir, "notes.txt")
        with open(other, "w") as f:
            f.write("hello")

        for _ in range(15):
            create_snapshot(self._sample_state(), self.tmpdir, "test")
            time.sleep(0.01)

        prune_snapshots(self.tmpdir)
        self.assertTrue(os.path.exists(other))

    def test_snapshot_tiered_retention(self):
        """Create snapshots across a simulated time range and verify tier logic."""
        from ct.core.snapshot import prune_snapshots, TIERS

        # Manually create snapshots with fake timestamps spanning 5 days
        now = datetime.now()
        offsets_minutes = [
            0, 1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 60, 120, 360,
            720, 1440, 2880, 4320, 5760,
        ]
        for offset in offsets_minutes:
            ts = now - timedelta(minutes=offset)
            fname = f"state_{ts.strftime('%Y%m%d_%H%M%S_%f')}.json"
            with open(os.path.join(self.tmpdir, fname), "w") as f:
                json.dump({"meta": {}}, f)

        prune_snapshots(self.tmpdir)

        remaining = [f for f in os.listdir(self.tmpdir)
                     if f.startswith("state_")]
        # Should keep at most len(TIERS) + 1 (newest)
        self.assertLessEqual(len(remaining), len(TIERS) + 1)
        # Should keep at least 2 (newest + one tier match)
        self.assertGreaterEqual(len(remaining), 2)


def from_backup_tiers():
    from ct.core.snapshot import TIERS
    return TIERS


class TestSnapshotScheduler(unittest.TestCase):
    """Tests for the debounce/coalesce snapshot scheduler."""

    def test_high_priority_fires_immediately(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=300, debounce=30)
        result = sched.request("app_exit", "high")
        self.assertTrue(result)

    def test_normal_priority_returns_false(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=300, debounce=30)
        result = sched.request("layout_change")
        self.assertFalse(result)

    def test_check_fires_after_debounce_and_interval(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=0.05, debounce=0.05)
        sched.request("layout_change")
        time.sleep(0.1)
        should, reason = sched.check()
        self.assertTrue(should)
        self.assertEqual(reason, "layout_change")

    def test_check_does_not_fire_during_debounce(self):
        from ct.core.snapshot import SnapshotScheduler
        # Large min_interval so periodic path doesn't interfere
        sched = SnapshotScheduler(min_interval=9999, debounce=10)
        sched.mark_done()  # set last_snapshot_time to now
        sched.request("layout_change")
        # Immediately check — debounce hasn't expired
        should, _ = sched.check()
        self.assertFalse(should)

    def test_check_does_not_fire_during_min_interval(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=10, debounce=0)
        sched.mark_done()  # reset last_snapshot_time to now
        sched.request("layout_change")
        time.sleep(0.05)  # debounce passed but interval hasn't
        should, _ = sched.check()
        self.assertFalse(should)

    def test_periodic_fires_when_not_dirty(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=0.05, debounce=30)
        # Don't request anything (not dirty)
        time.sleep(0.1)
        should, reason = sched.check()
        self.assertTrue(should)
        self.assertEqual(reason, "periodic")

    def test_mark_done_resets_state(self):
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=10, debounce=0)
        sched.request("layout_change")
        self.assertTrue(sched._dirty)
        sched.mark_done()
        self.assertFalse(sched._dirty)
        self.assertIsNone(sched._dirty_reason)
        self.assertIsNone(sched._last_action_time)

    def test_debounce_coalesces_rapid_requests(self):
        """Multiple rapid requests coalesce — only last reason matters."""
        from ct.core.snapshot import SnapshotScheduler
        sched = SnapshotScheduler(min_interval=0.05, debounce=0.1)
        sched.request("reason_1")
        sched.request("reason_2")
        sched.request("reason_3")
        time.sleep(0.2)
        should, reason = sched.check()
        self.assertTrue(should)
        self.assertEqual(reason, "reason_3")


# ──────────────────────────────────────────────────────────────────────────
# Integration tests
# ──────────────────────────────────────────────────────────────────────────

class TestIntegration(unittest.TestCase):
    """End-to-end tests: state round-trips through config + backup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._tmppath = Path(self.tmpdir)
        from ct.core import config
        self._orig_state_path = config.STATE_PATH
        self._orig_snapshot_dir = config.SNAPSHOT_DIR
        self._orig_completed_dir = config.COMPLETED_DIR
        self._orig_old_config = config._OLD_CONFIG_PATH
        self._orig_old_save = config._OLD_SAVE_PATH

        config.STATE_PATH = self._tmppath / "state.json"
        config.SNAPSHOT_DIR = self._tmppath / "snapshots"
        config.COMPLETED_DIR = self._tmppath / "completed_sessions"
        config._OLD_CONFIG_PATH = self._tmppath / "config.json"
        config._OLD_SAVE_PATH = self._tmppath / "recent_save.json"

    def tearDown(self):
        from ct.core import config
        config.STATE_PATH = self._orig_state_path
        config.SNAPSHOT_DIR = self._orig_snapshot_dir
        config.COMPLETED_DIR = self._orig_completed_dir
        config._OLD_CONFIG_PATH = self._orig_old_config
        config._OLD_SAVE_PATH = self._orig_old_save
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_lifecycle(self):
        """Simulate: fresh start → add clients → save → snapshot → reload."""
        from ct.core.config import load_state, save_state, now_iso, SNAPSHOT_DIR
        from ct.core.snapshot import create_snapshot, prune_snapshots
        from ct.core.state import ClientState

        # Fresh start
        state = load_state()
        self.assertEqual(state["layout"]["rows"], [])

        # Add a separator and two timers
        rows = [
            {"rowid": 0, "name": "Sysco", "type": "separator", "bg": None},
            {"rowid": 1, "name": "Calls", "type": "timer", "bg": None},
            {"rowid": 2, "name": "Tickets", "type": "timer", "bg": None},
        ]
        state["layout"]["rows"] = rows

        # Simulate timer running
        cs = ClientState("Calls", elapsed=0.0)
        cs.start()
        time.sleep(0.05)
        cs.freeze()
        state["session"]["tracked_times"]["1"] = {
            "elapsed": cs.elapsed,
            "running_since": cs.started_at.isoformat(),
        }
        state["session"]["tracked_times"]["2"] = {"elapsed": 300.0}

        # Save
        save_state(state)

        # Snapshot
        snap_path = create_snapshot(state, SNAPSHOT_DIR, "layout_change")
        self.assertTrue(os.path.exists(snap_path))

        # Reload
        loaded = load_state()
        self.assertEqual(len(loaded["layout"]["rows"]), 3)
        tt = loaded["session"]["tracked_times"]
        self.assertIn("running_since", tt["1"])
        self.assertAlmostEqual(tt["2"]["elapsed"], 300.0)

        # Recover running timer from state
        cs2 = ClientState(
            "Calls",
            elapsed=tt["1"]["elapsed"],
            running_since=tt["1"]["running_since"],
        )
        self.assertTrue(cs2.running)
        self.assertGreater(cs2.current_elapsed, 0.0)

    def test_snapshot_then_prune_cycle(self):
        """Create several snapshots and prune — verify no crash and files remain."""
        from ct.core.config import load_state, SNAPSHOT_DIR
        from ct.core.snapshot import create_snapshot, prune_snapshots

        state = load_state()
        for i in range(10):
            state["meta"]["saved_at"] = f"iter_{i}"
            create_snapshot(state, SNAPSHOT_DIR, f"test_{i}")
            time.sleep(0.01)

        prune_snapshots(SNAPSHOT_DIR)
        remaining = os.listdir(SNAPSHOT_DIR)
        self.assertGreater(len(remaining), 0)

    def test_migration_then_snapshot(self):
        """Migrate from old format, then create snapshot of migrated state."""
        from ct.core import config
        from ct.core.snapshot import create_snapshot

        # Write old format
        old_cfg = {
            "clients": ["#Team", "Alice", "Bob"],
            "theme": "Galaxy Dark",
            "backup_frequency": 20,
        }
        with open(config._OLD_CONFIG_PATH, "w") as f:
            json.dump(old_cfg, f)

        old_save = {
            "date": date.today().isoformat(),
            "clients": {"Alice": 120.0, "Bob": 240.0},
        }
        with open(config._OLD_SAVE_PATH, "w") as f:
            json.dump(old_save, f)

        # Load (triggers migration)
        state = config.load_state()
        self.assertEqual(state["settings"]["snapshot_min_minutes"], 20)
        self.assertEqual(len(state["layout"]["rows"]), 3)

        # Snapshot the migrated state
        snap = create_snapshot(state, config.SNAPSHOT_DIR, "post_migration")
        with open(snap) as f:
            snap_data = json.load(f)
        self.assertEqual(snap_data["settings"]["snapshot_min_minutes"], 20)
        self.assertEqual(snap_data["meta"]["snapshot_reason"], "post_migration")

    def test_save_completed_session(self):
        """save_completed_session writes a self-contained finalized file."""
        from ct.core.config import load_state, save_completed_session, COMPLETED_DIR

        state = load_state()
        state["layout"]["rows"] = [
            {"rowid": 0, "name": "A", "type": "timer", "bg": None},
        ]
        state["session"]["tracked_times"] = {"0": {"elapsed": 500.0}}

        boundary = datetime.now().astimezone()
        path = save_completed_session(state, boundary)

        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.basename(path).startswith("session_"))

        with open(path) as f:
            data = json.load(f)

        # It's a full state file
        self.assertIn("meta", data)
        self.assertIn("layout", data)
        self.assertIn("settings", data)
        self.assertIn("session", data)

        # Completion flags
        self.assertTrue(data["meta"]["is_completed_session"])
        self.assertEqual(data["session"]["end"], boundary.isoformat())

        # Data integrity
        self.assertEqual(len(data["layout"]["rows"]), 1)
        self.assertAlmostEqual(
            data["session"]["tracked_times"]["0"]["elapsed"], 500.0)

    def test_completed_session_does_not_mutate_source(self):
        """save_completed_session deep-copies — original state unchanged."""
        from ct.core.config import load_state, save_completed_session

        state = load_state()
        boundary = datetime.now().astimezone()
        save_completed_session(state, boundary)

        self.assertFalse(state["meta"]["is_completed_session"])
        self.assertNotIn("end", state["session"])

    def test_completed_session_dir_created(self):
        """completed_sessions/ dir is created if missing."""
        from ct.core import config

        completed_dir = self._tmppath / "completed_sessions"
        config.COMPLETED_DIR = completed_dir

        state = config.load_state()
        boundary = datetime.now().astimezone()
        config.save_completed_session(state, boundary)

        self.assertTrue(os.path.isdir(completed_dir))
        files = os.listdir(completed_dir)
        self.assertEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
