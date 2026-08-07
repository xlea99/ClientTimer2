from dataclasses import dataclass
from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QPushButton

# A unified UI Blueprint dataclass to share across all UI builders.
@dataclass
class UIBlueprint:
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
    adj_w: int
    indent_px: int
    time_font: QFont
    action_font: QFont
    bold_label_font: QFont
    bold_time_font: QFont
    has_mdl2: bool

    # Builds the context from current settings.
    @staticmethod
    def compute(theme, size, font_family, rows, has_mdl2):
        # Establish spacing based on given size preset
        horizontal_spacing = size.get("h_spacing", size["padding"])
        button_spacing = max(1, horizontal_spacing // 2)

        # Initialize font objects
        time_font = QFont(font_family, size["time"])
        action_font = QFont(font_family, size["action"])

        # Get Column-0 reference size (square) by actually instantiating and measuring it briefly
        _ref = QPushButton("\u2261")
        _ref.setFont(action_font)
        _h = _ref.sizeHint().height()
        col0_size = QSize(_h, _h)
        _ref.deleteLater()

        # Get Column-5 reference size (square) by actually instantiating and measuring it briefly
        _ref_x = QPushButton("X")
        _ref_x.setFont(action_font)
        _hx = _ref_x.sizeHint().height()
        col5_size = QSize(_hx, _hx)
        _ref_x.deleteLater()

        # The one button cycles through all three of these, so size it to
        # the widest or it would resize under the cursor every time a
        # timer starts — and a row that changes width re-lays-out the grid.
        _fm_time = QFontMetrics(time_font)
        # Per-size, not a flat +20: that constant did not shrink with the
        # preset, so it was ~2x the proportional weight at Microscopic that
        # it was at Gargantuan. See sizes.py.
        start_min_w = (max(_fm_time.horizontalAdvance(lbl)
                           for lbl in ("Start", "Stop", "Add"))
                       + size.get("btn_pad", 20))

        bold_label = QFont(font_family, size["label"])
        bold_label.setBold(True)
        fm_label = QFontMetrics(bold_label)
        indent_px = fm_label.horizontalAdvance("  ")

        if rows:
            display_names = [r["name"] for r in rows]
            min_name_w = max(
                fm_label.horizontalAdvance(dn) for dn in display_names
            ) + indent_px + size.get("name_pad", 4)
        else:
            min_name_w = 80

        # Width of the -5/+5 cluster. The group header reserves exactly this
        # much so its X lines up with the timer rows' when the buttons are on.
        _ref_adj = QPushButton("-5")
        _ref_adj.setFont(action_font)
        adj_w = _ref_adj.sizeHint().width() * 2 + button_spacing
        _ref_adj.deleteLater()

        bold_time = QFont(font_family, size["time"])
        bold_time.setBold(True)
        min_time_w = QFontMetrics(bold_time).horizontalAdvance("00:00:00 ")

        return UIBlueprint(
            theme=theme, size=size, font_family=font_family,
            h_spacing=horizontal_spacing, btn_spacing=button_spacing,
            col0_size=col0_size, col5_size=col5_size,
            start_min_w=start_min_w, min_name_w=min_name_w,
            min_time_w=min_time_w, adj_w=adj_w, indent_px=indent_px,
            time_font=time_font, action_font=action_font,
            bold_label_font=bold_label, bold_time_font=bold_time,
            has_mdl2=has_mdl2,
        )
