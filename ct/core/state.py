"""Per-client timer state — pure logic, no UI."""

import time
from datetime import datetime


class ClientState:
    """Tracks elapsed time for one client using time.monotonic().

    Runtime tracking uses monotonic seconds for accuracy (immune to clock
    changes).  Wall-clock ``started_at`` is recorded for persistence as
    ``running_since`` in state.json.
    """

    def __init__(self, name, elapsed=0.0, running_since=None):
        self.name = name
        self.elapsed = float(elapsed)
        self.running = False
        self._mono = None
        self.started_at = None  # aware datetime, set when running

        if running_since is not None:
            # Timer was running when last saved — restore and restart
            self.started_at = datetime.fromisoformat(running_since)
            self.start()

    @property
    def current_elapsed(self):
        if self.running and self._mono is not None:
            return self.elapsed + (time.monotonic() - self._mono)
        return self.elapsed

    def start(self):
        if not self.running:
            self.running = True
            self._mono = time.monotonic()
            if self.started_at is None:
                self.started_at = datetime.now().astimezone()

    def stop(self):
        if self.running:
            self.elapsed += time.monotonic() - self._mono
            self.running = False
            self._mono = None
            self.started_at = None

    def freeze(self):
        """Snapshot running time into elapsed without stopping the timer."""
        if self.running and self._mono is not None:
            now = time.monotonic()
            self.elapsed += now - self._mono
            self._mono = now

    def adjust(self, seconds):
        self.freeze()
        self.elapsed = max(0.0, self.elapsed + seconds)

    def reset(self):
        self.running = False
        self._mono = None
        self.started_at = None
        self.elapsed = 0.0
