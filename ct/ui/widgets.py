"""Small shared widgets used by more than one screen."""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton


class TickCheckBox(QCheckBox):
    """A checkbox that keeps its stylesheet-drawn box AND shows a tick.

    Styling QCheckBox::indicator hands indicator painting to the stylesheet,
    which draws the border/background but no check mark. So the base class
    paints the box and the label, and we draw the tick over it ourselves.
    """

    def __init__(self, text, tick_color, parent=None):
        super().__init__(text, parent)
        self._tick_color = QColor(tick_color)

    @staticmethod
    def style_for(theme):
        """The box styling this widget is designed to sit on top of.

        Note the explicit QCheckBox selector: bare properties and selector
        rules can't be mixed in one stylesheet (it voids both). The box looks
        the same checked or not — the tick is painted over it.
        """
        return (f"QCheckBox {{ color: {theme['app_fg']}; background: transparent; }}"
                f"QCheckBox::indicator {{ width: 13px; height: 13px;"
                f" border: 1px solid {theme['chrome_line']};"
                f" background: {theme['app_bg']}; }}")

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        r = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, self)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._tick_color, max(1.6, r.height() * 0.16))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        p.drawPolyline(QPolygonF([
            QPointF(x + w * 0.24, y + h * 0.52),
            QPointF(x + w * 0.43, y + h * 0.72),
            QPointF(x + w * 0.77, y + h * 0.29),
        ]))
        p.end()
