"""Row widget builders — separator rows, timer rows, and footer.

Each builder returns a (container, widget_dict) tuple.  The container is
a QWidget with objectName "rowBg" that can be inserted into the grid.
The widget_dict maps logical names to sub-widgets for later updates.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)


def _format_time(seconds):
    """Format elapsed seconds as HH:MM:SS. Negative values clamp to zero."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


@dataclass
class BuildContext:
    """Pre-computed values shared across all row builders in one rebuild pass."""
    theme: dict          # resolved theme dict (THEMES[name])
    size: dict           # resolved size dict (SIZES[name])
    font_family: str
    h_spacing: int
    btn_spacing: int
    col0_size: QSize
    col5_size: QSize
    start_min_w: int
    min_name_w: int
    min_time_w: int
    indent_px: int
    time_font: QFont
    action_font: QFont
    bold_label_font: QFont
    bold_time_font: QFont
    has_mdl2: bool

    @staticmethod
    def compute(theme, size, font_family, rows, has_mdl2):
        """Build a context from current settings.  Returns None if rows is empty."""
        t = theme
        s = size
        h_spacing = s.get("h_spacing", s["padding"])
        btn_spacing = max(1, h_spacing // 2)
        time_font = QFont(font_family, s["time"])
        action_font = QFont(font_family, s["action"])

        # Column-0 reference size (square)
        _ref = QPushButton("\u2261")
        _ref.setFont(action_font)
        _h = _ref.sizeHint().height()
        col0_size = QSize(_h, _h)
        _ref.deleteLater()

        # Column-5 reference size (square)
        _ref_x = QPushButton("X")
        _ref_x.setFont(action_font)
        _hx = _ref_x.sizeHint().height()
        col5_size = QSize(_hx, _hx)
        _ref_x.deleteLater()

        start_min_w = QFontMetrics(time_font).horizontalAdvance("Start") + 20

        bold_label = QFont(font_family, s["label"])
        bold_label.setBold(True)
        fm_label = QFontMetrics(bold_label)
        indent_px = fm_label.horizontalAdvance("  ")

        if rows:
            display_names = [r["name"] for r in rows]
            min_name_w = max(
                fm_label.horizontalAdvance(dn) for dn in display_names
            ) + indent_px + 4
        else:
            min_name_w = 80

        bold_time = QFont(font_family, s["time"])
        bold_time.setBold(True)
        min_time_w = QFontMetrics(bold_time).horizontalAdvance("00:00:00 ")

        return BuildContext(
            theme=t, size=s, font_family=font_family,
            h_spacing=h_spacing, btn_spacing=btn_spacing,
            col0_size=col0_size, col5_size=col5_size,
            start_min_w=start_min_w, min_name_w=min_name_w,
            min_time_w=min_time_w, indent_px=indent_px,
            time_font=time_font, action_font=action_font,
            bold_label_font=bold_label, bold_time_font=bold_time,
            has_mdl2=has_mdl2,
        )


def build_separator_row(ctx, rid, row, children, collapsed,
                        has_running, total_time, show_count, show_time,
                        is_child, row_bg, border_css,
                        on_toggle, on_remove):
    """Build a separator (group header) row.

    Returns (container, widget_dict).
    """
    t = ctx.theme
    group_header_fg = t.get("group_header_text", t["text"])
    group_running_fg = t.get("group_running_text", group_header_fg)

    margin_css = ""  # separators are never indented
    rc = QWidget()
    rc.setObjectName("rowBg")
    rc.setStyleSheet(
        f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")
    rc_lay = QHBoxLayout(rc)
    rc_lay.setContentsMargins(0, 0, 0, 0)
    rc_lay.setSpacing(ctx.h_spacing)

    # Col 0: toggle
    toggle_btn = QPushButton("\u25B8" if collapsed else "\u25BE")
    toggle_btn.setFont(ctx.action_font)
    toggle_btn.setFixedSize(ctx.col0_size)
    toggle_btn.setStyleSheet("padding: 0px;")
    toggle_btn.clicked.connect(lambda _=False: on_toggle(rid))
    rc_lay.addWidget(toggle_btn)

    # Col 1: name
    name_lbl = QLabel(row["name"])
    grp_name_font = QFont(ctx.font_family, ctx.size["label"])
    grp_name_font.setBold(has_running)
    name_lbl.setFont(grp_name_font)
    name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    name_lbl.setFixedWidth(ctx.min_name_w)
    fg = group_running_fg if has_running else group_header_fg
    name_lbl.setStyleSheet(f"color: {fg};")
    rc_lay.addWidget(name_lbl)

    # Col 2: child count
    count_lbl = QLabel(f"({len(children)})")
    count_lbl.setFont(ctx.action_font)
    count_lbl.setAlignment(Qt.AlignCenter)
    count_lbl.setStyleSheet(f"color: {group_header_fg};")
    if not show_count:
        count_lbl.setVisible(False)
    rc_lay.addWidget(count_lbl)

    # Col 3: aggregate time
    time_lbl = QLabel(_format_time(total_time))
    grp_time_font = QFont(ctx.font_family, ctx.size["time"])
    if has_running:
        grp_time_font.setBold(True)
    time_lbl.setFont(grp_time_font)
    time_lbl.setAlignment(Qt.AlignCenter)
    time_lbl.setFixedWidth(ctx.min_time_w)
    time_lbl.setStyleSheet(f"color: {fg};")
    if not show_time:
        time_lbl.setVisible(False)
    rc_lay.addWidget(time_lbl)

    # Col 4: spacer
    spacer = QLabel("")
    rc_lay.addWidget(spacer)

    # Col 5: delete
    x_btn = QPushButton("X")
    x_btn.setFont(ctx.action_font)
    x_btn.setFixedWidth(ctx.col5_size.width())
    x_btn.clicked.connect(lambda _=False: on_remove(rid))
    rc_lay.addWidget(x_btn)

    widget_dict = {
        "name": name_lbl, "time": time_lbl,
        "count": count_lbl, "x": x_btn,
        "container": rc, "is_group": True,
    }
    return rc, widget_dict


def build_timer_row(ctx, rid, row, client_state, is_child, row_bg,
                    border_css, shift_held, label_align,
                    on_start, on_stop, on_adjust, on_remove):
    """Build a timer row.

    Returns (container, widget_dict).
    """
    t = ctx.theme
    running_fg = t.get("running_text", t["text"])
    fg = running_fg if client_state.running else t["text"]

    _ALIGN = {"Left": Qt.AlignLeft | Qt.AlignVCenter,
              "Center": Qt.AlignCenter,
              "Right": Qt.AlignRight | Qt.AlignVCenter}

    margin_css = (f"margin-left: {ctx.indent_px - 3}px;" if is_child else "")
    rc = QWidget()
    rc.setObjectName("rowBg")
    rc.setStyleSheet(
        f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")
    rc_lay = QHBoxLayout(rc)
    rc_lay.setContentsMargins(0, 0, 0, 0)
    rc_lay.setSpacing(ctx.h_spacing)

    # Col 0: bullet
    bullet = QLabel("\u2022" if client_state.running else "")
    bullet.setFont(ctx.action_font)
    bullet.setAlignment(Qt.AlignCenter)
    bullet.setFixedSize(ctx.col0_size)
    bullet.setStyleSheet(f"color: {fg};")
    rc_lay.addWidget(bullet)

    # Col 1: name
    name_lbl = QLabel(row["name"])
    name_lbl.setFont(QFont(ctx.font_family, ctx.size["label"]))
    name_lbl.setAlignment(_ALIGN.get(label_align, Qt.AlignCenter))
    name_lbl.setFixedWidth(ctx.min_name_w)
    name_lbl.setStyleSheet(f"color: {fg};")
    rc_lay.addWidget(name_lbl)

    # Col 2: Start / Stop
    sh = shift_held
    start_btn = QPushButton("Add" if sh else "Start")
    start_btn.setFont(ctx.time_font)
    start_btn.setMinimumWidth(ctx.start_min_w)
    start_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    start_btn.clicked.connect(lambda _=False: on_start(rid))

    stop_btn = QPushButton("Stop")
    stop_btn.setFont(ctx.time_font)
    stop_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    stop_btn.clicked.connect(lambda _=False: on_stop(rid))

    ss_container = QWidget()
    ss_container.setObjectName("ssCt")
    ss_container.setStyleSheet("#ssCt { background: transparent; }")
    ss_lay = QHBoxLayout(ss_container)
    ss_lay.setContentsMargins(0, 0, 0, 0)
    ss_lay.setSpacing(ctx.btn_spacing)
    ss_lay.addWidget(start_btn)
    ss_lay.addWidget(stop_btn)
    rc_lay.addWidget(ss_container)

    # Col 3: time
    time_lbl = QLabel(_format_time(client_state.current_elapsed))
    time_lbl.setFont(ctx.time_font)
    time_lbl.setAlignment(Qt.AlignCenter)
    time_lbl.setFixedWidth(ctx.min_time_w)
    time_lbl.setStyleSheet(f"color: {fg};")
    rc_lay.addWidget(time_lbl)

    # Col 4: -5/+5
    minus_btn = QPushButton("-1" if sh else "-5")
    minus_btn.setFont(ctx.action_font)
    minus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    minus_btn.clicked.connect(lambda _=False: on_adjust(rid, -1))

    plus_btn = QPushButton("+1" if sh else "+5")
    plus_btn.setFont(ctx.action_font)
    plus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    plus_btn.clicked.connect(lambda _=False: on_adjust(rid, 1))

    adj_container = QWidget()
    adj_container.setObjectName("adjCt")
    adj_container.setStyleSheet("#adjCt { background: transparent; }")
    adj_lay = QHBoxLayout(adj_container)
    adj_lay.setContentsMargins(0, 0, 0, 0)
    adj_lay.setSpacing(ctx.btn_spacing)
    adj_lay.addWidget(minus_btn)
    adj_lay.addWidget(plus_btn)
    rc_lay.addWidget(adj_container)

    # Col 5: X / 0
    x_btn = QPushButton("0" if sh else "X")
    x_btn.setFont(ctx.action_font)
    x_btn.setFixedWidth(ctx.col5_size.width())
    x_btn.clicked.connect(lambda _=False: on_remove(rid))
    rc_lay.addWidget(x_btn)

    widget_dict = {
        "name": name_lbl, "time": time_lbl,
        "start": start_btn, "stop": stop_btn,
        "minus": minus_btn, "plus": plus_btn,
        "x": x_btn, "bullet": bullet,
        "container": rc,
    }
    return rc, widget_dict


def build_footer(ctx, rearranging, on_rearrange, on_add, on_add_group,
                 on_config, on_add_input_return):
    """Build the footer bar with add/config controls.

    Returns (container, footer_widgets) where footer_widgets has keys:
    rearrange_btn, add_btn, add_group_btn, add_input, cfg_btn.
    """
    t = ctx.theme
    footer_font = QFont(ctx.font_family, ctx.size["action"])

    if ctx.has_mdl2:
        lock_char = "\uE72E"
        unlock_char = "\uE785"
        lock_font = QFont("Segoe MDL2 Assets", ctx.size["action"])
    else:
        lock_char = "\u25A0"
        unlock_char = "\u25A1"
        lock_font = footer_font

    rearrange_btn = QPushButton(unlock_char if rearranging else lock_char)
    rearrange_btn.setFont(lock_font)
    rearrange_btn.setFixedSize(ctx.col0_size)
    rearrange_btn.setStyleSheet("padding: 0px;")
    rearrange_btn.clicked.connect(on_rearrange)
    if rearranging:
        rearrange_btn.setToolTip("Lock UI layout")
    else:
        rearrange_btn.setToolTip("Unlock UI layout (drag rows to rearrange)")

    add_btn = QPushButton("Add Client")
    add_btn.setFont(footer_font)
    add_btn.clicked.connect(on_add)
    add_btn.setToolTip("Add a new client timer to UI")

    add_group_btn = QPushButton("Add Separator")
    add_group_btn.setFont(footer_font)
    add_group_btn.clicked.connect(on_add_group)
    add_group_btn.setToolTip("Add a new separator timer to UI")

    add_btns = QWidget()
    add_btns.setObjectName("addBtns")
    add_btns.setStyleSheet("#addBtns { background: transparent; }")
    add_btns_lay = QHBoxLayout(add_btns)
    add_btns_lay.setContentsMargins(0, 0, 0, 0)
    add_btns_lay.setSpacing(ctx.btn_spacing)
    add_btns_lay.addWidget(add_btn)
    add_btns_lay.addWidget(add_group_btn)

    add_input = QLineEdit()
    add_input.setFont(footer_font)
    add_input.setPlaceholderText("Client name...")
    add_input.returnPressed.connect(on_add_input_return)

    if ctx.has_mdl2:
        cfg_btn = QPushButton("\uE713")
        cfg_btn.setFont(QFont("Segoe MDL2 Assets", ctx.size["action"]))
    else:
        cfg_btn = QPushButton("\u2699")
        cfg_btn.setFont(footer_font)
    cfg_btn.setFixedSize(ctx.col5_size)
    cfg_btn.setStyleSheet("padding: 0px;")
    cfg_btn.clicked.connect(on_config)
    cfg_btn.setToolTip("Settings")

    footer = QWidget()
    footer.setObjectName("footer")
    footer.setStyleSheet("#footer { background: transparent; }")
    f_lay = QHBoxLayout(footer)
    f_lay.setContentsMargins(0, 0, 0, 0)
    f_lay.setSpacing(ctx.h_spacing)
    f_lay.addWidget(rearrange_btn)
    f_lay.addWidget(add_btns)
    f_lay.addWidget(add_input, 1)
    f_lay.addWidget(cfg_btn)

    footer_widgets = {
        "rearrange_btn": rearrange_btn,
        "add_btn": add_btn,
        "add_group_btn": add_group_btn,
        "add_input": add_input,
        "cfg_btn": cfg_btn,
    }
    return footer, footer_widgets
