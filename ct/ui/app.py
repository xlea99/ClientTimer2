import ctypes
from ctypes import wintypes
import re
import time
import sys
from pathlib import Path
from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QEvent, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from ct.common.logger import log
from ct.common.setup import PATHS
from ct.core.config import AppState, save_completed_session
from ct.core.snapshot import create_snapshot, prune_snapshots
from ct.core.timer_state import TimerState
from ct.ui.dialogs import ConfigDialog
from ct.ui.drag import DragController
from ct.ui.theme import THEMES, SIZES, build_stylesheet, build_menu_stylesheet
from ct.ui.ui_blueprint import UIBlueprint
from ct.ui.row_factory import RowFactory
from ct.util import format_time

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



# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

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

        # -- Drag controller --
        self._drag = DragController(self)

        # -- Snapshot handling --
        self._last_snapshot_time = 0.0
        self._snapshot_debounce  = 10.0  # seconds between non-high-priority snapshots

        # -- Pre-UI startup checks --
        self._startup_checks()

        # -- Build UI skeleton --
        central = QWidget()
        self.setCentralWidget(central)
        self._main_lay = QVBoxLayout(central)
        self._main_lay.setContentsMargins(0, 0, 0, 0)

        self._time_labels    = {}    # time QLabel -> rowid, for click-to-copy
        self._scroll_area    = None  # the row viewport, rebuilt with the grid
        self._expected_size  = None  # last size we asked for ourselves
        self._programmatic_resize = False
        self._user_resizing  = False  # true between ENTER/EXITSIZEMOVE
        self._ready_for_user_resize = False  # set once show() has settled
        self._resize_settle  = QTimer(self)
        self._resize_settle.setSingleShot(True)
        self._resize_settle.setInterval(200)
        self._resize_settle.timeout.connect(self._on_resize_settled)
        self._grid_widget    = None  # created fresh each _rebuild_rows
        self._content_widget = None  # single swappable child: grid + footer

        # -- Toast notification bar --
        self._toast_container = QWidget()
        self._toast_container.setVisible(False)
        toast_lay = QVBoxLayout(self._toast_container)
        toast_lay.setContentsMargins(0, 0, 0, 0)
        toast_lay.setSpacing(0)

        self._toast = QLabel()
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.setFont(QFont("Calibri", 9))
        self._toast.setContentsMargins(4, 2, 4, 2)
        self._toast.setWordWrap(True)
        toast_lay.addWidget(self._toast)

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
                "Some saved settings couldn't be read and were adjusted — see log")

        # 0.5. Retired-theme migration notice (takes priority over the above).
        if getattr(self._state, "theme_renamed", False):
            self._pending_toast = (
                "Cupertino Light has found its way to theme heaven. You've been moved to E-Ink. "
                "16 other themes exist in settings.")

        # 1. CT1 migration notification
        #    Migration data is handled in AppState.load(). The old config.txt
        #    is left intact as a permanent migration source — the installer
        #    handles killing CT1's exe (the actual threat).
        if self._state.migrated_from_ct1:
            m = self._state.migrated_from_ct1
            timers = ", ".join(m.get("Timers", []))
            ct1_times = m.get("Times", {})

            if ct1_times:
                # Build a summary of the times we found
                lines = "\n".join(
                    f"  \u2022 {name}: {format_time(secs)}"
                    for name, secs in ct1_times.items() if secs > 0
                )
                msg = QMessageBox(self)
                msg.setWindowTitle("Welcome to Client Timer 2")
                msg.setIcon(QMessageBox.Question)
                msg.setText(
                    f"Your Client Timer 1 data has been migrated!\n\n"
                    f"Timers: {timers}\n"
                    f"Theme: {m.get('Theme', 'E-Ink (Default)')}\n"
                    f"Size: {m.get('Size', 'Regular')}\n\n"
                    f"CT1 has existing times on these clients.\n"
                    f"Would you like to carry them over?"
                )
                msg.setInformativeText(lines)
                yes_btn = msg.addButton(
                    "Migrate Times", QMessageBox.AcceptRole)
                msg.addButton(
                    "Start Fresh", QMessageBox.RejectRole)
                msg.exec()

                if msg.clickedButton() == yes_btn:
                    for ts in self.timers.values():
                        if ts.name in ct1_times and ct1_times[ts.name] > 0:
                            ts.elapsed = float(ct1_times[ts.name])
                            log.info(f"Migrated {ct1_times[ts.name]}s for "
                                     f"timer '{ts.name}' from CT1")
            else:
                QMessageBox.information(
                    self,
                    "Welcome to Client Timer 2",
                    f"Your Client Timer 1 data has been migrated!\n\n"
                    f"Timers: {timers}\n"
                    f"Theme: {m.get('Theme', 'E-Ink (Default)')}\n"
                    f"Size: {m.get('Size', 'Regular')}",
                )
            self._state.migrated_from_ct1 = None
            # Persist immediately — this materializes state.json, so a crash
            # before the first autosave can't re-run the migration prompt or
            # lose the user's carry-over choice.
            self._save_state()

        # 2. Daily reset catch-up — if the app was closed and we missed a
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

    def _rebuild_rows_impl(self):
        """Tear down and recreate the entire grid: client rows + footer."""
        self._widgets.clear()
        self._time_labels = {}   # time QLabel -> rowid, for click-to-copy

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
            lbl = QLabel("No clients. Add one to begin!")
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
                        show_x=(ss.button_visibility == "All"),
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
                        button_visibility=ss.button_visibility,
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
                        on_start=self._on_start,
                        on_stop=self._on_stop,
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
                for child in row_container.findChildren(QLabel):
                    child.setAttribute(Qt.WA_TransparentForMouseEvents)
                if self._rearranging:
                    row_container.setCursor(Qt.OpenHandCursor)
                    for child in row_container.findChildren(QPushButton):
                        child.setCursor(Qt.ArrowCursor)
                elif not widget_dict.get("is_group"):
                    # Click the time to copy it. Undo the blanket
                    # transparent-for-mouse above for this one label so it can
                    # hover and be clicked; in rearrange mode it stays
                    # transparent so dragging by the time still works.
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
        # Wheel scrolling moves whole rows — see eventFilter.
        scroll.viewport().installEventFilter(self)
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
            self._main_lay.removeWidget(old_content)
            old_content.setParent(None)
            old_content.deleteLater()
        self._main_lay.insertWidget(0, content)
        # Show it NOW (it is parented, so this is safe — never setVisible a
        # parentless widget). Qt would otherwise only show it on the next
        # event-loop turn, and until then QLayout skips it when measuring, so
        # the window's size hint reads 0x0.
        content.setVisible(True)

        QTimer.singleShot(0, self._sync_footer_heights)

    # ------------------------------------------------------------------ #
    #  Shift-key visual feedback                                           #
    # ------------------------------------------------------------------ #

    def _update_shift_labels(self):
        sh = self._shift_held
        for w in self._widgets.values():
            if w.get("is_group"):
                continue
            w["minus"].setText("-1" if sh else "-5")
            w["plus"].setText("+1" if sh else "+5")
            w["start"].setText("Add" if sh else "Start")
            w["stop"].setText("Stop")
            w["x"].setText("0" if sh else "X")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self._shift_held = True
            self._update_shift_labels()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self._shift_held = False
            self._update_shift_labels()
        super().keyReleaseEvent(event)

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
        if self._drag.active:
            return self._drag.handle_event(obj, event)

        # Wheel over the row viewport: advance by whole rows, not pixels.
        if (event.type() == QEvent.Wheel and self._scroll_area is not None
                and obj is self._scroll_area.viewport()):
            delta = event.angleDelta().y()
            if delta:
                notches = -delta / 120.0
                rows = int(notches) or (1 if notches > 0 else -1)
                self._scroll_by_rows(rows)
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

        if event.type() == QEvent.Enter:
            rid = self._drag.rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, True)
        elif event.type() == QEvent.Leave:
            rid = self._drag.rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, False)

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

    def _on_start(self, rowid):
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            self._start_additional(rowid)
        else:
            self._start_exclusive(rowid)
        self._save_state()

    def _on_stop(self, rowid):
        self._stop_one(rowid)
        self._save_state()

    def _on_adjust(self, rowid, direction):
        minutes = 1 if (QApplication.keyboardModifiers() & Qt.ShiftModifier) else 5
        self.timers[rowid].adjust(direction * minutes * 60)
        self._update_display(rowid)
        self._update_parent_group_time(rowid)
        self._save_state()

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

    def _on_remove_group(self, rowid):
        if self._state.settings.confirm_delete:
            name = next(
                (r["name"] for r in self._state.rows if r["rowid"] == rowid), "")
            if QMessageBox.question(
                    self, "Confirm Delete",
                    f"Delete group '{name}'?"
            ) != QMessageBox.Yes:
                return
        self._state.collapsed_groups.discard(rowid)
        self._state.rows = [r for r in self._state.rows if r["rowid"] != rowid]
        self._save_state()
        self._try_snapshot(reason="layout_change", priority="medium")
        self._rebuild_rows()
        self._shrink_to_fit()

    def _on_group_toggle(self, rowid):
        if rowid in self._state.collapsed_groups:
            self._state.collapsed_groups.discard(rowid)
        else:
            self._state.collapsed_groups.add(rowid)
        self._save_state()
        self._rebuild_rows()
        self._shrink_to_fit()

    def _on_remove(self, rowid):
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            if self._state.settings.confirm_reset:
                name = self.timers[rowid].name
                if QMessageBox.question(
                        self, "Confirm Reset",
                        f"Reset timer '{name}' to zero?"
                ) != QMessageBox.Yes:
                    return
            self.timers[rowid].stop()
            self.timers[rowid].reset()
            self._set_bold(rowid, False)
            self._update_display(rowid)
            self._update_parent_group_time(rowid)
            self._save_state()
        else:
            if self._state.settings.confirm_delete:
                name = next(
                    (r["name"] for r in self._state.rows if r["rowid"] == rowid), "")
                if QMessageBox.question(
                        self, "Confirm Delete",
                        f"Delete '{name}'?"
                ) != QMessageBox.Yes:
                    return
            self.timers[rowid].stop()
            del self.timers[rowid]
            self._state.rows = [r for r in self._state.rows if r["rowid"] != rowid]
            self._save_state()
            self._try_snapshot(reason="layout_change", priority="medium")
            self._rebuild_rows()
            self._shrink_to_fit()

    def _on_rearrange_toggle(self):
        self._rearranging = not self._rearranging
        self._rebuild_rows()
        # Rearrange mode gives every row a uniform line_gap so drags never
        # resize anything — absorb that height change here, at the toggle.
        self._shrink_to_fit()

    # ------------------------------------------------------------------ #
    #  Hover and context menu                                              #
    # ------------------------------------------------------------------ #

    def _on_row_hover(self, rid, entering):
        if rid not in self._widgets:
            return
        name_lbl = self._widgets[rid]["name"]
        f = name_lbl.font()
        f.setUnderline(entering)
        name_lbl.setFont(f)

    def _on_time_hover(self, rid, entering):
        """Hovering the time underlines the name AND the time.

        Entering the label sends Leave to the row container, which clears the
        name's underline — so re-apply it here to keep both marked.
        """
        if rid not in self._widgets:
            return
        for key in ("name", "time"):
            lbl = self._widgets[rid].get(key)
            if lbl is None:
                continue
            f = lbl.font()
            f.setUnderline(entering)
            lbl.setFont(f)

    def _copy_timer_time(self, rid):
        """Copy a live timer's current time to the clipboard, and toast it."""
        if rid not in self.timers:
            return
        ts = self.timers[rid]
        time_str = format_time(ts.current_elapsed)
        QApplication.clipboard().setText(time_str)
        self.show_toast(f"Time for {ts.name} ({time_str}) copied to clipboard", 4)

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
        menu.addSeparator()
        set_time     = menu.addAction("Set Time") if is_timer else None
        delete_action = menu.addAction("Delete")

        action = menu.exec(global_pos)
        if action is None:
            return

        if action == rename_action:
            text, ok = QInputDialog.getText(
                self, "Rename", "New name:", text=row["name"])
            if ok and text.strip():
                new_name = _SANITIZE.sub("", text).strip()
                if new_name:
                    row["name"] = new_name
                    if is_timer and rowid in self.timers:
                        self.timers[rowid].name = new_name
                    self._save_state()
                    self._try_snapshot(reason="layout_change", priority="medium")
                    self._rebuild_rows()
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
            if self._state.settings.confirm_delete:
                if QMessageBox.question(
                        self, "Confirm Delete",
                        f"Delete '{row['name']}'?"
                ) != QMessageBox.Yes:
                    return
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
            self._restore_from_snapshot(dlg.restore_path)
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
        old_dr_enabled = s.daily_reset_enabled
        old_dr_time    = s.daily_reset_time
        s.daily_reset_enabled  = dlg.chosen_daily_reset_enabled
        s.daily_reset_time     = dlg.chosen_daily_reset_time

        # If daily reset was just enabled or the time changed, anchor
        # session_start to now so only future boundaries trigger resets.
        if (s.daily_reset_enabled
                and (not old_dr_enabled or s.daily_reset_time != old_dr_time)):
            self._state.session_start = datetime.now().astimezone()
        s.snapshot_min_minutes = dlg.chosen_snapshot_min_minutes
        s.button_visibility    = dlg.chosen_button_visibility

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

    def _restore_from_snapshot(self, path: Path):
        try:
            new_state = AppState.load(path)
        except Exception:
            log.exception(f"Failed to restore from snapshot '{path}'.")
            self.show_toast("Restore failed — current state unchanged", 5)
            return
        self._stop_all()
        self._state = new_state
        self._next_rowid = max(
            (r["rowid"] for r in self._state.rows), default=-1) + 1
        self.timers = {}
        for row in self._state.rows:
            if row["type"] == "timer":
                rid = row["rowid"]
                tt  = self._state.tracked_times.get(str(rid), {})
                # Don't restore running_since — timers start stopped after restore
                self.timers[rid] = TimerState(row["name"], elapsed=tt.get("elapsed", 0.0))
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
        self.show_toast(f"Restored from backup ({time_str})", 5)

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

    def _reset_all(self):
        if QMessageBox.question(
                self, "Confirm", "Reset all times to zero?"
        ) == QMessageBox.Yes:
            self._stop_all()
            for ts in self.timers.values():
                ts.reset()
            self._save_state()
            self._rebuild_rows()
            self.show_toast("Reset all times to zero.")

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
        return super().nativeEvent(eventType, message)

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
        self._state.window_height = event.size().height()
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

    def _content_height(self):
        heights = self._row_heights()
        if not heights:
            return 0
        m = self._grid.contentsMargins()
        return (sum(heights) + self._grid.spacing() * (len(heights) - 1)
                + m.top() + m.bottom())

    def _snapped_height(self, target_h, chrome):
        """Largest height <= target_h that shows only whole rows.

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
                if (avail - used) * 2 > step:
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
            # One definition of chrome, used for both the fit and the snap.
            chrome = (hint.height() - self._scroll_area.sizeHint().height()
                      + extra_h)
            want_h = self._content_height() + chrome
            ceiling = self._state.window_height
            if ceiling > 0 and want_h > ceiling:
                # Scrollbar is about to appear — leave room so rows don't clip.
                want_w += self._scroll_area.verticalScrollBar().sizeHint().width()
                want_h = self._snapped_height(ceiling, chrome)
        self._auto_resize(want_w, want_h)

    def show_toast(self, message, seconds=5):
        """Show a transient notification at the bottom of the window."""
        # Cancel any in-flight fade so it can't hide the new toast.
        if self._toast_fade is not None:
            self._toast_fade.stop()
            self._toast_fade = None
        t = THEMES.get(self._state.settings.theme, THEMES["E-Ink (Default)"])
        self._toast.setText(message)
        self._toast.setStyleSheet(
            f"background-color: {t['toast_bg']};"
            f" color: {t['toast_fg']};"
            f" padding: 3px 8px;")
        self._toast_opacity.setOpacity(1.0)
        # Cap the toast at the window's current width so long messages wrap
        # downward instead of widening the window.
        margins = self._main_lay.contentsMargins()
        avail = self.centralWidget().width() - margins.left() - margins.right()
        if avail > 50:
            self._toast.setMaximumWidth(avail)
        self._toast_container.setVisible(True)
        self._shrink_to_fit()
        self._toast_timer.start(int(seconds * 1000))

    def _fade_toast(self):
        self._toast_fade = QPropertyAnimation(self._toast_opacity, b"opacity")
        self._toast_fade.setDuration(300)
        self._toast_fade.setStartValue(1.0)
        self._toast_fade.setEndValue(0.0)
        self._toast_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._toast_fade.finished.connect(self._dismiss_toast)
        self._toast_fade.start()

    def _dismiss_toast(self):
        if self._toast_container.isVisible():
            self._toast_container.setVisible(False)
            self._toast_opacity.setOpacity(1.0)
            self._shrink_to_fit()

    def _sync_footer_heights(self):
        if hasattr(self, "_add_btn") and self._add_btn.height() > 0:
            h = self._add_btn.height()
            self._add_input.setFixedHeight(h)
            self._rearrange_btn.setFixedHeight(h)
            self._cfg_btn.setFixedHeight(h)
            if hasattr(self, "_add_group_btn"):
                self._add_group_btn.setFixedHeight(h)
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
        min_secs = self._state.settings.snapshot_min_minutes * 60
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
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
