"""Main application module — MainWindow and timer logic."""

import ctypes
import os
import re
import sys
from datetime import date, datetime

from PySide6.QtCore import Qt, QEvent, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QInputDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from ct import config
from ct.backup import create_backup
from ct.dialogs import ConfigDialog
from ct.state import ClientState
from ct.themes import THEMES, SIZES, FONTS

_SANITIZE = re.compile(r"[^a-zA-Z0-9\s'.]+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_time(seconds):
    """Format elapsed seconds as HH:MM:SS. Negative values clamp to zero."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _stylesheet(theme_name):
    """Build a Qt stylesheet string from a theme name."""
    t = THEMES.get(theme_name, THEMES["Cupertino Light"])
    return (
        f"QMainWindow, QDialog, QWidget {{ background-color: {t['bg']}; }}"
        f"QLabel {{ color: {t['text']}; background: transparent; }}"
        f"QPushButton {{"
        f"  color: {t['button_text']};"
        f"  background-color: {t['button_bg']};"
        f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
        f"  padding: 4px 8px;"
        f"}}"
        f"QPushButton:hover, QPushButton:pressed {{"
        f"  background-color: {t['button_active']};"
        f"}}"
        f"QLineEdit {{"
        f"  color: {t['button_text']};"
        f"  background-color: {t['button_bg']};"
        f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
        f"  padding: 3px 5px;"
        f"}}"
        f"QComboBox {{"
        f"  color: {t['button_text']};"
        f"  background-color: {t['button_bg']};"
        f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
        f"  padding: 3px 5px;"
        f"}}"
        f"QComboBox QAbstractItemView {{"
        f"  color: {t['button_text']};"
        f"  background-color: {t['button_bg']};"
        f"  selection-background-color: {t['button_active']};"
        f"}}"
        f"QSpinBox, QTimeEdit {{"
        f"  color: {t['button_text']};"
        f"  background-color: {t['button_bg']};"
        f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
        f"  padding: 3px 5px;"
        f"}}"
        f"QListWidget {{"
        f"  background-color: {t['button_bg']};"
        f"  border: 1px solid rgba(128,128,128,0.4);"
        f"  outline: none;"
        f"}}"
        f"QListWidget::item {{"
        f"  color: {t['button_text']};"
        f"  padding: 8px 12px;"
        f"}}"
        f"QListWidget::item:selected {{"
        f"  background-color: {t['button_active']};"
        f"  color: {t['button_text']};"
        f"}}"
        f"QToolTip {{"
        f"  background-color: {t['bg']};"
        f"  color: {t['text']};"
        f"  border: 1px solid {t['separator']};"
        f"  padding: 4px 8px;"
        f"}}"
    )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Client Timer")

        # Transparent 1x1 icon — prevents ugly default exe icon on title bar
        _blank = QPixmap(1, 1)
        _blank.fill(Qt.transparent)
        self.setWindowIcon(QIcon(_blank))

        # -- Load config --
        cfg = config.load_config()
        self.theme = cfg["theme"] if cfg["theme"] in THEMES else "Cupertino Light"
        self.ui_size = cfg["size"] if cfg["size"] in SIZES else "Regular"
        self.label_align = cfg.get("label_align", "Left")
        self.client_separators = cfg.get("client_separators", False)
        self.show_group_count = cfg.get("show_group_count", True)
        self.show_group_time = cfg.get("show_group_time", True)
        self.font_family = cfg.get("font", "Calibri")
        self.always_on_top = cfg.get("always_on_top", True)
        self.backup_frequency = cfg.get("backup_frequency", 15)
        self.max_backups = cfg.get("max_backups", 5)
        self.confirm_delete = cfg.get("confirm_delete", True)
        self.confirm_reset = cfg.get("confirm_reset", True)
        self.daily_reset_enabled = cfg.get("daily_reset_enabled", False)
        self.daily_reset_time = cfg.get("daily_reset_time", "00:00")

        if self.always_on_top:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        # -- Row data --
        # Each row: {"rowid": int, "name": str, "type": "timer"|"separator", "bg": str|None}
        self.rows = list(cfg["clients"])
        self._next_rowid = max((r["rowid"] for r in self.rows), default=-1) + 1
        self.clients = {}  # rowid -> ClientState (timers only)
        for row in self.rows:
            if row["type"] == "timer":
                self.clients[row["rowid"]] = ClientState(row["name"])

        self._widgets = {}        # rowid -> widget dict
        self._has_mdl2 = "Segoe MDL2 Assets" in QFontDatabase.families()
        self._shift_held = False
        self._rearranging = False
        self._dragging_client = None  # rowid or None
        self._drag_last_row = -1
        self._drag_group_rids = None  # set of rowids when dragging collapsed group
        self._drag_hidden_rids = None  # snapshot of hidden rids during separator drag
        self._collapsed_groups = set(cfg.get("collapsed_groups", []))
        self._visible_rowids = []  # populated by _rebuild_rows
        self._last_reset_date = None  # daily auto-reset tracking

        # -- Restore today's save (before building UI) --
        saved = config.load_times()
        if saved:
            for key, elapsed in saved.items():
                try:
                    rid = int(key)
                    if rid in self.clients:
                        self.clients[rid].elapsed = elapsed
                except ValueError:
                    # Old name-based format — match by name
                    for row in self.rows:
                        if row["type"] == "timer" and row["name"] == key:
                            rid = row["rowid"]
                            if rid in self.clients:
                                self.clients[rid].elapsed = elapsed
                            break

        # -- Build UI skeleton --
        central = QWidget()
        self.setCentralWidget(central)
        self._main_lay = QVBoxLayout(central)

        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
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
    #  Style / row building                                                #
    # ------------------------------------------------------------------ #

    def _apply_style(self):
        style = _stylesheet(self.theme)

        # Keep this so widgets under MainWindow are styled
        self.setStyleSheet(style)

        # Also apply globally so QToolTip picks up QToolTip rules
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(style)

        s = SIZES.get(self.ui_size, SIZES["Regular"])
        self._main_lay.setContentsMargins(
            s["frame_pad"], s["frame_pad"], s["frame_pad"], s["frame_pad"]
        )
        self._main_lay.setSpacing(s["padding"])

    def _group_children(self, group_rowid):
        """Return rowids of timer rows belonging to a separator (all rows
        after it until the next separator or end of list).

        During a collapsed-group drag, uses the snapshot for the dragged group
        so that positional interlopers don't affect counts/times."""
        if (self._drag_group_rids is not None
                and group_rowid == self._dragging_client):
            return list(self._drag_group_rids)
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
        """Sum of floored current_elapsed for all children of a separator.
        We floor each child individually so the aggregate matches the
        sum of the displayed HH:MM:SS values (no off-by-one weirdness)."""
        total = 0
        for child_rid in self._group_children(group_rowid):
            if child_rid in self.clients:
                total += int(self.clients[child_rid].current_elapsed)
        return total

    def _rebuild_rows(self):
        """Tear down and recreate the entire grid: client rows + footer.

        Every row (separator and timer) is built inside its own container
        widget that spans all grid columns.  The container's background
        colour fills the entire row width seamlessly — no inter-column
        gaps showing the window bg.
        """
        self._widgets.clear()

        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        s = SIZES.get(self.ui_size, SIZES["Regular"])
        sh = self._shift_held

        h_spacing = s.get("h_spacing", s["padding"])
        btn_spacing = max(1, h_spacing // 2)

        # Grid is now just a vertical stack — rows handle their own h-spacing
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(s.get("v_spacing", s["padding"]))

        ncols = 6  # kept for addWidget spanning
        time_font = QFont(self.font_family, s["time"])
        action_font = QFont(self.font_family, s["action"])

        # Pre-compute column-0 reference size (square)
        _ref = QPushButton("\u2261")
        _ref.setFont(action_font)
        _h = _ref.sizeHint().height()
        col0_size = QSize(_h, _h)
        _ref.deleteLater()

        # Pre-compute column-5 reference size (square)
        _ref_x = QPushButton("X")
        _ref_x.setFont(action_font)
        _hx = _ref_x.sizeHint().height()
        col5_size = QSize(_hx, _hx)
        _ref_x.deleteLater()

        start_min_w = QFontMetrics(time_font).horizontalAdvance("Start") + 20

        if not self.rows:
            lbl = QLabel("No clients. Add one to begin!")
            lbl.setFont(QFont(self.font_family, s["label"]))
            lbl.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(lbl, 0, 0, 1, ncols)
            self._visible_rowids = []
            footer_row = 1
        else:
            bold_label = QFont(self.font_family, s["label"])
            bold_label.setBold(True)
            fm_label = QFontMetrics(bold_label)
            indent_px = fm_label.horizontalAdvance("  ")
            display_names = [r["name"] for r in self.rows]
            min_name_w = max(
                fm_label.horizontalAdvance(dn) for dn in display_names
            ) + indent_px + 4
            bold_time = QFont(self.font_family, s["time"])
            bold_time.setBold(True)
            min_time_w = QFontMetrics(bold_time).horizontalAdvance("00:00:00 ")

            _ALIGN = {"Left": Qt.AlignLeft | Qt.AlignVCenter,
                      "Center": Qt.AlignCenter,
                      "Right": Qt.AlignRight | Qt.AlignVCenter}

            # Determine visible entries (skip collapsed children)
            current_group_rid = None
            visible_entries = []  # (row_dict, is_child)
            dragging_group = (self._dragging_client is not None
                              and self._drag_group_rids is not None)
            for row in self.rows:
                if row["type"] == "separator":
                    current_group_rid = row["rowid"]
                    visible_entries.append((row, False))
                else:
                    # During a group drag, hide snapshot members of the
                    # dragged group (the "no suck-up" rule: new positional
                    # children stay visible).
                    if dragging_group and row["rowid"] in self._drag_group_rids:
                        continue
                    # During any separator drag, keep originally-hidden
                    # timers hidden so repositioning doesn't make collapsed
                    # groups appear to expand.
                    if (self._drag_hidden_rids is not None
                            and row["rowid"] in self._drag_hidden_rids):
                        continue
                    # Hide children of other collapsed groups normally
                    is_child = current_group_rid is not None
                    if (is_child
                            and current_group_rid in self._collapsed_groups
                            and not (dragging_group
                                     and current_group_rid == self._dragging_client)):
                        continue
                    visible_entries.append((row, is_child))

            self._visible_rowids = [r["rowid"] for r, _ in visible_entries]
            group_header_bg = t.get("group_header_bg", t["bg"])
            group_header_fg = t.get("group_header_text", t["text"])
            group_running_fg = t.get("group_running_text", group_header_fg)
            running_fg = t.get("running_text", t["text"])

            grow = 0
            for idx, (row, is_child) in enumerate(visible_entries):
                rid = row["rowid"]

                # -- Compute row background --
                if row["type"] == "separator":
                    row_bg = row.get("bg") or group_header_bg
                else:
                    row_bg = row.get("bg") or t["bg"]
                if self._dragging_client == rid:
                    row_bg = t["row_dragged"]

                # -- Row container (spans all grid columns) --
                rc = QWidget()
                rc.setObjectName("rowBg")
                margin_css = f"margin-left: {indent_px-3}px;" if (row["type"] == "timer" and is_child) else ""
                needs_sep = (self.client_separators
                             and idx < len(visible_entries) - 1
                             and row["type"] == "timer"
                             and visible_entries[idx + 1][0]["type"] == "timer")
                border_css = f"border-bottom: 1px solid {t['row_separator']};" if needs_sep else ""
                rc.setStyleSheet(f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")
                rc_lay = QHBoxLayout(rc)
                rc_lay.setContentsMargins(0, 0, 0, 0)
                rc_lay.setSpacing(h_spacing)

                if row["type"] == "separator":
                    # ── Separator row ──
                    collapsed = rid in self._collapsed_groups
                    # During group drag, use snapshot children for the dragged group
                    if (dragging_group and rid == self._dragging_client):
                        children = list(self._drag_group_rids)
                        collapsed = True  # still visually collapsed during drag
                    else:
                        children = self._group_children(rid)

                    # Col 0: toggle
                    toggle_btn = QPushButton(
                        "\u25B8" if collapsed else "\u25BE"
                    )
                    toggle_btn.setFont(action_font)
                    toggle_btn.setFixedSize(col0_size)
                    toggle_btn.setStyleSheet("padding: 0px;")
                    toggle_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_group_toggle(r)
                    )
                    rc_lay.addWidget(toggle_btn)

                    # Col 1: name (underlined always, bold if children running)
                    name_lbl = QLabel(row["name"])
                    grp_name_font = QFont(self.font_family, s["label"])
                    has_running = any(
                        cid in self.clients and self.clients[cid].running
                        for cid in children
                    )
                    grp_name_font.setBold(has_running)
                    name_lbl.setFont(grp_name_font)
                    name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    name_lbl.setFixedWidth(min_name_w)
                    name_lbl.setStyleSheet(f"color: {group_running_fg if has_running else group_header_fg};")
                    rc_lay.addWidget(name_lbl)

                    # Col 2: child count
                    count_lbl = QLabel(f"({len(children)})")
                    count_lbl.setFont(action_font)
                    count_lbl.setAlignment(Qt.AlignCenter)
                    count_lbl.setStyleSheet(f"color: {group_header_fg};")
                    if not self.show_group_count:
                        count_lbl.setVisible(False)
                    rc_lay.addWidget(count_lbl)

                    # Col 3: aggregate time
                    total = self._group_total_time(rid)
                    time_lbl = QLabel(_format_time(total))
                    grp_time_font = QFont(self.font_family, s["time"])
                    if has_running:
                        grp_time_font.setBold(True)
                    time_lbl.setFont(grp_time_font)
                    time_lbl.setAlignment(Qt.AlignCenter)
                    time_lbl.setFixedWidth(min_time_w)
                    time_lbl.setStyleSheet(f"color: {group_running_fg if has_running else group_header_fg};")
                    if not self.show_group_time:
                        time_lbl.setVisible(False)
                    rc_lay.addWidget(time_lbl)

                    # Col 4: empty (same width as -5/+5 area)
                    spacer = QLabel("")
                    rc_lay.addWidget(spacer)

                    # Col 5: delete
                    x_btn = QPushButton("X")
                    x_btn.setFont(action_font)
                    x_btn.setFixedWidth(col5_size.width())
                    x_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_remove_group(r)
                    )
                    rc_lay.addWidget(x_btn)

                    self._widgets[rid] = {
                        "name": name_lbl, "time": time_lbl,
                        "count": count_lbl, "x": x_btn,
                        "container": rc, "is_group": True,
                    }

                else:
                    # ── Timer row ──
                    state = self.clients[rid]
                    fg = running_fg if state.running else t["text"]

                    # Col 0: bullet
                    bullet = QLabel(
                        "\u2022" if state.running else ""
                    )
                    bullet.setFont(action_font)
                    bullet.setAlignment(Qt.AlignCenter)
                    bullet.setFixedSize(col0_size)
                    bullet.setStyleSheet(f"color: {fg};")
                    rc_lay.addWidget(bullet)

                    # Col 1: name
                    name_lbl = QLabel(row["name"])
                    name_lbl.setFont(QFont(self.font_family, s["label"]))
                    name_lbl.setAlignment(
                        _ALIGN.get(self.label_align, Qt.AlignCenter)
                    )
                    name_lbl.setFixedWidth(min_name_w)
                    name_lbl.setStyleSheet(f"color: {fg};")
                    rc_lay.addWidget(name_lbl)

                    # Col 2: Start / Stop
                    start_btn = QPushButton("Add" if sh else "Start")
                    start_btn.setFont(time_font)
                    start_btn.setMinimumWidth(start_min_w)
                    start_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    start_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_start(r)
                    )

                    stop_btn = QPushButton("Stop")
                    stop_btn.setFont(time_font)
                    stop_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    stop_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_stop(r)
                    )

                    ss_container = QWidget()
                    ss_container.setObjectName("ssCt")
                    ss_container.setStyleSheet("#ssCt { background: transparent; }")
                    ss_lay = QHBoxLayout(ss_container)
                    ss_lay.setContentsMargins(0, 0, 0, 0)
                    ss_lay.setSpacing(btn_spacing)
                    ss_lay.addWidget(start_btn)
                    ss_lay.addWidget(stop_btn)
                    rc_lay.addWidget(ss_container)

                    # Col 3: time
                    time_lbl = QLabel(_format_time(state.current_elapsed))
                    time_lbl.setFont(time_font)
                    time_lbl.setAlignment(Qt.AlignCenter)
                    time_lbl.setFixedWidth(min_time_w)
                    time_lbl.setStyleSheet(f"color: {fg};")
                    rc_lay.addWidget(time_lbl)

                    # Col 4: -5/+5
                    minus_btn = QPushButton("-1" if sh else "-5")
                    minus_btn.setFont(action_font)
                    minus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    minus_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_adjust(r, -1)
                    )

                    plus_btn = QPushButton("+1" if sh else "+5")
                    plus_btn.setFont(action_font)
                    plus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                    plus_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_adjust(r, 1)
                    )

                    adj_container = QWidget()
                    adj_container.setObjectName("adjCt")
                    adj_container.setStyleSheet("#adjCt { background: transparent; }")
                    adj_lay = QHBoxLayout(adj_container)
                    adj_lay.setContentsMargins(0, 0, 0, 0)
                    adj_lay.setSpacing(btn_spacing)
                    adj_lay.addWidget(minus_btn)
                    adj_lay.addWidget(plus_btn)
                    rc_lay.addWidget(adj_container)

                    # Col 5: X / 0
                    x_btn = QPushButton("0" if sh else "X")
                    x_btn.setFont(action_font)
                    x_btn.setFixedWidth(col5_size.width())
                    x_btn.clicked.connect(
                        lambda _=False, r=rid: self._on_remove(r)
                    )
                    rc_lay.addWidget(x_btn)

                    self._widgets[rid] = {
                        "name": name_lbl, "time": time_lbl,
                        "start": start_btn, "stop": stop_btn,
                        "minus": minus_btn, "plus": plus_btn,
                        "x": x_btn, "bullet": bullet,
                        "container": rc,
                    }

                    if state.running:
                        self._set_bold(rid, True)

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

                # Add the row container to the grid (spans all columns)
                self._grid.addWidget(rc, grow, 0, 1, ncols)
                grow += 1

            footer_row = grow

        # -- Footer separator --
        if self.rows:
            sep = QWidget()
            sep.setFixedHeight(2)
            sep.setStyleSheet(f"background-color: {t['separator']};")
            self._grid.addWidget(sep, footer_row, 0, 1, ncols)
            footer_row += 1

        # -- Footer (also in a container for consistent layout) --
        footer_font = QFont(self.font_family, s["action"])

        if self._has_mdl2:
            lock_char = "\uE72E"     # Lock
            unlock_char = "\uE785"   # Unlock
            lock_font = QFont("Segoe MDL2 Assets", s["action"])
        else:
            # Fallback to plain shapes (see option 2)
            lock_char = "\u25A0"
            unlock_char = "\u25A1"
            lock_font = footer_font

        self._rearrange_btn = QPushButton(
            unlock_char if self._rearranging else lock_char
        )
        self._rearrange_btn.setFont(lock_font)
        self._rearrange_btn.setFixedSize(col0_size)
        self._rearrange_btn.setStyleSheet("padding: 0px;")
        self._rearrange_btn.clicked.connect(self._on_rearrange_toggle)
        if self._rearranging:
            self._rearrange_btn.setToolTip("Lock UI layout")
        else:
            self._rearrange_btn.setToolTip("Unlock UI layout (drag rows to rearrange)")

        self._add_btn = QPushButton("Add Client")
        self._add_btn.setFont(footer_font)
        self._add_btn.clicked.connect(self._on_add)
        self._add_btn.setToolTip("Add a new client timer to UI")

        self._add_group_btn = QPushButton("Add Separator")
        self._add_group_btn.setFont(footer_font)
        self._add_group_btn.clicked.connect(self._on_add_group)
        self._add_group_btn.setToolTip("Add a new separator timer to UI")

        add_btns = QWidget()
        add_btns.setObjectName("addBtns")
        add_btns.setStyleSheet("#addBtns { background: transparent; }")
        add_btns_lay = QHBoxLayout(add_btns)
        add_btns_lay.setContentsMargins(0, 0, 0, 0)
        add_btns_lay.setSpacing(btn_spacing)
        add_btns_lay.addWidget(self._add_btn)
        add_btns_lay.addWidget(self._add_group_btn)

        self._add_input = QLineEdit()
        self._add_input.setFont(footer_font)
        self._add_input.setPlaceholderText("Client name...")
        self._add_input.returnPressed.connect(self._on_add)

        if self._has_mdl2:
            self._cfg_btn = QPushButton("\uE713")
            self._cfg_btn.setFont(QFont("Segoe MDL2 Assets", s["action"]))
        else:
            self._cfg_btn = QPushButton("\u2699")
            self._cfg_btn.setFont(footer_font)
        self._cfg_btn.setFixedSize(col5_size)
        self._cfg_btn.setStyleSheet("padding: 0px;")
        self._cfg_btn.clicked.connect(self._on_config)
        self._cfg_btn.setToolTip("Settings")

        footer = QWidget()
        footer.setObjectName("footer")
        footer.setStyleSheet("#footer { background: transparent; }")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(0, 0, 0, 0)
        f_lay.setSpacing(h_spacing)
        f_lay.addWidget(self._rearrange_btn)
        f_lay.addWidget(add_btns)
        f_lay.addWidget(self._add_input, 1)  # stretch to fill
        f_lay.addWidget(self._cfg_btn)

        self._grid.addWidget(footer, footer_row, 0, 1, ncols)

        QTimer.singleShot(0, self._sync_footer_heights)

    # ------------------------------------------------------------------ #
    #  Shift-key visual feedback                                           #
    # ------------------------------------------------------------------ #

    def _update_shift_labels(self):
        """Swap button text to reflect current Shift state — no resizing."""
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
                if self._dragging_client is not None:
                    self._end_row_drag()
        super().changeEvent(event)

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

    def _on_add(self):
        raw = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self.rows.append({"rowid": rid, "name": name, "type": "timer", "bg": None})
        self.clients[rid] = ClientState(name)
        self._persist_client_list()
        self._rebuild_rows()

    def _on_add_group(self):
        raw = self._add_input.text().strip()
        name = _SANITIZE.sub("", raw).strip()
        if not name:
            return
        rid = self._next_rowid
        self._next_rowid += 1
        self.rows.append({"rowid": rid, "name": name, "type": "separator", "bg": None})
        self._persist_client_list()
        self._rebuild_rows()

    def _on_remove_group(self, rowid):
        """Remove a separator — children stay in the list."""
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
        self._persist_client_list()
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

    def _on_group_toggle(self, rowid):
        """Expand or collapse a separator."""
        if rowid in self._collapsed_groups:
            self._collapsed_groups.discard(rowid)
        else:
            self._collapsed_groups.add(rowid)
        self._persist_client_list()
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

    def _on_remove(self, rowid):
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            # Shift+X: reset time to zero, keep the client
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
            self._persist_client_list()
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

    def _on_rearrange_toggle(self):
        self._rearranging = not self._rearranging
        self._rebuild_rows()

    def _hidden_rids_snapshot(self):
        """Return set of timer rowids currently hidden under collapsed groups."""
        hidden = set()
        parent = None
        for row in self.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif parent is not None and parent in self._collapsed_groups:
                hidden.add(row["rowid"])
        return hidden

    def _start_row_drag(self, rowid):
        """Begin drag-reordering a row."""
        self._dragging_client = rowid

        # Detect collapsed separator → group drag
        row = next(r for r in self.rows if r["rowid"] == rowid)
        if row["type"] == "separator" and rowid in self._collapsed_groups:
            children = self._group_children(rowid)
            self._drag_group_rids = set(children)  # snapshot (no separator itself)
        else:
            self._drag_group_rids = None

        # For any separator drag, freeze which timers are hidden so that
        # repositioning doesn't make collapsed groups appear to expand.
        if row["type"] == "separator":
            self._drag_hidden_rids = self._hidden_rids_snapshot()
        else:
            self._drag_hidden_rids = None

        self._drag_last_row = self._visible_rowids.index(rowid)
        # Lock window size during drag to prevent layout glitches
        self.setFixedSize(self.size())
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        QApplication.instance().installEventFilter(self)
        self._rebuild_rows()

    def eventFilter(self, obj, event):
        # Global drag handling (installed on QApplication during drag)
        if self._dragging_client is not None:
            if event.type() == QEvent.MouseMove:
                global_pos = event.globalPosition().toPoint()
                local_pos = self._grid_widget.mapFromGlobal(global_pos)
                target_vis = self._row_at_y(local_pos.y())
                if target_vis is not None and target_vis != self._drag_last_row:
                    drag_rid = self._dragging_client
                    target_rid = self._visible_rowids[target_vis]

                    if self._drag_group_rids is not None:
                        # Group drag: move separator + snapshot children as block
                        block = [r for r in self.rows
                                 if r["rowid"] == drag_rid
                                 or r["rowid"] in self._drag_group_rids]
                        self.rows = [r for r in self.rows
                                     if r["rowid"] != drag_rid
                                     and r["rowid"] not in self._drag_group_rids]
                        target_idx = next(
                            (i for i, r in enumerate(self.rows)
                             if r["rowid"] == target_rid), len(self.rows)
                        )
                        if target_vis > self._drag_last_row:
                            target_idx += 1
                            # Skip past target separator's children so we
                            # don't steal them by inserting between parent
                            # and children.
                            if (target_idx > 0
                                    and self.rows[target_idx - 1]["type"]
                                    == "separator"):
                                while (target_idx < len(self.rows)
                                       and self.rows[target_idx]["type"]
                                       != "separator"):
                                    target_idx += 1
                        for j, br in enumerate(block):
                            self.rows.insert(target_idx + j, br)
                    else:
                        # Single row drag
                        drag_row = next(
                            r for r in self.rows if r["rowid"] == drag_rid
                        )
                        self.rows.remove(drag_row)
                        target_idx = next(
                            i for i, r in enumerate(self.rows)
                            if r["rowid"] == target_rid
                        )
                        if target_vis > self._drag_last_row:
                            insert_idx = target_idx + 1
                            # When dragging a separator down past another
                            # separator, skip past its children so we don't
                            # hijack them.
                            if (self._drag_hidden_rids is not None
                                    and self.rows[target_idx]["type"]
                                    == "separator"):
                                while (insert_idx < len(self.rows)
                                       and self.rows[insert_idx]["type"]
                                       != "separator"):
                                    insert_idx += 1
                            self.rows.insert(insert_idx, drag_row)
                        else:
                            self.rows.insert(target_idx, drag_row)

                    # Pre-expand any collapsed group that would swallow the row
                    # (only for single-timer drags — separators never need this)
                    drag_row_obj = next(
                        (r for r in self.rows if r["rowid"] == drag_rid), None)
                    if (drag_row_obj and drag_row_obj["type"] == "timer"
                            and self._drag_group_rids is None):
                        parent = self._parent_group(drag_rid)
                        if parent is not None and parent in self._collapsed_groups:
                            self._collapsed_groups.discard(parent)

                    self._rebuild_rows()
                    if drag_rid in self._visible_rowids:
                        self._drag_last_row = self._visible_rowids.index(drag_rid)
                return True

            if event.type() == QEvent.MouseButtonRelease:
                self._end_row_drag()
                return True

            return False

        # Row hover handling (installed on containers)
        if event.type() == QEvent.Enter:
            rid = self._rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, True)
        elif event.type() == QEvent.Leave:
            rid = self._rid_for_container(obj)
            if rid is not None:
                self._on_row_hover(rid, False)

        # Drag initiation (unlocked + left click on non-button area)
        if self._rearranging and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                rid = self._rid_for_container(obj)
                if rid is not None:
                    self._start_row_drag(rid)
                    return True

        return super().eventFilter(obj, event)

    def _row_at_y(self, y):
        """Return the visible row index whose vertical center is closest to y."""
        best_row = None
        best_dist = float("inf")
        for vis_idx, rid in enumerate(self._visible_rowids):
            if rid in self._widgets and "container" in self._widgets[rid]:
                rect = self._widgets[rid]["container"].geometry()
                dist = abs(y - rect.center().y())
                if dist < best_dist:
                    best_dist = dist
                    best_row = vis_idx
        return best_row

    def _end_row_drag(self):
        """Finish drag-reordering and persist the new order."""
        drag_rid = self._dragging_client
        was_group_drag = self._drag_group_rids is not None

        self._dragging_client = None
        self._drag_last_row = -1
        self._drag_group_rids = None
        self._drag_hidden_rids = None

        # Auto-expand the group on drop so user sees the new membership
        if was_group_drag and drag_rid is not None:
            self._collapsed_groups.discard(drag_rid)

        self._persist_client_list()
        QApplication.restoreOverrideCursor()
        QApplication.instance().removeEventFilter(self)
        # Unlock window size (was locked at drag start)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._rebuild_rows()
        QTimer.singleShot(0, self.adjustSize)

    def _rid_for_container(self, widget):
        """Map a container widget back to its rowid."""
        for rid, w in self._widgets.items():
            if w.get("container") is widget:
                return rid
        return None

    def _on_row_hover(self, rid, entering):
        """Underline the row's name label on hover."""
        if rid not in self._widgets:
            return
        name_lbl = self._widgets[rid]["name"]
        f = name_lbl.font()
        f.setUnderline(entering)
        name_lbl.setFont(f)

    def _on_row_context_menu(self, rowid, global_pos):
        """Show a right-click context menu for a row."""
        row = next((r for r in self.rows if r["rowid"] == rowid), None)
        if row is None:
            return
        is_timer = row["type"] == "timer"

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {t['button_bg']}; color: {t['button_text']};"
            f"  border: 1px solid rgba(128,128,128,0.4); }}"
            f"QMenu::item {{ padding: 4px 20px; }}"
            f"QMenu::item:selected {{ background-color: {t['button_active']};"
            f"  color: {t['button_text']}; }}"
            f"QMenu::separator {{ height: 1px; background: rgba(128,128,128,0.4);"
            f"  margin: 4px 0px; }}"
        )

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
                    self._persist_client_list()
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
                self._persist_client_list()
                self._rebuild_rows()
        elif action == reset_color:
            row["bg"] = None
            self._persist_client_list()
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
            self._persist_client_list()
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

    @staticmethod
    def _parse_time_input(text):
        """Parse HH:MM:SS, MM:SS, or bare minutes into seconds."""
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

    def _on_config(self):
        cfg = config.load_config()
        cfg["theme"] = self.theme
        cfg["size"] = self.ui_size
        cfg["font"] = self.font_family
        cfg["label_align"] = self.label_align
        cfg["client_separators"] = self.client_separators
        cfg["show_group_count"] = self.show_group_count
        cfg["show_group_time"] = self.show_group_time
        cfg["always_on_top"] = self.always_on_top
        cfg["backup_frequency"] = self.backup_frequency
        cfg["max_backups"] = self.max_backups
        cfg["confirm_delete"] = self.confirm_delete
        cfg["confirm_reset"] = self.confirm_reset
        cfg["daily_reset_enabled"] = self.daily_reset_enabled
        cfg["daily_reset_time"] = self.daily_reset_time

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
            self.backup_frequency = dlg.chosen_backup_frequency
            self.max_backups = dlg.chosen_max_backups
            self.confirm_delete = dlg.chosen_confirm_delete
            self.confirm_reset = dlg.chosen_confirm_reset
            self.daily_reset_enabled = dlg.chosen_daily_reset_enabled
            self.daily_reset_time = dlg.chosen_daily_reset_time

            cfg["theme"] = self.theme
            cfg["size"] = self.ui_size
            cfg["font"] = self.font_family
            cfg["label_align"] = self.label_align
            cfg["client_separators"] = self.client_separators
            cfg["show_group_count"] = self.show_group_count
            cfg["show_group_time"] = self.show_group_time
            cfg["always_on_top"] = self.always_on_top
            cfg["backup_frequency"] = self.backup_frequency
            cfg["max_backups"] = self.max_backups
            cfg["confirm_delete"] = self.confirm_delete
            cfg["confirm_reset"] = self.confirm_reset
            cfg["daily_reset_enabled"] = self.daily_reset_enabled
            cfg["daily_reset_time"] = self.daily_reset_time
            config.save_config(cfg)

            self._apply_style()
            self._rebuild_rows()
            QTimer.singleShot(0, self.adjustSize)

            # Toggle always-on-top via Win32 API (avoids window
            # recreation which can grey-out the close button)
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
        """Stop everything, then start this one."""
        self._stop_all()
        self._start_additional(rowid)

    def _start_additional(self, rowid):
        """Start a timer without touching others."""
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

    def _set_bold(self, rowid, bold):
        """Visual running marker for timer rows (bold + color + bullet)."""
        if rowid not in self._widgets:
            return
        if self._widgets[rowid].get("is_group"):
            return

        t = THEMES.get(self.theme, THEMES["Cupertino Light"])
        normal_fg = t["text"]
        running_fg = t.get("running_text", normal_fg)
        color = running_fg if bold else normal_fg

        w = self._widgets[rowid]

        # Name + time: bold + color
        for key in ("name", "time"):
            lbl = w[key]
            f = lbl.font()
            f.setBold(bold)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color};")

        # Bullet: dot + color when running
        b = w.get("bullet")
        if b is not None:
            b.setText("\u2022" if bold else "")
            b.setStyleSheet(f"color: {color};")

        # Propagate to parent group
        parent = self._parent_group(rowid)
        if parent is not None:
            self._update_group_bold(parent)

    def _parent_group(self, rowid):
        """Return the separator rowid that owns this timer, or None."""
        parent = None
        for row in self.rows:
            if row["type"] == "separator":
                parent = row["rowid"]
            elif row["rowid"] == rowid:
                return parent
        return None

    def _update_group_bold(self, group_rowid):
        """Bold + recolor a group's header based on running children."""
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
        """Force all footer widgets to the same height after layout settles."""
        if hasattr(self, "_add_btn") and self._add_btn.height() > 0:
            h = self._add_btn.height()
            self._add_input.setFixedHeight(h)
            self._rearrange_btn.setFixedHeight(h)
            self._cfg_btn.setFixedHeight(h)
            if hasattr(self, "_add_group_btn"):
                self._add_group_btn.setFixedHeight(h)

    # ------------------------------------------------------------------ #
    #  Tick / autosave / backup                                            #
    # ------------------------------------------------------------------ #

    def _tick(self):
        any_running = False
        for rid, state in self.clients.items():
            if state.running:
                any_running = True
                self._update_display(rid)

        # Update separator aggregate times when any timer is running
        if any_running:
            for rid in self._visible_rowids:
                if rid in self._widgets and self._widgets[rid].get("is_group"):
                    w = self._widgets[rid]
                    if self.show_group_time:
                        w["time"].setText(_format_time(self._group_total_time(rid)))
                    if self.show_group_count:
                        w["count"].setText(
                            f"({len(self._group_children(rid))})"
                        )

        # Daily auto-reset check
        if self.daily_reset_enabled:
            now = datetime.now()
            try:
                rh, rm = map(int, self.daily_reset_time.split(":"))
            except ValueError:
                rh, rm = 0, 0
            if (now.hour == rh and now.minute == rm
                    and self._last_reset_date != date.today()):
                self._last_reset_date = date.today()
                self._stop_all()
                for state in self.clients.values():
                    state.reset()
                self._update_all_displays()
                self._save_times()

        self._tick_n += 1
        if self._tick_n % 20 == 0:
            self._save_times()
        backup_ticks = max(60, self.backup_frequency * 60)
        if self._tick_n % backup_ticks == 0:
            create_backup(config.SAVE_PATH, config.BACKUP_DIR,
                          limit=self.max_backups)

    def _save_times(self):
        times = {}
        for rid, state in self.clients.items():
            state.freeze()
            times[str(rid)] = state.elapsed
        config.save_times(times)

    # ------------------------------------------------------------------ #
    #  Persistence helpers                                                 #
    # ------------------------------------------------------------------ #

    def _persist_client_list(self):
        cfg = config.load_config()
        cfg["clients"] = list(self.rows)
        cfg["collapsed_groups"] = list(self._collapsed_groups)
        config.save_config(cfg)

    # ------------------------------------------------------------------ #
    #  Window close                                                        #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        try:
            self._save_times()
        except Exception as e:
            QMessageBox.warning(self, "Save Error",
                                f"Failed to save times:\n{e}")
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
