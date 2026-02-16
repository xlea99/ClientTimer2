"""Configuration dialog for Client Timer — tabbed sidebar layout."""

from PySide6.QtCore import Qt, QTime, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from ct.common.setup import PATHS
from ct.ui.theme import THEMES, SIZES, FONTS

# Simple tabbed settings dialog with a left sidebar for different categories. Opens when the user clicks the little
# gear icon in main app
class ConfigDialog(QDialog):

    def __init__(self, parent, cfg, on_reset):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setModal(True)

        # Output attributes — read by MainWindow after dialog closes
        self.chosen_theme = cfg.get("theme", "Cupertino Light")
        self.chosen_size = cfg.get("size", "Regular")
        self.chosen_font = cfg.get("font", "Calibri")
        self.chosen_label_align = cfg.get("label_align", "Left")
        self.chosen_client_separators = cfg.get("client_separators", False)
        self.chosen_show_group_count = cfg.get("show_group_count", True)
        self.chosen_show_group_time = cfg.get("show_group_time", True)
        self.chosen_always_on_top = cfg.get("always_on_top", True)
        self.chosen_snapshot_min_minutes = cfg.get("snapshot_min_minutes", 5)
        self.chosen_confirm_delete = cfg.get("confirm_delete", True)
        self.chosen_confirm_reset = cfg.get("confirm_reset", True)
        self.chosen_daily_reset_enabled = cfg.get("daily_reset_enabled", False)
        self.chosen_daily_reset_time = cfg.get("daily_reset_time", "00:00")
        self.style_changed = False

        # --- Layout ---
        outer = QVBoxLayout(self)

        body = QHBoxLayout()

        # Left sidebar
        self._tab_list = QListWidget()
        self._tab_list.setFixedWidth(140)
        self._tab_list.setFont(QFont("Calibri", 12))
        self._tab_list.addItem("General")
        self._tab_list.addItem("Daily Reset")
        self._tab_list.addItem("Appearance")
        self._tab_list.setCurrentRow(0)
        self._tab_list.currentRowChanged.connect(self._on_tab_changed)
        body.addWidget(self._tab_list)

        # Right content
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_general_page(cfg, on_reset))
        self._stack.addWidget(self._build_daily_reset_page(cfg))
        self._stack.addWidget(self._build_appearance_page(cfg))
        body.addWidget(self._stack, 1)

        outer.addLayout(body, 1)

        # Bottom row: restart indicator + Apply
        btn_row = QHBoxLayout()
        self._restart_lbl = QLabel("* Restart required")
        self._restart_lbl.setFont(QFont("Calibri", 10))
        self._restart_lbl.setStyleSheet("color: #888888;")
        self._restart_lbl.setVisible(False)
        btn_row.addWidget(self._restart_lbl)
        btn_row.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.setFont(QFont("Calibri", 12))
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)
        outer.addLayout(btn_row)

        # Track initial values for restart detection
        self._initial_always_on_top = cfg.get("always_on_top", True)

    def _on_tab_changed(self, index):
        self._stack.setCurrentIndex(index)

    def _check_restart_needed(self):
        current_aot = self._always_on_top.currentText() == "Always On Top"
        self._restart_lbl.setVisible(
            current_aot != self._initial_always_on_top)

    def _on_daily_reset_toggle(self):
        enabled = self._daily_reset.currentText() == "On"
        # Enable/disable child controls
        for widget in self._dr_child_widgets:
            widget.setEnabled(enabled)
        # Gray out/restore child labels
        t = THEMES.get(self.chosen_theme, THEMES["Cupertino Light"])
        color = t["text"] if enabled else t.get("text_grayed_out", "#888888")
        for lbl in self._dr_child_labels:
            lbl.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------ #
    #  General page                                                        #
    # ------------------------------------------------------------------ #

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
        self._always_on_top.currentTextChanged.connect(
            self._check_restart_needed)
        row.addWidget(lbl)
        row.addWidget(self._always_on_top)
        lay.addLayout(row)

        # Snapshot Interval
        row = QHBoxLayout()
        lbl = QLabel("Snapshot Interval:")
        snapshot_interval_tooltip = "Will try to keep a fresh snapshot/backup of the current user configuration every N minutes."
        lbl.setFont(QFont("Calibri", 12, QFont.Bold))
        lbl.setToolTip(snapshot_interval_tooltip)
        self._snapshot_interval = QSpinBox()
        self._snapshot_interval.setRange(1, 60)
        self._snapshot_interval.setValue(cfg.get("snapshot_min_minutes", 5))
        self._snapshot_interval.setSuffix(" min")
        self._snapshot_interval.setMinimumWidth(200)
        self._snapshot_interval.setToolTip(snapshot_interval_tooltip)
        row.addWidget(lbl)
        row.addWidget(self._snapshot_interval)
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

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep)

        # Action buttons
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset All Times")
        reset_btn.setFont(QFont("Calibri", 11))
        reset_btn.clicked.connect(on_reset)
        folder_btn = QPushButton("Open Snapshot Folder")
        folder_btn.setFont(QFont("Calibri", 11))
        folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(PATHS.snapshots)))
        )
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(folder_btn)
        lay.addLayout(btn_row)

        lay.addStretch()
        return page

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
            "On" if cfg.get("daily_reset_enabled", False) else "Off")
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
            h, m = map(int, cfg.get("daily_reset_time", "00:00").split(":"))
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
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep2)

        # Open Sessions Folder button (child — grayed when off)
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

        self._dr_child_widgets.append(self._sessions_folder_btn)

        lay.addStretch()

        # Apply initial enabled/grayed state
        self._on_daily_reset_toggle()

        return page

    # ------------------------------------------------------------------ #
    #  Appearance page                                                     #
    # ------------------------------------------------------------------ #

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
        _base = ["Cupertino Light", "Galaxy Dark"]
        _extra = [t for t in THEMES if t not in _base]
        self._theme.addItems(_base)
        self._theme.addItem("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        self._theme_sep_idx = len(_base)
        model = self._theme.model()
        item = model.item(self._theme_sep_idx)
        item.setEnabled(False)
        self._theme.addItems(_extra)
        self._theme.setCurrentText(cfg.get("theme", "Cupertino Light"))
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
            "Yes" if cfg.get("client_separators", False) else "No")
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
        grp_lay.setContentsMargins(0, 0, 0, 0)
        grp_lay.setSpacing(6)
        self._p_toggle = QPushButton("\u25BE")
        self._p_gname = QLabel("Sysco")
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
        t1_lay.setContentsMargins(0, 0, 0, 0)
        t1_lay.setSpacing(6)
        self._p1_bullet = QLabel("\u2022")
        self._p1_bullet.setAlignment(Qt.AlignCenter)
        self._p1_name = QLabel("Sysco Calls")
        self._p1_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._p1_start = QPushButton("Start")
        self._p1_stop = QPushButton("Stop")
        self._p1_time = QLabel("00:05:21")
        self._p1_time.setAlignment(Qt.AlignCenter)
        self._p1_minus = QPushButton("-5")
        self._p1_plus = QPushButton("+5")
        self._p1_x = QPushButton("X")
        for w in (self._p1_bullet, self._p1_name, self._p1_start,
                  self._p1_stop, self._p1_time, self._p1_minus,
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
        self._p2_name = QLabel("Sysco Tickets")
        self._p2_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._p2_start = QPushButton("Start")
        self._p2_stop = QPushButton("Stop")
        self._p2_time = QLabel("00:07:13")
        self._p2_time.setAlignment(Qt.AlignCenter)
        self._p2_minus = QPushButton("-5")
        self._p2_plus = QPushButton("+5")
        self._p2_x = QPushButton("X")
        for w in (self._p2_bullet, self._p2_name, self._p2_start,
                  self._p2_stop, self._p2_time, self._p2_minus,
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
        ghbg = t.get("group_header_bg", t["bg"])

        normal_fg = t["text"]
        running_fg = t.get("running_text", normal_fg)
        ghfg = t.get("group_header_text", normal_fg)
        ghfg_running = t.get("group_running_text", ghfg)

        # Style the theme dropdown separator item
        model = self._theme.model()
        sep_item = model.item(self._theme_sep_idx)
        sep_item.setForeground(QColor(t.get("separator", "#888888")))

        # Outer preview frame
        self._preview.setStyleSheet(
            f"#preview {{ background-color: {t['bg']};"
            f"  border: 2px solid gray; }}"
        )

        # Row backgrounds (with optional client separator line)
        sep_on = self._sep.currentText() == "Yes"
        sep_css = (f"border-bottom: 1px solid {t['row_separator']};"
                   if sep_on else "")
        self._p_grp_row.setStyleSheet(
            f"#pGrpRow {{ background-color: {ghbg}; }}")
        self._p_t1_row.setStyleSheet(
            f"#pT1Row {{ background-color: {t['bg']};"
            f"  margin-left: 12px; {sep_css} }}")
        self._p_t2_row.setStyleSheet(
            f"#pT2Row {{ background-color: {t['bg']};"
            f"  margin-left: 12px; }}")

        # Group header label colours (preview shows running children)
        grp_lbl_running = (
            f"color: {ghfg_running}; background: transparent;")
        for lbl in (self._p_gname, self._p_gcount, self._p_gtime):
            lbl.setStyleSheet(grp_lbl_running)

        # Timer label colours
        tmr_lbl = f"color: {t['text']}; background: transparent;"
        for lbl in (self._p1_bullet, self._p1_name, self._p1_time,
                    self._p2_bullet, self._p2_name, self._p2_time):
            lbl.setStyleSheet(tmr_lbl)

        # Button styling
        btn_style = (
            f"QPushButton {{ color: {t['button_text']};"
            f"  background-color: {t['button_bg']};"
            f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
            f"  padding: 4px 8px; }}"
            f"QPushButton:hover, QPushButton:pressed {{"
            f"  background-color: {t['button_active']}; }}"
        )
        btn_sq = (
            f"QPushButton {{ color: {t['button_text']};"
            f"  background-color: {t['button_bg']};"
            f"  border: {t['border']}px solid rgba(128,128,128,0.4);"
            f"  padding: 0px; }}"
            f"QPushButton:hover, QPushButton:pressed {{"
            f"  background-color: {t['button_active']}; }}"
        )
        for btn in (self._p1_start, self._p1_stop, self._p1_minus,
                    self._p1_plus, self._p2_start, self._p2_stop,
                    self._p2_minus, self._p2_plus):
            btn.setStyleSheet(btn_style)
        for btn in (self._p_toggle, self._p_gx, self._p1_x, self._p2_x):
            btn.setStyleSheet(btn_sq)

        # Compute fixed widths for alignment across rows
        bold_label_font = QFont(font_family, s["label"])
        bold_label_font.setBold(True)
        bfm = QFontMetrics(bold_label_font)
        name_w = max(bfm.horizontalAdvance("Sysco Tickets"),
                     bfm.horizontalAdvance("Sysco Calls"),
                     bfm.horizontalAdvance("Sysco")) + 8
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
        for (bullet, name, start, stop, time_l, minus, plus, x,
             is_running) in (
                (self._p1_bullet, self._p1_name, self._p1_start,
                 self._p1_stop, self._p1_time, self._p1_minus,
                 self._p1_plus, self._p1_x, True),
                (self._p2_bullet, self._p2_name, self._p2_start,
                 self._p2_stop, self._p2_time, self._p2_minus,
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
            stop.setFont(QFont(font_family, s["time"]))
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

    # ------------------------------------------------------------------ #
    #  Apply                                                               #
    # ------------------------------------------------------------------ #

    def _apply(self):
        # General
        self.chosen_always_on_top = (
            self._always_on_top.currentText() == "Always On Top")
        self.chosen_snapshot_min_minutes = self._snapshot_interval.value()
        self.chosen_confirm_delete = (
            self._confirm_delete.currentText() == "Yes")
        self.chosen_confirm_reset = (
            self._confirm_reset.currentText() == "Yes")
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
        self.style_changed = True
        self.accept()
