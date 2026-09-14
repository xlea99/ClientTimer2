"""The window's own title bar.

The native caption is gone (see MainWindow._install_custom_frame): Windows
draws nothing above the client area, so this widget is the whole top of the
window — icon, title, grow-height, minimize, close. It is an ordinary child
of the central widget, which is what makes it themeable and what keeps it
inside the sizing model for free: it is part of the central widget's hint,
so `chrome` in _shrink_to_fit already accounts for it.

Moving is still done by Windows. A drag on the bar hands off to the OS move
loop (SC_MOVE), so multi-monitor DPI changes, the settle messages the sizing
model relies on, and every keyboard shortcut keep working. Because the
window carries no WS_MAXIMIZEBOX, that loop never snaps.
"""

import ctypes
import sys

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

_WM_SYSCOMMAND = 0x0112
_SC_MOVE_CAPTION = 0xF012      # SC_MOVE | HTCAPTION: "the user grabbed the caption"

# Caption glyphs: Segoe MDL2 Assets when present, plain text otherwise.
_GLYPHS = {
    "grow":     ("", "□"),    # ChromeMaximize / □
    "minimize": ("", "–"),    # ChromeMinimize / –
    "close":    ("", "✕"),    # ChromeClose / ✕
}


class TitleBar(QWidget):
    grow_requested = Signal()
    minimize_requested = Signal()
    close_requested = Signal()

    # Windows' own caption buttons are 46px wide, which on a window this
    # narrow leaves the title three letters. 36 keeps them an easy target.
    # The bar is as tall as the theme's action font needs, floored so the
    # buttons stay hittable.
    BUTTON_W = 36
    MIN_H = 30

    def __init__(self, title, icon_path, has_mdl2, parent=None, light_icon_path=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        # A QWidget subclass does not paint a stylesheet background unless
        # told to — without this the bar was whatever sat behind it.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._has_mdl2 = has_mdl2
        self._press_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(6)

        self._icon = QLabel()
        self._icon.setObjectName("titleIcon")
        # Black ink and white ink; the theme's use_light_icon picks.
        self._icon_sources = {
            False: QIcon(str(icon_path)) if icon_path else QIcon(),
            True: (QIcon(str(light_icon_path)) if light_icon_path
                   else (QIcon(str(icon_path)) if icon_path else QIcon())),
        }
        lay.addWidget(self._icon)

        self._title = QLabel(title)
        self._title.setObjectName("titleText")
        # Ignored: the title must never be what decides the window's width
        # — the rows and the footer do that. It elides to whatever is left.
        self._title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._title.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self._title, 1)

        self._buttons = {}
        # Windows' order: minimize, then the size button, then close.
        for key, signal, tip in (("minimize", self.minimize_requested, "Minimize"),
                                 ("grow", self.grow_requested, "Grow to fit the screen"),
                                 ("close", self.close_requested, "Close")):
            btn = QPushButton()
            btn.setObjectName("titleClose" if key == "close" else "titleBtn")
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            lay.addWidget(btn)
            self._buttons[key] = btn

        self._full_title = title
        self.apply_theme({}, {"action": 9})

    # ---- appearance ---------------------------------------------------- #

    def apply_theme(self, theme, size):
        """Restyle from the theme's window_header_* / title_* keys and the size's action font."""
        pt = size.get("action", 9)
        font = QFont(self.font().family(), max(pt, 9))
        self._title.setFont(font)
        h = max(self.MIN_H, QFontMetrics(font).height() + 12)
        self.setFixedHeight(h)
        self._icon.setFixedSize(h - 12, h - 12)
        source = self._icon_sources[bool(theme.get("use_light_icon", False))]
        if not source.isNull():
            self._icon.setPixmap(source.pixmap(h - 12, h - 12))

        glyph_font = (QFont("Segoe MDL2 Assets", 8) if self._has_mdl2
                      else QFont(self.font().family(), 10))
        for key, btn in self._buttons.items():
            btn.setText(_GLYPHS[key][0 if self._has_mdl2 else 1])
            btn.setFont(glyph_font)
            btn.setFixedSize(self.BUTTON_W, h)

        bg = theme.get("window_header_bg", "#F0F0F0")
        fg = theme.get("window_header_fg", "#000000")
        hover = theme.get("title_hover_bg", "#DDDDDD")
        close_bg = theme.get("title_close_hover_bg", "#C42B1C")
        close_fg = theme.get("title_close_hover_fg", "#FFFFFF")
        self.setStyleSheet(
            f"#titleBar {{ background-color: {bg}; }}"
            f"#titleBar QLabel {{ color: {fg}; background: transparent; }}"
            f"#titleBar QPushButton {{ background: transparent; border: none;"
            f"  border-radius: 0px; padding: 0px; margin: 0px; color: {fg}; }}"
            f"#titleBar QPushButton:hover {{ background-color: {hover}; }}"
            f"#titleBar QPushButton#titleClose:hover {{"
            f"  background-color: {close_bg}; color: {close_fg}; }}")
        self._elide()

    def set_title(self, text):
        self._full_title = text
        self._elide()

    def _elide(self):
        fm = QFontMetrics(self._title.font())
        avail = max(0, self._title.width() - 2)
        self._title.setText(fm.elidedText(self._full_title, Qt.ElideRight, avail)
                            if avail else self._full_title)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    # ---- moving -------------------------------------------------------- #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_pos is not None
                and event.buttons() & Qt.LeftButton
                and (event.globalPosition().toPoint() - self._press_pos).manhattanLength() > 3):
            self._press_pos = None
            self._start_system_move()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = None
            self.grow_requested.emit()
            return
        super().mouseDoubleClickEvent(event)

    def _start_system_move(self):
        """Hand the drag to Windows' own move loop.

        ReleaseCapture first: Qt captured the mouse on press, and the OS
        loop needs it. SendMessage blocks until the drop, which is fine —
        nothing here has anything to do until then.
        """
        win = self.window()
        if sys.platform != "win32" or win is None:
            return
        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        user32.SendMessageW(int(win.winId()), _WM_SYSCOMMAND, _SC_MOVE_CAPTION, 0)
