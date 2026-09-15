"""The custom window frame, shared by every window that draws its own.

Qt makes the window frameless; this puts a real Win32 frame back, minus
the caption and minus the maximize bit, then answers the two non-client
messages that make it behave: WM_NCCALCSIZE ("the client area is the whole
window") and WM_NCHITTEST ("this edge resizes"). The title bar itself is an
ordinary widget — see ct/ui/title_bar.py.

Why no WS_MAXIMIZEBOX: it is the style bit Aero Snap keys off. Without it,
dragging the window into a screen corner or against an edge just puts it
there, which is the entire reason the native frame had to go.

Used as a mixin ahead of the Qt base class:

    class MainWindow(CustomFrame, QMainWindow): ...
    class ConfigDialog(CustomFrame, QDialog): ...

The host sets `RESIZE_EDGES` to "vertical" (top and bottom only — the main
window's width is automatic) or "all" (a dialog resizes freely), passes
Qt.FramelessWindowHint in its flags, and calls _install_custom_frame() once
the QObject exists. Its nativeEvent hands every Windows message to
_frame_native_event first and returns whatever that returns, if anything.
"""

import ctypes
import sys
from ctypes import wintypes

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

_GWL_STYLE = -16
_WS_MAXIMIZEBOX, _WS_MINIMIZEBOX = 0x00010000, 0x00020000
_WS_THICKFRAME, _WS_CAPTION = 0x00040000, 0x00C00000
_RESIZE_BORDER_PX = 8           # logical; the strip along an edge that resizes


class _MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


class CustomFrame:
    RESIZE_EDGES = "vertical"       # or "all"

    def _install_custom_frame(self):
        """Give the frameless window a real Win32 frame, minus the caption.

        Qt made it frameless (WS_POPUP, nothing else). Put back the styles
        that make Windows treat it as a proper window — a thick frame so
        edge-resizing, the DWM shadow and Windows 11's rounded corners
        work, a caption bit so the window animates and the taskbar can
        minimize it, WS_MINIMIZEBOX for the same taskbar reason (without
        it the taskbar button and Win+Down go silent) — and deliberately
        NOT WS_MAXIMIZEBOX.

        The caption that WS_CAPTION would normally reserve is removed
        again in _frame_native_event by answering WM_NCCALCSIZE with "no
        non-client area at all", so the client — and our own title bar —
        fills the window. The one-pixel DWM frame extension is what keeps
        the shadow drawn once the caption is gone.

        winId() creates the native window early; Qt does not recompute
        styles on show, so this sticks.
        """
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
        style |= _WS_THICKFRAME | _WS_CAPTION | _WS_MINIMIZEBOX
        style &= ~_WS_MAXIMIZEBOX
        user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        try:
            dwm = ctypes.windll.dwmapi
            margins = _MARGINS(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2. Windows
            # 10 has neither and returns an error, which is fine.
            pref = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except (OSError, AttributeError):
            pass
        # SWP_FRAMECHANGED | SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004)

    def _hit_test(self, x_phys, y_phys):
        """Where a screen point falls on the frame, in Win32 HT* terms.

        "vertical" resizes from the top and bottom strips only — the main
        window's width is automatic, and a side drag would only be snapped
        back by the next fit. "all" adds the sides and corners. Everything
        else is client; the title bar starts its own move.
        """
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(rect))
        border = max(1, round(_RESIZE_BORDER_PX * self.devicePixelRatio()))
        if not (rect.left <= x_phys < rect.right and rect.top <= y_phys < rect.bottom):
            return HTCLIENT
        top = y_phys < rect.top + border
        bottom = y_phys >= rect.bottom - border
        if self.RESIZE_EDGES == "all":
            left = x_phys < rect.left + border
            right = x_phys >= rect.right - border
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        return HTCLIENT

    def _frame_native_event(self, msg):
        """Answer the two frame messages. None means "not ours"."""
        if msg.message == _WM_NCCALCSIZE:
            # No non-client area: the client rect IS the window rect. This
            # is what removes the caption WS_CAPTION reserved. Returning 0
            # for both forms of the message keeps the proposed rect intact.
            return True, 0
        if msg.message == _WM_NCHITTEST:
            # lParam packs the screen point as two signed 16-bit ints.
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            ht = self._hit_test(x, y)
            if ht != HTCLIENT:
                return True, ht
        return None
