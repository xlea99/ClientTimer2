import ctypes
from ctypes import wintypes
import re
import time
import sys
from pathlib import Path
from datetime import datetime, timedelta
from PySide6.QtCore import (Qt, QEvent, QTimer, QPropertyAnimation,
                            QEasingCurve, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QIcon,
                           QKeySequence)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from ct.common import crash
from ct.common.logger import log
from ct.common.setup import PATHS
from ct.core.config import AppState, save_completed_session
from ct.core.snapshot import create_snapshot, prune_snapshots
from ct.core.update import launch_installer as update_launch
from ct.core.timer_state import TimerState
from ct.core.undo import (DeleteRow, RenameRow, ReorderRows, ResetTimes,
                          UndoStack)
from ct.ui.dialogs import ConfigDialog
from ct.ui.drag import DragController
from ct.ui.theme import THEMES, SIZES, build_stylesheet, build_menu_stylesheet
from ct.ui.ui_blueprint import UIBlueprint
from ct.ui.row_factory import RowFactory
from ct.ui.widgets import TickCheckBox
from ct.util import format_time, format_copy_time, now_iso

# Permissive denylist: real client names use unicode, '&', '-', ',', etc.
# Only control characters are stripped — name labels render as PlainText so
# nothing else needs escaping.
_SANITIZE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

# Windows sends these around an interactive move/resize of the window frame.
# They bracket the whole gesture, so they tell us when the user has actually
# let go — resize events alone can't, since holding the edge still looks
# identical to having stopped.
_WM_ENTERSIZEMOVE = 0x0231
_WM_EXITSIZEMOVE  = 0x0232

# Maximize arrives as a system command — from the title-bar button, from
# double-clicking the title bar, and from Win+Up. Intercepting it here is what
# lets the button mean "as tall as this screen allows" instead of handing the
# window to Windows' maximize state, which this app's own sizing then has to
# fight. The low four bits of wParam are reserved by Windows, hence the mask.
_WM_SYSCOMMAND = 0x0112
_SC_MAXIMIZE   = 0xF030
_SC_MASK       = 0xFFF0

# Strips the client separator rule out of a row's stylesheet. Group headers
# use a full "border:" box, not "border-bottom:", so they are left alone.

# Width reserved for the toast's dismiss button. A constant rather than a
# measurement because show_toast subtracts it from the available width before
# the button has ever been laid out.
_TOAST_CLOSE_W = 18



# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    # Results from the update worker threads come back through these, NOT
    # through QTimer.singleShot. A QTimer created on a plain threading.Thread
    # belongs to a thread with no Qt event loop, so it never fires — the work
    # completes, logs happily, and the result silently evaporates. Signals
    # are thread-safe and queue onto the receiver's thread, which is the
    # whole point of them.
    _update_checked = Signal(str, object)   # (status, manifest|None)
    _update_downloaded = Signal(object)     # Path, or None on failure

    def __init__(self):
        # Load state before super().__init__() so we can pass the correct
        # window flags directly — avoids a second HWND creation (and visible
        # flash) that setWindowFlags() would cause after the fact.
        state = AppState.load()
        flags = Qt.Window
        if state.settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(flags=flags)
        self._state = state

        self.setWindowTitle("Client Timer 2")
        self.setWindowIcon(QIcon(str(PATHS.assets / "icon.ico")))

        self._next_rowid = max(
            (r["rowid"] for r in self._state.rows), default=-1) + 1

        # Restore live timer objects from saved tracked_times
        self.timers = {}
        for row in self._state.rows:
            if row["type"] == "timer":
                rid = row["rowid"]
                tt  = self._state.tracked_times.get(str(rid), {})
                self.timers[rid] = TimerState(
                    row["name"],
                    elapsed=tt.get("elapsed", 0.0),
                    running_since=tt.get("running_since"),
                )

        self._widgets      = {}
        self._has_mdl2     = "Segoe MDL2 Assets" in QFontDatabase.families()
        self._shift_held   = False
        self._rearranging  = False
        self._visible_rowids = []  # populated by _rebuild_rows
        self._undo         = UndoStack()

        # -- Drag controller --
        self._drag = DragController(self)

        # -- Snapshot handling --
        self._last_snapshot_time = 0.0
        # Seconds between non-high-priority snapshots. Short on purpose: a
        # snapshot is ~2.5 KB, and a rapid run of deletes/resets deserves a
        # restore point each rather than one shared between them.
        self._snapshot_debounce  = 2.0
        # Seconds between IDLE snapshots — the heartbeat taken because time
        # passed, with nothing happening. A tuning value, not a preference:
        # it used to be a "Backup Interval" setting and was removed because
        # nothing a user could reason about depended on it. Crash safety is
        # state.json (rewritten every 20 ticks), and history depth is the
        # tier ladder in snapshot.py — neither is affected by this. All it
        # changes is how densely the newest-20 buffer is packed.
        self._snapshot_idle_secs = 5 * 60

        # -- Pre-UI startup checks --
        self._startup_checks()

        # -- Build UI skeleton --
        central = QWidget()
        self.setCentralWidget(central)
        self._main_lay = QVBoxLayout(central)
        self._main_lay.setContentsMargins(0, 0, 0, 0)

        self._time_labels    = {}    # time QLabel -> rowid, for click-to-copy
        self._name_labels    = {}    # name QLabel -> rowid, for dbl-click rename
        self._row_children   = {}    # any row sub-widget -> rowid, for hover
        self._hovered_rid    = None  # row the pointer is actually inside
        self._inline_editor  = None  # (QLineEdit, rowid) while renaming in place
        self._last_chrome    = None  # non-viewport height, set by _shrink_to_fit
        self._scroll_area    = None  # the row viewport, rebuilt with the grid
        self._hidden_line    = None  # row whose separator is currently hidden
        self._hover_strip    = None  # fills the gap above the hovered row
        self._expected_size  = None  # last size we asked for ourselves
        self._programmatic_resize = False
        self._user_resizing  = False  # true between ENTER/EXITSIZEMOVE
        self._ready_for_user_resize = False  # set once show() has settled
        self._resize_settle  = QTimer(self)
        self._resize_settle.setSingleShot(True)
        self._resize_settle.setInterval(200)
        self._resize_settle.timeout.connect(self._on_resize_settled)
        # Qt does not guarantee a Leave when the pointer exits quickly, and
        # this window is usually not the focused one, so a missed Leave used
        # to strand the hover tint until the user came back and hovered
        # something else. Runs ONLY while a row is tinted, so it costs
        # nothing the rest of the time.
        self._hover_poll     = QTimer(self)
        self._hover_poll.setInterval(150)
        self._hover_poll.timeout.connect(self._sync_hover_to_cursor)
        self._grid_widget    = None  # created fresh each _rebuild_rows
        self._content_widget = None  # single swappable child: grid + footer

        # -- Toast notification bar --
        self._toast_container = QWidget()
        self._toast_container.setVisible(False)
        toast_lay = QVBoxLayout(self._toast_container)
        toast_lay.setContentsMargins(0, 0, 0, 0)
        toast_lay.setSpacing(0)

        # One coloured bar holding [X][message], so the dismiss button sits
        # inside the toast rather than floating beside it.
        self._toast_bar = QWidget()
        self._toast_bar.setObjectName("toastBar")
        bar_lay = QHBoxLayout(self._toast_bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(0)

        self._toast_close = QPushButton("✕")
        self._toast_close.setFont(QFont("Calibri", 9))
        self._toast_close.setFixedWidth(_TOAST_CLOSE_W)
        self._toast_close.setCursor(Qt.PointingHandCursor)
        self._toast_close.setToolTip("Dismiss")
        self._toast_close.clicked.connect(self._dismiss_toast)
        bar_lay.addWidget(self._toast_close)

        self._toast = QLabel()
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.setFont(QFont("Calibri", 9))
        self._toast.setContentsMargins(4, 2, 4, 2)
        self._toast.setWordWrap(True)
        bar_lay.addWidget(self._toast, 1)

        # Optional action, e.g. "Update Now". Hidden for ordinary toasts, so
        # the common case is exactly the bar it has always been. There is no
        # matching "Later" button on purpose: the X already means that.
        self._toast_action = QPushButton()
        self._toast_action.setFont(QFont("Calibri", 9))
        self._toast_action.setCursor(Qt.PointingHandCursor)
        self._toast_action.setVisible(False)
        self._toast_action.clicked.connect(self._on_toast_action)
        bar_lay.addWidget(self._toast_action)
        self._toast_action_cb = None

        toast_lay.addWidget(self._toast_bar)

        self._toast_opacity = QGraphicsOpacityEffect(self._toast_container)
        self._toast_container.setGraphicsEffect(self._toast_opacity)
        self._toast_opacity.setOpacity(1.0)

        # Single persistent timer: restarting it cancels the previous
        # deadline, so a new toast can't be faded early by an old one's.
        self._toast_fade  = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._fade_toast)

        # Toast is parented once and stays last in the layout forever —
        # rebuilds insert content above it instead of re-adding it.
        self._main_lay.addWidget(self._toast_container)

        self._apply_style()
        self._rebuild_rows()
        self._shrink_to_fit()

        # -- Show any pending toast from startup checks --
        if self._pending_toast:
            msg = self._pending_toast
            self._pending_toast = None
            QTimer.singleShot(0, lambda: self.show_toast(msg, 6))

        # Update check. Deferred so it never sits in front of the first paint,
        # and threaded so a slow CDN cannot hold the window hostage.
        self._update_checked.connect(self._on_update_checked)
        self._update_downloaded.connect(self._install_update)
        QTimer.singleShot(2500, self._start_update_check)

        # -- Tick timer (1 s) --
        self._tick_n = 0
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)


    # ------------------------------------------------------------------ #
    #  Startup checks (runs before UI is built)                            #
    # ------------------------------------------------------------------ #

    def _startup_checks(self):
        """All pre-UI initialization: migration, daily reset catch-up, etc."""
        self._pending_toast = None

        # 0. If any saved settings couldn't be understood (hand-edited
        #    state.json), let the user know. Set first so the daily-reset
        #    toast below wins if both fire.
        if getattr(self._state.settings, "coerced_keys", []):
            self._pending_toast = (
                "Some saved settings couldn't be read and were adjusted. See log for more info")

        # Daily reset catch-up — if the app was closed and we missed a
        #    reset boundary, save the old session and zero out timers.
        if self._state.settings.daily_reset_enabled:
            boundary = self._most_recent_reset_boundary()
            if self._state.session_start < boundary:
                # Check if any timers were left running when the app closed.
                # If so, ask the user whether to credit that time to the
                # completed session before saving it.
                running_timers = []
                if self._state.settings.recover_running_time:
                    for ts in self.timers.values():
                        if ts.running and ts.started_at:
                            gap = (boundary - ts.started_at).total_seconds()
                            if gap > 0:
                                running_timers.append((ts, gap))

                # Stop them NOW, before anything blocking happens.
                #
                # A timer restored with running_since is start()ed during
                # __init__, so it is genuinely running from launch onward.
                # The _save_state() below calls freeze(), which adds
                # (now - launch) — and "now" is after a modal dialog the user
                # may leave open indefinitely. That inflation lands in the
                # PERMANENT session archive, and it happened on the Discard
                # path too, so discarding did not discard.
                #
                # After running_timers is built, because that loop tests
                # ts.running. Safe because every timer is reset a few lines
                # below anyway; all this changes is that the archived numbers
                # are the ones the dialog actually showed.
                for ts in self.timers.values():
                    ts.stop()

                if running_timers:
                    lines = "\n".join(
                        f"  \u2022 {ts.name}:  {format_time(ts.elapsed)}"
                        f"  \u2192  {format_time(ts.elapsed + gap)}"
                        for ts, gap in running_timers
                    )
                    msg = QMessageBox(self)
                    msg.setWindowTitle("Session Recovery")
                    msg.setIcon(QMessageBox.Question)
                    msg.setText(
                        "The previous session completed while the app was "
                        "closed and some timers were still running.\n\n"
                        "Would you like to add the elapsed time to the "
                        "completed session?"
                    )
                    msg.setInformativeText(lines)
                    add_btn = msg.addButton(
                        "Add Elapsed Time && Save Session", QMessageBox.AcceptRole)
                    msg.addButton(
                        "Discard Elapsed && Save Session", QMessageBox.RejectRole)
                    msg.exec()

                    if msg.clickedButton() == add_btn:
                        for ts, gap in running_timers:
                            ts.elapsed += gap
                            log.info(
                                f"Added {gap:.0f}s recovery to timer "
                                f"'{ts.name}' for completed session")

                state = self._save_state()
                save_completed_session(state, boundary)
                for ts in self.timers.values():
                    ts.reset()
                self._state.session_start = boundary
                self._save_state()
                time_str = boundary.strftime("%#I:%M %p")
                self._pending_toast = f"Session saved and reset at {time_str}"

        # 3. Recover time for timers that were running while the app was closed.
        #    Only applies if the timer survived the daily reset above (i.e. no
        #    boundary was crossed, so reset() was never called).
        if self._state.settings.recover_running_time:
            now = datetime.now().astimezone()
            for ts in self.timers.values():
                if ts.running and ts.started_at:
                    gap = (now - ts.started_at).total_seconds()
                    if gap > 0:
                        ts.elapsed += gap
                        ts.started_at = now
                        log.info(f"Recovered {gap:.0f}s for timer '{ts.name}'")

    # ------------------------------------------------------------------ #
    #  Style                                                               #
    # ------------------------------------------------------------------ #

    def _apply_style(self):
        style = build_stylesheet(self._state.settings.theme)
        self.setStyleSheet(style)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(style)

        s = SIZES.get(self._state.settings.size, SIZES["Regular"])
        self._main_lay.setContentsMargins(
            s["frame_pad"], s["frame_pad"], s["frame_pad"], s["frame_pad"]
        )
        # Gaps between grid / footer / toast are controlled explicitly
        # (footer_gap on the footer, padding above the toast), not globally.
        self._main_lay.setSpacing(0)
        self._toast_container.layout().setContentsMargins(0, s["padding"], 0, 0)

    # ------------------------------------------------------------------ #
    #  Group helpers                                                       #
    # ------------------------------------------------------------------ #

    def _group_children(self, group_rowid):
        """Return rowids of timer rows belonging to a separator."""
        if (self._drag.group_rids is not None
                and group_rowid == self._drag.dragging_rid):
            return list(self._drag.group_rids)
        children = []
        found = False
        for row in self._state.rows:
            if row["rowid"] == group_rowid:
                found = True
                continue
            if found:
                if row["type"] == "separator":
                    break
                children.append(row["rowid"])
        return children

    def _group_total_time(self, group_rowid):
        """Sum of floored current_elapsed for all children of a separator."""
        total = 0
        for child_rid in self._group_children(group_rowid):
            if child_rid in self.timers:
                total += int(self.timers[child_rid].current_elapsed)
        return total

    def _parent_group(self, rowid):
        """Return the separator rowid that owns this timer, or None."""
        parent = None
        for row in self._state.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif row["rowid"] == rowid:
                return parent
        return None

    # ------------------------------------------------------------------ #
    #  Row building                                                        #
    # ------------------------------------------------------------------ #

    def _rebuild_rows(self):
        # Suppress painting during teardown/rebuild — the grid and footer are
        # separate children of the top-level layout now, and swapping them can
        # flush a partially-built frame to screen (a one-frame flicker on
        # drag-drop). Batch everything into a single repaint.
        # A rebuild throws away the scroll viewport and builds a new one, so
        # the position has to be carried across by hand. Done here rather than
        # at the call sites because _reorder_visual rebuilds mid-drag, and
        # that path would otherwise yank the list back to the top.
        keep = 0
        if self._scroll_area is not None:
            keep = self._scroll_area.verticalScrollBar().value()
        self.setUpdatesEnabled(False)
        try:
            self._rebuild_rows_impl()
            if keep:
                self._restore_scroll(keep)
            self._update_bottom_line()
            self._update_status()
        finally:
            self.setUpdatesEnabled(True)

    def _restore_scroll(self, value):
        """Put the viewport back where it was before the rebuild."""
        if self._scroll_area is None:
            return
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(value)
        if bar.value() != value:
            # The fresh content hasn't been measured yet, so the scrollbar's
            # range is still stale and clamped us. Try again once it has.
            QTimer.singleShot(0, lambda v=value: self._reapply_scroll(v))

    def _reapply_scroll(self, value):
        if self._scroll_area is not None:
            self._scroll_area.verticalScrollBar().setValue(value)

    def _sync_scrub_terms(self):
        """Tell the crash reporter which strings are client names.

        Without this the redaction in crash.py is inert — it can only remove
        terms it has been told about.
        """
        try:
            crash.set_scrub_terms([r.get("name", "") for r in self._state.rows])
        except Exception:
            pass          # reporting must never be able to break the app

    def _rebuild_rows_impl(self):
        """Tear down and recreate the entire grid: client rows + footer."""
        self._sync_scrub_terms()
        self._widgets.clear()
        self._time_labels = {}   # time QLabel -> rowid, for click-to-copy
        self._name_labels = {}   # name QLabel -> rowid, for dbl-click rename
        self._row_children = {}  # sub-widget -> rowid, for hover tracking
        # The editor lived in the tree about to be replaced.
        self._inline_editor = None

        # Build the entire new content (row grid + footer) fully offline as
        # ONE widget tree, then swap it into the window in a single adjacent
        # remove/insert. Swapping grid/footer as separate top-level layout
        # children lets Qt flush a partial frame between event-loop
        # iterations — the drag-drop flicker.
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)

        self._grid_widget = QWidget()
        self._grid = QVBoxLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        # NOTE: the grid is added to content_lay further down — bare when the
        # row count is under the cap, wrapped in a QScrollArea when it isn't.

        ss = self._state.settings
        t  = THEMES.get(ss.theme, THEMES["E-Ink (Default)"])
        s  = SIZES.get(ss.size, SIZES["Regular"])

        self._grid.setSpacing(s.get("v_spacing", s["padding"]))

        blueprint = UIBlueprint.compute(t, s, ss.font, self._state.rows, self._has_mdl2)

        row_containers = []    # every row widget, for the uniform-height pass

        if not self._state.rows:
            lbl = QLabel("No clients. Click the unlock button in\nthe bottom left and add one to begin!")
            lbl.setFont(QFont(ss.font, s["label"]))
            lbl.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(lbl)
            self._visible_rowids = []
        else:
            current_group_rid = None
            visible_entries   = []
            dragging_group    = (self._drag.active and self._drag.group_rids is not None)

            for row in self._state.rows:
                if row["type"] == "separator":
                    current_group_rid = row["rowid"]
                    visible_entries.append((row, False))
                else:
                    if dragging_group and row["rowid"] in self._drag.group_rids:
                        continue
                    if (self._drag.hidden_rids is not None
                            and row["rowid"] in self._drag.hidden_rids):
                        continue
                    is_child = current_group_rid is not None
                    if (is_child
                            and current_group_rid in self._state.collapsed_groups
                            and not (dragging_group
                                     and current_group_rid == self._drag.dragging_rid)):
                        continue
                    visible_entries.append((row, is_child))

            self._visible_rowids = [r["rowid"] for r, _ in visible_entries]

            for idx, (row, is_child) in enumerate(visible_entries):
                rid = row["rowid"]

                if row["type"] == "separator":
                    collapsed = rid in self._state.collapsed_groups
                    if dragging_group and rid == self._drag.dragging_rid:
                        children  = list(self._drag.group_rids)
                        collapsed = True
                    else:
                        children = self._group_children(rid)
                    has_running = any(
                        cid in self.timers and self.timers[cid].running
                        for cid in children)
                    total = self._group_total_time(rid)

                    row_container, widget_dict = RowFactory.separator(
                        blueprint=blueprint, rid=rid, row=row,
                        children=children, total_time=total,
                        is_dragging=self._drag.dragging_rid == rid,
                        collapsed=collapsed, has_running=has_running,
                        show_count=ss.show_group_count, show_time=ss.show_group_time,
                        show_x=self._rearranging,
                        on_toggle=self._on_group_toggle,
                        on_remove=self._on_remove_group,
                    )
                else:
                    needs_sep = (ss.client_separators
                                 and idx < len(visible_entries) - 1
                                 and visible_entries[idx + 1][0]["type"] == "timer")
                    # Bottom-most row: replace its client separator with the
                    # thick footer line instead of stacking both.
                    timer_state = self.timers[rid]
                    row_container, widget_dict = RowFactory.timer(
                        blueprint=blueprint, rid=rid, row=row, state=timer_state,
                        shift_held=self._shift_held, label_align=ss.label_align,
                        show_adjust=ss.show_adjust_buttons,
                        show_x=self._rearranging,
                        is_child=is_child,
                        is_dragging=self._drag.dragging_rid == rid,
                        draw_separator_line=needs_sep,
                        # The thick rule sits below the scroll viewport now,
                        # so no row ever carries it.
                        footer_line=False,
                        # Every timer row reserves the separator gap, whether
                        # or not it draws a line. Without this, rows that
                        # don't draw one are shorter and their contents sit
                        # at a different height from their neighbours'.
                        force_line_gap=ss.client_separators,
                        on_toggle=self._on_toggle_timer,
                        on_adjust=self._on_adjust,
                        on_remove=self._on_remove,
                    )
                    if timer_state.running:
                        self._set_bold(rid, True, widget_dict)

                self._widgets[rid] = widget_dict

                row_container.installEventFilter(self)
                row_container.setContextMenuPolicy(Qt.CustomContextMenu)
                row_container.customContextMenuRequested.connect(
                    lambda pos, r=rid, w=row_container: self._on_row_context_menu(
                        r, w.mapToGlobal(pos))
                )
                for child in row_container.findChildren(QPushButton):
                    child.setContextMenuPolicy(Qt.PreventContextMenu)
                    # A button is a child, so entering it sends Leave to the
                    # container. Track them too or the row un-tints the moment
                    # the pointer crosses onto Start.
                    child.installEventFilter(self)
                    self._row_children[child] = rid
                for child in row_container.findChildren(QLabel):
                    child.setAttribute(Qt.WA_TransparentForMouseEvents)
                if self._rearranging:
                    row_container.setCursor(Qt.OpenHandCursor)
                    for child in row_container.findChildren(QPushButton):
                        child.setCursor(Qt.ArrowCursor)
                else:
                    # These two labels undo the blanket transparent-for-mouse
                    # above so they can be hovered and clicked in their own
                    # right. In rearrange mode they stay transparent, so
                    # dragging a row by its name or time still works.

                    # Double-click the name to rename — rows and groups both.
                    nlbl = widget_dict.get("name")
                    if nlbl is not None:
                        nlbl.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                        nlbl.installEventFilter(self)
                        self._name_labels[nlbl] = rid

                    # Click the time to copy it.
                    if not widget_dict.get("is_group"):
                        tlbl = widget_dict.get("time")
                        if tlbl is not None:
                            tlbl.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                            tlbl.installEventFilter(self)
                            tlbl.setCursor(Qt.PointingHandCursor)
                            self._time_labels[tlbl] = rid

                row_containers.append(row_container)
                self._grid.addWidget(row_container)

        # Every row gets the same height — the tallest one's. Group headers
        # and timer rows naturally differ by a few pixels, and that made any
        # given window of N rows a different total height from the next,
        # so the bottom row was clipped by a varying amount while scrolling.
        # One pitch means both edges stay flush at every scroll position.
        if row_containers:
            uniform = max(c.sizeHint().height() for c in row_containers)
            for c in row_containers:
                c.setFixedHeight(uniform)

        # The grid always lives in a scroll viewport. Without one the layout's
        # minimum size is the whole row list, so the user physically cannot
        # drag the window shorter than its contents.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(self._grid_widget)
        # Breathing room between the rows and the scrollbar. Applied as a
        # margin on the bar itself so it only takes space when the bar is
        # actually rendered.
        sb_gap = s.get("scrollbar_gap", 0)
        if sb_gap:
            scroll.verticalScrollBar().setStyleSheet(
                f"QScrollBar:vertical {{ margin-left: {sb_gap}px; }}")
        # Wheel scrolling moves whole rows — see eventFilter. The viewport's
        # own resizes come through there too, since which row sits at the
        # bottom edge changes with both scrolling and window height.
        scroll.viewport().installEventFilter(self)
        scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: (self._update_bottom_line(), self._update_status()))
        content_lay.addWidget(scroll)
        self._scroll_area = scroll

        # Footer separator — below the viewport so it stays put while the
        # rows scroll under it.
        if self._state.rows:
            # Gap above the rule, independent of "footer_gap" (which is the
            # gap below it, before the buttons).
            line_gap_above = s.get("footer_line_gap", 0)
            if line_gap_above:
                content_lay.addSpacing(line_gap_above)
            sep = QWidget()
            sep.setFixedHeight(2)
            sep.setStyleSheet(f"background-color: {t['chrome_line']};")
            content_lay.addWidget(sep)

        # Footer
        footer, fw = RowFactory.footer(
            blueprint=blueprint, rearranging=self._rearranging,
            on_rearrange=self._on_rearrange_toggle,
            on_add=self._on_add,
            on_add_group=self._on_add_group,
            on_config=self._on_config,
            on_add_input_return=self._on_add,
        )
        self._rearrange_btn  = fw["rearrange_btn"]
        self._add_btn        = fw["add_btn"]
        self._add_group_btn  = fw["add_group_btn"]
        self._add_input      = fw["add_input"]
        self._status_lbl     = fw["status_lbl"]
        self._status_lbl.installEventFilter(self)
        self._cfg_btn        = fw["cfg_btn"]
        # Footer sits outside the row grid so its gap above is governed by
        # the per-size "footer_gap", independent of v_spacing.
        footer.layout().setContentsMargins(
            0, s.get("footer_gap", s["v_spacing"]), 0, 0)
        content_lay.addWidget(footer)

        # Atomic swap — old content out, new content in, toast stays last.
        old_content = self._content_widget
        self._content_widget = content
        if old_content is not None:
            # The hover strip lives inside the old tree and dies with it.
            # Drop the reference here rather than letting the next hover
            # discover the corpse.
            self._hover_strip = None
            self._hovered_rid = None
            self._main_lay.removeWidget(old_content)
            old_content.setParent(None)
            old_content.deleteLater()
        self._main_lay.insertWidget(0, content)
        # Show it NOW (it is parented, so this is safe — never setVisible a
        # parentless widget). Qt would otherwise only show it on the next
        # event-loop turn, and until then QLayout skips it when measuring, so
        # the window's size hint reads 0x0.
        content.setVisible(True)
        # The strip was destroyed with the old tree; a live drag still wants
        # one. Must come after the swap so the new containers have geometry.
        self._sync_drag_strip()

        QTimer.singleShot(0, self._sync_footer_heights)
        # Deferred so the new rows have geometry to hit-test against. This is
        # what re-tints the row under a stationary pointer after a drag ends.
        QTimer.singleShot(0, self._sync_hover_to_cursor)
        self._schedule_bottom_line()

    # ------------------------------------------------------------------ #
    #  Shift-key visual feedback                                           #
    # ------------------------------------------------------------------ #

    def _update_shift_labels(self):
        sh = self._shift_held
        for rid, w in self._widgets.items():
            if w.get("is_group"):
                continue
            w["minus"].setText("-1" if sh else "-5")
            w["plus"].setText("+1" if sh else "+5")
            running = rid in self.timers and self.timers[rid].running
            w["toggle"].setText(self._toggle_label(running))
            # X does NOT change on Shift any more — see _on_remove. It stays
            # X because that is the only thing it does.

    # ------------------------------------------------------------------ #
    #  Undo                                                                #
    # ------------------------------------------------------------------ #

    def _undo_last(self):
        cmd = self._undo.peek()
        if cmd is None:
            self.show_toast("Nothing to undo", 3)
            return
        mode = "revert"
        conflicts = cmd.conflicts(self.timers)
        if conflicts:
            mode = self._ask_undo_mode(conflicts)
            if mode is None:
                return             # cancelled — leave it on the stack
        self._undo.pop()
        # Bank the current state first: undo is itself a change, and the
        # snapshot history should be able to get back past it.
        self._try_snapshot(reason="pre_undo", priority="medium")
        cmd.undo(self._state, self.timers, mode)
        self._save_state()
        self._rebuild_rows()
        self._shrink_to_fit()
        self.show_toast(f"Undid {cmd.label}", 4)

    def _ask_undo_mode(self, conflicts):
        """Ask what to do with time accrued since a reset. None == cancel."""
        listing = "\n".join(f"    {name} — {format_time(secs)}"
                            for name, secs in conflicts)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Undo Reset")
        box.setText("These timers have accumulated time since being reset:")
        box.setInformativeText(
            f"{listing}\n\n"
            "Add that time on top of the restored values, or revert them to "
            "exactly what they were before the reset?")
        add_btn    = box.addButton("Add Time", QMessageBox.AcceptRole)
        revert_btn = box.addButton("Revert Exactly", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(add_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is add_btn:
            return "add"
        if clicked is revert_btn:
            return "revert"
        return None

    def keyPressEvent(self, event):
        # Handled here rather than as a QShortcut on purpose: a focused
        # QLineEdit (the inline rename editor) consumes Ctrl+Z for its own
        # undo before it ever reaches the window, which is what you want.
        if event.matches(QKeySequence.Undo):
            self._undo_last()
            return
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self._shift_held = True
            self._update_shift_labels()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self._shift_held = False
            self._update_shift_labels()
        super().keyReleaseEvent(event)

    def leaveEvent(self, event):
        # The window itself noticed the exit — take the fast path rather than
        # waiting up to one poll interval.
        self._sync_hover_to_cursor()
        super().leaveEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                if self._shift_held:
                    self._shift_held = False
                    self._update_shift_labels()
                if self._drag.active:
                    self._drag.end()
        super().changeEvent(event)

    # ------------------------------------------------------------------ #
    #  Event filter — delegates to DragController                          #
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        # An open inline editor owns its own keys and focus, unconditionally.
        if self._inline_editor is not None and obj is self._inline_editor[0]:
            if (event.type() == QEvent.KeyPress
                    and event.key() == Qt.Key_Escape):
                self._end_inline_rename(commit=False)
                return True
            if event.type() == QEvent.FocusOut:
                # Clicking away saves, same as pressing Enter.
                self._end_inline_rename(commit=True)
                return False

        if self._drag.active:
            return self._drag.handle_event(obj, event)

        if self._scroll_area is not None and obj is self._scroll_area.viewport():
            # Wheel over the row viewport: advance by whole rows, not pixels.
            if event.type() == QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta:
                    notches = -delta / 120.0
                    rows = int(notches) or (1 if notches > 0 else -1)
                    self._scroll_by_rows(rows)
                return True
            # A shorter viewport puts a different row against the bottom edge,
            # and can push a running timer out of view.
            if event.type() == QEvent.Resize:
                self._update_bottom_line()
                self._schedule_bottom_line()
                self._update_status()

        # The footer status line is a click target too.
        if obj is getattr(self, "_status_lbl", None):
            if event.type() == QEvent.Enter:
                self._on_status_hover(True)
            elif event.type() == QEvent.Leave:
                self._on_status_hover(False)
            elif (event.type() == QEvent.MouseButtonPress
                  and event.button() == Qt.LeftButton):
                self._on_status_click()
                return True
            elif event.type() == QEvent.ContextMenu:
                self._on_status_context()
                return True

        # The name label is its own target: double-click renames. It still has
        # to drive the row's hover underline, because entering it sends Leave
        # to the container.
        name_rid = self._name_labels.get(obj)
        if name_rid is not None:
            if event.type() == QEvent.Enter:
                self._on_row_hover(name_rid, True)
            elif event.type() == QEvent.Leave:
                self._on_row_hover(name_rid, False)
            elif (event.type() == QEvent.MouseButtonDblClick
                  and event.button() == Qt.LeftButton):
                self._begin_inline_rename(name_rid)
                return True

        # The time label is its own hover/click target inside the row.
        time_rid = self._time_labels.get(obj)
        if time_rid is not None:
            if event.type() == QEvent.Enter:
                self._on_time_hover(time_rid, True)
            elif event.type() == QEvent.Leave:
                self._on_time_hover(time_rid, False)
            elif (event.type() == QEvent.MouseButtonPress
                  and event.button() == Qt.LeftButton):
                self._copy_timer_time(time_rid)
                return True

        if event.type() in (QEvent.Enter, QEvent.Leave):
            # Any crossing in or out of a row or one of its children is a
            # reason to re-ask where the pointer is; the answer, not the
            # event, decides which row is tinted.
            if (obj in self._row_children
                    or obj in self._name_labels
                    or obj in self._time_labels
                    or self._drag.rid_for_container(obj) is not None
                    or (self._scroll_area is not None
                        and obj is self._scroll_area.viewport())):
                self._sync_hover_to_cursor()

        if self._rearranging and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                rid = self._drag.rid_for_container(obj)
                if rid is not None:
                    self._drag.start(rid)
                    return True

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    #  Button handlers                                                     #
    # ------------------------------------------------------------------ #

    def _toggle_label(self, running):
        """What the single Start/Stop button should read right now."""
        if running:
            return "Stop"                 # shift is irrelevant once running
        return "Add" if self._shift_held else "Start"

    def _on_toggle_timer(self, rowid):
        """The one button: stop it if it runs, start it if it doesn't."""
        if rowid in self.timers and self.timers[rowid].running:
            self._on_stop(rowid)
        else:
            self._on_start(rowid)

    def _on_start(self, rowid):
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            self._start_additional(rowid)
        else:
            self._start_exclusive(rowid)
        self._save_state()
        self._update_status()

    def _on_stop(self, rowid):
        self._stop_one(rowid)
        self._save_state()
        self._update_status()

    def _on_adjust(self, rowid, direction):
        minutes = 1 if (QApplication.keyboardModifiers() & Qt.ShiftModifier) else 5
        self.timers[rowid].adjust(direction * minutes * 60)
        self._update_display(rowid)
        self._update_parent_group_time(rowid)
        self._save_state()
        self._update_status()

    def _push_delete_undo(self, rowid):
        """Record everything needed to put a row back. Call before removing.

        Returns the row's name so the caller can name it in a toast.
        """
        row = next((r for r in self._state.rows if r["rowid"] == rowid), None)
        if row is None:
            return None
        ts = self.timers.get(rowid)
        self._undo.push(DeleteRow(
            f"the deletion of '{row['name']}'",
            row=dict(row),
            index=self._state.rows.index(row),
            elapsed=ts.current_elapsed if ts is not None else 0.0,
            was_collapsed=rowid in self._state.collapsed_groups,
        ))
        return row["name"]

    def _on_add(self):
        raw  = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self._state.rows.append({"rowid": rid, "name": name, "type": "timer", "bg": None})
        self.timers[rid] = TimerState(name)
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()

    def _on_add_group(self):
        raw  = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self._state.rows.append({"rowid": rid, "name": name, "type": "separator", "bg": None})
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()

    def _confirm(self, setting, title, question, disabled_msg):
        """Ask before something destructive, with a 'Don't ask again' opt-out.

        `setting` names the Settings flag that gates the prompt. The tickbox
        only takes effect when the user actually confirms — ticking it and
        then backing out shouldn't silently disarm the next one.
        """
        if not getattr(self._state.settings, setting):
            return True
        t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        box = QMessageBox(QMessageBox.Question, title, question,
                          QMessageBox.Yes | QMessageBox.No, self)
        # Same box as "Show Zero Times" in the session preview — a plain
        # QCheckBox here renders no box and no tick at all.
        never_again = TickCheckBox("Don't ask again", t["row_running_fg"])
        never_again.setStyleSheet(TickCheckBox.style_for(t))
        box.setCheckBox(never_again)
        if box.exec() != QMessageBox.Yes:
            return False
        if never_again.isChecked():
            setattr(self._state.settings, setting, False)
            self._save_state()
            self.show_toast(disabled_msg, 6)
        return True

    def _confirm_delete(self, question):
        return self._confirm(
            "confirm_delete", "Confirm Delete", question,
            "Delete confirmations off, can be toggled back on in Settings")

    def _confirm_reset(self, question):
        return self._confirm(
            "confirm_reset", "Confirm Reset", question,
            "Reset confirmations off, can be toggled back on in Settings")

    def _on_remove_group(self, rowid):
        name = next(
            (r["name"] for r in self._state.rows if r["rowid"] == rowid), "")
        if not self._confirm_delete(f"Delete group '{name}'?"):
            return
        self._push_delete_undo(rowid)
        self._state.collapsed_groups.discard(rowid)
        self._state.rows = [r for r in self._state.rows if r["rowid"] != rowid]
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()
        self._shrink_to_fit()
        self.show_toast(f"Deleted group '{name}' — Ctrl+Z to undo", 5)

    def _on_group_toggle(self, rowid):
        if rowid in self._state.collapsed_groups:
            self._state.collapsed_groups.discard(rowid)
        else:
            self._state.collapsed_groups.add(rowid)
        self._save_state()
        self._rebuild_rows()
        self._shrink_to_fit()

    def _on_remove(self, rowid):
        # X deletes. It used to reset the timer instead when Shift was held,
        # relabelling itself to "0" — a leftover from when X was permanently
        # on every row. It stopped making sense once X appeared only in edit
        # mode: a modifier on an already-hidden button is unfindable, and the
        # two outcomes are wildly mismatched. Mistime the Shift and you
        # delete a row when you meant to zero it. Reset Time lives in the
        # right-click menu, which says what it does.
        name = next(
            (r["name"] for r in self._state.rows if r["rowid"] == rowid), "")
        if not self._confirm_delete(f"Delete '{name}'?"):
            return
        self._push_delete_undo(rowid)
        self.timers[rowid].stop()
        del self.timers[rowid]
        self._state.rows = [r for r in self._state.rows if r["rowid"] != rowid]
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()
        self._shrink_to_fit()
        self.show_toast(f"Deleted '{name}' — Ctrl+Z to undo", 5)

    def _on_rearrange_toggle(self):
        self._rearranging = not self._rearranging
        self._rebuild_rows()
        # Rearrange mode gives every row a uniform line_gap so drags never
        # resize anything — absorb that height change here, at the toggle.
        self._shrink_to_fit()

    # ------------------------------------------------------------------ #
    #  Hover and context menu                                              #
    # ------------------------------------------------------------------ #

    def _sync_hover_to_cursor(self):
        """Re-derive the hover, then keep polling for as long as one is up."""
        self._sync_hover_impl()
        want = (self._hovered_rid is not None
                and self._drag.dragging_rid is None)
        if want and not self._hover_poll.isActive():
            self._hover_poll.start()
        elif not want and self._hover_poll.isActive():
            self._hover_poll.stop()

    def _sync_hover_impl(self):
        """Tint whichever row the pointer is actually inside.

        Enter/Leave can't express this on their own:

        * Entering a CHILD sends Leave to the row container, so the row
          un-tints the moment the pointer crosses onto a Start button — even
          though it is plainly still inside the row.
        * A rebuild swaps in fresh containers under a stationary pointer and
          never generates an Enter for the new one, so after a drag ends the
          row under the mouse comes back untinted until you move.

        Both go away once the tint is derived from the cursor position rather
        than from the last crossing event.
        """
        if self._drag.dragging_rid is not None:
            return                       # a drag owns the tint and the strip
        pos = QCursor.pos()
        target = None
        if self._scroll_area is not None:
            vp = self._scroll_area.viewport()
            # Rows scrolled out of view still have geometry; the viewport
            # test keeps the pointer from "hovering" one of them.
            if vp.rect().contains(vp.mapFromGlobal(pos)):
                for rid, w in self._widgets.items():
                    rc = w.get("container")
                    if rc is None:
                        continue
                    try:
                        if (rc.isVisible()
                                and rc.rect().contains(rc.mapFromGlobal(pos))):
                            target = rid
                            break
                    except RuntimeError:
                        continue         # container died with a rebuild
        if target == self._hovered_rid:
            if target is not None:
                # Same row, but a rebuild may have moved it — re-place.
                self._on_row_hover(target, True)
            return
        prev, self._hovered_rid = self._hovered_rid, target
        if prev is not None:
            self._on_row_hover(prev, False)
        if target is not None:
            self._on_row_hover(target, True)

    def _on_row_hover(self, rid, entering):
        """Tint the whole row rather than underlining its name.

        Driven by a dynamic property against a selector baked into the row's
        stylesheet, NOT by rewriting that stylesheet. _update_bottom_line
        already rewrites row stylesheets — it strips the separator under the
        bottom-most row and remembers the exact string it replaced so it can
        put it back. If hover edited the same string, scrolling while hovering
        would capture the tinted version as the row's resting colour and the
        highlight would stick after the mouse left.
        """
        w = self._widgets.get(rid)
        if not w:
            return
        rc = w.get("container")
        if rc is None:
            return
        if self._drag.dragging_rid is not None:
            # A drag owns the strip; don't let a stray Enter steal it.
            return
        rc.setProperty("hov", "1" if entering else "")
        rc.style().unpolish(rc)
        rc.style().polish(rc)
        # Group headers are a bordered box, not an open row — running their
        # fill up into the gap reads as a tab sticking out of the top of it.
        # Passing None also clears a strip left over from the row before.
        fill = (entering and not w.get("is_group"))
        self._place_hover_strip(rc, w.get("bg_left", 0) if fill else None)

    def _sync_drag_strip(self):
        """Extend the dragged row's fill into the gap above it too.

        The drag colour is painted by the row's own #rowBg rule (row_factory
        picks row_drag_bg when is_dragging), so it stops at the row's rect
        exactly like the hover tint used to — and a rebuild clears the strip.
        Re-place it after every rebuild while a drag is live.
        """
        rid = self._drag.dragging_rid
        if rid is None or rid not in self._widgets:
            return
        # The strip is positioned from the row's laid-out y(), and callers
        # reach here at different points in the layout cycle — _rebuild_rows
        # right after swapping the tree in, before Qt has assigned geometry.
        # Reading a stale y() puts the fill on the row's previous position.
        self._grid.activate()
        w = self._widgets[rid]
        if w.get("is_group"):
            # Same reason as hover: a bordered header doesn't take the fill.
            self._place_hover_strip(None, None)
            return
        rc = w.get("container")
        if rc is not None:
            t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
            self._place_hover_strip(rc, w.get("bg_left", 0), t["row_drag_bg"])

    def _live_strip(self):
        """The hover strip, or None once Qt has destroyed it.

        Every rebuild swaps in a fresh content tree and deletes the old one,
        taking the strip with it — but the Python wrapper survives, so the
        attribute is not None and ANY call on it raises RuntimeError. That
        includes the parentWidget() call used to notice the swap, which is
        how this first showed up: unlock, hover, stack trace per mouse move.
        _rebuild_rows clears the attribute so this is normally moot; the
        guard covers deletions from anywhere else (a close, a deleteLater).
        """
        strip = self._hover_strip
        if strip is None:
            return None
        try:
            strip.parentWidget()
        except RuntimeError:
            self._hover_strip = None
            return None
        return strip

    def _place_hover_strip(self, rc, bg_left, color=None):
        """Fill the layout gap ABOVE a hovered row so the tint reads as the
        whole row, flush with the separator above it.

        A widget cannot paint outside its own rect — a negative margin-top is
        clipped away (and moves the box out of its own clip, so the row loses
        its fill entirely). Growing the rows by v_spacing and zeroing the grid
        spacing would work, but that is the row-pitch arithmetic the whole
        window-snapping model rests on, for four pixels of polish.

        So: one reusable strip, positioned in the gap. It is parented INSIDE
        the scrolled content, so it scrolls with the rows for free and needs
        no handling on scroll.
        """
        strip = self._live_strip()
        if bg_left is None:
            if strip is not None:
                strip.hide()
            return
        gap = self._grid.spacing()
        parent = rc.parentWidget()
        if gap <= 0 or parent is None or rc.y() < gap:
            # No gap, or this is the top row and there is nothing above it.
            if strip is not None:
                strip.hide()
            return
        # A strip left over from a previous content tree belongs to a dead
        # parent — build a fresh one.
        if strip is None or strip.parentWidget() is not parent:
            strip = QWidget(parent)
            strip.setObjectName("hoverStrip")
            strip.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._hover_strip = strip
        if color is None:
            t = THEMES.get(self._state.settings.theme,
                           THEMES["E-Ink (Default)"])
            color = t["row_hover_bg"]
        strip.setStyleSheet(
            f"#hoverStrip {{ background-color: {color}; }}")
        # One extra pixel of height, but ONLY for a row carrying a graphics
        # effect — i.e. the row being dragged.
        #
        # Such a row is rendered through an offscreen pixmap, and compositing
        # that pixmap under fractional display scaling (125%, 150% — the
        # Windows default on most laptops) rounds its top edge away, letting
        # the parent show through for one device pixel. The row pitch is odd,
        # so the rounding flips with the row's y parity and the seam appears
        # on every other position. Measured: no effect = never; effect =
        # alternating; effect + the extra pixel = never.
        #
        # Conditional, not always on, because the strip is RAISED above the
        # row: any overlap covers the top pixel of the row's contents too. On
        # a bordered button that eats the top of the border and reads as the
        # button being sliced. A hovered row has no effect and therefore no
        # seam, so it must not pay that cost.
        overlap = 1 if rc.graphicsEffect() is not None else 0
        strip.setGeometry(rc.x() + bg_left, rc.y() - gap,
                          max(0, rc.width() - bg_left), gap + overlap)
        strip.raise_()
        strip.show()

    def _on_time_hover(self, rid, entering):
        """The time is a click target nested inside an already-tinted row.

        The tint marks the row; the underline marks the smaller target within
        it, so the two read as nested rather than saying the same thing twice.
        Entering the label sends Leave to the container, which would drop the
        tint — so re-assert it here.
        """
        if rid not in self._widgets:
            return
        self._sync_hover_to_cursor()
        lbl = self._widgets[rid].get("time")
        if lbl is not None:
            f = lbl.font()
            f.setUnderline(entering)
            lbl.setFont(f)

    # ------------------------------------------------------------------ #
    #  Footer status line (locked mode)                                    #
    # ------------------------------------------------------------------ #

    def _running_rids(self):
        return [rid for rid, ts in self.timers.items() if ts.running]

    def _running_offscreen(self):
        """True when a running timer isn't fully in view.

        This is what the dot's colour reports: muted when everything running
        is on screen (nothing to tell you), accent when it isn't.
        """
        if self._scroll_area is None:
            return False
        top = self._scroll_area.verticalScrollBar().value()
        bot = top + self._scroll_area.viewport().height()
        for rid in self._running_rids():
            w = self._widgets.get(rid, {}).get("container")
            if w is None:
                return True          # hidden inside a collapsed group
            if w.y() < top or w.y() + w.height() > bot:
                return True
        return False

    def _update_status(self):
        """Refresh the locked footer's status line.

        Deliberately additive: the total is the plain sum of every row, so it
        always matches what a user gets by adding the rows up by hand. Running
        two timers at once therefore advances it at 2s/s — the count sitting
        right beside it is what explains that.
        """
        if not hasattr(self, "_status_lbl"):
            return
        t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        running = len(self._running_rids())
        total   = sum(ts.current_elapsed for ts in self.timers.values())
        # "Today" only means anything while daily reset is drawing the
        # boundary. With it off, session_start never advances on its own, so
        # the app can't honestly name the period — so it doesn't claim one.
        daily   = self._state.settings.daily_reset_enabled
        period  = "Today" if daily else "Total"
        parts = []
        if running:
            # Accent only when there's something you can't already see —
            # otherwise the dot is just punctuation and shouldn't shout.
            dot = (t["row_running_fg"] if self._running_offscreen()
                   else t["app_fg_muted"])
            parts.append(f"<span style='color: {dot};'>●</span>"
                         f" {running} running")
        parts.append(f"{period} {format_time(total)}")
        self._status_lbl.setText(" · ".join(parts))
        self._status_lbl.setToolTip(
            f"Since {self._state.session_start.strftime('%#I:%M %p')}"
            if daily else "")

    def _on_status_hover(self, entering):
        f = self._status_lbl.font()
        f.setUnderline(entering)
        self._status_lbl.setFont(f)

    def _on_status_click(self):
        """Copy every time, whatever is running.

        Copying used to be reachable only when nothing was running, because
        the click did double duty as jump-to-the-running-row. But the times
        you most want on the clipboard are the ones still accruing, and
        _copy_session already reports current_elapsed, so a running timer
        copies as it reads right now. Jump moved to right-click, which is
        where every other secondary action in this app lives.
        """
        self._copy_session()

    def _on_status_context(self):
        """Right-click the status line: go to whatever is running."""
        running = self._running_rids()
        if not running:
            return
        if len(running) == 1:
            self._scroll_to_row(running[0])
        else:
            # More than one and no unambiguous target, so ask. Same menu
            # machinery as the row context menu.
            menu = QMenu(self)
            menu.setStyleSheet(build_menu_stylesheet(self._state.settings.theme))
            actions = {menu.addAction(self.timers[rid].name): rid
                       for rid in running}
            chosen = menu.exec(self._status_lbl.mapToGlobal(
                self._status_lbl.rect().bottomLeft()))
            if chosen is not None:
                self._scroll_to_row(actions[chosen])

    def _scroll_to_row(self, rid):
        """Bring a row into view, keeping the viewport flush to a boundary."""
        # A timer inside a collapsed group has no widget on screen at all.
        parent = self._parent_group(rid)
        if parent is not None and parent in self._state.collapsed_groups:
            self._state.collapsed_groups.discard(parent)
            self._save_state()
            self._rebuild_rows()
            self._shrink_to_fit()
        w = self._widgets.get(rid, {}).get("container")
        if w is None or self._scroll_area is None:
            return
        bar = self._scroll_area.verticalScrollBar()
        view_top = bar.value()
        view_bot = view_top + self._scroll_area.viewport().height()
        if w.y() >= view_top and w.y() + w.height() <= view_bot:
            return                       # already fully visible, don't jolt
        # Its own y() is by definition a flush position — see _row_offsets.
        bar.setValue(min(w.y(), bar.maximum()))

    def _session_lines(self):
        """Every non-zero time, one row per line, in display order.

        Split out from _copy_session so it can be checked without going
        through the system clipboard — that is a single global resource
        shared with every other app on the machine, so a test that reads it
        back fails whenever something else happens to hold it.

        current_elapsed, not elapsed: a running timer copies as it reads.
        """
        fmt = self._state.settings.copy_format
        lines = []
        for row in self._state.rows:
            if row["type"] != "timer":
                continue
            ts = self.timers.get(row["rowid"])
            if ts is None or ts.current_elapsed < 1:
                continue
            lines.append(f"{ts.name}: "
                         f"{format_copy_time(ts.current_elapsed, fmt)}")
        return lines

    def _copy_session(self):
        """Copy every non-zero time to the clipboard, one row per line."""
        lines = self._session_lines()
        if not lines:
            self.show_toast("No times to copy yet", 4)
            return
        QApplication.clipboard().setText("\n".join(lines))
        self.show_toast(
            f"{len(lines)} client time{'s' if len(lines) > 1 else ''} "
            f"copied to clipboard", 2.5)

    def _copy_timer_time(self, rid):
        """Copy a live timer's current time to the clipboard, and toast it."""
        if rid not in self.timers:
            return
        ts = self.timers[rid]
        time_str = format_copy_time(ts.current_elapsed,
                                    self._state.settings.copy_format)
        QApplication.clipboard().setText(time_str)
        self.show_toast(f"Time for {ts.name} ({time_str}) copied to clipboard",
                        2.5)

    def _begin_inline_rename(self, rowid):
        """Edit a row's name in place — the label becomes a text box.

        The editor is an OVERLAY on top of the label, not a swap for it: the
        label stays in the layout, so the row's geometry cannot shift. Every
        row being exactly one height is what the window's row-snapping rests
        on, and a widget swap here would put that at risk for a cosmetic win.
        """
        self._end_inline_rename(commit=False)      # only one open at a time
        w = self._widgets.get(rowid) or {}
        lbl, container = w.get("name"), w.get("container")
        if lbl is None or container is None:
            return
        t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])

        editor = QLineEdit(lbl.text(), container)
        font = QFont(lbl.font())
        font.setUnderline(False)      # the hover underline shouldn't carry in
        editor.setFont(font)
        editor.setAlignment(lbl.alignment())
        editor.setMaxLength(120)
        editor.setStyleSheet(
            f"QLineEdit {{ color: {t['control_fg']};"
            f" background-color: {t['control_bg']};"
            # Always a visible edge, even on themes whose control_border_px is
            # 0 — an open editor has to read as one.
            f" border: 1px solid {t['control_line']};"
            f" padding: 0px 1px; }}")
        editor.setGeometry(lbl.geometry())
        editor.installEventFilter(self)
        editor.returnPressed.connect(
            lambda: self._end_inline_rename(commit=True))
        editor.show()
        editor.setFocus(Qt.OtherFocusReason)
        editor.selectAll()
        self._inline_editor = (editor, rowid)

    def _end_inline_rename(self, commit):
        # Cleared FIRST: tearing the editor down fires its own focus-out,
        # which lands back here and must find nothing to do.
        state, self._inline_editor = self._inline_editor, None
        if state is None:
            return
        editor, rowid = state
        try:
            text = editor.text()
        except RuntimeError:
            return                     # editor died with a rebuild
        editor.removeEventFilter(self)
        editor.hide()
        editor.deleteLater()
        if commit:
            self._apply_rename(rowid, text)

    def _apply_rename(self, rowid, text):
        row = next((r for r in self._state.rows if r["rowid"] == rowid), None)
        if row is None:
            return
        new_name = _SANITIZE.sub("", text).strip()
        if not new_name or new_name == row["name"]:
            return
        self._undo.push(RenameRow(
            f"renaming '{row['name']}'", rowid, row["name"]))
        row["name"] = new_name
        if row["type"] == "timer" and rowid in self.timers:
            self.timers[rowid].name = new_name
        self._sync_scrub_terms()
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()
        self._shrink_to_fit()

    def _on_row_context_menu(self, rowid, global_pos):
        row = next((r for r in self._state.rows if r["rowid"] == rowid), None)
        if row is None:
            return
        is_timer = row["type"] == "timer"

        menu = QMenu(self)
        menu.setStyleSheet(build_menu_stylesheet(self._state.settings.theme))

        rename_action = menu.addAction("Rename")
        menu.addSeparator()
        set_color    = menu.addAction("Set Color")
        reset_color  = menu.addAction("Reset Color")
        # View actions live here rather than in the footer: they're episodic,
        # and the right-click menu is reachable from any row, so they cost no
        # permanent screen space.
        menu.addSeparator()
        group_rids   = [r["rowid"] for r in self._state.rows
                        if r["type"] == "separator"]
        collapsed    = self._state.collapsed_groups
        collapse_all = menu.addAction("Collapse All")
        expand_all   = menu.addAction("Expand All")
        collapse_all.setEnabled(any(g not in collapsed for g in group_rids))
        expand_all.setEnabled(any(g in collapsed for g in group_rids))

        menu.addSeparator()
        # Only when something is actually running — otherwise it is a dead
        # entry on every menu. Here rather than in the footer: starting is
        # exclusive by default, so 0 or 1 timers run and the row's own button
        # already covers the common case. This is for "I'm done, stop
        # whatever is going" without hunting for the row.
        running = self._running_rids()
        stop_all = menu.addAction("Stop All Timers") if running else None
        set_time      = menu.addAction("Set Time") if is_timer else None
        reset_time    = menu.addAction("Reset Time") if is_timer else None
        if reset_time is not None:
            reset_time.setEnabled(self.timers[rowid].current_elapsed >= 1)
        reset_all     = menu.addAction("Reset ALL Times")
        reset_all.setEnabled(
            any(ts.current_elapsed >= 1 for ts in self.timers.values()))
        delete_action = menu.addAction("Delete")

        action = menu.exec(global_pos)
        if action is None:
            return

        if stop_all is not None and action == stop_all:
            # No rebuild: _stop_all calls _set_bold per row, which is already
            # the one place the toggle button's label follows. Same shape as
            # _on_stop, just plural.
            self._stop_all()
            self._save_state()
            self._update_status()
            return
        if action == reset_all:
            self._reset_all()
            return
        if is_timer and action == reset_time:
            self._reset_one(rowid)
            return

        if action in (collapse_all, expand_all):
            if action == collapse_all:
                collapsed.update(group_rids)
            else:
                collapsed.clear()
            self._save_state()
            self._rebuild_rows()
            self._shrink_to_fit()
            return

        if action == rename_action:
            self._begin_inline_rename(rowid)
        elif action == set_color:
            current_bg = row.get("bg")
            initial    = QColor(current_bg) if current_bg else QColor(255, 255, 255)
            cdlg = QColorDialog(initial, self)
            cdlg.setStyleSheet(
                "QColorDialog { background-color: #2a2a2a; }"
                "QLabel { color: #FFFFFF; background: transparent; }"
                "QPushButton { color: #FFFFFF; background-color: #555555;"
                "  border: 1px solid #777; padding: 4px 8px; }"
                "QPushButton:hover { background-color: #666666; }"
                "QLineEdit { color: #FFFFFF; background-color: #555555;"
                "  border: 1px solid #777; }"
                "QSpinBox { color: #FFFFFF; background-color: #555555;"
                "  border: 1px solid #777; }"
            )
            if cdlg.exec() == QDialog.Accepted:
                row["bg"] = cdlg.currentColor().name()
                self._save_state()
                self._try_snapshot(reason="layout_change", priority="medium")
                self._rebuild_rows()
        elif action == reset_color:
            row["bg"] = None
            self._save_state()
            self._try_snapshot(reason="layout_change", priority="medium")
            self._rebuild_rows()
        elif is_timer and action == set_time:
            current = format_time(self.timers[rowid].current_elapsed)
            text, ok = QInputDialog.getText(
                self, "Set Time", "Enter time (HH:MM:SS):", text=current)
            if ok and text.strip():
                secs = self._parse_time_input(text.strip())
                if secs is not None:
                    ts = self.timers[rowid]
                    was_running = ts.running
                    if was_running:
                        ts.stop()
                    ts.elapsed = secs
                    if was_running:
                        ts.start()
                    self._update_display(rowid)
                    self._update_parent_group_time(rowid)
                    self._save_state()
        elif action == delete_action:
            if not self._confirm_delete(f"Delete '{row['name']}'?"):
                return
            self._push_delete_undo(rowid)
            if is_timer:
                self.timers[rowid].stop()
                del self.timers[rowid]
            else:
                self._state.collapsed_groups.discard(rowid)
            self._state.rows = [r for r in self._state.rows if r["rowid"] != rowid]
            self._save_state()
            self._try_snapshot(reason="layout_change", priority="medium")
            self._rebuild_rows()
            self._shrink_to_fit()
            label = "" if is_timer else "group "
            self.show_toast(
                f"Deleted {label}'{row['name']}' — Ctrl+Z to undo", 5)

    @staticmethod
    def _parse_time_input(text):
        parts = text.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 1:
                return int(parts[0]) * 60
        except ValueError:
            return None
        return None

    # ------------------------------------------------------------------ #
    #  Settings dialog                                                     #
    # ------------------------------------------------------------------ #

    def _on_config(self):
        dlg = ConfigDialog(self, self._state.settings.to_dict(), on_reset=self._reset_all)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.restore_path:
            self._restore_from_snapshot(dlg.restore_path, dlg.restore_mode)
            return
        if not dlg.style_changed:
            return

        old_aot = self._state.settings.always_on_top

        s = self._state.settings
        s.theme                = dlg.chosen_theme
        s.size                 = dlg.chosen_size
        s.font                 = dlg.chosen_font
        s.label_align          = dlg.chosen_label_align
        s.client_separators    = dlg.chosen_client_separators
        s.show_group_count     = dlg.chosen_show_group_count
        s.show_group_time      = dlg.chosen_show_group_time
        s.always_on_top        = dlg.chosen_always_on_top
        s.confirm_delete       = dlg.chosen_confirm_delete
        s.confirm_reset        = dlg.chosen_confirm_reset
        s.recover_running_time = dlg.chosen_recover_running_time
        s.copy_format          = dlg.chosen_copy_format
        old_dr_enabled = s.daily_reset_enabled
        old_dr_time    = s.daily_reset_time
        s.daily_reset_enabled  = dlg.chosen_daily_reset_enabled
        s.daily_reset_time     = dlg.chosen_daily_reset_time

        # If daily reset was just enabled or the time changed, anchor
        # session_start to now so only future boundaries trigger resets.
        if (s.daily_reset_enabled
                and (not old_dr_enabled or s.daily_reset_time != old_dr_time)):
            self._state.session_start = datetime.now().astimezone()
        s.show_adjust_buttons  = dlg.chosen_show_adjust_buttons

        self._save_state()
        self._try_snapshot(reason="layout_change", priority="high")

        self._apply_style()
        self._rebuild_rows()
        self._shrink_to_fit()

        if self._state.settings.always_on_top != old_aot:
            if sys.platform == "win32":
                hwnd = int(self.winId())
                flag = -1 if self._state.settings.always_on_top else -2
                ctypes.windll.user32.SetWindowPos(
                    hwnd, flag, 0, 0, 0, 0, 0x0013)
            else:
                self.setWindowFlag(
                    Qt.WindowStaysOnTopHint, self._state.settings.always_on_top)
                self.show()

    @staticmethod
    def _timer_rows(rows):
        return [r for r in rows if r["type"] == "timer"]

    def _restore_times_only(self, snap):
        """Bring back elapsed times for clients that still exist. Layout,
        ordering and groups are left exactly as they are.

        Matched on rowid first, then on name — a client that was deleted and
        re-added has a new rowid but is still, to the user, the same client.
        """
        live_rids = {r["rowid"] for r in self._timer_rows(self._state.rows)}
        by_name = {}
        for r in self._timer_rows(self._state.rows):
            by_name.setdefault(r["name"], []).append(r["rowid"])

        used, count = set(), 0
        for row in self._timer_rows(snap.rows):
            rid = row["rowid"]
            target = rid if (rid in live_rids and rid not in used) else None
            if target is None:
                for cand in by_name.get(row["name"], []):
                    if cand not in used:
                        target = cand
                        break
            if target is None or target not in self.timers:
                continue
            used.add(target)
            ts = self.timers[target]
            ts.stop()
            ts.elapsed = float(
                snap.tracked_times.get(str(rid), {}).get("elapsed", 0.0))
            count += 1
        return count

    def _restore_rows_only(self, snap):
        """Restore the layout only.

        A row that survives the swap keeps the time it has right now; a row
        coming back from the snapshot starts at zero. Rows that exist live but
        aren't in the snapshot go away with it — that is what restoring a
        layout means. The pre-restore snapshot is the way back.
        """
        kept = {rid: ts.current_elapsed for rid, ts in self.timers.items()}
        kept_by_name = {}
        for r in self._timer_rows(self._state.rows):
            if r["rowid"] in kept:
                kept_by_name.setdefault(r["name"], []).append(r["rowid"])

        self._state.rows = [dict(r) for r in snap.rows]
        self._state.collapsed_groups = set(snap.collapsed_groups)
        self.timers = {}
        used, carried = set(), 0
        for row in self._timer_rows(self._state.rows):
            rid = row["rowid"]
            elapsed = 0.0
            if rid in kept and rid not in used:
                elapsed = kept[rid]
                used.add(rid)
                carried += 1
            else:
                for cand in kept_by_name.get(row["name"], []):
                    if cand not in used:
                        elapsed = kept[cand]
                        used.add(cand)
                        carried += 1
                        break
            self.timers[rid] = TimerState(row["name"], elapsed=elapsed)
        return carried

    def _restore_from_snapshot(self, path: Path, mode: str = "all"):
        """mode: 'all' (times + rows), 'times' (times only), 'rows' (layout)."""
        try:
            new_state = AppState.load(path)
        except Exception:
            log.exception(f"Failed to restore from snapshot '{path}'.")
            self.show_toast("Restore failed — current state unchanged", 5)
            return
        # Every mode below overwrites live data, so bank what's here first.
        self._try_snapshot(reason="pre_restore", priority="high")
        # Queued undo commands point at rows this is about to replace.
        self._undo.clear()
        self._stop_all()

        if mode == "times":
            n = self._restore_times_only(new_state)
            summary = (f"Restored times for {n} client{'' if n == 1 else 's'}"
                       if n else "No matching clients — nothing restored")
        elif mode == "rows":
            n = self._restore_rows_only(new_state)
            summary = (f"Restored rows, kept {n} live time"
                       f"{'' if n == 1 else 's'}")
        else:
            self._state = new_state
            self.timers = {}
            for row in self._timer_rows(self._state.rows):
                rid = row["rowid"]
                tt  = self._state.tracked_times.get(str(rid), {})
                # Don't restore running_since — timers start stopped after restore
                self.timers[rid] = TimerState(row["name"],
                                              elapsed=tt.get("elapsed", 0.0))
            # The snapshot brought its own session_start with it. If that is
            # older than today's reset boundary — which it is for anything
            # taken before this morning — the next _tick fires the daily
            # reset, archives what was just restored and zeroes it. Within a
            # second. It reads as "restore is broken".
            #
            # max(), not assignment: a snapshot from later than the boundary
            # keeps its own start, so this is a no-op in the common case and
            # only ever moves the clock forward.
            self._state.session_start = max(
                self._state.session_start, self._most_recent_reset_boundary())
            summary = "Restored times and rows"

        self._next_rowid = max(
            (r["rowid"] for r in self._state.rows), default=-1) + 1
        self._state.save(self.timers)
        self._apply_style()
        self._rebuild_rows()
        self._shrink_to_fit()
        # Parse backup filename: state_YYYYMMDD_HHMMSS_nonce
        m = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", path.stem)
        if m:
            y, mo, d, h, mi, s = m.groups()
            time_str = f"{y}-{mo}-{d} {h}:{mi}:{s}"
        else:
            time_str = path.stem
        self.show_toast(f"{summary} ({time_str})", 5)

    # ------------------------------------------------------------------ #
    #  Timer control                                                       #
    # ------------------------------------------------------------------ #

    def _start_exclusive(self, rowid):
        self._stop_all()
        self._start_additional(rowid)

    def _start_additional(self, rowid):
        self.timers[rowid].start()
        self._set_bold(rowid, True)

    def _stop_all(self):
        for rid, ts in self.timers.items():
            if ts.running:
                ts.stop()
                self._set_bold(rid, False)
                self._update_display(rid)

    def _stop_one(self, rowid):
        ts = self.timers[rowid]
        if ts.running:
            ts.stop()
            self._set_bold(rowid, False)
            self._update_display(rowid)

    def _reset_one(self, rowid):
        """Zero a single timer, after confirming. Shared by shift-X and the
        context menu's Reset Time."""
        if rowid not in self.timers:
            return
        name = self.timers[rowid].name
        if not self._confirm_reset(f"Reset timer '{name}' to zero?"):
            return
        # Before the wipe, not after — the snapshot has to hold the time that
        # is about to be thrown away. Same treatment a deleted row gets.
        self._try_snapshot(reason="reset_timer", priority="medium")
        self._undo.push(ResetTimes(
            f"the reset of '{name}'",
            {rowid: self.timers[rowid].current_elapsed}))
        self.timers[rowid].stop()
        self.timers[rowid].reset()
        self._set_bold(rowid, False)
        self._update_display(rowid)
        self._update_parent_group_time(rowid)
        self._save_state()
        self._update_status()
        self.show_toast(f"Reset '{name}' — Ctrl+Z to undo", 5)

    def _reset_all(self):
        if not self._confirm_reset("Reset ALL times to zero?"):
            return
        # high priority: this is the largest data loss the app can do, so it
        # snapshots unconditionally rather than being debounced away.
        self._try_snapshot(reason="reset_all", priority="high")
        self._undo.push(ResetTimes(
            "the reset of all times",
            {rid: ts.current_elapsed for rid, ts in self.timers.items()}))
        self._stop_all()
        for ts in self.timers.values():
            ts.reset()
        self._save_state()
        self._rebuild_rows()
        self.show_toast("Reset all times to zero — Ctrl+Z to undo", 5)

    # ------------------------------------------------------------------ #
    #  Display helpers                                                     #
    # ------------------------------------------------------------------ #

    def _set_bold(self, rowid, bold, widget_dict=None):
        """Visual running marker for timer rows (bold + color + bullet)."""
        w = widget_dict or self._widgets.get(rowid)
        if not w or w.get("is_group"):
            return

        t         = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        normal_fg = t["app_fg"]
        running_fg = t["row_running_fg"]
        color     = running_fg if bold else normal_fg

        for key in ("name", "time"):
            lbl = w[key]
            f   = lbl.font()
            f.setBold(bold)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color};")

        b = w.get("bullet")
        if b is not None:
            b.setText("\u2022" if bold else "")
            b.setStyleSheet(f"color: {color};")

        # `bold` IS the running state, and this runs on every start and stop,
        # so it is the one place the single button's label has to follow.
        tgl = w.get("toggle")
        if tgl is not None:
            tgl.setText(self._toggle_label(bold))

        parent = self._parent_group(rowid)
        if parent is not None:
            self._update_group_bold(parent)

    def _update_group_bold(self, group_rowid):
        if group_rowid not in self._widgets:
            return

        has_running = any(
            self.timers[cid].running
            for cid in self._group_children(group_rowid)
            if cid in self.timers
        )

        t          = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        normal_fg  = t["group_fg"]
        running_fg = t["group_running_fg"]
        color      = running_fg if has_running else normal_fg

        w = self._widgets[group_rowid]
        for key in ("name", "time"):
            lbl = w[key]
            f   = lbl.font()
            f.setBold(has_running)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color};")

    def _update_display(self, rowid):
        if rowid in self._widgets:
            self._widgets[rowid]["time"].setText(
                format_time(self.timers[rowid].current_elapsed)
            )

    def _update_parent_group_time(self, rowid):
        """Refresh the parent separator's total after a child's time changed."""
        parent = self._parent_group(rowid)
        if parent is not None and parent in self._widgets:
            self._widgets[parent]["time"].setText(
                format_time(self._group_total_time(parent)))

    def _update_all_displays(self):
        for rid in self.timers:
            self._update_display(rid)

    def _auto_resize(self, width, height):
        """Resize the window ourselves, clamped to the user's height ceiling.

        Every programmatic resize goes through here so resizeEvent can tell
        our own resizes apart from the user dragging the window edge.
        """
        if self._user_resizing:
            # Hands off the window while the user is holding its edge.
            return
        # No ceiling clamp here: _shrink_to_fit owns that, and it may round
        # UP to the nearest whole row, which would land just above the
        # ceiling. Clamping here would undo the rounding.
        # resize() delivers its event asynchronously and the layout can queue
        # more of them, so neither a bare flag nor a size match is reliable
        # alone. Use both: remember the size we asked for, and stay "busy"
        # until the event loop has drained this turn's resize events.
        self._programmatic_resize = True
        self.resize(width, height)
        self._expected_size = self.size()
        QTimer.singleShot(0, self._end_programmatic_resize)

    def _end_programmatic_resize(self):
        self._programmatic_resize = False

    def showEvent(self, event):
        super().showEvent(event)
        # Showing the window emits resize events of its own. Without this the
        # very first one is mistaken for a user drag and pins the ceiling to
        # whatever height the window happened to open at.
        self._expected_size = self.size()
        QTimer.singleShot(0, self._mark_ready_for_user_resize)

    def _mark_ready_for_user_resize(self):
        self._ready_for_user_resize = True

    def nativeEvent(self, eventType, message):
        """Bracket interactive resizes so nothing fights the user's mouse."""
        if eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_ENTERSIZEMOVE:
                self._user_resizing = True
                self._resize_settle.stop()
            elif msg.message == _WM_EXITSIZEMOVE and self._user_resizing:
                self._user_resizing = False
                self._resize_settle.stop()
                self._on_resize_settled()
            elif (msg.message == _WM_SYSCOMMAND
                    and (msg.wParam & _SC_MASK) == _SC_MAXIMIZE):
                self._maximize_height()
                return True, 0        # swallow it — never actually maximize
        return super().nativeEvent(eventType, message)

    def _maximize_height(self):
        """What the maximize button does here: raise the height ceiling as far
        as fits and re-fit, instead of maximizing the window.

        A real maximize leaves Windows believing the window is maximized while
        this app's own sizing immediately resizes it back down — the window
        ends up in a state that only resolves once the user moves it.

        The window grows in place: it extends downward from where it already
        sits and never moves. So the reach is whatever room is left below it,
        not the whole screen — sitting low on the screen means a shorter
        window, which is the trade for the frame staying put.
        """
        screen = self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        # Room from the current top edge down to the bottom of the work area.
        usable = avail.bottom() - frame.top() + 1
        # window_height is a CLIENT height (resize() excludes the frame), so
        # take the decoration off before storing it. Capped at the screen's
        # own height in case the title bar has been dragged above the top.
        frame_h = max(0, frame.height() - self.height())
        ceiling = min(usable, avail.height()) - frame_h
        if ceiling <= 0:
            return

        self._state.window_height = ceiling
        self._shrink_to_fit()
        # Trim the ceiling down to a whole number of rows. Left at the raw
        # screen height, a later fit would round UP into the row that only
        # half fits and hang the window off the bottom edge.
        #
        # Derived arithmetically rather than by packing the rows that happen
        # to exist: with a short list that would return the CONTENT height,
        # and storing that as the ceiling would mean adding clients later
        # scrolled them instead of growing the window. Every row is the same
        # height by construction, so one pitch describes any row count.
        heights = self._row_heights()
        gap = self._grid.spacing()
        if heights and self._last_chrome is not None:
            pitch = heights[0] + gap
            rows_that_fit = (ceiling - self._last_chrome + gap) // pitch
            if rows_that_fit >= 1:
                exact = self._last_chrome + rows_that_fit * pitch - gap
                if exact != ceiling:
                    self._state.window_height = exact
                    self._shrink_to_fit()
        self._save_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Height only: the width shifts by itself when the scrollbar appears
        # or disappears, and comparing the full size would read our own
        # resize as the user's.
        if (not self._ready_for_user_resize or self._programmatic_resize
                or self._expected_size is None
                or event.size().height() == self._expected_size.height()):
            return
        # A resize we didn't ask for is the user dragging the edge: that
        # height becomes the new ceiling. Snapping waits until they stop,
        # otherwise the window fights the mouse mid-drag.
        # The toast rides on top of the ceiling rather than inside it, so
        # don't bake its height into the number being stored.
        self._state.window_height = event.size().height() - self._toast_height()
        # While the frame is being dragged, WM_EXITSIZEMOVE is what ends the
        # gesture. The timer is only a fallback for platforms without it.
        if not self._user_resizing:
            self._resize_settle.start()

    def _on_resize_settled(self):
        """User finished dragging the edge: snap to whole rows and remember."""
        self._shrink_to_fit()
        self._save_state()

    def _row_heights(self):
        """Heights of the row widgets, measured straight from the widgets.

        Deliberately NOT _grid_widget.sizeHint(): a QLayout caches its hint,
        and the rows grow slightly once the style has polished them, so the
        cached value reads short and the window ends up scrolling content
        that would have fitted.
        """
        out = []
        for i in range(self._grid.count()):
            w = self._grid.itemAt(i).widget()
            if w is not None:
                # Rows carry a fixed height (the uniform-height pass), which
                # sizeHint() alone doesn't reflect.
                out.append(max(w.sizeHint().height(), w.minimumHeight()))
        return out

    def _row_offsets(self):
        """Scroll positions at which each row sits flush with the viewport top.

        Read from the rows' real positions rather than summing sizeHints: a
        hint can disagree with the laid-out height (a running row turns bold,
        a time label's text changes width) and the error would accumulate
        down the list.
        """
        self._grid.activate()
        offsets = []
        for i in range(self._grid.count()):
            w = self._grid.itemAt(i).widget()
            if w is not None:
                offsets.append(w.y())
        return offsets

    def _scroll_by_rows(self, rows):
        """Scroll by whole rows so the top of the viewport is always flush.

        Stepping by a fixed pixel amount would drift, since group headers and
        timer rows aren't the same height.
        """
        if self._scroll_area is None or not rows:
            return
        bar = self._scroll_area.verticalScrollBar()
        offsets = self._row_offsets()
        if not offsets:
            return
        value = bar.value()
        # Current top row: the last one at or above the scroll position.
        idx = 0
        for i, off in enumerate(offsets):
            if off <= value + 1:
                idx = i
            else:
                break
        # The bottom of the list can't always be reached on a row boundary —
        # the final step clamps to maximum(), leaving a partial row at the
        # top. From there the first scroll up should re-align to the row we
        # are inside, not skip past it to the one before.
        if rows < 0 and abs(offsets[idx] - value) > 1:
            rows += 1
        target = max(0, min(len(offsets) - 1, idx + rows))
        bar.setValue(min(offsets[target], bar.maximum()))

    def _schedule_bottom_line(self):
        """Re-pick the bottom row once the layout has actually settled.

        _update_bottom_line hit-tests real widget positions, but its callers
        run mid-fit: during startup the rows still carry the PREVIOUS pitch
        (invalidate/activate re-lays-out from cached hints and can't fix
        that), so it matched a row one place too high and hid the wrong
        separator with nothing to correct it. Re-running on the next
        event-loop turn is the only point where the geometry is real.
        """
        QTimer.singleShot(0, self._update_bottom_line)

    def _update_bottom_line(self):
        """Drop the client separator under the bottom-most visible row.

        The thick footer rule sits just below the viewport, so whichever row
        is flush with the bottom edge draws its own thin line a couple of
        pixels above it — two rules stacked, which reads as a mistake. Which
        row that is changes with every scroll, so it can't be decided at
        build time the way the last row's line is.

        Only the painted border is removed; the row keeps its reserved gap,
        so no geometry moves and the uniform row pitch is untouched.

        Driven by a dynamic property, NOT by rewriting the stylesheet. The
        old version stripped the declaration, stored the exact string it had
        replaced, and restored only on an exact match so it couldn't clobber
        a rewrite from elsewhere. But a rewrite in between (a drag reorder
        rewrites every row's) made that guard fail silently: the restore was
        skipped, the reference was dropped, and the row kept a border that
        nothing would ever put back — one separator missing at random until
        the next rebuild.
        """
        prev = self._hidden_line
        self._hidden_line = None
        if prev is not None:
            try:
                prev.setProperty("nosep", "")
                prev.style().unpolish(prev)
                prev.style().polish(prev)
            except RuntimeError:
                pass                      # container died with a rebuild

        if self._scroll_area is None:
            return
        # invalidate() BEFORE activate(): activate() alone re-lays-out from
        # cached hints, and the rows' setFixedHeight from the uniform-height
        # pass only schedules its geometry update. At startup that left the
        # row POSITIONS on the previous, taller pitch while their heights had
        # already shrunk — so the hit test below matched a row one place too
        # high and hid the wrong separator, with nothing to correct it later.
        self._grid.invalidate()
        self._grid.activate()
        vp_bottom = (self._scroll_area.verticalScrollBar().value()
                     + self._scroll_area.viewport().height())
        # A cut landing in the gap between two rows still leaves the upper
        # row's line showing right above the footer rule, so count that as
        # flush too. Any deeper and a partial row covers it.
        tol = max(self._grid.spacing(), 2)
        # Take the CLOSEST match, not the first. The tolerance equals the
        # inter-row gap, so whenever the rows fill the viewport exactly the
        # second-to-last row sits exactly `tol` away and passes this test as
        # well — and breaking on the first hit then hid the separator one row
        # too high, permanently, since nothing else would ever pick it up.
        target, best = None, None
        for i in range(self._grid.count()):
            w = self._grid.itemAt(i).widget()
            if w is None:
                continue
            delta = vp_bottom - (w.y() + w.height())
            if 0 <= delta <= tol and (best is None or delta < best):
                target, best = w, delta
        if target is None or "border-bottom" not in target.styleSheet():
            return                        # this row draws no line anyway
        target.setProperty("nosep", "1")
        target.style().unpolish(target)
        target.style().polish(target)
        self._hidden_line = target

    def _content_height(self):
        heights = self._row_heights()
        if not heights:
            return 0
        m = self._grid.contentsMargins()
        return (sum(heights) + self._grid.spacing() * (len(heights) - 1)
                + m.top() + m.bottom())

    def _snapped_height(self, target_h, chrome, round_up=True):
        """Largest height <= target_h that shows only whole rows.

        `round_up` takes the next whole row when more than half of it fits.
        That's right for a ceiling the user dragged — they get the row they
        were reaching for — but wrong when the ceiling is the screen itself,
        since overshooting there hangs the window off the bottom edge.

        `chrome` (everything that isn't viewport: footer, rule, margins) is
        passed in rather than measured from live geometry. Deriving it here
        from self.height() - viewport.height() disagreed with the caller's
        layout-derived value whenever the geometry was mid-update — which is
        exactly the case right after a drag, where the window has just been
        un-fixed and rebuilt.

        The ceiling itself keeps the user's exact number; only the window we
        draw is trimmed, so repeated rebuilds never creep the value.
        """
        avail = target_h - chrome
        if avail <= 0:
            return target_h
        spacing = self._grid.spacing()
        used = 0
        for i, h in enumerate(self._row_heights()):
            step = h + (spacing if i else 0)
            if used + step > avail:
                # Round to the NEAREST row rather than always trimming: if
                # more than half of the next one fits, take the whole thing.
                if round_up and (avail - used) * 2 > step:
                    used += step
                break
            used += step
        return used + chrome if used > 0 else target_h

    def _shrink_to_fit(self):
        """Resize window to tightly fit its contents (allows shrinking)."""
        # The central widget's hint covers grid + footer + margins — the
        # footer no longer lives inside the grid, so measure the whole thing.
        cw = self.centralWidget()
        # invalidate() as well as activate(): a child whose size constraints
        # changed this turn (the scroll viewport) leaves a stale cached hint.
        cw.layout().invalidate()
        cw.layout().activate()
        hint = cw.sizeHint()
        if hint.isEmpty():
            # Nothing measurable yet. Resizing to 0x0 here would make Windows
            # hide the window outright — the taskbar button vanishes and pops
            # back a frame later. Let Qt size it instead.
            self.adjustSize()
            self._expected_size = self.size()
            return
        # The central widget's geometry lags a window resize by a layout pass;
        # measuring the difference before it catches up bakes the stale gap
        # into every later fit.
        if self.layout() is not None:
            self.layout().activate()
        extra_h = self.height() - cw.height()
        extra_w = self.width() - cw.width()
        self.setMinimumSize(0, 0)
        cw.setMinimumSize(0, 0)

        want_h = hint.height() + extra_h
        want_w = hint.width() + extra_w
        if self._scroll_area is not None:
            # QScrollArea's own sizeHint is capped by Qt, so it under-reports
            # a long list. Measure the real grid and add the chrome around it.
            # One definition of chrome, used for both the fit and the snap —
            # and kept for _maximize_height, so it never re-derives its own.
            chrome = (hint.height() - self._scroll_area.sizeHint().height()
                      + extra_h)
            # A toast is additive: the window grows downward to carry it
            # rather than the rows giving up space for it. So the ceiling
            # governs everything EXCEPT the toast, and the toast's height is
            # added back after snapping.
            toast_h = self._toast_height()
            self._last_chrome = chrome - toast_h
            want_h = self._content_height() + chrome
            ceiling = self._state.window_height
            if ceiling > 0 and want_h - toast_h > ceiling:
                # Scrollbar is about to appear — leave room so rows don't clip.
                want_w += self._scroll_area.verticalScrollBar().sizeHint().width()
                want_h = self._snapped_height(ceiling, self._last_chrome) + toast_h
        self._auto_resize(want_w, want_h)

    def _toast_height(self):
        """Height the visible toast adds to the window, 0 when hidden.

        Not height(): show_toast fits the window in the same breath as making
        the toast visible, before it has ever been laid out.

        Not sizeHint() either. The message label word-wraps, so its hint is
        Qt's guess at a comfortable *unwrapped* shape — for a one-line toast
        it reads 60px when the row is 30px. The enclosing layout ignores that
        hint and asks heightForWidth at the width the toast will really get,
        so that is the number the window has to make room for. Trusting the
        hint made the chrome 30px too small and the viewport handed the rows
        a whole extra row for as long as the toast was up.
        """
        if not self._toast_container.isVisible():
            return 0
        c = self._toast_container
        if c.hasHeightForWidth():
            m = self._main_lay.contentsMargins()
            width = self.centralWidget().width() - m.left() - m.right()
            if width > 0:
                return max(c.heightForWidth(width),
                           c.minimumSizeHint().height())
        return c.sizeHint().height()

    def show_toast(self, message, seconds=5, action_text=None, on_action=None):
        """Show a transient notification at the bottom of the window.

        `seconds` is how long it stays up before fading; the X dismisses it
        immediately whatever that was set to. `seconds=0` means it does not
        fade at all — for a toast that asks a question rather than reporting
        something, since a 2.5s fade means most people never see it.

        `action_text`/`on_action` add a single button. Deliberately one, not
        two: the X is already "not now".
        """
        # Cancel any in-flight fade so it can't hide the new toast.
        fade, self._toast_fade = self._toast_fade, None
        if fade is not None:
            fade.stop()
        t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        self._toast.setText(message)
        # Colour lives on the bar so the button and the message read as one
        # object; the label itself is transparent on top of it.
        self._toast_bar.setStyleSheet(
            f"#toastBar {{ background-color: {t['toast_bg']}; }}")
        self._toast.setStyleSheet(
            f"background: transparent;"
            f" color: {t['toast_fg']};"
            f" padding: 3px 8px;")
        self._toast_close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" padding: 0px; color: {t['toast_fg']}; }}"
            f"QPushButton:hover {{ background-color: {t['toast_fg']};"
            f" color: {t['toast_bg']}; }}")

        self._toast_action_cb = on_action
        self._toast_action.setVisible(bool(action_text))
        if action_text:
            self._toast_action.setText(f"  {action_text}  ")
            # Outlined rather than filled: it has to read as a button against
            # the toast colour without competing with the message.
            self._toast_action.setStyleSheet(
                f"QPushButton {{ background: transparent;"
                f" border: 1px solid {t['toast_fg']}; border-radius: 2px;"
                f" margin: 2px 4px; padding: 1px 4px;"
                f" color: {t['toast_fg']}; }}"
                f"QPushButton:hover {{ background-color: {t['toast_fg']};"
                f" color: {t['toast_bg']}; }}")
        self._toast_opacity.setOpacity(1.0)
        # Cap the toast at the window's current width so long messages wrap
        # downward instead of widening the window. The X takes its share.
        margins = self._main_lay.contentsMargins()
        avail = (self.centralWidget().width() - margins.left()
                 - margins.right() - _TOAST_CLOSE_W)
        if avail > 50:
            self._toast.setMaximumWidth(avail)
        self._set_toast_visible(True)
        if seconds > 0:
            self._toast_timer.start(int(seconds * 1000))
        else:
            self._toast_timer.stop()      # stays until dismissed or answered

    def _set_toast_visible(self, visible):
        """Show or hide the toast without disturbing the scroll position.

        The window resize and the toast's own layout land in different turns,
        so for one pass the scroll area is the full new height with nothing
        below it. Its range briefly shrinks, Qt clamps the position into it,
        and when the range comes back the list is left sitting a toast-height
        off a row boundary — permanently, since nothing re-snaps it.
        """
        bar = (self._scroll_area.verticalScrollBar()
               if self._scroll_area is not None else None)
        keep = bar.value() if bar is not None else None
        self._toast_container.setVisible(visible)
        self._refit_toast(keep)
        # How far the text wraps depends on the width that fit just settled
        # on, so the height measured a moment ago can be a few pixels out.
        # Re-fit once the layout has caught up; it's a no-op if it agreed.
        QTimer.singleShot(0, lambda: self._refit_toast(keep))

    def _refit_toast(self, keep):
        self._shrink_to_fit()
        if keep is not None and self._scroll_area is not None:
            # Clamps by itself if the list really did get shorter.
            self._scroll_area.verticalScrollBar().setValue(keep)

    # ------------------------------------------------------------------ #
    #  Updates                                                             #
    # ------------------------------------------------------------------ #

    def _start_update_check(self, forced=False):
        """Look for a newer release on a worker thread.

        Checks on every launch, but only PROMPTS once a day — the check is
        free and silent, the prompt is the thing with a cost. `forced` is a
        check the user asked for from the About page: it skips the daily gate
        and reports every outcome, because a button that does nothing visible
        reads as broken.
        """
        import threading
        from ct.core import update

        def worker():
            status, manifest = update.check()
            # Back to the GUI thread. Must be a signal, not a QTimer — see
            # the class-level comment on _update_checked.
            self._update_checked.emit(status, manifest)

        threading.Thread(target=worker, daemon=True,
                         name="ct2-update-check").start()
        self._update_forced = forced

    def _on_update_checked(self, status, manifest):
        from ct.core import update
        from ct.common.version import __version__ as installed
        forced = getattr(self, "_update_forced", False)
        self._update_forced = False
        if status == update.UPDATE:
            self._offer_update(manifest, forced=forced)
        elif forced and status == update.CURRENT:
            self.show_toast(f"You're up to date ({installed})", 4)
        elif forced:
            self.show_toast("Couldn't check for updates right now", 4)

    def _due_for_update_prompt(self):
        """True if the user has not been shown a prompt in the last 24h.

        Measured from the last PROMPT, not the last check or the last launch.
        Six restarts in a morning must not mean six prompts, and an app left
        open all week must still get one.
        """
        stamp = self._state.settings.last_update_prompt
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except (ValueError, TypeError):
            return True                      # unreadable: treat as never
        return (datetime.now().astimezone() - last).total_seconds() >= 86400

    def _offer_update(self, manifest, forced=False):
        """The one user-visible moment in the whole update path.

        `forced` skips the once-a-day gate: if someone just clicked Check For
        Updates, withholding the answer because they were told yesterday
        would be absurd. It still stamps the clock, so they don't get the
        automatic prompt an hour later as well.
        """
        if not forced and not self._due_for_update_prompt():
            return
        version = manifest.get("version", "")
        self._state.settings.last_update_prompt = now_iso()
        self._save_state()
        # seconds=0: this asks a question, so it waits for an answer instead
        # of fading. The X is "not now" and brings it back tomorrow.
        self.show_toast(
            f"Version {version} is available",
            seconds=0,
            action_text="Update Now",
            on_action=lambda: self._do_update(manifest),
        )

    def _do_update(self, manifest):
        """Download, then hand off to the installer.

        The download runs on a worker thread — 39 MB on a corporate VPN is
        not instant, and freezing the UI while it happens would look exactly
        like a crash.
        """
        import threading
        from ct.core import update

        self.show_toast("Downloading update…", 0)

        def worker():
            # .get() — a manifest published before checksums existed simply
            # has no claim to check, which download() treats as "skip".
            path = update.download(manifest["url"],
                                   sha256=manifest.get("sha256"))
            self._update_downloaded.emit(path)

        threading.Thread(target=worker, daemon=True,
                         name="ct2-update-download").start()

    def _install_update(self, path):
        if path is None:
            self.show_toast("Update download failed — try again later", 6)
            return
        # Save BEFORE handing off. Restart Manager closes the app for us, and
        # relying on closeEvent firing correctly under an RM-initiated close
        # is not a bet worth taking with the user's times.
        self._save_state()
        if not update_launch(path):
            self.show_toast("Could not start the installer", 6)
            return
        self.show_toast("Installing… the app will reopen", 0)

    def _on_toast_action(self):
        """Run the current toast's action, then clear it.

        The callback is read and cleared BEFORE running, so an action that
        raises cannot leave a live button wired to a stale handler.
        """
        cb, self._toast_action_cb = self._toast_action_cb, None
        self._dismiss_toast()
        if cb is not None:
            cb()

    def _fade_toast(self):
        self._toast_fade = QPropertyAnimation(self._toast_opacity, b"opacity")
        self._toast_fade.setDuration(300)
        self._toast_fade.setStartValue(1.0)
        self._toast_fade.setEndValue(0.0)
        self._toast_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._toast_fade.finished.connect(self._dismiss_toast)
        self._toast_fade.start()

    def _dismiss_toast(self):
        # Also reachable from the X, mid-countdown — kill both the pending
        # fade and the timer that would have started one.
        self._toast_timer.stop()
        fade, self._toast_fade = self._toast_fade, None
        if fade is not None:
            fade.stop()
        self._toast_action_cb = None
        self._toast_action.setVisible(False)
        if self._toast_container.isVisible():
            self._toast_opacity.setOpacity(1.0)
            self._set_toast_visible(False)

    def _sync_footer_heights(self):
        # sizeHint, not height(): the edit controls live on a stacked page that
        # is never laid out while the footer is locked, so their height() is a
        # meaningless default (Qt's 640x480) — pinning everything to that blew
        # the footer up to ~480px tall. A size hint is valid either way.
        if hasattr(self, "_add_btn") and self._add_btn.sizeHint().height() > 0:
            h = self._add_btn.sizeHint().height()
            for w in (self._add_btn, self._add_group_btn, self._add_input,
                      self._rearrange_btn, self._cfg_btn,
                      # The locked page must measure exactly the same as the
                      # edit page — see the stack in RowFactory.footer.
                      self._status_lbl):
                w.setFixedHeight(h)
        # This runs deferred, after the fit that followed the rebuild — and it
        # just changed the footer's height. Re-fit so the window isn't left
        # short (which would show a scrollbar it doesn't need). A no-op resize
        # when nothing moved.
        self._shrink_to_fit()

    # ------------------------------------------------------------------ #
    #  Tick / autosave / snapshots                                         #
    # ------------------------------------------------------------------ #

    def _tick(self):
        any_running = False
        for rid, ts in self.timers.items():
            if ts.running:
                any_running = True
                self._update_display(rid)

        if any_running:
            for rid in self._visible_rowids:
                if rid in self._widgets and self._widgets[rid].get("is_group"):
                    w = self._widgets[rid]
                    if self._state.settings.show_group_time:
                        w["time"].setText(format_time(self._group_total_time(rid)))
                    if self._state.settings.show_group_count:
                        w["count"].setText(
                            f"({len(self._group_children(rid))})")

        # Unconditional: the total also moves on manual edits (Set Time,
        # +5/-5). setText early-returns when the string is unchanged, so an
        # idle app doesn't repaint here every second.
        self._update_status()

        if self._state.settings.daily_reset_enabled:
            self._check_daily_reset_boundary()

        self._tick_n += 1
        if self._tick_n % 20 == 0:
            self._save_state()

        self._try_snapshot(reason="tick", priority="low")

    # ------------------------------------------------------------------ #
    #  Persistence helpers                                                 #
    # ------------------------------------------------------------------ #

    def _save_state(self):
        return self._state.save(self.timers)

    def _try_snapshot(self, reason, priority="low"):
        now = time.monotonic()
        min_secs = self._snapshot_idle_secs
        if ((priority == "low" and now - self._last_snapshot_time > min_secs)
                or (priority == "medium" and now - self._last_snapshot_time > self._snapshot_debounce)
                or priority == "high"):
            state = self._save_state()
            created_snapshot_path = create_snapshot(state, reason, priority)
            self._last_snapshot_time = now
            prune_snapshots()
            return created_snapshot_path
        return None

    # ------------------------------------------------------------------ #
    #  Daily reset                                                         #
    # ------------------------------------------------------------------ #

    def _most_recent_reset_boundary(self):
        try:
            rh, rm = map(int, self._state.settings.daily_reset_time.split(":"))
        except ValueError:
            rh, rm = 0, 0
        now = datetime.now().astimezone()
        boundary_today = now.replace(hour=rh, minute=rm, second=0, microsecond=0)
        if now >= boundary_today:
            return boundary_today
        return boundary_today - timedelta(days=1)

    def _check_daily_reset_boundary(self):
        boundary = self._most_recent_reset_boundary()
        if self._state.session_start < boundary:
            self._do_daily_reset(boundary)

    def _do_daily_reset(self, boundary_dt):
        state = self._save_state()
        save_completed_session(state, boundary_dt)

        # The session just ended and was archived. Undoing across that line
        # would put yesterday's edits back on top of today's fresh zeroes.
        self._undo.clear()
        self._stop_all()
        for ts in self.timers.values():
            ts.reset()
        self._rebuild_rows()

        self._state.session_start = boundary_dt
        self._try_snapshot(reason="daily_reset_rollover", priority="high")

        time_str = boundary_dt.strftime("%#I:%M %p")
        self.show_toast(f"Session saved and reset at {time_str}", 6)

    # ------------------------------------------------------------------ #
    #  Window close                                                        #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        try:
            self._try_snapshot(reason="app_exit", priority="high")
        except Exception as e:
            QMessageBox.warning(self, "Save Error",
                                f"Failed to save state:\n{e}")
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    # No setStyle() — the platform default stays. Forcing Fusion was tried
    # and reverted: it fixed combo-popup chrome but restyled every native
    # widget in the app, which was a far bigger change than the problem.
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
