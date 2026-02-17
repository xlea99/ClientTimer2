import time
from datetime import datetime
from ct.common.logger import log

# This object handles actual time tracking for a single client. It uses monotonic seconds for accuracy (clock change
# immunity).
class TimerState:

    # Simple __init__, with option to specify how long the timer has already been running
    def __init__(self, name, elapsed=0.0, running_since=None):
        self.name = name
        self.elapsed = float(elapsed)
        self.running = False
        self._mono = None
        self.started_at = None  # aware datetime, set when running

        # This means that the timer was running when last saved, restore and restart
        if running_since is not None:
            self.started_at = datetime.fromisoformat(running_since)
            self.start()

        log.debug(f"Initialized new timer '{name}', with elapsed of {elapsed} that has been running_since {running_since}")

    # Returns how much time has elapsed since `start()` was run.
    @property
    def current_elapsed(self):
        if self.running and self._mono is not None:
            return self.elapsed + (time.monotonic() - self._mono)
        return self.elapsed

    # Start and stop methods for the timer.
    def start(self):
        if not self.running:
            self.running = True
            self._mono = time.monotonic()
            if self.started_at is None:
                self.started_at = datetime.now().astimezone()
            log.debug(f"Started timer '{self.name}' at mono {self._mono}")
    def stop(self):
        if self.running:
            now = time.monotonic()
            self.elapsed += time.monotonic() - self._mono
            self.running = False
            self._mono = None
            self.started_at = None
            log.debug(f"Stopped timer '{self.name}' at mono {now}")
    # Simply restores the timer to 0:00
    def reset(self):
        self.running = False
        self._mono = None
        self.started_at = None
        self.elapsed = 0.0
        log.debug(f"Reset timer '{self.name}' to 0.0")

    # "Freezes" the timer's running time from internal _mono into elapsed, without actually stopping the timer.
    def freeze(self):
        if self.running and self._mono is not None:
            now = time.monotonic()
            self.elapsed += now - self._mono
            self._mono = now
    # Manually sets the timer to the given time.
    def adjust(self, seconds):
        self.freeze()
        self.elapsed = max(0.0, self.elapsed + seconds)
        log.debug(f"Manually set timer '{self.name}' time to {seconds} seconds")

