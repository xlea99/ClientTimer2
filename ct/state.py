"""Per-client timer state — pure logic, no UI."""

import time


class ClientState:
    """Tracks elapsed time for one client using time.monotonic().

    No datetime.fromtimestamp(). No timezone-dependent epoch constant.
    Just seconds as a float, like God intended.
    """

    def __init__(self, name, elapsed=0.0):
        self.name = name
        self.elapsed = elapsed
        self.running = False
        self._mono = None

    @property
    def current_elapsed(self):
        if self.running and self._mono is not None:
            return self.elapsed + (time.monotonic() - self._mono)
        return self.elapsed

    def start(self):
        if not self.running:
            self.running = True
            self._mono = time.monotonic()

    def stop(self):
        if self.running:
            self.elapsed += time.monotonic() - self._mono
            self.running = False
            self._mono = None

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
        self.elapsed = 0.0
