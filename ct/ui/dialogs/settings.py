"""Configuration dialog for Client Timer — tabbed sidebar layout."""

import json
import re
from datetime import datetime
from pathlib import Path
import random
from PySide6.QtCore import Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from ct.common.setup import PATHS

# ---------------------------------------------------------------------------
# Tips
# ---------------------------------------------------------------------------
# Almost everything this app can do is a gesture with no affordance: clicking
# a time, double-clicking a name, Shift for a different step size. Nobody
# discovers those on their own, and the app's whole point is not interrupting
# people, so the teaching has to be somewhere they already go rather than
# something that arrives uninvited.
#
# Settings is that place. Themes and the session history both live here, so
# every user ends up in this dialog on their own steam. The strip sits below
# the page stack, which means it shows on EVERY page without any page having
# to know about it.
#
# Keep each line SHORT - one line at the dialog's width, no wrapping, so the
# strip can never change height and jostle the dialog while it cycles. There
# is a test for it.
TIPS = (
    "Click any timer's time to copy just that one.",
    "Click any timer row in a historical saved session to copy its time.",
    "Double-click a row's name to rename it in place.",
    "Right-click a row for Set Time, colors, and more.",
    "Shift-click Start to run a timer alongside the others.",
    "Hold Shift when clicking time adjusts for 1 minute increments instead of 5.",
    "Click the footer status line to copy every time at once.",
    "Ctrl+Z undoes deletes, resets, renames and reorders.",
    "Height can be shrunken from the maximum, and will persist (with scroll).",
    "Unlock the UI (bottom left) to add, rearrange, and delete rows.",
    "Past sessions live under History. Backups live under General.",
    "Copy Format, under General, decides how times reach your clipboard.",
    "More than 20 themes are available in Appearance to improve your visual experience.",
    "CT2 maintains backups for unexpected interruptions. Restore them in General.",
)

# Slow on purpose. Long enough to finish reading and glance away, short
# enough that a minute of picking a theme shows you a few.
TIP_INTERVAL_MS = 9000

# Fixed label, never part of the cycling text and never elided. Without it a
# lone italic sentence in the corner reads as a status message about whatever
# page you happen to be on.
TIP_PREFIX = "Tip: "
from ct.ui.theme import THEMES, SIZES, FONTS, build_menu_stylesheet
from ct.ui.widgets import TickCheckBox
from ct.util import (format_time, format_copy_time,
                     COPY_FORMATS, DEFAULT_COPY_FORMAT)


class PreviewRow(QWidget):
    """A timer row in the session preview — click it to copy its time."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _pretty_date(iso):
    """'2026-08-05' -> 'August 5, 2026'. Returns the input on anything odd,
    so a hand-edited RELEASE_DATE can never blank the About page."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _format_span(start_iso, end_iso):
    """Format a start–end ISO pair as a human-readable AM/PM span string.

    Same day:      'Mar 12, 3:43 AM – 7:45 PM'
    Different day:  'Mar 11, 11:30 PM – Mar 12, 3:00 AM'
    """
    try:
        s = datetime.fromisoformat(start_iso)
        e = datetime.fromisoformat(end_iso)
    except (ValueError, TypeError):
        return None
    fmt_time = "%#I:%M %p"        # Windows no-leading-zero hour
    fmt_date = "%b %#d"           # 'Mar 12' style
    if s.date() == e.date():
        return f"{s.strftime(fmt_date)}, {s.strftime(fmt_time)} – {e.strftime(fmt_time)}"
    return (f"{s.strftime(fmt_date)}, {s.strftime(fmt_time)} – "
            f"{e.strftime(fmt_date)}, {e.strftime(fmt_time)}")

# Simple tabbed settings dialog with a left sidebar for different categories. Opens when the user clicks the little
# gear icon in main app
class ReportProblemDialog(QDialog):
    """Describe a problem and send it, without leaving the app.

    Two things are deliberate. The description is sent AS WRITTEN — it is
    the one part the user chose to include, and redacting it would turn
    "Acme's timer stopped" into nonsense. Everything attached alongside it
    is scrubbed, because none of that was chosen. The dialog says both, so
    nobody has to guess what leaves the machine.
    """

    def __init__(self, parent, version, theme):
        super().__init__(parent)
        self.setWindowTitle("Report a Problem")
        self.setModal(True)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QLabel("What went wrong?")
        head.setFont(QFont("Calibri", 13, QFont.Bold))
        lay.addWidget(head)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(
            "What were you doing, and what happened instead?")
        self._text.setMinimumHeight(120)
        # Colored explicitly: without this the editor inherits a foreground
        # that sits almost on top of its own background on most themes, and
        # the one box the user has to type into is the one they can't read.
        t = THEMES.get(theme, THEMES["E-Ink (Default)"])
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ color: {t['app_fg']};"
            f" background-color: {t['control_bg']};"
            f" border: 1px solid {t['control_line']}; }}")
        lay.addWidget(self._text)

        note = QLabel(
            f"Sent with this report: version {version}, your operating "
            f"system, and the last part of the app log.\n"
            f"Timer names are removed automatically — but anything you "
            f"type above is sent exactly as written.")
        note.setWordWrap(True)
        note.setFont(QFont("Calibri", 10))
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._send = QPushButton("Send Report")
        self._send.setDefault(True)
        self._send.setEnabled(False)          # nothing to say, nothing to send
        self._send.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self._send)
        lay.addLayout(row)

        self._text.textChanged.connect(
            lambda: self._send.setEnabled(bool(self._text.toPlainText().strip())))

    def description(self):
        return self._text.toPlainText().strip()


class ConfigDialog(QDialog):

    def __init__(self, parent, cfg, on_reset):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(True)

        # Output attributes — read by MainWindow after dialog closes
        self.chosen_theme = cfg.get("theme", "E-Ink (Default)")
        self.chosen_size = cfg.get("size", "Regular")
        self.chosen_font = cfg.get("font", "Calibri")
        self.chosen_label_align = cfg.get("label_align", "Left")
        self.chosen_client_separators = cfg.get("client_separators", True)
        self.chosen_show_group_count = cfg.get("show_group_count", True)
        self.chosen_show_group_time = cfg.get("show_group_time", True)
        self.chosen_always_on_top = cfg.get("always_on_top", True)
        self.chosen_confirm_delete = cfg.get("confirm_delete", True)
        self.chosen_confirm_reset = cfg.get("confirm_reset", True)
        self.chosen_daily_reset_enabled = cfg.get("daily_reset_enabled", True)
        self.chosen_daily_reset_time = cfg.get("daily_reset_time", "03:00")
        self.chosen_show_adjust_buttons = cfg.get("show_adjust_buttons", True)
        self.chosen_recover_running_time = cfg.get("recover_running_time", True)
        self.chosen_copy_format = cfg.get("copy_format", DEFAULT_COPY_FORMAT)
        self.restore_path = None
        self.restore_mode = "all"     # "all" | "times" | "rows"
        self.style_changed = False

        # Kept for comparison in _apply — no changes means no rebuild.
        self._initial_cfg = dict(cfg)

        # --- Layout ---
        outer = QHBoxLayout(self)

        # Left column: sidebar + pages + apply button
        left_col = QVBoxLayout()

        pages = QHBoxLayout()
        # Left sidebar
        self._tab_list = QListWidget()
        self._tab_list.setFixedWidth(140)
        self._tab_list.setFont(QFont("Calibri", 12))
        # Sidebar order and stack order are index-matched by _on_tab_changed,
        # so these two lists must stay in lockstep.
        self._tab_list.addItem("About")
        self._tab_list.addItem("General")
        self._tab_list.addItem("History")
        self._tab_list.addItem("Appearance")
        # About sits at the top but is not where you land — the dialog opens
        # on the page people actually came to change. The matching stack page
        # is selected below, once the stack exists: this runs before the
        # signal is connected (and before _stack is built), so setting the row
        # here does NOT move the stack on its own.
        self._tab_list.setCurrentRow(1)
        self._tab_list.currentRowChanged.connect(self._on_tab_changed)
        pages.addWidget(self._tab_list)

        # Right content
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_about_page())
        self._stack.addWidget(self._build_general_page(cfg, on_reset))
        self._stack.addWidget(self._build_daily_reset_page(cfg))
        self._stack.addWidget(self._build_appearance_page(cfg))
        # Sync the stack to the pre-selected row. Without this the sidebar
        # highlights General while the stack still shows page 0.
        self._stack.setCurrentIndex(self._tab_list.currentRow())
        pages.addWidget(self._stack, 1)

        left_col.addLayout(pages, 1)

        # Bottom row: a cycling tip on the left, Apply on the right.
        btn_row = QHBoxLayout()
        # Stretch factor on the label, and NO addStretch: with an Ignored
        # width policy the label demands nothing, so a stretch item beside it
        # takes the entire row and the strip renders zero pixels wide.
        btn_row.addWidget(self._build_tip_strip(), 1)
        apply_btn = QPushButton("Apply")
        apply_btn.setFont(QFont("Calibri", 12))
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)
        left_col.addLayout(btn_row)

        outer.addLayout(left_col, 1)

        # State preview panel (right side, shown when a snapshot/session is clicked)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setFixedWidth(260)
        self._preview_scroll.setVisible(False)
        self._preview_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        outer.addWidget(self._preview_scroll)

        # Track which table is active so we can deselect the other
        self._active_table = None

        # Session preview: hide timers that never ran (and groups made up
        # entirely of them) unless the user asks to see them.
        self._show_zero_times = False
        self._preview_ctx = None

        self._positioned = False

    def showEvent(self, event):
        super().showEvent(event)
        # Position beside the main window instead of covering it, so the
        # timers stay visible while tweaking settings.
        if self._positioned:
            return
        self._positioned = True
        parent = self.parentWidget()
        if parent is None:
            return
        gap    = 12
        pgeo   = parent.frameGeometry()
        screen = parent.screen().availableGeometry()
        fgeo   = self.frameGeometry()
        w, h   = fgeo.width(), fgeo.height()
        x = pgeo.right() + gap                    # prefer the right side
        if x + w > screen.right():
            x = pgeo.left() - gap - w             # fall back to the left
        if x < screen.left():                     # no room either side —
            x = max(screen.left(),                # keep it on-screen
                    min(pgeo.right() + gap, screen.right() - w))
        y = max(screen.top(), min(pgeo.top(), screen.bottom() - h))
        self.move(x, y)

    def _on_tab_changed(self, index):
        self._stack.setCurrentIndex(index)
        # Hide preview, backup browser, and clear selections when switching tabs.
        # Block table signals to prevent clearSelection from re-triggering
        # _on_table_selected and re-showing the preview.
        self._hide_preview()
        if hasattr(self, '_backup_browser'):
            self._backup_browser.setVisible(False)
        if hasattr(self, '_snap_table'):
            self._snap_table.blockSignals(True)
            self._snap_table.clearSelection()
            self._snap_table.blockSignals(False)
        if hasattr(self, '_session_table'):
            self._session_table.blockSignals(True)
            self._session_table.clearSelection()
            self._session_table.blockSignals(False)
        if hasattr(self, '_restore_btn'):
            self._restore_btn.setEnabled(False)

    def _on_daily_reset_toggle(self):
        enabled = self._daily_reset.currentText() == "On"
        # Enable/disable child controls
        for widget in self._dr_child_widgets:
            widget.setEnabled(enabled)
        # Gray out/restore child labels
        t = THEMES.get(self.chosen_theme, THEMES["E-Ink (Default)"])
        color = t["app_fg"] if enabled else t["app_fg_muted"]
        for lbl in self._dr_child_labels:
            lbl.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------ #
    #  General page                                                        #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  Tip strip                                                           #
    # ------------------------------------------------------------------ #

    def _build_tip_strip(self):
        """One quiet line, bottom-left, cycling forever while the dialog is open.

        Built once and parented below the page stack rather than added to
        each page, so a new settings page gets it for free and can never
        forget it.
        """
        self._tip_lbl = QLabel()
        self._tip_lbl.setFont(QFont("Calibri", 10))
        self._apply_tip_color()
        # Never wrap, never grow. Wrapping would change the strip's height as
        # it cycles, which would jostle the whole dialog every nine seconds;
        # Ignored width means a long line clips instead of widening the
        # dialog. Both are belt-and-braces around the length test on TIPS.
        self._tip_lbl.setWordWrap(False)
        self._tip_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._tip_lbl.setTextInteractionFlags(Qt.NoTextInteraction)

        # Start somewhere random so opening settings twice does not always
        # greet you with the same line.
        self._tip_index = random.randrange(len(TIPS)) if TIPS else 0
        self._show_tip()

        self._tip_timer = QTimer(self)
        self._tip_timer.setInterval(TIP_INTERVAL_MS)
        self._tip_timer.timeout.connect(self._next_tip)
        self._tip_timer.start()
        return self._tip_lbl

    def _apply_tip_color(self):
        """Tint the strip with the theme's muted foreground.

        app_fg_muted is the right key because this dialog's background IS
        app_bg — build_stylesheet paints QDialog with it and the sheet is
        set on the QApplication, so it cascades here. The pair is designed
        to sit together.

        The SAVED theme, deliberately, not the live combo. _apply_style runs
        only after this dialog closes, so the background behind the strip
        stays the old theme while it is open. Following the dropdown would
        put the new theme's muted color on the old theme's background —
        the one combination nobody designed.
        """
        t = THEMES.get(self.chosen_theme, THEMES["E-Ink (Default)"])
        self._tip_lbl.setStyleSheet(
            f"color: {t['app_fg_muted']}; font-style: italic;")

    def _show_tip(self):
        """Render the current tip, elided to whatever width the strip has.

        Elided rather than trusted to fit: the dialog is resizable and the
        user picks their own font elsewhere in the app, so no fixed length
        is safe. Eliding degrades to "…" instead of a line chopped
        mid-word, and the tooltip still carries the whole thing.

        The "Tip: " prefix is held OUT of the elision and re-attached after,
        so a narrow dialog eats the tip rather than the label telling you
        what it is.
        """
        if not TIPS or not hasattr(self, "_tip_lbl"):
            return
        full = TIPS[self._tip_index % len(TIPS)]
        fm = QFontMetrics(self._tip_lbl.font())
        width = self._tip_lbl.width() - fm.horizontalAdvance(TIP_PREFIX)
        text = (fm.elidedText(full, Qt.ElideRight, width)
                if width > 0 else full)
        self._tip_lbl.setText(TIP_PREFIX + text)
        self._tip_lbl.setToolTip(TIP_PREFIX + full)

    def _next_tip(self):
        self._tip_index = (self._tip_index + 1) % len(TIPS)
        self._show_tip()

    def resizeEvent(self, event):
        # Re-elide against the new width. Without this a dialog dragged
        # wider keeps showing the ellipsis it needed when it was narrow.
        super().resizeEvent(event)
        self._show_tip()

    def _build_general_page(self, cfg, on_reset):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)

        # Window Behavior
        row = QHBoxLayout()
        lbl = QLabel("Window Behavior:")
        window_behavior_tooltip = "Always On Top: Will remain as a focused window even while clicking on other windows.\n\nNormal Window: Behaves like a normal window."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(window_behavior_tooltip)
        self._always_on_top = QComboBox()
        self._always_on_top.addItems(["Always On Top", "Normal Window"])
        self._always_on_top.setCurrentText(
            "Always On Top" if cfg.get("always_on_top", True)
            else "Normal Window"
        )
        self._always_on_top.setMinimumWidth(200)
        self._always_on_top.setToolTip(window_behavior_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._always_on_top)
        lay.addLayout(row)

        # Confirm Delete
        row = QHBoxLayout()
        lbl = QLabel("Confirm Delete:")
        confirm_delete_tooltip = "Whether to prompt the user for confirmation when trying to delete a row."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(confirm_delete_tooltip)
        self._confirm_delete = QComboBox()
        self._confirm_delete.addItems(["Yes", "No"])
        self._confirm_delete.setCurrentText(
            "Yes" if cfg.get("confirm_delete", True) else "No")
        self._confirm_delete.setMinimumWidth(200)
        self._confirm_delete.setToolTip(confirm_delete_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._confirm_delete)
        lay.addLayout(row)

        # Confirm Reset
        row = QHBoxLayout()
        lbl = QLabel("Confirm Reset:")
        confirm_reset_tooltip = "Whether to prompt the user for confirmation when trying to reset a timer."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(confirm_reset_tooltip)
        self._confirm_reset = QComboBox()
        self._confirm_reset.addItems(["Yes", "No"])
        self._confirm_reset.setCurrentText(
            "Yes" if cfg.get("confirm_reset", True) else "No")
        self._confirm_reset.setMinimumWidth(200)
        self._confirm_reset.setToolTip(confirm_reset_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._confirm_reset)
        lay.addLayout(row)

        # Copy Format
        row = QHBoxLayout()
        lbl = QLabel("Copy Format:")
        # Each option shows itself worked through the same example, because
        # "Decimal" and "Raw Minutes" mean nothing until you see one.
        copy_format_tooltip = (
            "What a copied time is put on the clipboard as.\n\n"
            "For a timer reading 05:15:00:\n"
            "    HH:MM        05:15\n"
            "    HH:MM:SS     05:15:00\n"
            "    Decimal      5.25\n"
            "    Raw Minutes  315\n\n"
            "HH:MM and Raw Minutes drop any leftover seconds rather")
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(copy_format_tooltip)
        self._copy_fmt = QComboBox()
        self._copy_fmt.addItems(COPY_FORMATS)
        self._copy_fmt.setCurrentText(
            cfg.get("copy_format", DEFAULT_COPY_FORMAT))
        self._copy_fmt.setMinimumWidth(200)
        self._copy_fmt.setToolTip(copy_format_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._copy_fmt)
        lay.addLayout(row)

        # Recover Running Time
        row = QHBoxLayout()
        lbl = QLabel("Recover Running Time:")
        recover_tooltip = "If a timer was running when the app closed, add the elapsed time on next launch."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(recover_tooltip)
        self._recover_running = QComboBox()
        self._recover_running.addItems(["Yes", "No"])
        self._recover_running.setCurrentText(
            "Yes" if cfg.get("recover_running_time", True) else "No")
        self._recover_running.setMinimumWidth(200)
        self._recover_running.setToolTip(recover_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._recover_running)
        lay.addLayout(row)

        # Reset All Times — next to the confirm settings it relates to
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("Reset All Times")
        reset_btn.setFont(QFont("Calibri", 11))
        reset_btn.clicked.connect(on_reset)
        btn_row.addWidget(reset_btn)
        lay.addLayout(btn_row)

        # Separator
        sep = QFrame()
        sep.setObjectName("settingsSep")
        sep.setFixedHeight(2)
        lay.addWidget(sep)

        # Backups
        self._snap_paths = []

        # Restore from Backup — toggle button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        browse_btn = QPushButton("Restore from Backup")
        browse_btn.setFont(QFont("Calibri", 11))
        browse_btn.clicked.connect(self._toggle_backup_browser)
        btn_row.addWidget(browse_btn)
        lay.addLayout(btn_row)

        # Backup browser — hidden until toggled
        self._backup_browser = QWidget()
        backup_lay = QVBoxLayout(self._backup_browser)
        backup_lay.setContentsMargins(0, 0, 0, 0)
        backup_lay.setSpacing(6)

        self._snap_table = QTableWidget(0, 2)
        self._snap_table.setHorizontalHeaderLabels(["Backup Span", "Age"])
        self._snap_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._snap_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._snap_table.verticalHeader().setVisible(False)
        self._snap_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._snap_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._snap_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._snap_table.setMinimumHeight(160)
        self._snap_table.itemSelectionChanged.connect(
            lambda: self._on_table_selected(self._snap_table, self._snap_paths))
        self._snap_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._snap_table.customContextMenuRequested.connect(
            lambda pos: self._on_history_context_menu(
                self._snap_table, self._snap_paths, pos))
        backup_lay.addWidget(self._snap_table)

        self._restore_btn = QPushButton("Restore")
        self._restore_btn.setFont(QFont("Calibri", 11))
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_restore_clicked)
        backup_lay.addWidget(self._restore_btn)

        self._backup_browser.setVisible(False)
        lay.addWidget(self._backup_browser)

        lay.addStretch()
        return page

    # ------------------------------------------------------------------ #
    #  Backup browser                                                      #
    # ------------------------------------------------------------------ #

    def _hide_preview(self):
        """Hide the preview panel and shrink the dialog back to fit."""
        if self._preview_scroll.isVisible():
            self._preview_scroll.setVisible(False)
            self.layout().activate()
            hint = self.layout().sizeHint()
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.resize(hint)

    def _toggle_backup_browser(self):
        visible = not self._backup_browser.isVisible()
        self._backup_browser.setVisible(visible)
        if visible:
            self._load_snapshots()
        else:
            self._snap_table.clearSelection()
            self._restore_btn.setEnabled(False)
            self._hide_preview()

    def _load_snapshots(self):
        _SNAP_RE = re.compile(r"state_(\d{8}_\d{6})_\d+\.json")
        now = datetime.now()
        entries = []
        try:
            for path in PATHS.snapshots.iterdir():
                m = _SNAP_RE.match(path.name)
                if not m:
                    continue
                try:
                    dt = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                except ValueError:
                    continue
                entries.append((dt, path))
        except OSError:
            pass
        entries.sort(reverse=True)

        self._snap_table.setRowCount(0)
        self._snap_paths = []
        for dt, path in entries:
            row = self._snap_table.rowCount()
            self._snap_table.insertRow(row)
            self._snap_paths.append(path)

            # Try to read session span from the JSON
            span_str = None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                start = data.get("session", {}).get("start")
                end = data.get("meta", {}).get("saved_at")
                span_str = _format_span(start, end)
            except Exception:
                pass
            if not span_str:
                span_str = dt.strftime("%b %#d, %#I:%M %p")

            secs = int((now - dt).total_seconds())
            if secs < 60:
                age_str = f"{secs}s ago"
            elif secs < 3600:
                age_str = f"{secs // 60}m {secs % 60}s ago"
            elif secs < 86400:
                age_str = f"{secs // 3600}h {(secs % 3600) // 60}m ago"
            else:
                age_str = f"{secs // 86400}d {(secs % 86400) // 3600}h ago"

            self._snap_table.setItem(row, 0, QTableWidgetItem(span_str))
            self._snap_table.setItem(row, 1, QTableWidgetItem(age_str))

        self._restore_btn.setEnabled(False)

    def _on_restore_clicked(self):
        row = self._snap_table.currentRow()
        if row < 0 or row >= len(self._snap_paths):
            return
        path     = self._snap_paths[row]
        time_str = self._snap_table.item(row, 0).text()
        answer = QMessageBox.question(
            self,
            "Restore from Backup",
            f"Restore from backup taken at:\n{time_str}\n\nThis will overwrite the current state.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.restore_path = path
            self.restore_mode = "all"
            self.accept()

    # ------------------------------------------------------------------ #
    #  Right-click on a backup / completed session                         #
    # ------------------------------------------------------------------ #

    # (menu label, restore mode, what the confirmation should warn about)
    _RESTORE_MODES = (
        ("Restore Times and Rows", "all",
         "This replaces every row and every time with the ones from this "
         "entry."),
        ("Restore Times Only", "times",
         "Times come back for clients that still exist. Rows, groups and "
         "ordering are left alone."),
        ("Restore Rows Only", "rows",
         "Rows, groups and ordering come back. Times you have now are kept "
         "for rows that survive; rows not in this entry are removed."),
    )

    def _on_history_context_menu(self, table, paths, pos):
        row = table.rowAt(pos.y())
        if row < 0 or row >= len(paths):
            return
        table.selectRow(row)
        path  = paths[row]
        label = " ".join(table.item(row, 0).text().split())

        menu = QMenu(self)
        menu.setStyleSheet(build_menu_stylesheet(self._theme.currentText()))
        actions = {}
        # Copying is not destructive, so it gets its own section away from
        # the three that are.
        copy_action = menu.addAction("Copy All Times")
        menu.addSeparator()

        for text, mode, warning in self._RESTORE_MODES:
            actions[menu.addAction(text)] = (mode, warning)

        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is copy_action:
            self._copy_session_times(path, label)
            return

        mode, warning = actions[chosen]
        if QMessageBox.question(
                self, chosen.text(),
                f"{chosen.text()} from:\n{label}\n\n{warning}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        self.restore_path = path
        self.restore_mode = mode
        self.accept()

    def _copy_session_times(self, path, label):
        """Clipboard copy of one saved entry, matching the main window's
        'copy the whole session' format."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self._toast_main("Couldn't read that entry, nothing copied")
            return
        tracked = data.get("session", {}).get("tracked_times", {})
        fmt = self._copy_format()
        lines = []
        for row in data.get("layout", {}).get("rows", []):
            if row.get("type") != "timer":
                continue
            elapsed = int(tracked.get(str(row.get("rowid")), {})
                          .get("elapsed", 0))
            if elapsed < 1:
                continue
            lines.append(f"{row.get('name', '')}: "
                         f"{format_copy_time(elapsed, fmt)}")
        if not lines:
            self._toast_main(f"No times to copy from {label}")
            return
        QApplication.clipboard().setText("\n".join(lines))
        self._toast_main(f"{len(lines)} client time"
                         f"{'' if len(lines) == 1 else 's'} copied from {label}")

    def _toast_main(self, message):
        main = self.parentWidget()
        if main is not None and hasattr(main, "show_toast"):
            # Copy confirmations only need to be seen, not read — the
            # clipboard already has the payload.
            main.show_toast(message, 2.5)

    # ------------------------------------------------------------------ #
    #  Shared table selection / state preview                              #
    # ------------------------------------------------------------------ #

    def _on_table_selected(self, table, paths):
        """Handle row selection in either snapshot or session table."""
        # Enable restore button only for snapshot table
        if table is self._snap_table:
            self._restore_btn.setEnabled(bool(table.selectedItems()))

        # Deselect the other table so only one selection is active
        if table is self._snap_table and hasattr(self, '_session_table'):
            self._session_table.clearSelection()
        elif table is not self._snap_table and hasattr(self, '_snap_table'):
            self._snap_table.clearSelection()
            self._restore_btn.setEnabled(False)
        self._active_table = table

        row = table.currentRow()
        if row < 0 or row >= len(paths):
            self._hide_preview()
            return
        span_str = table.item(row, 0).text()
        if table is self._snap_table:
            title = f"Backup\n{span_str}"
        else:
            title = f"Completed Session\n{span_str}"
        self._show_state_preview(paths[row], title,
                                 is_session=table is not self._snap_table)

    def _show_state_preview(self, path, title="", is_session=False):
        """Load a state JSON file and display a read-only view in the preview panel."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._hide_preview()
            return

        rows = data.get("layout", {}).get("rows", [])
        tracked = data.get("session", {}).get("tracked_times", {})
        self._preview_ctx = (path, title, is_session)

        def elapsed_of(row):
            return tracked.get(str(row.get("rowid", "")), {}).get("elapsed", 0)

        # Work out what to leave out. A timer is "zero" when it would render as
        # 00:00:00; a group is dropped when every timer under it is zero (which
        # includes a group with no timers at all).
        hidden_rids = set()
        if is_session and not self._show_zero_times:
            group_children = {}
            current_sep = None
            for row in rows:
                if row.get("type") == "separator":
                    current_sep = row.get("rowid")
                    group_children[current_sep] = []
                else:
                    if int(elapsed_of(row)) <= 0:
                        hidden_rids.add(row.get("rowid"))
                    if current_sep is not None:
                        group_children[current_sep].append(row.get("rowid"))
            for sep_rid, child_rids in group_children.items():
                if all(c in hidden_rids for c in child_rids):
                    hidden_rids.add(sep_rid)

        # Resolve theme for colors
        theme_name = self.chosen_theme
        t = THEMES.get(theme_name, THEMES["E-Ink (Default)"])

        # Build the preview widget
        content = QWidget()
        content.setStyleSheet(f"background-color: {t['app_bg']};")
        lay = QVBoxLayout(content)
        lay.setSpacing(2)
        lay.setContentsMargins(6, 6, 6, 6)

        # Title
        if title:
            title_lbl = QLabel(title)
            title_lbl.setFont(QFont("Calibri", 10, QFont.Bold))
            title_lbl.setAlignment(Qt.AlignCenter)
            title_lbl.setStyleSheet(
                f"color: {t['app_fg']}; background: transparent;")
            title_lbl.setWordWrap(True)
            lay.addWidget(title_lbl)
            sep = QFrame()
            sep.setObjectName("settingsSep")
            sep.setFixedHeight(1)
            lay.addWidget(sep)

        if is_session:
            zero_cb = TickCheckBox("Show Zero Times", t["row_running_fg"])
            zero_cb.setFont(QFont("Calibri", 9))
            zero_cb.setChecked(self._show_zero_times)
            zero_cb.setStyleSheet(TickCheckBox.style_for(t))
            zero_cb.toggled.connect(self._on_show_zero_times)
            lay.addWidget(zero_cb)

        label_font = QFont("Calibri", 10)
        label_font_bold = QFont("Calibri", 10)
        label_font_bold.setBold(True)
        time_font = QFont("Calibri", 10)

        # Track group structure for collapsible separators
        self._preview_groups = {}  # sep_rowid -> (toggle_btn, [child_widgets])

        current_sep_rid = None
        for row in rows:
            rid = row.get("rowid", 0)
            rtype = row.get("type", "timer")
            name = row.get("name", "?")

            if rtype == "separator":
                current_sep_rid = rid
                if rid in hidden_rids:
                    continue
                # Gather children that follow this separator and sum their time
                children = []
                found = False
                for r2 in rows:
                    if r2 is row:
                        found = True
                        continue
                    if not found:
                        continue
                    if r2.get("type") == "separator":
                        break
                    children.append(r2)
                total = sum(
                    tracked.get(str(c.get("rowid", "")), {}).get("elapsed", 0)
                    for c in children)

                sep_w = QWidget()
                sep_w.setObjectName(f"pvSep{rid}")
                ghbg = row.get("bg") or t["group_bg"]
                sep_w.setStyleSheet(
                    f"#pvSep{rid} {{ background-color: {ghbg}; }}")
                sep_lay = QHBoxLayout(sep_w)
                sep_lay.setContentsMargins(2, 2, 2, 2)
                sep_lay.setSpacing(4)

                toggle = QPushButton("\u25BE")
                toggle.setFixedSize(18, 18)
                toggle.setStyleSheet("padding: 0; border: none; background: transparent;"
                                     f" color: {t['group_fg']};")
                sep_lay.addWidget(toggle)

                name_lbl = QLabel(name)
                name_lbl.setFont(label_font_bold)
                name_lbl.setStyleSheet(
                    f"color: {t['group_fg']};"
                    " background: transparent;")
                sep_lay.addWidget(name_lbl, 1)

                time_lbl = QLabel(format_time(int(total)))
                time_lbl.setFont(time_font)
                time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                time_lbl.setStyleSheet(
                    f"color: {t['group_fg']};"
                    " background: transparent;")
                sep_lay.addWidget(time_lbl)

                lay.addWidget(sep_w)
                self._preview_groups[rid] = (toggle, [])
                toggle.clicked.connect(
                    lambda _=False, r=rid: self._toggle_preview_group(r))

            else:
                if rid in hidden_rids:
                    continue
                elapsed = tracked.get(str(rid), {}).get("elapsed", 0)
                is_child = current_sep_rid is not None

                timer_w = PreviewRow()
                timer_w.setObjectName(f"pvTmr{rid}")
                # A plain QWidget ignores stylesheet backgrounds (and :hover)
                # unless it is told to paint them.
                timer_w.setAttribute(Qt.WA_StyledBackground, True)
                rbg = row.get("bg") or t["app_bg"]
                timer_w.setStyleSheet(
                    f"#pvTmr{rid} {{ background-color: {rbg}; }}"
                    f"#pvTmr{rid}:hover {{ background-color: {t['row_drag_bg']}; }}")
                timer_w.setCursor(Qt.PointingHandCursor)
                timer_w.setToolTip("Click to copy this time")
                timer_w.clicked.connect(
                    lambda n=name, e=elapsed: self._copy_row_time(n, e))
                tmr_lay = QHBoxLayout(timer_w)
                # Indent the CONTENT, not the widget: a stylesheet margin
                # would shift the painted background (and so the hover
                # highlight) while leaving the labels where they were.
                tmr_lay.setContentsMargins(16 if is_child else 4, 1, 4, 1)
                tmr_lay.setSpacing(4)

                name_lbl = QLabel(name)
                name_lbl.setFont(label_font)
                name_lbl.setStyleSheet(
                    f"color: {t['app_fg']}; background: transparent;")
                # Labels must not swallow the mouse, or moving across them
                # would drop the row out of :hover and eat the click.
                name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                tmr_lay.addWidget(name_lbl, 1)

                time_lbl = QLabel(format_time(int(elapsed)))
                time_lbl.setFont(time_font)
                time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                time_lbl.setStyleSheet(
                    f"color: {t['app_fg']}; background: transparent;")
                time_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                tmr_lay.addWidget(time_lbl)

                lay.addWidget(timer_w)

                if current_sep_rid is not None and current_sep_rid in self._preview_groups:
                    self._preview_groups[current_sep_rid][1].append(timer_w)

        if not [r for r in rows if r.get("rowid") not in hidden_rids]:
            empty_lbl = QLabel("Nothing was tracked in this session.")
            empty_lbl.setFont(QFont("Calibri", 9))
            empty_lbl.setAlignment(Qt.AlignCenter)
            empty_lbl.setWordWrap(True)
            empty_lbl.setStyleSheet(
                f"color: {t['app_fg_muted']}; background: transparent;")
            lay.addWidget(empty_lbl)

        lay.addStretch()
        self._preview_scroll.setWidget(content)
        self._preview_scroll.setVisible(True)

    def _copy_format(self):
        """The format to copy in, read LIVE from the dropdown.

        Not self.chosen_copy_format, which only updates on Apply. Changing
        the dropdown and immediately copying from a preview should give the
        format you just picked — that is the obvious way to check what an
        option actually produces before committing to it.
        """
        combo = getattr(self, "_copy_fmt", None)
        return combo.currentText() if combo is not None else self.chosen_copy_format

    def _copy_row_time(self, name, elapsed):
        """Copy a previewed timer's time, and say so on the main window."""
        time_str = format_copy_time(int(elapsed), self._copy_format())
        QApplication.clipboard().setText(time_str)
        main = self.parentWidget()
        if main is not None and hasattr(main, "show_toast"):
            main.show_toast(
                f"Time for {name} ({time_str}) copied to clipboard", 2.5)

    def _on_show_zero_times(self, checked):
        """Re-render the open session preview with or without zero-time rows."""
        self._show_zero_times = checked
        if self._preview_ctx is not None:
            self._show_state_preview(*self._preview_ctx)

    def _toggle_preview_group(self, sep_rid):
        """Toggle collapse/expand of a group in the state preview."""
        if sep_rid not in self._preview_groups:
            return
        toggle, children = self._preview_groups[sep_rid]
        collapsed = children and children[0].isVisible()
        for w in children:
            w.setVisible(not collapsed)
        toggle.setText("\u25B8" if collapsed else "\u25BE")

    def _load_sessions(self):
        """Load completed session files into the session table."""
        _SESSION_RE = re.compile(r"session_(\d{8}_\d{6})_\d+\.json")
        now = datetime.now()
        entries = []
        try:
            for path in PATHS.sessions.iterdir():
                m = _SESSION_RE.match(path.name)
                if not m:
                    continue
                try:
                    dt = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                except ValueError:
                    continue
                entries.append((dt, path))
        except OSError:
            pass
        entries.sort(reverse=True)

        self._session_table.setRowCount(0)
        self._session_paths = []
        for dt, path in entries:
            row = self._session_table.rowCount()
            self._session_table.insertRow(row)
            self._session_paths.append(path)

            # Try to read session span from the JSON
            span_str = None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                start = data.get("session", {}).get("start")
                end = data.get("session", {}).get("end")
                span_str = _format_span(start, end)
            except Exception:
                pass
            if not span_str:
                span_str = dt.strftime("%b %#d, %#I:%M %p")

            secs = int((now - dt).total_seconds())
            if secs < 60:
                age_str = f"{secs}s ago"
            elif secs < 3600:
                age_str = f"{secs // 60}m {secs % 60}s ago"
            elif secs < 86400:
                age_str = f"{secs // 3600}h {(secs % 3600) // 60}m ago"
            else:
                age_str = f"{secs // 86400}d {(secs % 86400) // 3600}h ago"

            self._session_table.setItem(row, 0, QTableWidgetItem(span_str))
            self._session_table.setItem(row, 1, QTableWidgetItem(age_str))

    # ------------------------------------------------------------------ #
    #  Daily Reset page                                                    #
    # ------------------------------------------------------------------ #

    def _build_daily_reset_page(self, cfg):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)

        # Collect child widgets/labels for graying out
        self._dr_child_widgets = []
        self._dr_child_labels = []

        # Daily Reset toggle (always active — this is the master switch)
        row = QHBoxLayout()
        lbl = QLabel("Daily Reset:")
        daily_reset_tooltip = "When ON, ClientTimer resets all timers to 0 at the scheduled time each day and saves the completed session to the sessions folder. If the app is closed, the reset happens on next launch if the time has already passed."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(daily_reset_tooltip)
        self._daily_reset = QComboBox()
        self._daily_reset.addItems(["Off", "On"])
        self._daily_reset.setCurrentText(
            "On" if cfg.get("daily_reset_enabled", True) else "Off")
        self._daily_reset.setMinimumWidth(200)
        self._daily_reset.setToolTip(daily_reset_tooltip)
        self._daily_reset.currentTextChanged.connect(
            self._on_daily_reset_toggle)
        row.addWidget(lbl)
        row.addWidget(self._daily_reset)
        lay.addLayout(row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep)

        # Reset Time (child — grayed when off)
        row = QHBoxLayout()
        lbl_time = QLabel("Reset Time:")
        reset_time_tooltip = "The time of day each that ClientTimer will reset all times and store the previous session as completed."
        lbl_time.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl_time.setToolTip(reset_time_tooltip)
        self._daily_reset_time = QTimeEdit()
        self._daily_reset_time.setButtonSymbols(QAbstractSpinBox.NoButtons)
        try:
            h, m = map(int, cfg.get("daily_reset_time", "03:00").split(":"))
        except ValueError:
            h, m = 0, 0
        self._daily_reset_time.setTime(QTime(h, m))
        self._daily_reset_time.setDisplayFormat("hh:mm AP")
        self._daily_reset_time.setToolTip(reset_time_tooltip)
        row.addWidget(lbl_time)
        row.addWidget(self._daily_reset_time)
        lay.addLayout(row)

        self._dr_child_widgets.append(self._daily_reset_time)
        self._dr_child_labels.append(lbl_time)

        # Separator
        sep2 = QFrame()
        sep2.setObjectName("settingsSep")
        sep2.setFixedHeight(2)
        lay.addWidget(sep2)

        # Past Sessions
        self._session_paths = []

        session_lbl = QLabel("Past Sessions")
        session_lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lay.addWidget(session_lbl)

        self._session_table = QTableWidget(0, 2)
        self._session_table.setHorizontalHeaderLabels(["Session Span", "Age"])
        self._session_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._session_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._session_table.verticalHeader().setVisible(False)
        self._session_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._session_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._session_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._session_table.setMinimumHeight(160)
        self._session_table.itemSelectionChanged.connect(
            lambda: self._on_table_selected(self._session_table, self._session_paths))
        self._session_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._session_table.customContextMenuRequested.connect(
            lambda pos: self._on_history_context_menu(
                self._session_table, self._session_paths, pos))
        lay.addWidget(self._session_table)

        # Open Sessions Folder button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._sessions_folder_btn = QPushButton("Open Sessions Folder")
        self._sessions_folder_btn.setFont(QFont("Calibri", 11))
        self._sessions_folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(PATHS.sessions)))
        )
        btn_row.addWidget(self._sessions_folder_btn)
        lay.addLayout(btn_row)

        self._load_sessions()

        lay.addStretch()

        # Apply initial enabled/grayed state
        self._on_daily_reset_toggle()

        return page

    # ------------------------------------------------------------------ #
    #  Appearance page                                                     #
    # ------------------------------------------------------------------ #

    def _build_about_page(self):
        """Which build am I running?

        Reads ct.common.version, NOT latest.json — this states what is
        INSTALLED. The manifest states what EXISTS on the server, and an
        update check is the comparison of the two. Sourcing this from the
        manifest would make the page confidently report a version the user
        does not have.

        Deliberately sparse: Check For Updates and Report A Problem land here
        later, and a page that starts crowded has nowhere to put them.
        """
        from ct.common.version import __version__, RELEASE_DATE

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(12)

        title = QLabel("Client Timer 2")
        title.setFont(QFont("Calibri", 18, QFont.Bold))
        lay.addWidget(title)

        for caption, value in (("Version:", __version__),
                               ("Released:", _pretty_date(RELEASE_DATE))):
            row = QHBoxLayout()
            lbl = QLabel(caption)
            lbl.setFont(QFont("Calibri", 12, QFont.Bold))
            lbl.setFixedWidth(90)
            val = QLabel(value)
            val.setFont(QFont("Calibri", 12))
            # Selectable so a bug report can carry the exact build without
            # the user having to transcribe it.
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val)
            row.addStretch()
            lay.addLayout(row)

        row = QHBoxLayout()
        check_btn = QPushButton("Check for Updates")
        check_btn.clicked.connect(self._on_check_updates)
        row.addWidget(check_btn)
        notes_btn = QPushButton("Release Notes")
        notes_btn.setToolTip("Opens the releases page in your browser")
        notes_btn.clicked.connect(self._on_release_notes)
        row.addWidget(notes_btn)
        row.addStretch()
        lay.addLayout(row)

        row2 = QHBoxLayout()
        report_btn = QPushButton("Report a Problem")
        report_btn.clicked.connect(self._on_report_problem)
        row2.addWidget(report_btn)
        row2.addStretch()
        lay.addLayout(row2)

        lay.addStretch()
        return page

    def _on_check_updates(self):
        """Run a check the user explicitly asked for.

        Closes the dialog first: the answer arrives as a toast on the main
        window, which would otherwise appear behind this modal and look like
        nothing happened.
        """
        main = self.parentWidget()
        if main is not None and hasattr(main, "_start_update_check"):
            self.reject()
            main._start_update_check(forced=True)

    def _on_release_notes(self):
        """Open the GitHub releases page — that is where the changelog is.

        The manifest's `notes` is one line sized for a toast. The release
        body is the real thing, with markdown and every past version, and a
        browser renders it better than any panel here would.
        """
        from ct.core.update import release_page_url
        QDesktopServices.openUrl(QUrl(release_page_url()))

    def _on_report_problem(self):
        from ct.common import crash
        from ct.common.version import __version__
        dlg = ReportProblemDialog(self, __version__, self.chosen_theme)
        if dlg.exec() != QDialog.Accepted:
            return
        import platform
        ok = crash.report(
            dlg.description(),
            attachments=[("log_tail.txt", crash.log_tail()),
                         ("state.json", crash.state_snapshot())],
            context={"version": __version__,
                     "os": platform.platform(terse=True)},
        )
        self._toast_main("Report sent, thank you!" if ok
                         else "Could not send the report right now")

    def _build_appearance_page(self, cfg):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setSpacing(8)

        # -- Size --
        row = QHBoxLayout()
        lbl = QLabel("Program Size:")
        appearance_size_tooltip = "Size of the program."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_size_tooltip)
        self._size = QComboBox()
        self._size.addItems(SIZES)
        self._size.setCurrentText(cfg.get("size", "Regular"))
        self._size.setMinimumWidth(230)
        self._size.setToolTip(appearance_size_tooltip)
        self._size.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._size)
        lay.addLayout(row)

        # -- Theme --
        row = QHBoxLayout()
        lbl = QLabel("Program Theme:")
        appearance_theme_tooltip = "Color scheme of the program."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_theme_tooltip)
        self._theme = QComboBox()
        self._theme.addItems(THEMES)
        self._theme.setCurrentText(cfg.get("theme", "E-Ink (Default)"))
        self._theme.setMinimumWidth(230)
        self._theme.setToolTip(appearance_theme_tooltip)
        self._theme.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._theme)
        lay.addLayout(row)

        # -- Font --
        row = QHBoxLayout()
        lbl = QLabel("Program Font:")
        appearance_font_tooltip = "Font used by all text in the program."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_font_tooltip)
        self._font = QComboBox()
        for fn in FONTS:
            display = f"{fn} (Default)" if fn == "Calibri" else fn
            self._font.addItem(display, fn)
        idx = self._font.findData(cfg.get("font", "Calibri"))
        if idx >= 0:
            self._font.setCurrentIndex(idx)
        self._font.setMinimumWidth(230)
        self._font.setToolTip(appearance_font_tooltip)
        self._font.currentIndexChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._font)
        lay.addLayout(row)

        # -- Label Alignment --
        row = QHBoxLayout()
        lbl = QLabel("Label Alignment:")
        appearance_label_alignment_tooltip = "Which direction to align timer/separator labels to."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_label_alignment_tooltip)
        self._align = QComboBox()
        self._align.addItems(["Left", "Center", "Right"])
        self._align.setCurrentText(cfg.get("label_align", "Left"))
        self._align.setMinimumWidth(230)
        self._align.setToolTip(appearance_label_alignment_tooltip)
        self._align.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._align)
        lay.addLayout(row)

        # -- Client Row Separators --
        row = QHBoxLayout()
        lbl = QLabel("Client Separators:")
        appearance_client_separators_tooltip = "Whether to draw a line between clients in the UI."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_client_separators_tooltip)
        self._sep = QComboBox()
        self._sep.addItems(["No", "Yes"])
        self._sep.setCurrentText(
            "Yes" if cfg.get("client_separators", True) else "No")
        self._sep.setMinimumWidth(230)
        self._sep.setToolTip(appearance_client_separators_tooltip)
        self._sep.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._sep)
        lay.addLayout(row)

        # -- Show Group Count --
        row = QHBoxLayout()
        lbl = QLabel("Show Group Count:")
        appearance_group_count_tooltip = "Whether to show a count of how many timers are nested under a separator."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_group_count_tooltip)
        self._grp_count = QComboBox()
        self._grp_count.addItems(["No", "Yes"])
        self._grp_count.setCurrentText(
            "Yes" if cfg.get("show_group_count", True) else "No")
        self._grp_count.setMinimumWidth(230)
        self._grp_count.setToolTip(appearance_group_count_tooltip)
        self._grp_count.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._grp_count)
        lay.addLayout(row)

        # -- Show Group Time --
        row = QHBoxLayout()
        lbl = QLabel("Show Group Time:")
        appearance_group_time_tooltip = "Whether to show a live sum of all timers nested under a separator."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(appearance_group_time_tooltip)
        self._grp_time = QComboBox()
        self._grp_time.addItems(["No", "Yes"])
        self._grp_time.setCurrentText(
            "Yes" if cfg.get("show_group_time", True) else "No")
        self._grp_time.setMinimumWidth(230)
        self._grp_time.setToolTip(appearance_group_time_tooltip)
        self._grp_time.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._grp_time)
        lay.addLayout(row)

        # -- Adjust Buttons --
        row = QHBoxLayout()
        lbl = QLabel("Adjust Buttons:")
        adj_tooltip = ("Show the +5/-5 buttons on each timer row. The X button "
                       "is not configurable — it appears only while the layout "
                       "is unlocked.")
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(adj_tooltip)
        self._adj_btns = QComboBox()
        self._adj_btns.addItems(["Yes", "No"])
        self._adj_btns.setCurrentText(
            "Yes" if cfg.get("show_adjust_buttons", True) else "No")
        self._adj_btns.setMinimumWidth(230)
        self._adj_btns.setToolTip(adj_tooltip)
        self._adj_btns.currentTextChanged.connect(self._refresh_preview)
        row.addWidget(lbl)
        row.addWidget(self._adj_btns)
        lay.addLayout(row)

        # -- Live preview (group + 2 timers) --
        self._preview = QFrame()
        self._preview.setObjectName("preview")
        self._preview.setFrameStyle(QFrame.Box | QFrame.Plain)
        self._preview.setLineWidth(2)
        pv_lay = QVBoxLayout(self._preview)
        pv_lay.setSpacing(2)
        pv_lay.setContentsMargins(4, 4, 4, 4)

        # Row 1: Group header
        self._p_grp_row = QWidget()
        self._p_grp_row.setObjectName("pGrpRow")
        grp_lay = QHBoxLayout(self._p_grp_row)
        grp_lay.setContentsMargins(3, 3, 3, 3)
        grp_lay.setSpacing(6)
        self._p_toggle = QPushButton("\u25BE")
        self._p_gname = QLabel("Acme Corp")
        self._p_gname.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._p_gcount = QLabel("(2)")
        self._p_gcount.setAlignment(Qt.AlignCenter)
        self._p_gtime = QLabel("00:12:34")
        self._p_gtime.setAlignment(Qt.AlignCenter)
        self._p_gspacer = QLabel("")
        self._p_gx = QPushButton("X")
        for w in (self._p_toggle, self._p_gname, self._p_gcount,
                  self._p_gtime, self._p_gspacer, self._p_gx):
            grp_lay.addWidget(w)
        pv_lay.addWidget(self._p_grp_row)

        # Row 2: Timer 1 (shown as "running")
        self._p_t1_row = QWidget()
        self._p_t1_row.setObjectName("pT1Row")
        t1_lay = QHBoxLayout(self._p_t1_row)
        t1_lay.setContentsMargins(0, 0, 0, 0)  # bottom set in _refresh_preview
        t1_lay.setSpacing(6)
        self._p1_bullet = QLabel("\u2022")
        self._p1_bullet.setAlignment(Qt.AlignCenter)
        self._p1_name = QLabel("Acme Calls")
        self._p1_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # One button, not two: it reads Stop while running and Start while
        # stopped, exactly like the real row. Row 1 is the running sample.
        self._p1_start = QPushButton("Stop")
        self._p1_time = QLabel("00:05:21")
        self._p1_time.setAlignment(Qt.AlignCenter)
        self._p1_minus = QPushButton("-5")
        self._p1_plus = QPushButton("+5")
        self._p1_x = QPushButton("X")
        for w in (self._p1_bullet, self._p1_name, self._p1_start,
                  self._p1_time, self._p1_minus,
                  self._p1_plus, self._p1_x):
            t1_lay.addWidget(w)
        pv_lay.addWidget(self._p_t1_row)

        # Row 3: Timer 2 (shown as "stopped")
        self._p_t2_row = QWidget()
        self._p_t2_row.setObjectName("pT2Row")
        t2_lay = QHBoxLayout(self._p_t2_row)
        t2_lay.setContentsMargins(0, 0, 0, 0)
        t2_lay.setSpacing(6)
        self._p2_bullet = QLabel("")
        self._p2_bullet.setAlignment(Qt.AlignCenter)
        self._p2_name = QLabel("Acme Tickets")
        self._p2_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._p2_start = QPushButton("Start")
        self._p2_time = QLabel("00:07:13")
        self._p2_time.setAlignment(Qt.AlignCenter)
        self._p2_minus = QPushButton("-5")
        self._p2_plus = QPushButton("+5")
        self._p2_x = QPushButton("X")
        for w in (self._p2_bullet, self._p2_name, self._p2_start,
                  self._p2_time, self._p2_minus,
                  self._p2_plus, self._p2_x):
            t2_lay.addWidget(w)
        pv_lay.addWidget(self._p_t2_row)

        lay.addWidget(self._preview)

        self._refresh_preview()
        return page

    # ------------------------------------------------------------------ #
    #  Preview refresh                                                     #
    # ------------------------------------------------------------------ #

    def _refresh_preview(self):
        theme_name = self._theme.currentText()
        if theme_name not in THEMES:
            return
        t = THEMES[theme_name]
        s = SIZES[self._size.currentText()]
        font_family = self._font.currentData()
        ghbg = t["group_bg"]

        normal_fg = t["app_fg"]
        running_fg = t["row_running_fg"]
        ghfg = t["group_fg"]
        ghfg_running = t["group_running_fg"]

        # Outer preview frame
        self._preview.setStyleSheet(
            f"#preview {{ background-color: {t['app_bg']};"
            f"  border: 2px solid gray; }}"
        )

        # Row backgrounds (with optional client separator line)
        sep_on = self._sep.currentText() == "Yes"
        sep_css = (f"border-bottom: 1px solid {t['row_line']};"
                   if sep_on else "")
        self._p_t1_row.layout().setContentsMargins(
            0, 0, 0, s.get("line_gap", 0) if sep_on else 0)
        group_line = t["group_line"]
        self._p_grp_row.setStyleSheet(
            f"#pGrpRow {{ background-color: {ghbg};"
            f"  border: 2px solid {group_line}; }}")
        self._p_t1_row.setStyleSheet(
            f"#pT1Row {{ background-color: {t['app_bg']};"
            f"  margin-left: 12px; {sep_css} }}")
        self._p_t2_row.setStyleSheet(
            f"#pT2Row {{ background-color: {t['app_bg']};"
            f"  margin-left: 12px; }}")

        # Group header label colors (preview shows running children)
        grp_lbl_running = (
            f"color: {ghfg_running}; background: transparent;")
        for lbl in (self._p_gname, self._p_gcount, self._p_gtime):
            lbl.setStyleSheet(grp_lbl_running)

        # Timer label colors
        tmr_lbl = f"color: {t['app_fg']}; background: transparent;"
        for lbl in (self._p1_bullet, self._p1_name, self._p1_time,
                    self._p2_bullet, self._p2_name, self._p2_time):
            lbl.setStyleSheet(tmr_lbl)

        # Button styling
        act_fg = t["control_hover_fg"]
        line_c = t["control_line"]
        btn_style = (
            f"QPushButton {{ color: {t['control_fg']};"
            f"  background-color: {t['control_bg']};"
            f"  border: {t['control_border_px']}px solid {line_c};"
            f"  padding: 4px 8px; }}"
            f"QPushButton:hover, QPushButton:pressed {{"
            f"  color: {act_fg};"
            f"  background-color: {t['control_hover_bg']}; }}"
        )
        btn_sq = (
            f"QPushButton {{ color: {t['control_fg']};"
            f"  background-color: {t['control_bg']};"
            f"  border: {t['control_border_px']}px solid {line_c};"
            f"  padding: 0px; }}"
            f"QPushButton:hover, QPushButton:pressed {{"
            f"  color: {act_fg};"
            f"  background-color: {t['control_hover_bg']}; }}"
        )
        for btn in (self._p1_start, self._p1_minus,
                    self._p1_plus, self._p2_start,
                    self._p2_minus, self._p2_plus):
            btn.setStyleSheet(btn_style)
        for btn in (self._p_toggle, self._p_gx, self._p1_x, self._p2_x):
            btn.setStyleSheet(btn_sq)

        # Compute fixed widths for alignment across rows
        bold_label_font = QFont(font_family, s["label"])
        bold_label_font.setBold(True)
        bfm = QFontMetrics(bold_label_font)
        name_w = max(bfm.horizontalAdvance("Acme Tickets"),
                     bfm.horizontalAdvance("Acme Calls"),
                     bfm.horizontalAdvance("Acme Corp")) + 8
        bold_time_font = QFont(font_family, s["time"])
        bold_time_font.setBold(True)
        time_w = QFontMetrics(bold_time_font).horizontalAdvance("00:00:00 ")

        # Square size for toggle/bullet/X
        fm_action = QFontMetrics(QFont(font_family, s["action"]))
        sq = fm_action.height() + 10

        # Group header fonts (bold — child is "running")
        self._p_gname.setFont(bold_label_font)
        self._p_gname.setFixedWidth(name_w)
        self._p_gcount.setFont(QFont(font_family, s["action"]))
        self._p_gtime.setFont(bold_time_font)
        self._p_gtime.setFixedWidth(time_w)
        self._p_toggle.setFont(QFont(font_family, s["action"]))
        self._p_toggle.setFixedSize(sq, sq)
        self._p_gx.setFont(QFont(font_family, s["action"]))
        self._p_gx.setFixedSize(sq, sq)

        # Label alignment (group name always left, timers follow setting)
        _ALIGN = {"Left": Qt.AlignLeft | Qt.AlignVCenter,
                  "Center": Qt.AlignCenter,
                  "Right": Qt.AlignRight | Qt.AlignVCenter}
        tmr_align = _ALIGN.get(
            self._align.currentText(), Qt.AlignLeft | Qt.AlignVCenter)

        # Timer row fonts
        for (bullet, name, start, time_l, minus, plus, x,
             is_running) in (
                (self._p1_bullet, self._p1_name, self._p1_start,
                 self._p1_time, self._p1_minus,
                 self._p1_plus, self._p1_x, True),
                (self._p2_bullet, self._p2_name, self._p2_start,
                 self._p2_time, self._p2_minus,
                 self._p2_plus, self._p2_x, False),
        ):
            bullet.setFont(QFont(font_family, s["action"]))
            bullet.setFixedSize(sq, sq)

            if is_running:
                name.setFont(bold_label_font)
                time_l.setFont(bold_time_font)
                color = running_fg
            else:
                name.setFont(QFont(font_family, s["label"]))
                time_l.setFont(QFont(font_family, s["time"]))
                color = normal_fg

            name.setFixedWidth(name_w)
            name.setAlignment(tmr_align)
            start.setFont(QFont(font_family, s["time"]))
            time_l.setFixedWidth(time_w)
            minus.setFont(QFont(font_family, s["action"]))
            plus.setFont(QFont(font_family, s["action"]))
            x.setFont(QFont(font_family, s["action"]))
            x.setFixedSize(sq, sq)

            for lbl in (bullet, name, time_l):
                lbl.setStyleSheet(
                    f"color: {color}; background: transparent;"
                )

        # Preview count/time visibility
        self._p_gcount.setVisible(
            self._grp_count.currentText() == "Yes")
        self._p_gtime.setVisible(
            self._grp_time.currentText() == "Yes")

        # Adjust buttons
        show_adjust = self._adj_btns.currentText() == "Yes"
        for w in (self._p1_minus, self._p1_plus, self._p2_minus, self._p2_plus):
            w.setVisible(show_adjust)
        # The preview shows the app as it looks while locked, which is how it
        # looks nearly all the time — so no X buttons. They're still built
        # above because the row sizing maths uses their column width.
        for w in (self._p1_x, self._p2_x, self._p_gx):
            w.setVisible(False)

    # ------------------------------------------------------------------ #
    #  Apply                                                               #
    # ------------------------------------------------------------------ #

    def _apply(self):
        # General
        self.chosen_always_on_top = (
            self._always_on_top.currentText() == "Always On Top")
        self.chosen_confirm_delete = (
            self._confirm_delete.currentText() == "Yes")
        self.chosen_confirm_reset = (
            self._confirm_reset.currentText() == "Yes")
        self.chosen_recover_running_time = (
            self._recover_running.currentText() == "Yes")
        self.chosen_copy_format = self._copy_fmt.currentText()
        # Daily Reset
        self.chosen_daily_reset_enabled = (
            self._daily_reset.currentText() == "On")
        t = self._daily_reset_time.time()
        self.chosen_daily_reset_time = f"{t.hour():02d}:{t.minute():02d}"
        # Appearance
        self.chosen_theme = self._theme.currentText()
        self.chosen_size = self._size.currentText()
        self.chosen_font = self._font.currentData()
        self.chosen_label_align = self._align.currentText()
        self.chosen_client_separators = self._sep.currentText() == "Yes"
        self.chosen_show_group_count = (
            self._grp_count.currentText() == "Yes")
        self.chosen_show_group_time = (
            self._grp_time.currentText() == "Yes")
        self.chosen_show_adjust_buttons = self._adj_btns.currentText() == "Yes"
        # Only flag a change if something actually differs from the values
        # the dialog opened with — otherwise Apply is a no-op for the caller.
        chosen = {
            "theme":                self.chosen_theme,
            "size":                 self.chosen_size,
            "font":                 self.chosen_font,
            "label_align":          self.chosen_label_align,
            "client_separators":    self.chosen_client_separators,
            "show_group_count":     self.chosen_show_group_count,
            "show_group_time":      self.chosen_show_group_time,
            "always_on_top":        self.chosen_always_on_top,
            "confirm_delete":       self.chosen_confirm_delete,
            "confirm_reset":        self.chosen_confirm_reset,
            "daily_reset_enabled":  self.chosen_daily_reset_enabled,
            "daily_reset_time":     self.chosen_daily_reset_time,
            "show_adjust_buttons":  self.chosen_show_adjust_buttons,
            "recover_running_time": self.chosen_recover_running_time,
            "copy_format":          self.chosen_copy_format,
        }
        self.style_changed = any(
            self._initial_cfg.get(k) != v for k, v in chosen.items())
        self.accept()
