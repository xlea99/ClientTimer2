"""Undo stack — small, in-memory, and deliberately not persisted.

Each command reverses ONLY what its own action changed. A whole-state
rollback would be wrong in a timer app: the clock keeps running between an
action and its undo, so restoring a snapshot would silently discard time that
had nothing to do with the thing being undone.

Nothing in here touches Qt — commands operate on an AppState and the
rowid -> TimerState map, so they can be tested directly.
"""

from dataclasses import dataclass, field

from ct.core.timer_state import TimerState

MAX_DEPTH = 20


class Command:
    """Base for undoable actions.

    Subclasses are dataclasses whose first field is `label`, which reads as
    the tail of a sentence: "Undid <label>". Note there is deliberately no
    `label = ""` class attribute here — a dataclass subclass would inherit it
    as a *default* for the annotated field, which then forces every field
    after it to have one too.
    """

    def conflicts(self, timers):
        """Rows whose undo is ambiguous and needs the user to choose.

        Returns a list of (name, seconds accrued). Empty means undo straight
        through without asking.
        """
        return []

    def undo(self, state, timers, mode="revert"):
        raise NotImplementedError


@dataclass
class DeleteRow(Command):
    """A row (timer or separator) that was removed from the layout."""

    label: str
    row: dict
    index: int
    elapsed: float = 0.0
    was_collapsed: bool = False

    def undo(self, state, timers, mode="revert"):
        # The list may have changed length since; clamp rather than fail.
        idx = max(0, min(self.index, len(state.rows)))
        state.rows.insert(idx, dict(self.row))
        if self.row.get("type") == "timer":
            timers[self.row["rowid"]] = TimerState(
                self.row["name"], elapsed=self.elapsed)
        elif self.was_collapsed:
            state.collapsed_groups.add(self.row["rowid"])


@dataclass
class ResetTimes(Command):
    """One or many timers zeroed. `previous` is rowid -> elapsed before."""

    label: str
    previous: dict

    def conflicts(self, timers):
        out = []
        for rid in self.previous:
            ts = timers.get(rid)
            if ts is not None and ts.current_elapsed >= 1:
                out.append((ts.name, ts.current_elapsed))
        return out

    def undo(self, state, timers, mode="revert"):
        for rid, before in self.previous.items():
            ts = timers.get(rid)
            if ts is None:
                continue          # the row was deleted after the reset
            # stop() folds the live segment into elapsed, so afterwards
            # ts.elapsed is everything accrued since the reset. Setting
            # elapsed while running would double-count that segment.
            was_running = ts.running
            if was_running:
                ts.stop()
            accrued = ts.elapsed if mode == "add" else 0.0
            ts.elapsed = before + accrued
            if was_running:
                ts.start()


@dataclass
class ReorderRows(Command):
    """A drag that rearranged the layout. Times are untouched by a reorder,
    so restoring the whole list wholesale is safe here."""

    label: str
    rows: list
    collapsed: set = field(default_factory=set)

    def undo(self, state, timers, mode="revert"):
        state.rows[:] = [dict(r) for r in self.rows]
        state.collapsed_groups.clear()
        state.collapsed_groups.update(self.collapsed)


@dataclass
class RenameRow(Command):
    label: str
    rowid: int
    old_name: str

    def undo(self, state, timers, mode="revert"):
        for r in state.rows:
            if r["rowid"] == self.rowid:
                r["name"] = self.old_name
                break
        ts = timers.get(self.rowid)
        if ts is not None:
            ts.name = self.old_name


class UndoStack:
    """Bounded LIFO. Not saved to disk — undo dies with the session."""

    def __init__(self, depth=MAX_DEPTH):
        self._items = []
        self._depth = depth

    def push(self, command):
        self._items.append(command)
        del self._items[:-self._depth]      # no-op while under the cap

    def pop(self):
        return self._items.pop() if self._items else None

    def peek(self):
        return self._items[-1] if self._items else None

    def clear(self):
        self._items.clear()

    def __len__(self):
        return len(self._items)
