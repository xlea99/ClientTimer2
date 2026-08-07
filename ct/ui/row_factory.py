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
    QStackedWidget,
    QWidget,
)
from ct.core.timer_state import TimerState
from ct.ui.ui_blueprint import UIBlueprint
from ct.util import format_time


class FooterStack(QStackedWidget):
    """Height is the max over all pages. Width follows the CURRENT page.

    QStackedWidget normally reports the max over EVERY page in both
    directions. The height half of that is deliberate and load-bearing: it
    is what keeps the footer exactly one line tall in both lock states, and
    footer height feeds `chrome`, which decides how many whole rows the
    viewport snaps to.

    The width half was never wanted and was quietly setting the window's
    minimum width from a page nobody could see. The hidden edit page —
    "Add Client", "Add Separator" and the name input — hinted ~496px while
    the visible status line needed ~154px, so a locked window was hundreds
    of pixels wider than anything in it. The rows did not shrink to match
    either: the adjust-button container has a Preferred policy, so it
    expanded to swallow the slack, which is what surfaced as a huge unused
    gap in each row.

    Worst at the small size presets, because that 496px is button text plus
    style padding and barely scales — so at Compact it dominated, and
    Regular genuinely used its width better than Compact did.
    """

    def sizeHint(self):
        hint = super().sizeHint()
        page = self.currentWidget()
        if page is not None:
            hint.setWidth(page.sizeHint().width())
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        page = self.currentWidget()
        if page is not None:
            hint.setWidth(page.minimumSizeHint().width())
        return hint

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
                            show_adjust: bool,
                            is_dragging: bool,
                            show_x: bool,
                            on_toggle: Callable[...,Any],
                            on_remove: Callable[...,Any]):

        # Never indent separators
        margin_css = ""

        # Calculate what the row_bg should be based on if its being dragged and/or if there's a user-set background color
        if is_dragging:
            # Group headers get their own drag colour for the same reason
            # they get their own hover colour: a bordered box on group_bg
            # starts from somewhere completely different than an open row on
            # app_bg, so one shared tint cannot suit both.
            row_bg = blueprint.theme["group_drag_bg"]
        else:
            row_bg = row.get("bg") or blueprint.theme["group_bg"]

        # Build the row's contaner
        # The border is as much of the header as its fill — a 2px box that
        # keeps its resting colour while the inside changes reads as the
        # tint failing to take, the same way the row separator did on a
        # dragged timer. So the line follows the state too.
        group_line = (blueprint.theme["group_drag_line"] if is_dragging
                      else blueprint.theme["group_line"])
        row_container = QWidget()
        row_container.setObjectName("rowBg")
        row_container.setStyleSheet(
            f"#rowBg {{ background-color: {row_bg}; {margin_css}"
            f" border: 2px solid {group_line}; }}"
            f" #rowBg[hov=\"1\"] {{"
            f" background-color: {blueprint.theme['group_hover_bg']};"
            f" border-color: {blueprint.theme['group_hover_line']}; }}")
        row_container_layout = QHBoxLayout(row_container)
        # Per-size; was a flat 3 on all presets. Timer rows were already
        # size-driven (0, 0, 0, line_gap) — only group headers were not.
        _rp = blueprint.size.get("row_pad", 3)
        row_container_layout.setContentsMargins(_rp, _rp, _rp, _rp)
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
        name_lbl.setTextFormat(Qt.PlainText)
        grp_name_font = QFont(blueprint.font_family, blueprint.size["label"])
        grp_name_font.setBold(has_running)
        name_lbl.setFont(grp_name_font)
        name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name_lbl.setFixedWidth(blueprint.min_name_w)
        fg = blueprint.theme["group_running_fg"] if has_running else blueprint.theme["group_fg"]
        name_lbl.setStyleSheet(f"color: {fg};")
        row_container_layout.addWidget(name_lbl)

        # Col 2: child count
        # Col 2 is the Start button on a timer row. The count takes exactly
        # that width so the TIME column lands at the same x in both — a
        # shared column geometry rather than two layouts that happen to
        # start alike. Blanked rather than hidden when the count is off: a
        # hidden widget surrenders its width and the columns fall out of
        # step again.
        count_lbl = QLabel(f"({len(children)})" if show_count else "")
        count_lbl.setFont(blueprint.action_font)
        count_lbl.setAlignment(Qt.AlignCenter)
        count_lbl.setFixedWidth(blueprint.start_min_w)
        count_lbl.setStyleSheet(f"color: {blueprint.theme['group_fg']};")
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
            time_lbl.setText("")          # blank, not hidden — see col 2
        row_container_layout.addWidget(time_lbl)

        # Col 4: reserve exactly the -5/+5 cluster a timer row carries, so
        # the X below lines up with theirs. Zero when those buttons are off,
        # since then no timer row has them either.
        adj_gap = QWidget()
        adj_gap.setFixedWidth(blueprint.adj_w)
        adj_gap.setStyleSheet("background: transparent;")
        row_container_layout.addWidget(adj_gap)
        adj_gap.setVisible(show_adjust)
        # HIDDEN, not zero-width. A visible zero-width widget still collects
        # the layout's spacing on both sides, which put the group's X four
        # pixels off the timers'. A hidden one is skipped outright — exactly
        # what happens to the timer row's own adjust container.

        # Col 5: delete
        x_btn = QPushButton("X")
        x_btn.setFont(blueprint.action_font)
        x_btn.setFixedWidth(blueprint.col5_size.width())
        x_btn.clicked.connect(lambda _=False: on_remove(rid))
        row_container_layout.addWidget(x_btn)
        x_btn.setVisible(show_x)
        # Slack goes AFTER the X, so the row reads as one block against the
        # left and any surplus window width sits at the far edge. Before the
        # X it pooled between the time and the X, which is a hole in the
        # middle of the row — very visible unlocked, where the footer's edit
        # controls make the window much wider than the rows need.
        row_container_layout.addStretch(1)

        widget_dict = {
            "name": name_lbl, "time": time_lbl,
            "count": count_lbl, "x": x_btn,
            "container": row_container, "is_group": True,
            "bg_left": 0,          # separators are never indented
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
                        show_adjust: bool,
                        show_x: bool,
                        on_toggle: Callable[...,Any],
                        on_adjust: Callable[...,Any],
                        on_remove: Callable[...,Any],
                        force_line_gap: bool = False,
                        footer_line: bool = False):
        _ALIGN = {"Left": Qt.AlignLeft | Qt.AlignVCenter,
                  "Center": Qt.AlignCenter,
                  "Right": Qt.AlignRight | Qt.AlignVCenter}

        # Calculate the foreground based on if the timer is running or not.
        fg = blueprint.theme["row_running_fg"] if state.running else blueprint.theme["app_fg"]

        # Calculate what the row_bg should be based on if its being dragged and/or if there's a user-set background color
        if is_dragging:
            row_bg = blueprint.theme["row_drag_bg"]
        else:
            row_bg = row.get("bg") or blueprint.theme["app_bg"]

        margin_css = (f"margin-left: {blueprint.indent_px - 3}px;" if is_child else "")
        rc = QWidget()
        rc.setObjectName("rowBg")
        # footer_line: this is the bottom-most row — its client separator is
        # replaced by the thick footer separator so the two don't stack.
        if is_dragging:
            # A lifted row carries no separator. The line belongs to the LIST
            # — it divides this row from the next one — and the dragged row
            # has left the list. Kept, it paints a hard row_line edge along
            # the bottom of a row that has otherwise gone to row_drag_bg,
            # which on a high-contrast theme (95 Windows: teal drag on grey
            # chrome) reads as the bottom pixel of the row failing to tint.
            border_css = ""
        elif footer_line:
            border_css = f"border-bottom: 2px solid {blueprint.theme['chrome_line']};"
        elif draw_separator_line:
            border_css = f"border-bottom: 1px solid {blueprint.theme['row_line']};"
        else:
            border_css = ""
        # The hover tint is a SELECTOR, not a stylesheet the host rewrites —
        # see _on_row_hover for why that distinction matters.
        # nosep: _update_bottom_line drops the separator on whichever row is
        # flush with the viewport bottom, so it doesn't stack with the footer
        # rule. A selector, not a stylesheet edit — see _update_bottom_line.
        rc.setStyleSheet(
            f"#rowBg {{ background-color: {row_bg}; {margin_css} {border_css} }}"
            f" #rowBg[hov=\"1\"] {{"
            f" background-color: {blueprint.theme['row_hover_bg']}; }}"
            f" #rowBg[nosep=\"1\"] {{ border-bottom: none; }}")
        rc_lay = QHBoxLayout(rc)
        # Bottom margin only when a separator line is drawn there — the
        # stylesheet border paints inside the row's rect, so without this the
        # buttons sit directly on the line. Gap is per-size ("line_gap").
        # force_line_gap applies it regardless (used in rearrange mode so all
        # rows share one height and dragging never resizes anything).
        #
        # RIGHT margin matches the separator's row_pad so the two X buttons
        # line up in edit mode. Insetting the timer rather than flushing the
        # separator, because the separator is a bordered box: pushing its X
        # out to the edge would slide it under its own 2px border.
        rc_lay.setContentsMargins(
            blueprint.size.get("row_pad", 3), 0,
            blueprint.size.get("row_pad", 3),
            blueprint.size.get("line_gap", 0)
            if (draw_separator_line or footer_line or force_line_gap) else 0)
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
        name_lbl.setTextFormat(Qt.PlainText)
        name_lbl.setFont(QFont(blueprint.font_family, blueprint.size["label"]))
        name_lbl.setAlignment(_ALIGN.get(label_align, Qt.AlignCenter))
        name_lbl.setFixedWidth(blueprint.min_name_w)
        name_lbl.setStyleSheet(f"color: {fg};")
        rc_lay.addWidget(name_lbl)

        # Col 2: one button that starts a stopped timer and stops a running
        # one. Stop was the rarely-used half — in the chess-clock model you
        # switch clients by starting the next timer, not by stopping this one
        # — and it did nothing at all on a stopped row. FIXED width, not
        # minimum: the label changes on every start/stop, and a button that
        # resized would shift the columns under the cursor.
        toggle_btn = QPushButton(
            "Stop" if state.running else ("Add" if shift_held else "Start"))
        toggle_btn.setFont(blueprint.time_font)
        toggle_btn.setFixedWidth(blueprint.start_min_w)
        toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        toggle_btn.clicked.connect(lambda _=False: on_toggle(rid))
        rc_lay.addWidget(toggle_btn)

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
        adj_container.setVisible(show_adjust)

        # Col 5: X / 0
        # Always X. Shift used to turn it into "0" for reset-instead-of-delete;
        # that went away with the behaviour it advertised — see _on_remove.
        x_btn = QPushButton("X")
        x_btn.setFont(blueprint.action_font)
        x_btn.setFixedWidth(blueprint.col5_size.width())
        x_btn.clicked.connect(lambda _=False: on_remove(rid))
        rc_lay.addWidget(x_btn)
        # Removing a row is an edit, so it lives with the other edits — behind
        # the lock. A row full of X buttons is also the loudest possible signal
        # that edit mode is on, which a small lock glyph never was.
        x_btn.setVisible(show_x)
        # Same as the group row: slack after the X, never between the time
        # and the X.
        rc_lay.addStretch(1)

        widget_dict = {
            "name": name_lbl, "time": time_lbl,
            "toggle": toggle_btn,
            "minus": minus_btn, "plus": plus_btn,
            "x": x_btn, "bullet": bullet,
            "container": rc,
            # margin-left insets the PAINTED background without moving the
            # widget, so the container's geometry alone doesn't describe where
            # the row's colour actually starts. The hover strip needs to know.
            "bg_left": (blueprint.indent_px - 3) if is_child else 0,
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

        add_input = QLineEdit()
        add_input.setFont(footer_font)
        add_input.setPlaceholderText("Name...")
        add_input.returnPressed.connect(on_add_input_return)
        # Ignored width: it already has a stretch factor, so it FILLS the
        # footer — but its own sizeHint (~200px) was still being demanded,
        # and the footer is what sets the window's minimum width. That made
        # the unlocked window wider than the rows, leaving dead space to the
        # right of every one. Ignored means "give me what is left", which is
        # what a fill-the-rest field actually wants. The minimum keeps it
        # usable when there is little left to give.
        add_input.setSizePolicy(QSizePolicy.Ignored,
                                add_input.sizePolicy().verticalPolicy())
        add_input.setMinimumWidth(60)

        # Edit page: everything to do with authoring the list.
        edit_page = QWidget()
        edit_page.setObjectName("addBtns")
        edit_page.setStyleSheet("#addBtns { background: transparent; }")
        edit_lay = QHBoxLayout(edit_page)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(blueprint.btn_spacing)
        edit_lay.addWidget(add_btn)
        edit_lay.addWidget(add_group_btn)
        edit_lay.addWidget(add_input, 1)

        # Locked page: the status line. Text is filled in by
        # MainWindow._update_status — this only sets up the shell.
        status_lbl = QLabel()
        status_lbl.setFont(footer_font)
        status_lbl.setAlignment(Qt.AlignCenter)
        # RichText so the running dot can carry the accent colour while the
        # rest of the line stays muted. Only numbers and fixed words are
        # interpolated into it — no client names — so there's nothing to escape.
        status_lbl.setTextFormat(Qt.RichText)
        status_lbl.setCursor(Qt.PointingHandCursor)
        status_lbl.setStyleSheet(
            f"color: {blueprint.theme['app_fg_muted']}; background: transparent;")

        # A stack rather than show/hide: its size hint is the tallest page, so
        # the footer is exactly one line high in BOTH modes no matter what
        # either page grows into. Toggling the lock must never change the
        # window's height — footer height feeds `chrome`, and `chrome` decides
        # how many whole rows the viewport snaps to.
        # FooterStack, not QStackedWidget: same max-height-over-pages
        # behaviour, but width follows the visible page. See the class.
        middle = FooterStack()
        # Fixed vertically or the stack expands and the footer soaks up every
        # spare pixel in the column. One line, always — that slack belongs to
        # the row viewport.
        middle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        middle.setObjectName("footerMiddle")
        middle.setStyleSheet("#footerMiddle { background: transparent; }")
        middle.addWidget(status_lbl)
        middle.addWidget(edit_page)
        middle.setCurrentIndex(1 if rearranging else 0)

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
        f_lay.addWidget(middle, 1)
        f_lay.addWidget(cfg_btn)

        footer_widgets = {
            "rearrange_btn": rearrange_btn,
            "add_btn": add_btn,
            "add_group_btn": add_group_btn,
            "add_input": add_input,
            "status_lbl": status_lbl,
            "cfg_btn": cfg_btn,
        }
        return footer, footer_widgets
