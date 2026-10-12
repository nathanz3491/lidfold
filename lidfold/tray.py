"""System-tray control: lock/unlock, effect and settings menus, tools.

The tray runs on its own thread (pystray). Menu actions never touch the
camera or OpenGL directly: they update `Controller`, which records what
changed, and the render loop on the main thread applies it. That loop owns
the GL context and the camera.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import winreg
from pathlib import Path

import pystray

from .config import Settings, data_dir
from .icon import tray_icon

EFFECTS = [("duo", "Duo"), ("ghost", "Ghost"), ("flat", "Flat"), ("iris", "Iris")]
BLUR = [(0.0, "Off"), (0.35, "Soft"), (0.65, "Mac Duo default"), (0.9, "Strong")]
SHADOW = [(0.0, "Off"), (0.35, "Light"), (0.65, "Mac Duo default"), (0.9, "Deep")]
CLEAR = [(1.0, "1 s"), (2.0, "2 s"), (3.0, "3 s"), (5.0, "5 s"), (0.0, "Never")]
CAMERAS = range(4)
ACTION = [100.0, 90.0, 80.0, 70.0, 65.0, 60.0, 55.0, 50.0]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "lidfold"
CREATE_NO_WINDOW = 0x08000000


def _python_console() -> str:
    """python.exe next to the running interpreter (pythonw has no stdout)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console if console.exists() else exe)


def startup_command() -> str:
    exe = Path(sys.executable)
    launcher = exe.with_name("lidfoldw.exe")
    if launcher.exists():
        return f'"{launcher}" run'
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m lidfold run'


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


class Controller:
    """Shared state between the tray thread and the render loop."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._changes: set[str] = set()
        self.quit_requested = threading.Event()
        self.camera_idle = threading.Event()   # set by the render loop while it holds no camera
        self.busy: str | None = None        # label of a running tool
        self.calibrated_start_deg: float | None = None   # set by the render loop
        self.status = "starting"
        self.on_change = lambda: None
        self.notify = lambda message, title="lidfold": None

    # ------------------------------------------------------- render loop
    def take_changes(self) -> set[str]:
        with self._lock:
            changes, self._changes = self._changes, set()
            return changes

    @property
    def active(self) -> bool:
        return not self.settings.locked and self.busy is None

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.on_change()

    # ------------------------------------------------------- tray actions
    def _change(self, *what: str) -> None:
        self.settings.save()
        with self._lock:
            self._changes.update(what)
        self.on_change()

    def toggle_lock(self) -> None:
        self.settings.locked = not self.settings.locked
        self._change("lock")

    def set_effect(self, effect: str) -> None:
        self.settings.effect = effect
        self._change("style", "tracker")

    def set_blur(self, value: float) -> None:
        self.settings.blur = value
        self._change("style")

    def set_shadow(self, value: float) -> None:
        self.settings.shadow = value
        self._change("style")

    def set_clear_after(self, seconds: float) -> None:
        self.settings.clear_after = seconds
        self._change("tracker")

    def set_action_angle(self, degrees: float | None) -> None:
        self.settings.action_angle = degrees
        self._change("action")

    def set_camera(self, index: int) -> None:
        self.settings.camera = index
        self._change("camera")

    def launch(self, label: str, args: list[str], reload_model: bool = False) -> None:
        """Run a lidfold tool in its own process. The render loop pauses and
        releases the camera while it runs."""
        if self.busy is not None:
            self.notify(f"{self.busy} is still running.")
            return
        self.busy = label
        self._change("camera")

        def wait() -> None:
            try:
                # The camera is usually exclusive: start the tool only once the
                # render loop has actually closed it.
                self.camera_idle.wait(timeout=5.0)
                proc = subprocess.run([_python_console(), "-m", "lidfold", *args],
                                      capture_output=True, text=True,
                                      creationflags=CREATE_NO_WINDOW)
                lines = [l for l in (proc.stdout + proc.stderr).splitlines()
                         if l.strip() and "WARN" not in l]
                tail = lines[-1] if lines else ""
                if proc.returncode == 0:
                    self.notify(tail or f"{label} finished.", label)
                else:
                    self.notify(f"{label} stopped. {tail}", label)
            finally:
                self.busy = None
                self._change("camera", *(("model",) if reload_model else ()))

        threading.Thread(target=wait, daemon=True, name=f"tool-{label}").start()

    def quit(self) -> None:
        self.quit_requested.set()
        self.on_change()


class Tray:
    def __init__(self, ctl: Controller):
        self.ctl = ctl
        self.icon = pystray.Icon("lidfold", tray_icon(ctl.settings.locked), self._title(),
                                 menu=self._menu())
        ctl.on_change = self.refresh
        ctl.notify = self.notify

    def _title(self) -> str:
        effect = dict(EFFECTS).get(self.ctl.settings.effect, self.ctl.settings.effect)
        return f"lidfold - {effect} - {self.ctl.status}"

    def _menu(self) -> pystray.Menu:
        ctl, s = self.ctl, self.ctl.settings

        def radio(options, current, setter):
            # pystray rejects actions with more than (icon, item) parameters,
            # so bind each option's value in a factory instead of a default arg.
            def act(value):
                return lambda icon, item: setter(value)

            def is_current(value):
                return lambda item: current() == value

            return pystray.Menu(*(
                pystray.MenuItem(label, act(value), checked=is_current(value), radio=True)
                for value, label in options))

        return pystray.Menu(
            pystray.MenuItem(lambda item: self._title(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Locked (effect and camera off)", lambda icon, item: ctl.toggle_lock(),
                             checked=lambda item: s.locked, default=True),
            pystray.MenuItem("Effect", radio([(k, l) for k, l in EFFECTS], lambda: s.effect, ctl.set_effect)),
            pystray.MenuItem("Blur", radio(BLUR, lambda: s.blur, ctl.set_blur)),
            pystray.MenuItem("Shading", radio(SHADOW, lambda: s.shadow, ctl.set_shadow)),
            pystray.MenuItem("Starts at (degree of action)", self._action_menu()),
            pystray.MenuItem("Clear when the lid is still",
                             radio(CLEAR, lambda: s.clear_after, ctl.set_clear_after)),
            pystray.MenuItem("Camera", radio([(i, f"Camera {i}") for i in CAMERAS],
                                             lambda: s.camera, ctl.set_camera)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Calibrate...", lambda icon, item: ctl.launch(
                "Calibration", ["calibrate"], reload_model=True)),
            pystray.MenuItem("Live debug view...", lambda icon, item: ctl.launch("Debug view", ["debug"])),
            pystray.MenuItem("Record a 15 s clip...", lambda icon, item: ctl.launch(
                "Recording", ["record", "--seconds", "15"])),
            pystray.MenuItem("Preview the effect", lambda icon, item: ctl.launch(
                "Preview", ["--effect", s.effect, "simulate", "--seconds", "8"])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with Windows", lambda icon, item: set_startup(not startup_enabled()),
                             checked=lambda item: startup_enabled()),
            pystray.MenuItem("Open settings folder", lambda icon, item: os.startfile(data_dir())),
            pystray.MenuItem("Quit", lambda icon, item: ctl.quit()),
        )

    def _action_menu(self) -> pystray.Menu:
        ctl, s = self.ctl, self.ctl.settings

        def calibrated(item) -> str:
            deg = ctl.calibrated_start_deg
            return "Calibrated start" + (f" (~{deg:.0f} deg)" if deg is not None else "")

        def act(value):
            return lambda icon, item: ctl.set_action_angle(value)

        def is_current(value):
            return lambda item: s.action_angle == value

        def allowed(value):
            # The effect needs room to build before its maximum at the full stop.
            return lambda item: value >= s.full_angle + 5

        return pystray.Menu(
            pystray.MenuItem(calibrated, act(None), checked=is_current(None), radio=True),
            *(pystray.MenuItem(f"{deg:.0f} deg", act(deg), checked=is_current(deg), radio=True,
                               enabled=allowed(deg)) for deg in ACTION))

    def start(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def refresh(self) -> None:
        self.icon.icon = tray_icon(self.ctl.settings.locked)
        self.icon.title = self._title()
        self.icon.update_menu()

    def notify(self, message: str, title: str = "lidfold") -> None:
        try:
            self.icon.notify(message[:250], title)
        except Exception:   # notifications are best-effort
            pass

    def stop(self) -> None:
        self.icon.stop()
