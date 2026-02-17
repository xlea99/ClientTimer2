from typing import Any, Literal
from collections.abc import Callable
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)
from ct.core.timer_state import TimerState
from ct.ui.ui_blueprint import UIBlueprint
from ct.util import format_time

# Purely organizational class to group functions to build new rows (timers, separators, and the footer) in the main
# view. Each builder returns a (container, widget_dict) tuple.  The container is a QWidget with objectName "rowBg"
# that can be inserted into the grid. The widget_dict maps logical names to sub-widgets for later updates.
class RowFactory:
    @staticmethod
    # Given a UIBlueprint object and information about a row, this method builds it into a single separator row.
    def separator(blueprint: UIBlueprint,
                            rid: int,
                            row: dict,
                            children: list,
                            total_time: int,
                            collapsed: bool,
                            has_running: bool,
                            show_count: bool,
                            show_time: bool,
                            is_dragging: bool,
                            on_toggle: Callable[...,Any],
                            on_remove: Callable[...,Any]):

        # Never indent separators
        margin_css = ""

        # Calculate what the row_bg should be based on if its being dragged and/or if there's a user-set background color
        if is_dragging:
            row_bg = blueprint.theme["row_dragged"]
        else:
            row_bg = row.get("bg") or blueprint.theme["group_header_bg"]

        # Build the row's contaner
        row_container = QWidget()
        row_container.setObjectName("rowBg")
        row_container.setStyleSheet(
            f"#rowBg {{ background-color: {row_bg}; {margin_css} }}")
        row_container_layout = QHBoxLayout(row_container)
        row_container_layout.setContentsMargins(0, 0, 0, 0)
        row_container_layout.setSpacing(blueprint.h_spacing)

        # Col 0: toggle
        toggle_btn = QPushButton("\u25B8" if collapsed else "\u25BE")
        toggle_btn.setFont(blueprint.action_font)
        toggle_btn.setFixedSize(blueprint.col0_size)
        toggle_btn.setStyleSheet("padding: 0px;")
        toggle_btn.clicked.connect(lambda _=False: on_toggle(rid))
        row_container_layout.addWidget(toggle_btn)

        # Col 1: name
        name_lbl = QLabel(row["name"])
        grp_name_font = QFont(blueprint.font_family, blueprint.size["label"])
        grp_name_font.setBold(has_running)
        name_lbl.setFont(grp_name_font)
        name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name_lbl.setFixedWidth(blueprint.min_name_w)
        fg = blueprint.theme["group_running_text"] if has_running else blueprint.theme["group_header_text"]
        name_lbl.setStyleSheet(f"color: {fg};")
        row_container_layout.addWidget(name_lbl)

        # Col 2: child count
        count_lbl = QLabel(f"({len(children)})")
        count_lbl.setFont(blueprint.action_font)
        count_lbl.setAlignment(Qt.AlignCenter)
        count_lbl.setStyleSheet(f"color: {blueprint.theme["group_header_text"]};")
        if not show_count:
            count_lbl.setVisible(False)
        row_container_layout.addWidget(count_lbl)

        # Col 3: aggregate time
        time_lbl = QLabel(format_time(total_time))
        grp_time_font = QFont(blueprint.font_family, blueprint.size["time"])
        if has_running:
            grp_time_font.setBold(True)
        time_lbl.setFont(grp_time_font)
        time_lbl.setAlignment(Qt.AlignCenter)
        time_lbl.setFixedWidth(blueprint.min_time_w)
        time_lbl.setStyleSheet(f"color: {fg};")
        if not show_time:
            time_lbl.setVisible(False)
        row_container_layout.addWidget(time_lbl)

        # Col 4: spacer
        spacer = QLabel("")
        row_container_layout.addWidget(spacer)

        # Col 5: delete
        x_btn = QPushButton("X")
        x_btn.setFont(blueprint.action_font)
        x_btn.setFixedWidth(blueprint.col5_size.width())
        x_btn.clicked.connect(lambda _=False: on_remove(rid))
        row_container_layout.addWidget(x_btn)

        widget_dict = {
            "name": name_lbl, "time": time_lbl,
            "count": count_lbl, "x": x_btn,
            "container": row_container, "is_group": True,
        }
        return row_container, widget_dict

    LabelAlign = Literal["Left", "Center", "Right"]
    @staticmethod
    # Given a UIBlueprint object and information about a row, this method builds it into a single timer row.
    def timer(blueprint: UIBlueprint,
                        rid: int,
                        row: dict,
                        state: TimerState,
                        is_child: bool,
                        is_dragging: bool,
                        draw_separator_line: bool,
                        shift_held: bool,
                        label_align: LabelAlign,
                        on_start: Callable[...,Any],
                        on_stop: Callable[...,Any],
                        on_adjust: Callable[...,Any],
                        on_remove: Callable[...,Any]):
        _ALIGN = {"Left": Qt.AlignLeft | Qt.AlignVCenter,
                  "Center": Qt.AlignCenter,
                  "Right": Qt.AlignRight | Qt.AlignVCenter}

        # Calculate the foreground based on if the timer is running or not.
        fg = blueprint.theme["running_text"] if state.running else blueprint.theme["text"]

        # Calculate what the row_bg should be based on if its being dragged and/or if there's a user-set background color
        if is_dragging:
            row_bg = blueprint.theme["row_dragged"]
        else:
            row_bg = row.get("bg") or blueprint.theme["bg"]

        margin_css = (f"margin-left: {blueprint.indent_px - 3}px;" if is_child else "")
        rc = QWidget()
        rc.setObjectName("rowBg")
        border_css = (f"border-bottom: 1px solid {blueprint.theme['row_separator']};"
                      if draw_separator_line else "")
        rc.setStyleSheet(
            f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}")
        rc_lay = QHBoxLayout(rc)
        rc_lay.setContentsMargins(0, 0, 0, 0)
        rc_lay.setSpacing(blueprint.h_spacing)

        # Col 0: bullet
        bullet = QLabel("\u2022" if state.running else "")
        bullet.setFont(blueprint.action_font)
        bullet.setAlignment(Qt.AlignCenter)
        bullet.setFixedSize(blueprint.col0_size)
        bullet.setStyleSheet(f"color: {fg};")
        rc_lay.addWidget(bullet)

        # Col 1: name
        name_lbl = QLabel(row["name"])
        name_lbl.setFont(QFont(blueprint.font_family, blueprint.size["label"]))
        name_lbl.setAlignment(_ALIGN.get(label_align, Qt.AlignCenter))
        name_lbl.setFixedWidth(blueprint.min_name_w)
        name_lbl.setStyleSheet(f"color: {fg};")
        rc_lay.addWidget(name_lbl)

        # Col 2: Start / Stop
        start_btn = QPushButton("Add" if shift_held else "Start")
        start_btn.setFont(blueprint.time_font)
        start_btn.setMinimumWidth(blueprint.start_min_w)
        start_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        start_btn.clicked.connect(lambda _=False: on_start(rid))

        stop_btn = QPushButton("Stop")
        stop_btn.setFont(blueprint.time_font)
        stop_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        stop_btn.clicked.connect(lambda _=False: on_stop(rid))

        ss_container = QWidget()
        ss_container.setObjectName("ssCt")
        ss_container.setStyleSheet("#ssCt { background: transparent; }")
        ss_lay = QHBoxLayout(ss_container)
        ss_lay.setContentsMargins(0, 0, 0, 0)
        ss_lay.setSpacing(blueprint.btn_spacing)
        ss_lay.addWidget(start_btn)
        ss_lay.addWidget(stop_btn)
        rc_lay.addWidget(ss_container)

        # Col 3: time
        time_lbl = QLabel(format_time(state.current_elapsed))
        time_lbl.setFont(blueprint.time_font)
        time_lbl.setAlignment(Qt.AlignCenter)
        time_lbl.setFixedWidth(blueprint.min_time_w)
        time_lbl.setStyleSheet(f"color: {fg};")
        rc_lay.addWidget(time_lbl)

        # Col 4: -5/+5
        minus_btn = QPushButton("-1" if shift_held else "-5")
        minus_btn.setFont(blueprint.action_font)
        minus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        minus_btn.clicked.connect(lambda _=False: on_adjust(rid, -1))

        plus_btn = QPushButton("+1" if shift_held else "+5")
        plus_btn.setFont(blueprint.action_font)
        plus_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        plus_btn.clicked.connect(lambda _=False: on_adjust(rid, 1))

        adj_container = QWidget()
        adj_container.setObjectName("adjCt")
        adj_container.setStyleSheet("#adjCt { background: transparent; }")
        adj_lay = QHBoxLayout(adj_container)
        adj_lay.setContentsMargins(0, 0, 0, 0)
        adj_lay.setSpacing(blueprint.btn_spacing)
        adj_lay.addWidget(minus_btn)
        adj_lay.addWidget(plus_btn)
        rc_lay.addWidget(adj_container)

        # Col 5: X / 0
        x_btn = QPushButton("0" if shift_held else "X")
        x_btn.setFont(blueprint.action_font)
        x_btn.setFixedWidth(blueprint.col5_size.width())
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

    @staticmethod
    # Given a UIBlueprint, simply builds the footer bar for the bottom of the main app view.
    def footer(blueprint: UIBlueprint, rearranging: bool,
                     on_rearrange: Callable[...,Any],
                     on_add: Callable[...,Any],
                     on_add_group: Callable[...,Any],
                     on_add_input_return: Callable[...,Any],
                     on_config: Callable[...,Any]):
        # Set font up
        footer_font = QFont(blueprint.font_family, blueprint.size["action"])
        if blueprint.has_mdl2:
            lock_char = "\uE72E"
            unlock_char = "\uE785"
            lock_font = QFont("Segoe MDL2 Assets", blueprint.size["action"])
        else:
            lock_char = "\u25A0"
            unlock_char = "\u25A1"
            lock_font = footer_font

        # Build the rearrange/lock button.
        rearrange_btn = QPushButton(unlock_char if rearranging else lock_char)
        rearrange_btn.setFont(lock_font)
        rearrange_btn.setFixedSize(blueprint.col0_size)
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
        add_btns_lay.setSpacing(blueprint.btn_spacing)
        add_btns_lay.addWidget(add_btn)
        add_btns_lay.addWidget(add_group_btn)

        add_input = QLineEdit()
        add_input.setFont(footer_font)
        add_input.setPlaceholderText("Client name...")
        add_input.returnPressed.connect(on_add_input_return)

        if blueprint.has_mdl2:
            cfg_btn = QPushButton("\uE713")
            cfg_btn.setFont(QFont("Segoe MDL2 Assets", blueprint.size["action"]))
        else:
            cfg_btn = QPushButton("\u2699")
            cfg_btn.setFont(footer_font)
        cfg_btn.setFixedSize(blueprint.col5_size)
        cfg_btn.setStyleSheet("padding: 0px;")
        cfg_btn.clicked.connect(on_config)
        cfg_btn.setToolTip("Settings")

        footer = QWidget()
        footer.setObjectName("footer")
        footer.setStyleSheet("#footer { background: transparent; }")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(0, 0, 0, 0)
        f_lay.setSpacing(blueprint.h_spacing)
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