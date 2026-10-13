"""Companion UI process: popover panel, HUD, banners, welcome and about.

Started by the app (`lidfold ui`, with the pipe address and key in its
environment). Windows are web pages in the system WebView2, made frameless
and transparent, then given Windows 11's acrylic backdrop and rounded
corners through DWM, so they read as macOS-style vibrancy panels. All
windows are created hidden at startup so the popover opens instantly.
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

import webview
import win32con
import win32gui

from ..ipc import connect_from_env

WEB = Path(__file__).with_name("web")
PANEL = (340, 560)            # logical px; height follows content
HUD = (200, 200)
BANNER = (360, 86)
WELCOME = (500, 580)
ABOUT = (380, 360)
MARGIN = 12
HUD_SECONDS, BANNER_SECONDS = 1.4, 4.5
REOPEN_GUARD = 0.35           # a tray click right after a blur-close means "close"
BLUR_GRACE = 0.5              # ignore focus loss this soon after opening (focus can bounce)

DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_SYSTEMBACKDROP_TYPE = 20, 33, 38
ROUND, ROUND_SMALL = 2, 3
BACKDROP_MICA, BACKDROP_ACRYLIC = 2, 3


# ----------------------------------------------------------------- Win32
def dark_mode() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def scale() -> float:
    return ctypes.windll.user32.GetDpiForSystem() / 96.0


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def _dwm(hwnd: int, attribute: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), 4)


class _Margins(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def vibrancy(hwnd: int, backdrop: int, corners: int, tool: bool, click_through: bool = False) -> None:
    _dwm(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark_mode() else 0)
    _dwm(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, corners)
    _dwm(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, backdrop)
    margins = _Margins(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if tool:
        ex = (ex | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
    if click_through:
        ex |= win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)


def place(hwnd: int, x: int, y: int, w: int, h: int, show: bool, activate: bool = False) -> None:
    flags = win32con.SWP_NOACTIVATE if not activate else 0
    if show:
        flags |= win32con.SWP_SHOWWINDOW
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, w, h, flags)


# ------------------------------------------------------------------- app
class Ui:
    def __init__(self):
        self.conn = connect_from_env()
        self.send_lock = threading.Lock()
        self.state: dict = {}
        self.hwnd: dict[str, int] = {}
        self.windows: dict[str, webview.Window] = {}
        self.panel_height = PANEL[1]
        self.last_blur_hide = 0.0
        self.panel_opened_at = 0.0
        self.panel_open = False
        self.timers: dict[str, threading.Timer] = {}

    # ------------------------------------------------------------ helpers
    def send(self, message: dict) -> None:
        with self.send_lock:
            try:
                self.conn.send(message)
            except (OSError, EOFError):
                os._exit(0)

    def js(self, name: str, code: str) -> None:
        window = self.windows.get(name)
        if window is not None:
            try:
                window.evaluate_js(code)
            except Exception:
                pass

    def _hwnd(self, name: str) -> int:
        if name not in self.hwnd:
            window = self.windows[name]
            try:
                self.hwnd[name] = int(window.native.Handle.ToInt64())
            except Exception:
                self.hwnd[name] = win32gui.FindWindow(None, window.title)
        return self.hwnd[name]

    def _later(self, name: str, seconds: float, fn) -> None:
        if name in self.timers:
            self.timers[name].cancel()
        self.timers[name] = threading.Timer(seconds, fn)
        self.timers[name].daemon = True
        self.timers[name].start()

    # ------------------------------------------------------------ windows
    def create(self) -> None:
        common = dict(frameless=True, transparent=True, on_top=True, resizable=False, hidden=True,
                      js_api=Api(self), background_color="#000000")
        self.windows["panel"] = webview.create_window("lidfold panel", str(WEB / "panel.html"),
                                                      width=PANEL[0], height=PANEL[1], **common)
        self.windows["hud"] = webview.create_window("lidfold hud", str(WEB / "hud.html"),
                                                    width=HUD[0], height=HUD[1], focus=False, **common)
        self.windows["banner"] = webview.create_window("lidfold banner", str(WEB / "banner.html"),
                                                       width=BANNER[0], height=BANNER[1], focus=False, **common)

    def started(self) -> None:
        """Runs once the GUI loop is up: style windows, then serve the pipe."""
        time.sleep(0.6)
        vibrancy(self._hwnd("panel"), BACKDROP_ACRYLIC, ROUND, tool=True)
        vibrancy(self._hwnd("hud"), BACKDROP_ACRYLIC, ROUND, tool=True, click_through=True)
        vibrancy(self._hwnd("banner"), BACKDROP_ACRYLIC, ROUND, tool=True)
        self.send({"cmd": "hello", "pid": os.getpid()})
        self.read()

    def read(self) -> None:
        while True:
            try:
                message = self.conn.recv()
            except (EOFError, OSError):
                os._exit(0)               # the app is gone
            event = message.get("event")
            if event == "state":
                self.state = message["state"]
                payload = json.dumps(self.state)
                for name in ("panel", "welcome", "about"):
                    self.js(name, f"window.lidfold && lidfold.update({payload})")
            elif event == "show_panel":
                self.toggle_panel()
            elif event == "hud":
                self.show_hud(message)
            elif event == "toast":
                self.show_banner(message)
            elif event == "open" and message.get("name") in ("welcome", "about"):
                self.open_window(message["name"])
            elif event == "quit":
                os._exit(0)

    # -------------------------------------------------------------- panel
    def toggle_panel(self) -> None:
        if self.panel_open:
            self.hide_panel()
            return
        if time.monotonic() - self.last_blur_hide < REOPEN_GUARD:
            return                      # this click closed it (blur came first)
        s = scale()
        left, top, right, bottom = work_area()
        w, h = int(PANEL[0] * s), int(self.panel_height * s)
        hwnd = self._hwnd("panel")
        place(hwnd, right - w - int(MARGIN * s), bottom - h - int(MARGIN * s), w, h, show=True, activate=True)
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.panel_open = True
        self.panel_opened_at = time.monotonic()
        self.js("panel", f"lidfold.appear({json.dumps(self.state)})")

    def hide_panel(self, from_blur: bool = False) -> None:
        if not self.panel_open:
            return
        if from_blur and time.monotonic() - self.panel_opened_at < BLUR_GRACE:
            return
        self.panel_open = False
        if from_blur:
            self.last_blur_hide = time.monotonic()
        win32gui.ShowWindow(self._hwnd("panel"), win32con.SW_HIDE)

    def fit_panel(self, height: float) -> None:
        self.panel_height = max(200, min(900, int(height)))
        if self.panel_open:
            s = scale()
            left, top, right, bottom = work_area()
            w, h = int(PANEL[0] * s), int(self.panel_height * s)
            place(self._hwnd("panel"), right - w - int(MARGIN * s), bottom - h - int(MARGIN * s), w, h, show=False)

    # ---------------------------------------------------------- HUD/banner
    def show_hud(self, message: dict) -> None:
        s = scale()
        left, top, right, bottom = work_area()
        w, h = int(HUD[0] * s), int(HUD[1] * s)
        x = left + (right - left - w) // 2
        y = top + int((bottom - top) * 0.72) - h // 2
        self.js("hud", f"hud.show({json.dumps(message)}, {json.dumps(self.state.get('hotkey', ''))})")
        place(self._hwnd("hud"), x, y, w, h, show=True)
        self._later("hud", HUD_SECONDS, lambda: self._fade_out("hud"))

    def show_banner(self, message: dict) -> None:
        s = scale()
        left, top, right, bottom = work_area()
        w, h = int(BANNER[0] * s), int(BANNER[1] * s)
        self.js("banner", f"banner.show({json.dumps(message)})")
        place(self._hwnd("banner"), right - w - int(MARGIN * s), top + int(MARGIN * s), w, h, show=True)
        self._later("banner", BANNER_SECONDS, lambda: self._fade_out("banner"))

    def _fade_out(self, name: str) -> None:
        self.js(name, f"{name}.hide()")
        time.sleep(0.25)
        win32gui.ShowWindow(self._hwnd(name), win32con.SW_HIDE)

    # ----------------------------------------------------- welcome/about
    def open_window(self, name: str) -> None:
        if name in self.windows:
            hwnd = self._hwnd(name)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
            win32gui.SetForegroundWindow(hwnd)
            return
        size = WELCOME if name == "welcome" else ABOUT
        s = scale()
        left, top, right, bottom = work_area()
        x = (left + (right - left - int(size[0] * s)) // 2) / s
        y = (top + (bottom - top - int(size[1] * s)) // 2) / s
        window = webview.create_window(f"lidfold {name}", str(WEB / f"{name}.html"), width=size[0],
                                       height=size[1], x=int(x), y=int(y), frameless=True,
                                       transparent=True, resizable=False, js_api=Api(self),
                                       background_color="#000000")
        self.windows[name] = window

        def on_shown():
            hwnd = self._hwnd(name)
            vibrancy(hwnd, BACKDROP_MICA, ROUND, tool=False)
            self.js(name, f"window.lidfold && lidfold.update({json.dumps(self.state)})")
            try:                            # in front of any other lidfold window
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass

        window.events.shown += on_shown
        window.events.closed += lambda: (self.windows.pop(name, None), self.hwnd.pop(name, None))

    def close_window(self, name: str) -> None:
        if name == "panel":
            self.hide_panel()
        elif name in ("banner", "hud"):
            self._fade_out(name)
        elif name in self.windows:
            self.windows[name].destroy()


class Api:
    """Methods callable from the pages as `pywebview.api.<name>(...)`."""

    def __init__(self, ui: Ui):
        self._ui = ui

    def state(self):
        return self._ui.state

    def set(self, key, value):
        self._ui.send({"cmd": "set", "key": key, "value": value})

    def toggle_lock(self):
        self._ui.send({"cmd": "toggle_lock"})

    def launch(self, tool):
        self._ui.send({"cmd": "launch", "tool": tool})
        if tool in ("calibrate", "debug", "record"):
            self._ui.hide_panel()

    def startup(self, enabled):
        self._ui.send({"cmd": "startup", "value": bool(enabled)})

    def open_folder(self):
        self._ui.send({"cmd": "open_folder"})

    def quit_app(self):
        self._ui.send({"cmd": "quit"})

    def blur(self):
        self._ui.hide_panel(from_blur=True)

    def fit(self, height):
        self._ui.fit_panel(height)

    def open(self, name):
        if name == "about":
            self._ui.hide_panel()
        self._ui.open_window(name)

    def close(self, name):
        self._ui.close_window(name)

    def open_link(self, url):
        if isinstance(url, str) and url.startswith("https://"):
            os.startfile(url)


def main() -> None:
    ui = Ui()
    ui.create()
    webview.start(ui.started, private_mode=True)
