"""Nestsweep desktop window: a WebView2 page (nestsweep/ui) driven by nestsweep.api."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import webview

from . import common, updater
from .api import Api

UI_DIR = Path(__file__).resolve().parent / "ui"

BG = (0x15, 0x17, 0x1C)        # --bg
TEXT = (0xEC, 0xEE, 0xF2)


def _colorref(rgb) -> ctypes.c_int:
    r, g, b = rgb
    return ctypes.c_int(r | (g << 8) | (b << 16))


def _dark_title_bar(window) -> None:
    """Match the native title bar to the app (dark mode on Windows 10 20H1+, exact colour on Windows 11)."""
    try:
        hwnd = window.native.Handle.ToInt32()
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))       # DWMWA_USE_IMMERSIVE_DARK_MODE
        for attr, rgb in ((35, BG), (34, BG), (36, TEXT)):                              # caption, border, text colour
            val = _colorref(rgb)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def main() -> None:
    try:   # one icon in the taskbar, not grouped under python.exe when run from source
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Nestsweep.App")
    except Exception:
        pass
    updater.cleanup_old()
    api = Api()
    title = "Nestsweep" + (" (Administrator)" if common.is_admin() else "")
    window = webview.create_window(
        title, url=str(UI_DIR / "index.html"), js_api=api,
        width=1240, height=800, min_size=(1040, 680), background_color="#15171C", text_select=False)
    api._attach(window)
    window.events.shown += lambda: _dark_title_bar(window)
    webview.start(gui="edgechromium", debug="--debug" in sys.argv, private_mode=False,
                  storage_path=str(common.APP_DIR / "webview"))


if __name__ == "__main__":
    main()
