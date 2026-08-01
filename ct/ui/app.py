import ctypes
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
    QGraphicsOpacityEffect,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
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
        self.adjustSize()

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
        self.setUpdatesEnabled(False)
        try:
            self._rebuild_rows_impl()
        finally:
            self.setUpdatesEnabled(True)

    def _rebuild_rows_impl(self):
        """Tear down and recreate the entire grid: client rows + footer."""
        self._widgets.clear()

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
        content_lay.addWidget(self._grid_widget)

        ss = self._state.settings
        t  = THEMES.get(ss.theme, THEMES["E-Ink (Default)"])
        s  = SIZES.get(ss.size, SIZES["Regular"])

        self._grid.setSpacing(s.get("v_spacing", s["padding"]))

        blueprint = UIBlueprint.compute(t, s, ss.font, self._state.rows, self._has_mdl2)

        bottom_merged = False  # last row hosts the footer line itself

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
                    is_footer_row = (ss.client_separators
                                     and idx == len(visible_entries) - 1)
                    if is_footer_row:
                        bottom_merged = True

                    timer_state = self.timers[rid]
                    row_container, widget_dict = RowFactory.timer(
                        blueprint=blueprint, rid=rid, row=row, state=timer_state,
                        shift_held=self._shift_held, label_align=ss.label_align,
                        button_visibility=ss.button_visibility,
                        is_child=is_child,
                        is_dragging=self._drag.dragging_rid == rid,
                        draw_separator_line=needs_sep,
                        footer_line=is_footer_row,
                        force_line_gap=(self._rearranging
                                        and ss.client_separators),
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

                self._grid.addWidget(row_container)

        # Footer separator — standalone only when the bottom row doesn't
        # already carry it (separators off, or last visible row is a group).
        if self._state.rows and not bottom_merged:
            sep = QWidget()
            sep.setFixedHeight(2)
            sep.setStyleSheet(f"background-color: {t['chrome_line']};")
            self._grid.addWidget(sep)

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
        self.adjustSize()

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
            self.adjustSize()

    def _on_rearrange_toggle(self):
        self._rearranging = not self._rearranging
        self._rebuild_rows()
        # Rearrange mode gives every row a uniform line_gap so drags never
        # resize anything — absorb that height change here, at the toggle.
        self.adjustSize()

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
            self.adjustSize()

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
        self.adjustSize()

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
        self.adjustSize()
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

    def _shrink_to_fit(self):
        """Resize window to tightly fit its contents (allows shrinking)."""
        # The central widget's hint covers grid + footer + margins — the
        # footer no longer lives inside the grid, so measure the whole thing.
        cw = self.centralWidget()
        cw.layout().activate()          # hint is stale until the layout runs
        hint = cw.sizeHint()
        if hint.isEmpty():
            # Nothing measurable yet. Resizing to 0x0 here would make Windows
            # hide the window outright — the taskbar button vanishes and pops
            # back a frame later. Let Qt size it instead.
            self.adjustSize()
            return
        extra_h = self.height() - cw.height()
        extra_w = self.width() - cw.width()
        self.setMinimumSize(0, 0)
        cw.setMinimumSize(0, 0)
        self.resize(hint.width() + extra_w, hint.height() + extra_h)

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
        self.adjustSize()
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
