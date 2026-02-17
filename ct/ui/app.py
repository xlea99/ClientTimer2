import ctypes
import re
import sys
import time
from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from ct.common.setup import PATHS
from ct.core import config
from ct.core.snapshot import create_snapshot, prune_snapshots
from ct.core.state import ClientState
from ct.ui.dialogs import ConfigDialog
from ct.ui.drag import DragController
from ct.ui.theme import THEMES, SIZES, build_stylesheet, build_menu_stylesheet
from ct.ui.widgets import BuildContext, build_separator_row, build_timer_row, build_footer, _format_time

_SANITIZE = re.compile(r"[^a-zA-Z0-9\s'.]+")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

# Main window of the actual clienttimer2 application. What displays timers, separators, etc
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Client Timer 2")
        icon = QIcon(str(PATHS.assets / "icon.ico"))
        self.setWindowIcon(icon)

        # -- Load unified state --
        state = config.load_state()
        s = state["settings"]
        self.theme = s["theme"] if s["theme"] in THEMES else "Cupertino Light"
        self.ui_size = s["size"] if s["size"] in SIZES else "Regular"
        self.label_align = s.get("label_align", "Left")
        self.client_separators = s.get("client_separators", False)
        self.show_group_count = s.get("show_group_count", True)
        self.show_group_time = s.get("show_group_time", True)
        self.font_family = s.get("font", "Calibri")
        self.always_on_top = s.get("always_on_top", True)
        self.confirm_delete = s.get("confirm_delete", True)
        self.confirm_reset = s.get("confirm_reset", True)
        self.daily_reset_enabled = s.get("daily_reset_enabled", False)
        self.daily_reset_time = s.get("daily_reset_time", "00:00")
        self.snapshot_min_minutes = s.get("snapshot_min_minutes", 5)

        if self.always_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        # -- Layout --
        layout = state["layout"]
        self.rows = list(layout["rows"])
        self._next_rowid = max((r["rowid"] for r in self.rows), default=-1) + 1
        self._collapsed_groups = set(layout.get("collapsed_groups", []))

        # -- Session --
        session = state["session"]
        self._session_start = datetime.fromisoformat(session["start"])
        tracked = session.get("tracked_times", {})

        self.clients = {}  # rowid -> ClientState (timers only)
        for row in self.rows:
            if row["type"] == "timer":
                rid = row["rowid"]
                tt = tracked.get(str(rid), {})
                self.clients[rid] = ClientState(
                    row["name"],
                    elapsed=tt.get("elapsed", 0.0),
                    running_since=tt.get("running_since"),
                )

        self._widgets = {}        # rowid -> widget dict
        self._has_mdl2 = "Segoe MDL2 Assets" in QFontDatabase.families()
        self._shift_held = False
        self._rearranging = False
        self._visible_rowids = []  # populated by _rebuild_rows

        # -- Drag controller --
        self._drag = DragController(self)

        # -- Snapshot handling --
        self._last_snapshot_time = 0.0
        # Ensure snapshots happen at least this many seconds apart, unless high priority.
        self._snapshot_debounce = 10.0

        # -- Check for missed daily reset at startup --
        if self.daily_reset_enabled:
            self._check_daily_reset_boundary()

        # -- Build UI skeleton --
        central = QWidget()
        self.setCentralWidget(central)
        self._main_lay = QVBoxLayout(central)

        self._grid_widget = QWidget()
        self._grid = QVBoxLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._main_lay.addWidget(self._grid_widget)

        self._apply_style()
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

        # -- Tick timer (1 s) --
        self._tick_n = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # ------------------------------------------------------------------ #
    #  Style                                                               #
    # ------------------------------------------------------------------ #

    def _apply_style(self):
        style = build_stylesheet(self.theme)
        self.setStyleSheet(style)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(style)

        s = SIZES.get(self.ui_size, SIZES["Regular"])
        self._main_lay.setContentsMargins(
            s["frame_pad"], s["frame_pad"], s["frame_pad"], s["frame_pad"]
        )
        self._main_lay.setSpacing(s["padding"])

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
        for row in self.rows:
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
            if child_rid in self.clients:
                total += int(self.clients[child_rid].current_elapsed)
        return total

    def _parent_group(self, rowid):
        """Return the separator rowid that owns this timer, or None."""
        parent = None
        for row in self.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif row["rowid"] == rowid:
                return parent
        return None

    # ------------------------------------------------------------------ #
    #  Row building                                                        #
    # ------------------------------------------------------------------ #

    def _rebuild_rows(self):
        """Tear down and recreate the entire grid: client rows + footer."""
        self._widgets.clear()

        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        s = SIZES.get(self.ui_size, SIZES["Regular"])

        self._grid.setSpacing(s.get("v_spacing", s["padding"]))

        if not self.rows:
            lbl = QLabel("No clients. Add one to begin!")
            lbl.setFont(QFont(self.font_family, s["label"]))
            lbl.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(lbl)
            self._visible_rowids = []
        else:
            ctx = BuildContext.compute(t, s, self.font_family, self.rows, self._has_mdl2)

            # Determine visible entries
            current_group_rid = None
            visible_entries = []
            dragging_group = (self._drag.active and self._drag.group_rids is not None)
            for row in self.rows:
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
                            and current_group_rid in self._collapsed_groups
                            and not (dragging_group
                                     and current_group_rid == self._drag.dragging_rid)):
                        continue
                    visible_entries.append((row, is_child))

            self._visible_rowids = [r["rowid"] for r, _ in visible_entries]
            group_header_bg = t.get("group_header_bg", t["bg"])

            for idx, (row, is_child) in enumerate(visible_entries):
                rid = row["rowid"]

                # Row background
                if row["type"] == "separator":
                    row_bg = row.get("bg") or group_header_bg
                else:
                    row_bg = row.get("bg") or t["bg"]
                if self._drag.dragging_rid == rid:
                    row_bg = t["row_dragged"]

                # Border CSS for client separators
                needs_sep = (self.client_separators
                             and idx < len(visible_entries) - 1
                             and row["type"] == "timer"
                             and visible_entries[idx + 1][0]["type"] == "timer")
                border_css = (f"border-bottom: 1px solid {t['row_separator']};"
                              if needs_sep else "")

                if row["type"] == "separator":
                    collapsed = rid in self._collapsed_groups
                    if dragging_group and rid == self._drag.dragging_rid:
                        children = list(self._drag.group_rids)
                        collapsed = True
                    else:
                        children = self._group_children(rid)
                    has_running = any(
                        cid in self.clients and self.clients[cid].running
                        for cid in children)
                    total = self._group_total_time(rid)

                    rc, wd = build_separator_row(
                        ctx, rid, row, children, collapsed,
                        has_running, total,
                        self.show_group_count, self.show_group_time,
                        is_child, row_bg, border_css,
                        on_toggle=self._on_group_toggle,
                        on_remove=self._on_remove_group,
                    )
                else:
                    state = self.clients[rid]
                    rc, wd = build_timer_row(
                        ctx, rid, row, state, is_child, row_bg,
                        border_css, self._shift_held, self.label_align,
                        on_start=self._on_start,
                        on_stop=self._on_stop,
                        on_adjust=self._on_adjust,
                        on_remove=self._on_remove,
                    )
                    if state.running:
                        self._set_bold(rid, True, wd)

                self._widgets[rid] = wd

                # Hover underline + right-click context menu
                rc.installEventFilter(self)
                rc.setContextMenuPolicy(Qt.CustomContextMenu)
                rc.customContextMenuRequested.connect(
                    lambda pos, r=rid, w=rc: self._on_row_context_menu(
                        r, w.mapToGlobal(pos))
                )
                for child in rc.findChildren(QPushButton):
                    child.setContextMenuPolicy(Qt.PreventContextMenu)
                for child in rc.findChildren(QLabel):
                    child.setAttribute(Qt.WA_TransparentForMouseEvents)
                if self._rearranging:
                    rc.setCursor(Qt.OpenHandCursor)
                    for child in rc.findChildren(QPushButton):
                        child.setCursor(Qt.ArrowCursor)

                self._grid.addWidget(rc)

        # Footer separator
        if self.rows:
            sep = QWidget()
            sep.setFixedHeight(2)
            sep.setStyleSheet(f"background-color: {t['separator']};")
            self._grid.addWidget(sep)

        # Footer
        ctx_footer = BuildContext.compute(t, s, self.font_family, self.rows, self._has_mdl2)
        footer, fw = build_footer(
            ctx_footer, self._rearranging,
            on_rearrange=self._on_rearrange_toggle,
            on_add=self._on_add,
            on_add_group=self._on_add_group,
            on_config=self._on_config,
            on_add_input_return=self._on_add,
        )
        self._rearrange_btn = fw["rearrange_btn"]
        self._add_btn = fw["add_btn"]
        self._add_group_btn = fw["add_group_btn"]
        self._add_input = fw["add_input"]
        self._cfg_btn = fw["cfg_btn"]
        self._grid.addWidget(footer)

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
        # Active drag — delegate to controller
        if self._drag.active:
            return self._drag.handle_event(obj, event)

        # Row hover handling
        if event.type() == QEvent.Enter:
            rid = self._drag.rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, True)
        elif event.type() == QEvent.Leave:
            rid = self._drag.rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, False)

        # Drag initiation
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

    def _on_stop(self, rowid):
        self._stop_one(rowid)

    def _on_adjust(self, rowid, direction):
        minutes = 1 if (QApplication.keyboardModifiers() & Qt.ShiftModifier) else 5
        self.clients[rowid].adjust(direction * minutes * 60)
        self._update_display(rowid)
        self._save_state()

    def _on_add(self):
        raw = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self.rows.append({"rowid": rid, "name": name, "type": "timer", "bg": None})
        self.clients[rid] = ClientState(name)
        self._save_state()
        self._try_snapshot(reason="layout_change",priority="medium")
        self._rebuild_rows()

    def _on_add_group(self):
        raw = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self.rows.append({"rowid": rid, "name": name, "type": "separator", "bg": None})
        self._save_state()
        self._try_snapshot(reason="layout_change",priority="medium")
        self._rebuild_rows()

    def _on_remove_group(self, rowid):
        if self.confirm_delete:
            name = next(
                (r["name"] for r in self.rows if r["rowid"] == rowid), "")
            if QMessageBox.question(
                    self, "Confirm Delete",
                    f"Delete group '{name}'?"
            ) != QMessageBox.Yes:
                return
        self._collapsed_groups.discard(rowid)
        self.rows = [r for r in self.rows if r["rowid"] != rowid]
        self._save_state()
        self._try_snapshot(reason="layout_change",priority="medium")
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

    def _on_group_toggle(self, rowid):
        if rowid in self._collapsed_groups:
            self._collapsed_groups.discard(rowid)
        else:
            self._collapsed_groups.add(rowid)
        self._save_state()
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

    def _on_remove(self, rowid):
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            if self.confirm_reset:
                name = self.clients[rowid].name
                if QMessageBox.question(
                        self, "Confirm Reset",
                        f"Reset timer '{name}' to zero?"
                ) != QMessageBox.Yes:
                    return
            self.clients[rowid].stop()
            self.clients[rowid].reset()
            self._set_bold(rowid, False)
            self._update_display(rowid)
        else:
            if self.confirm_delete:
                name = next(
                    (r["name"] for r in self.rows if r["rowid"] == rowid), "")
                if QMessageBox.question(
                        self, "Confirm Delete",
                        f"Delete '{name}'?"
                ) != QMessageBox.Yes:
                    return
            self.clients[rowid].stop()
            del self.clients[rowid]
            self.rows = [r for r in self.rows if r["rowid"] != rowid]
            self._save_state()
            self._try_snapshot(reason="layout_change",priority="medium")
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

    def _on_rearrange_toggle(self):
        self._rearranging = not self._rearranging
        self._rebuild_rows()

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
        row = next((r for r in self.rows if r["rowid"] == rowid), None)
        if row is None:
            return
        is_timer = row["type"] == "timer"

        menu = QMenu(self)
        menu.setStyleSheet(build_menu_stylesheet(self.theme))

        rename_action = menu.addAction("Rename")
        menu.addSeparator()
        set_color = menu.addAction("Set Color")
        reset_color = menu.addAction("Reset Color")
        menu.addSeparator()
        set_time = menu.addAction("Set Time") if is_timer else None
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
                    if is_timer and rowid in self.clients:
                        self.clients[rowid].name = new_name
                    self._save_state()
                    self._try_snapshot(reason="layout_change",priority="medium")
                    self._rebuild_rows()
        elif action == set_color:
            current_bg = row.get("bg")
            initial = QColor(current_bg) if current_bg else QColor(255, 255, 255)
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
                self._try_snapshot(reason="layout_change",priority="medium")
                self._rebuild_rows()
        elif action == reset_color:
            row["bg"] = None
            self._save_state()
            self._try_snapshot(reason="layout_change",priority="medium")
            self._rebuild_rows()
        elif is_timer and action == set_time:
            current = _format_time(self.clients[rowid].current_elapsed)
            text, ok = QInputDialog.getText(
                self, "Set Time", "Enter time (HH:MM:SS):", text=current)
            if ok and text.strip():
                secs = self._parse_time_input(text.strip())
                if secs is not None:
                    state = self.clients[rowid]
                    was_running = state.running
                    if was_running:
                        state.stop()
                    state.elapsed = secs
                    if was_running:
                        state.start()
                    self._update_display(rowid)
                    parent = self._parent_group(rowid)
                    if parent is not None and parent in self._widgets:
                        self._widgets[parent]["time"].setText(
                            _format_time(self._group_total_time(parent)))
        elif action == delete_action:
            if self.confirm_delete:
                if QMessageBox.question(
                        self, "Confirm Delete",
                        f"Delete '{row['name']}'?"
                ) != QMessageBox.Yes:
                    return
            if is_timer:
                self.clients[rowid].stop()
                del self.clients[rowid]
            else:
                self._collapsed_groups.discard(rowid)
            self.rows = [r for r in self.rows if r["rowid"] != rowid]
            self._save_state()
            self._try_snapshot(reason="layout_change",priority="medium")
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

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
        cfg = {
            "theme": self.theme,
            "size": self.ui_size,
            "font": self.font_family,
            "label_align": self.label_align,
            "client_separators": self.client_separators,
            "show_group_count": self.show_group_count,
            "show_group_time": self.show_group_time,
            "always_on_top": self.always_on_top,
            "confirm_delete": self.confirm_delete,
            "confirm_reset": self.confirm_reset,
            "daily_reset_enabled": self.daily_reset_enabled,
            "daily_reset_time": self.daily_reset_time,
            "snapshot_min_minutes": self.snapshot_min_minutes,
        }

        dlg = ConfigDialog(self, cfg, on_reset=self._reset_all)
        if dlg.exec() == QDialog.Accepted and dlg.style_changed:
            old_aot = self.always_on_top

            self.theme = dlg.chosen_theme
            self.ui_size = dlg.chosen_size
            self.font_family = dlg.chosen_font
            self.label_align = dlg.chosen_label_align
            self.client_separators = dlg.chosen_client_separators
            self.show_group_count = dlg.chosen_show_group_count
            self.show_group_time = dlg.chosen_show_group_time
            self.always_on_top = dlg.chosen_always_on_top
            self.confirm_delete = dlg.chosen_confirm_delete
            self.confirm_reset = dlg.chosen_confirm_reset
            self.daily_reset_enabled = dlg.chosen_daily_reset_enabled
            self.daily_reset_time = dlg.chosen_daily_reset_time
            self.snapshot_min_minutes = dlg.chosen_snapshot_min_minutes

            self._save_state()
            self._try_snapshot(reason="layout_change",priority="high")

            self._apply_style()
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

            if self.always_on_top != old_aot:
                if sys.platform == "win32":
                    hwnd = int(self.winId())
                    flag = -1 if self.always_on_top else -2
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, flag, 0, 0, 0, 0, 0x0013)
                else:
                    self.setWindowFlag(
                        Qt.WindowStaysOnTopHint, self.always_on_top)
                    self.show()

    # ------------------------------------------------------------------ #
    #  Timer control                                                       #
    # ------------------------------------------------------------------ #

    def _start_exclusive(self, rowid):
        self._stop_all()
        self._start_additional(rowid)

    def _start_additional(self, rowid):
        self.clients[rowid].start()
        self._set_bold(rowid, True)

    def _stop_all(self):
        for rid, state in self.clients.items():
            if state.running:
                state.stop()
                self._set_bold(rid, False)
                self._update_display(rid)

    def _stop_one(self, rowid):
        state = self.clients[rowid]
        if state.running:
            state.stop()
            self._set_bold(rowid, False)
            self._update_display(rowid)

    def _reset_all(self):
        if QMessageBox.question(
                self, "Confirm", "Reset all times to zero?"
        ) == QMessageBox.Yes:
            self._stop_all()
            for state in self.clients.values():
                state.reset()
            self._update_all_displays()

    # ------------------------------------------------------------------ #
    #  Display helpers                                                     #
    # ------------------------------------------------------------------ #

    def _set_bold(self, rowid, bold, widget_dict=None):
        """Visual running marker for timer rows (bold + color + bullet)."""
        w = widget_dict or self._widgets.get(rowid)
        if not w or w.get("is_group"):
            return

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        normal_fg = t["text"]
        running_fg = t.get("running_text", normal_fg)
        color = running_fg if bold else normal_fg

        for key in ("name", "time"):
            lbl = w[key]
            f = lbl.font()
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
            self.clients[cid].running
            for cid in self._group_children(group_rowid)
            if cid in self.clients
        )

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        normal_fg = t.get("group_header_text", t["text"])
        running_fg = t.get("group_running_text", normal_fg)
        color = running_fg if has_running else normal_fg

        w = self._widgets[group_rowid]
        for key in ("name", "time"):
            lbl = w[key]
            f = lbl.font()
            f.setBold(has_running)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color};")

    def _update_display(self, rowid):
        if rowid in self._widgets:
            self._widgets[rowid]["time"].setText(
                _format_time(self.clients[rowid].current_elapsed)
            )

    def _update_all_displays(self):
        for rid in self.clients:
            self._update_display(rid)

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
        for rid, state in self.clients.items():
            if state.running:
                any_running = True
                self._update_display(rid)

        if any_running:
            for rid in self._visible_rowids:
                if rid in self._widgets and self._widgets[rid].get("is_group"):
                    w = self._widgets[rid]
                    if self.show_group_time:
                        w["time"].setText(_format_time(self._group_total_time(rid)))
                    if self.show_group_count:
                        w["count"].setText(
                            f"({len(self._group_children(rid))})")

        if self.daily_reset_enabled:
            self._check_daily_reset_boundary()

        self._tick_n += 1
        if self._tick_n % 20 == 0:
            self._save_state()

        self._try_snapshot(reason="tick",priority="low")

    # ------------------------------------------------------------------ #
    #  Persistence helpers                                                 #
    # ------------------------------------------------------------------ #

    def _build_state_dict(self):
        tracked = {}
        for rid, st in self.clients.items():
            st.freeze()
            entry = {"elapsed": st.elapsed}
            if st.running and st.started_at:
                entry["running_since"] = st.started_at.isoformat()
            tracked[str(rid)] = entry

        return {
            "meta": {
                "schema_version": 1,
                "saved_at": config.now_iso(),
                "is_completed_session": False,
            },
            "layout": {
                "rows": list(self.rows),
                "collapsed_groups": list(self._collapsed_groups),
            },
            "settings": {
                "theme": self.theme,
                "size": self.ui_size,
                "font": self.font_family,
                "label_align": self.label_align,
                "client_separators": self.client_separators,
                "show_group_count": self.show_group_count,
                "show_group_time": self.show_group_time,
                "always_on_top": self.always_on_top,
                "confirm_delete": self.confirm_delete,
                "confirm_reset": self.confirm_reset,
                "daily_reset_enabled": self.daily_reset_enabled,
                "daily_reset_time": self.daily_reset_time,
                "snapshot_min_minutes": self.snapshot_min_minutes,
            },
            "session": {
                "start": self._session_start.isoformat(),
                "tracked_times": tracked,
            },
        }

    def _save_state(self):
        state = self._build_state_dict()
        config.save_state(state)
        return state

    def _try_snapshot(self, reason, priority="low"):
        now = time.monotonic()
        if ((priority == "low" and now - self._last_snapshot_time > self.snapshot_min_minutes * 60)
            or (priority == "medium" and now - self._last_snapshot_time > self._snapshot_debounce)
            or priority == "high"):
            state = self._save_state()
            created_snapshot_path = create_snapshot(state, reason, priority)
            self._last_snapshot_time = now
            prune_snapshots()
            return created_snapshot_path
        else:
            return None

    # ------------------------------------------------------------------ #
    #  Daily reset                                                         #
    # ------------------------------------------------------------------ #

    def _most_recent_reset_boundary(self):
        try:
            rh, rm = map(int, self.daily_reset_time.split(":"))
        except ValueError:
            rh, rm = 0, 0
        now = datetime.now().astimezone()
        boundary_today = now.replace(hour=rh, minute=rm, second=0, microsecond=0)
        if now >= boundary_today:
            return boundary_today
        return boundary_today - timedelta(days=1)

    def _check_daily_reset_boundary(self):
        boundary = self._most_recent_reset_boundary()
        if self._session_start < boundary:
            self._do_daily_reset(boundary)

    def _do_daily_reset(self, boundary_dt):
        state = self._build_state_dict()
        config.save_completed_session(state, boundary_dt)

        self._stop_all()
        for st in self.clients.values():
            st.reset()
        self._update_all_displays()

        self._session_start = boundary_dt
        self._try_snapshot(reason="daily_reset_rollover", priority="high")

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
