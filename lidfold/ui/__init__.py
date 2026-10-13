"""Companion UI process: popover panel, HUD, banners, welcome and about.

Started by the app (`lidfold ui`, with the pipe address and key in its
environment). Windows are web pages in the system WebView2, made frameless
and given Windows 11's backdrop and rounded corners through DWM, so they
read as macOS-style vibrancy panels. All windows are created hidden at
startup so the popover opens instantly.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

import webview

from .. import log as logs
from ..ipc import APP_PID_ENV, connect_from_env

log = logs.get("ui")

WEB = Path(__file__).with_name("web")
PANEL = (340, 560)            # logical px; height follows content
HUD = (200, 200)
BANNER = (360, 86)
WELCOME = (500, 580)
ABOUT = (380, 360)
MARGIN = 12
HUD_SECONDS, BANNER_SECONDS = 1.4, 4.5
STATE_JS = "window.lidfold && lidfold.update("
REOPEN_GUARD = 0.35           # a tray click right after a blur-close means "close"
# Pages normally load in ~1.5 s. After the previous UI was killed, WebView2
# sometimes never loaded them, so tray clicks waited forever; past this
# deadline the UI exits and the app starts a fresh one.
LOAD_DEADLINE = 20.0
TOOL_WINDOWS = ("panel", "hud", "banner")
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


def surface_color(window: bool = False) -> str:
    """Matches --surface / --window in style.css for the current theme."""
    if dark_mode():
        return "#1e1e20" if window else "#202023"
    return "#fafafc" if window else "#f6f6f8"


def scale() -> float:
    return ctypes.windll.user32.GetDpiForSystem() / 96.0


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


# All window calls go through ctypes, which releases the GIL while it waits.
# pywin32's win32gui keeps holding it: when SetWindowLong waited on a window
# thread that needed the GIL for pywebview's Python callbacks, the whole UI
# process froze (even a join() timeout never fired).
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowLongW.restype = ctypes.c_long
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x00000020, 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def find_window(title: str) -> int:
    return user32.FindWindowW(None, title) or 0


def is_visible(hwnd: int) -> bool:
    return bool(hwnd and user32.IsWindowVisible(hwnd))


def foreground(hwnd: int) -> None:
    user32.SetForegroundWindow(hwnd)


def _dwm(hwnd: int, attribute: int, value: int) -> None:
    v = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), 4)


class _Margins(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def vibrancy(hwnd: int, backdrop: int, corners: int) -> None:
    """DWM attributes only: these do not message the window's thread."""
    _dwm(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark_mode() else 0)
    _dwm(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, corners)
    _dwm(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, backdrop)
    margins = _Margins(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))


def tool_style(hwnd: int, click_through: bool) -> bool:
    """No taskbar entry (and click-through for the HUD). SetWindowLong sends
    the window's own thread a synchronous message; during pywebview's load
    sequence that thread is tied up and the call deadlocked. So this runs
    just before a window is first shown, on a throwaway thread with a
    timeout. Returns False if it did not complete in time."""
    def apply():
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if click_through:
            ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    worker = threading.Thread(target=apply, daemon=True)
    worker.start()
    worker.join(2.0)
    return not worker.is_alive()


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
        self.panel_wanted = False      # clicked before the panel window existed
        self.loaded_names: set[str] = set()
        self.timers: dict[str, threading.Timer] = {}
        self.pending: dict[str, list[str]] = {}   # JS for pages that have not loaded yet
        self.pending_lock = threading.Lock()
        # Window operations go through pywebview's API (it keeps WinForms'
        # own visibility state right; raw ShowWindow on these hidden forms
        # left the panel invisible). Those calls block until the GUI thread
        # runs them, so one worker thread executes them in order, and the
        # pipe reader only ever queues.
        self.ops: queue.Queue = queue.Queue()
        self.tool_styled: set[str] = set()
        threading.Thread(target=self._run_ops, daemon=True, name="window-ops").start()

    # ------------------------------------------------------------ helpers
    def send(self, message: dict) -> None:
        with self.send_lock:
            try:
                self.conn.send(message)
            except (OSError, EOFError):
                os._exit(0)

    def js(self, name: str, code: str, replace_state: bool = False) -> None:
        """Run JS in a page without ever blocking the caller. pywebview's
        evaluate_js waits for the page to load, and a hidden page can take a
        long time: that wait once swallowed every tray click. Before load the
        code is queued (a state update replaces the previous one); after load
        it runs on a short-lived thread."""
        window = self.windows.get(name)
        if window is None:
            return
        with self.pending_lock:
            if not window.events.loaded.is_set():
                queue = self.pending.setdefault(name, [])
                if replace_state:
                    queue[:] = [c for c in queue if not c.startswith(STATE_JS)]
                queue.append(code)
                return
        threading.Thread(target=self._evaluate, args=(window, code), daemon=True).start()

    def _run_ops(self) -> None:
        while True:
            op = self.ops.get()
            try:
                op()
            except Exception:
                log.exception("window operation failed")

    def present(self, name: str, x: int, y: int, w: int, h: int, focus: bool) -> None:
        """Queue: move/resize (physical px in, logical px to pywebview), show,
        keep on top, optionally take focus."""
        def op():
            window, s = self.windows[name], scale()
            hwnd = self._hwnd(name)
            if name not in self.tool_styled and hwnd:
                if tool_style(hwnd, click_through=name == "hud"):
                    self.tool_styled.add(name)
                else:
                    log.warning("window style for %s timed out; will retry", name)
            window.resize(round(w / s), round(h / s))
            window.move(round(x / s), round(y / s))
            window.show()
            flags = SWP_NOMOVE | SWP_NOSIZE | (0 if focus else SWP_NOACTIVATE)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
            if focus:
                try:
                    foreground(hwnd)
                except Exception:
                    pass
            log.info("%s presented: visible=%s foreground=%s", name, is_visible(hwnd),
                     (user32.GetForegroundWindow() or 0) == hwnd)
        self.ops.put(op)

    def conceal(self, name: str) -> None:
        self.ops.put(lambda: self.windows[name].hide())

    @staticmethod
    def _evaluate(window, code: str) -> None:
        try:
            window.evaluate_js(code)
        except Exception:
            log.exception("evaluate_js failed")

    def loaded(self, name: str) -> None:
        """pywebview event handler: a page finished loading.

        Handlers run while pywebview's own load sequence is still in
        progress: it then injects its JS bridge, which needs the window's
        GUI thread. Styling the window here (SetWindowLong sends that thread
        a synchronous message) deadlocked the two, intermittently. So a
        handler only queues work and returns."""
        self.ops.put(lambda: self._after_load(name))

    def _after_load(self, name: str) -> None:
        """On the ops worker: style the window, flush queued JS, honour an
        early click."""
        tool = name in ("panel", "hud", "banner")
        hwnd = self._hwnd(name, wait=5.0)
        if hwnd:
            try:
                vibrancy(hwnd, BACKDROP_ACRYLIC if tool else BACKDROP_MICA, ROUND)
            except Exception:
                log.exception("styling %s failed", name)
        log.info("%s loaded (hwnd %s)", name, hwnd)
        self.loaded_names.add(name)
        with self.pending_lock:
            queued = self.pending.pop(name, [])
        window = self.windows.get(name)
        for code in queued:
            threading.Thread(target=self._evaluate, args=(window, code), daemon=True).start()
        if name == "panel" and self.panel_wanted:
            self.panel_wanted = False
            self.toggle_panel()

    def _hwnd(self, name: str, wait: float = 0.0) -> int:
        """The window's handle, found by its unique title through Win32 only.
        (Reading pywebview's WinForms `native.Handle` from this thread is a
        cross-thread WinForms call; it deadlocked the installed app's UI.)
        0 is never cached: the window may simply not exist yet."""
        if self.hwnd.get(name):
            return self.hwnd[name]
        title = self.windows[name].title
        deadline = time.monotonic() + wait
        while True:
            hwnd = find_window(title)
            if hwnd or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if hwnd:
            self.hwnd[name] = hwnd
        return hwnd

    def _later(self, name: str, seconds: float, fn) -> None:
        if name in self.timers:
            self.timers[name].cancel()
        self.timers[name] = threading.Timer(seconds, fn)
        self.timers[name].daemon = True
        self.timers[name].start()

    # ------------------------------------------------------------ windows
    def create(self) -> None:
        # Not transparent: pywebview ignores hidden=True for transparent
        # windows (they appeared 1-3 s after load as empty grey boxes). The
        # surfaces were near-opaque anyway; the window background matches them.
        # easy_drag=False: pywebview makes frameless windows draggable from
        # any point by default, so dragging a slider moved the whole panel.
        common = dict(frameless=True, easy_drag=False, on_top=True, resizable=False, hidden=True,
                      js_api=Api(self), background_color=surface_color())
        self.windows["panel"] = webview.create_window("lidfold panel", str(WEB / "panel.html"),
                                                      width=PANEL[0], height=PANEL[1], **common)
        self.windows["hud"] = webview.create_window("lidfold hud", str(WEB / "hud.html"),
                                                    width=HUD[0], height=HUD[1], focus=False, **common)
        self.windows["banner"] = webview.create_window("lidfold banner", str(WEB / "banner.html"),
                                                       width=BANNER[0], height=BANNER[1], focus=False, **common)
        for name in TOOL_WINDOWS:
            self.windows[name].events.loaded += (lambda n=name: self.loaded(n))

    def started(self) -> None:
        """Runs once the GUI loop is up. Serving the pipe comes first; each
        window is styled when its page loads (see `loaded`)."""
        log.info("gui loop up")
        self.send({"cmd": "hello", "pid": os.getpid()})
        log.info("hello sent; serving")
        watchdog = threading.Timer(LOAD_DEADLINE, self._check_loaded)
        watchdog.daemon = True
        watchdog.start()
        self.read()

    def _check_loaded(self) -> None:
        missing = [n for n in TOOL_WINDOWS if n not in self.loaded_names]
        if missing:
            log.warning("%s did not load within %.0f s; exiting so the app starts a fresh UI",
                        ", ".join(missing), LOAD_DEADLINE)
            os._exit(3)

    def read(self) -> None:
        while True:
            try:
                message = self.conn.recv()
            except (EOFError, OSError):
                log.info("app connection closed; exiting")
                os._exit(0)               # the app is gone
            try:
                self.handle(message)
            except Exception:             # one bad event must not stop the UI
                log.exception("handling %s failed", message.get("event"))

    def handle(self, message: dict) -> None:
        event = message.get("event")
        if event != "state":
            log.info("event %s", event)
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
            log.info("panel: open -> close")
            self.hide_panel()
            return
        if time.monotonic() - self.last_blur_hide < REOPEN_GUARD:
            log.info("panel: click right after blur-close, staying closed")
            return                      # this click closed it (blur came first)
        s = scale()
        left, top, right, bottom = work_area()
        w, h = self._panel_size()
        hwnd = self._hwnd("panel")
        if not hwnd or not self.windows["panel"].events.loaded.is_set():
            log.info("panel requested before it loaded; opening when ready")
            self.panel_wanted = True
            return
        self.panel_open = True
        self.panel_opened_at = time.monotonic()
        self.js("panel", f"lidfold.appear({json.dumps(self.state)})")
        self.present("panel", right - w - int(MARGIN * s), bottom - h - int(MARGIN * s), w, h, focus=True)

    def hide_panel(self, from_blur: bool = False) -> None:
        if not self.panel_open:
            return
        if from_blur and time.monotonic() - self.panel_opened_at < BLUR_GRACE:
            return
        self.panel_open = False
        if from_blur:
            self.last_blur_hide = time.monotonic()
        self.conceal("panel")

    def _panel_size(self) -> tuple[int, int]:
        """Physical size: the content's height, but never taller than the
        work area (the page scrolls then)."""
        s = scale()
        left, top, right, bottom = work_area()
        h = min(int(self.panel_height * s), bottom - top - 2 * int(MARGIN * s))
        return int(PANEL[0] * s), h

    def fit_panel(self, height: float) -> None:
        height = max(200, min(2000, int(height)))
        if height == self.panel_height:
            return                         # no-op: state updates arrive ~5 times a second
        self.panel_height = height
        if self.panel_open:
            s = scale()
            left, top, right, bottom = work_area()
            w, h = self._panel_size()
            x, y = right - w - int(MARGIN * s), bottom - h - int(MARGIN * s)
            self.ops.put(lambda: (self.windows["panel"].resize(round(w / s), round(h / s)),
                                  self.windows["panel"].move(round(x / s), round(y / s))))

    # ---------------------------------------------------------- HUD/banner
    def show_hud(self, message: dict) -> None:
        s = scale()
        left, top, right, bottom = work_area()
        w, h = int(HUD[0] * s), int(HUD[1] * s)
        x = left + (right - left - w) // 2
        y = top + int((bottom - top) * 0.72) - h // 2
        self.js("hud", f"hud.show({json.dumps(message)}, {json.dumps(self.state.get('hotkey', ''))})")
        self.present("hud", x, y, w, h, focus=False)
        self._later("hud", HUD_SECONDS, lambda: self._fade_out("hud"))

    def show_banner(self, message: dict) -> None:
        s = scale()
        left, top, right, bottom = work_area()
        w, h = int(BANNER[0] * s), int(BANNER[1] * s)
        self.js("banner", f"banner.show({json.dumps(message)})")
        self.present("banner", right - w - int(MARGIN * s), top + int(MARGIN * s), w, h, focus=False)
        self._later("banner", BANNER_SECONDS, lambda: self._fade_out("banner"))

    def _fade_out(self, name: str) -> None:
        self.js(name, f"{name}.hide()")
        time.sleep(0.25)
        self.conceal(name)

    # ----------------------------------------------------- welcome/about
    def open_window(self, name: str) -> None:
        if name in self.windows:
            def op():
                self.windows[name].show()
                try:
                    foreground(self._hwnd(name))
                except Exception:
                    pass
            self.ops.put(op)
            return
        size = WELCOME if name == "welcome" else ABOUT
        s = scale()
        left, top, right, bottom = work_area()
        x = (left + (right - left - int(size[0] * s)) // 2) / s
        y = (top + (bottom - top - int(size[1] * s)) // 2) / s
        window = webview.create_window(f"lidfold {name}", str(WEB / f"{name}.html"), width=size[0],
                                       height=size[1], x=int(x), y=int(y), frameless=True,
                                       easy_drag=False, resizable=False, js_api=Api(self),
                                       background_color=surface_color(window=True))
        self.windows[name] = window

        def after_load():
            self._after_load(name)
            self.js(name, f"{STATE_JS}{json.dumps(self.state)})", replace_state=True)
            try:                            # in front of any other lidfold window
                foreground(self._hwnd(name))
            except Exception:
                pass

        # Handlers only queue (see `loaded`).
        window.events.loaded += lambda: self.ops.put(after_load)
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


def watch_app() -> None:
    """Exit when the app that started us exits, whatever the UI is doing.
    (UIs stuck in a blocking call once outlived their app as orphans.)"""
    pid = int(os.environ.get(APP_PID_ENV) or 0)
    if not pid:
        return
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        log.info("app %s already gone; exiting", pid)
        os._exit(0)

    def wait():
        ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        log.info("app %s exited; ui exiting", pid)
        os._exit(0)

    threading.Thread(target=wait, daemon=True, name="watch-app").start()


def web_profile() -> Path:
    """A fresh WebView2 profile folder for this UI process.

    A WebView2 browser process can outlive a UI that was killed, still
    holding the UI's profile. A new UI on the same profile attaches to that
    orphan and its pages never load, so tray clicks did nothing. So each UI
    gets its own folder. In the background, orphaned browsers on older
    lidfold profiles are ended and their folders removed.

    (Not private_mode: that builds a temp profile and deletes old ones on
    the GUI thread, which kept it busy for many seconds.)"""
    from ..config import data_dir
    root = data_dir()
    current = root / f"webview-{os.getpid()}"
    threading.Thread(target=_clean_profiles, args=(root, current), daemon=True,
                     name="clean-profiles").start()
    return current


def _clean_profiles(root: Path, current: Path) -> None:
    import shutil
    import subprocess
    # Browser processes name their profile in --user-data-dir (WebView2 adds
    # an EBWebView subfolder). Only lidfold makes folders under root\webview*.
    quote = lambda path: str(path).replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | ForEach-Object { "
        "if ($_.CommandLine -match '--user-data-dir=\"?([^\"]+)') { $d = $Matches[1]; "
        f"if ($d -like '{quote(root)}\\webview*' -and $d -notlike '{quote(current)}\\*') "
        "{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                       timeout=30, creationflags=0x08000000)       # CREATE_NO_WINDOW
    except (OSError, subprocess.SubprocessError):
        log.exception("ending orphaned WebView2 processes failed")
    for old in root.glob("webview*"):
        if old != current and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)


def free_port() -> int:
    """A free localhost port for pywebview's page server.

    Outside private mode pywebview serves every app on the fixed port 42001.
    Windows lets a second server bind it too, and the pages then come from
    whichever process owns it: after an update the panel showed the old
    install's markup, and any other pywebview app could serve pages that
    reach this UI's API."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    log.info("ui starting")
    watch_app()
    ui = Ui()
    ui.create()
    webview.start(ui.started, private_mode=False, storage_path=str(web_profile()),
                  http_port=free_port())
