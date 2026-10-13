"""Bridge between the lidfold app and its companion UI process.

The UI (popover panel, HUD, welcome and about windows) runs in its own
process: the render loop owns the main thread for OpenGL, and GUI toolkits
want it too. A separate process also means a UI problem can never stop the
effect. They talk over a named pipe authenticated with a per-run random key,
which is handed to the child through its environment.

App -> UI:  {"event": "state", "state": {...}}  about 5 times a second, on change
            {"event": "show_panel"} / {"event": "hud", ...} / {"event": "toast", ...}
            {"event": "open", "name": "welcome"|"about"} / {"event": "quit"}
UI -> App:  {"cmd": "hello", "pid": ...} / {"cmd": "set", "key": ..., "value": ...}
            {"cmd": "toggle_lock"} / {"cmd": "launch", "tool": ...}
            {"cmd": "startup", "value": bool} / {"cmd": "open_folder"} / {"cmd": "quit"}
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Client, Listener

from . import __version__
from . import log as logs
from .config import EFFECT_NAMES, FPS_CHOICES

log = logs.get("bridge")

PIPE_ENV, KEY_ENV, APP_PID_ENV = "LIDFOLD_UI_PIPE", "LIDFOLD_UI_KEY", "LIDFOLD_APP_PID"
PUSH_EVERY = 0.2
CREATE_NO_WINDOW = 0x08000000
HOTKEY_LABEL = "Ctrl+Alt+Shift+F"
TOOLS = {
    "calibrate": ("Calibration", ["calibrate"], True),
    "debug": ("Debug view", ["debug"], False),
    "record": ("Recording", ["record", "--seconds", "15"], False),
}


def _float_in(value, low: float, high: float) -> float:
    value = float(value)
    if not low <= value <= high:
        raise ValueError(value)
    return value


class UiBridge:
    def __init__(self, ctl, spawn: bool = True):
        self.ctl = ctl
        self.key = os.urandom(32)
        self.address = rf"\\.\pipe\lidfold-ui-{os.getpid()}-{os.urandom(4).hex()}"
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.key)
        self.spawn_ui = spawn
        self._conn = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self.ui_pid: int | None = None
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.welcome_pending = False
        self._last_state: dict | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        threading.Thread(target=self._accept_loop, daemon=True, name="ui-accept").start()
        threading.Thread(target=self._push_loop, daemon=True, name="ui-push").start()
        if self.spawn_ui:
            self._spawn()

    def _spawn(self) -> None:
        env = dict(os.environ, **{PIPE_ENV: self.address, KEY_ENV: self.key.hex(),
                                  APP_PID_ENV: str(os.getpid())})
        self.proc = subprocess.Popen([sys.executable, "-m", "lidfold", "ui"], env=env,
                                     creationflags=CREATE_NO_WINDOW)
        log.info("spawned ui process %s (%s)", self.proc.pid, sys.executable)

    def stop(self) -> None:
        self._stop.set()
        self._send({"event": "quit"})
        self.close()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def close(self) -> None:
        try:
            self.listener.close()
        except OSError:
            pass

    @property
    def connected(self) -> bool:
        return self._conn is not None

    # ------------------------------------------------------------- traffic
    def _send(self, message: dict) -> bool:
        with self._send_lock:
            if self._conn is None:
                return False
            try:
                self._conn.send(message)
                return True
            except (OSError, EOFError, ValueError):
                self._conn = None
                return False

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn = self.listener.accept()
            except Exception:              # a bad key or a dropped connection attempt
                if self._stop.is_set():
                    return
                continue
            with self._send_lock:
                self._conn = conn
            log.info("ui connected")
            self._last_state = None
            self._read(conn)
            log.warning("ui disconnected")
            with self._send_lock:
                if self._conn is conn:
                    self._conn = None
            # The UI went away. Bring it back (a few times) unless we are quitting.
            if not self._stop.is_set() and self.spawn_ui and self.restarts < 3:
                self.restarts += 1
                time.sleep(1.0)
                self._spawn()

    def _read(self, conn) -> None:
        while not self._stop.is_set():
            try:
                message = conn.recv()
            except (EOFError, OSError):
                return
            try:
                self.handle(message)
            except Exception as exc:       # never let a bad message kill the bridge
                log.warning("rejected ui request %r: %s", message, exc)
                self._send({"event": "toast", "title": "lidfold", "body": f"Ignored request: {exc}",
                            "kind": "warning"})

    def _push_loop(self) -> None:
        while not self._stop.wait(PUSH_EVERY):
            state = self.state()
            if state != self._last_state and self._send({"event": "state", "state": state}):
                self._last_state = state

    # --------------------------------------------------------------- state
    def state(self) -> dict:
        c, s = self.ctl, self.ctl.settings
        return {
            "version": __version__, "locked": s.locked, "effect": s.effect,
            "blur": s.blur, "shadow": s.shadow, "clear_after": s.clear_after,
            "camera": s.camera, "camera_fps": s.camera_fps, "fps_choices": list(FPS_CHOICES),
            "action_angle": s.action_angle, "full_angle": s.full_angle, "max_angle": s.max_angle,
            "calibrated_start_deg": None if c.calibrated_start_deg is None else round(c.calibrated_start_deg),
            "lid_deg": None if c.lid_deg is None else round(c.lid_deg),
            "status": c.status, "busy": c.busy, "calibrated": c.calibrated,
            "startup": c.startup, "hotkey": HOTKEY_LABEL,
        }

    # ------------------------------------------------------------ commands
    def handle(self, message: dict) -> None:
        c = self.ctl
        cmd = message.get("cmd")
        if cmd == "hello":
            self.ui_pid = int(message.get("pid") or 0) or None
            log.info("ui ready (pid %s)", self.ui_pid)
            self._last_state = None
            if self.welcome_pending:
                self.welcome_pending = False
                self.open_window("welcome")
        elif cmd == "toggle_lock":
            c.toggle_lock(hud=False)
        elif cmd == "set":
            key, value = message["key"], message["value"]
            if key == "effect" and value in EFFECT_NAMES:
                c.set_effect(value)
            elif key == "blur":
                c.set_blur(_float_in(value, 0.0, 1.0))
            elif key == "shadow":
                c.set_shadow(_float_in(value, 0.0, 1.0))
            elif key == "clear_after":
                c.set_clear_after(_float_in(value, 0.0, 30.0))
            elif key == "camera":
                c.set_camera(int(_float_in(value, 0, 9)))
            elif key == "camera_fps" and int(value) in FPS_CHOICES:
                c.set_camera_fps(int(value))
            elif key == "action_angle":
                c.set_action_angle(None if value is None else _float_in(value, c.max_deg() + 5, 170.0))
            elif key == "max_angle":
                c.set_max_angle(None if value is None else _float_in(value, 10.0, c.start_deg() - 5))
            elif key == "range":                  # both ends at once, from the range slider
                start, end = value
                if start is None and end is None:
                    c.settings.max_angle = None
                    c.set_action_angle(None)
                else:
                    end = _float_in(end, 10.0, 160.0)
                    start = _float_in(start, end + 5, 170.0)
                    c.settings.max_angle = end
                    c.set_action_angle(start)
            else:
                raise ValueError(f"{key}={value!r}")
        elif cmd == "launch":
            tool = message.get("tool")
            if tool == "preview":
                c.launch("Preview", ["--effect", c.settings.effect, "simulate", "--seconds", "8"])
            elif tool in TOOLS:
                label, args, reload_model = TOOLS[tool]
                c.launch(label, args, reload_model=reload_model)
            else:
                raise ValueError(f"unknown tool {tool!r}")
        elif cmd == "startup":
            c.set_startup(bool(message["value"]))
        elif cmd == "open_folder":
            from .config import data_dir
            os.startfile(data_dir())
        elif cmd == "quit":
            c.quit()
        else:
            raise ValueError(f"unknown command {cmd!r}")

    # -------------------------------------------------------------- events
    def show_panel(self) -> bool:
        if not self.connected:
            log.warning("panel requested but the ui is not connected")
            return False
        # The tray click gave this process the foreground right; pass it on
        # so the panel can take focus (and close itself when it loses it).
        allowed = ctypes.windll.user32.AllowSetForegroundWindow(self.ui_pid or -1)
        sent = self._send({"event": "show_panel"})
        log.info("panel requested: sent=%s foreground-allowed=%s ui_pid=%s", sent, bool(allowed), self.ui_pid)
        return sent

    def hud(self, kind: str, title: str | None = None, body: str | None = None) -> bool:
        return self._send({"event": "hud", "kind": kind, "title": title, "body": body})

    def toast(self, title: str, body: str, kind: str = "info") -> bool:
        return self._send({"event": "toast", "title": title, "body": body, "kind": kind})

    def open_window(self, name: str) -> bool:
        ctypes.windll.user32.AllowSetForegroundWindow(self.ui_pid or -1)
        return self._send({"event": "open", "name": name})

    def show_welcome(self) -> None:
        if not self.open_window("welcome"):
            self.welcome_pending = True       # sent once the UI says hello


def connect_from_env():
    """UI side: connect back to the app that spawned us."""
    return Client(os.environ[PIPE_ENV], family="AF_PIPE", authkey=bytes.fromhex(os.environ[KEY_ENV]))
