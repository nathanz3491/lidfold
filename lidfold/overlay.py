"""Full-screen click-through overlay that redraws the desktop through a shader.

Windows-specific pieces:
* The window is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE), so
  Desktop Duplication keeps seeing the real desktop *under* it and the
  effect can follow a live desktop without capturing itself.
* WS_EX_LAYERED | WS_EX_TRANSPARENT makes it click-through, WS_EX_NOACTIVATE
  keeps focus where it was, WS_EX_TOOLWINDOW keeps it off the taskbar.
* It is hidden whenever the effect is clear, so it costs nothing at rest.
"""
from __future__ import annotations

import ctypes
import math
import threading
import time

import dxcam
import glfw
import moderngl
import numpy as np
import win32con
import win32gui

from . import shaders
from .fold import GHOST_REFERENCE_DEG, PERSPECTIVE, Visual

WDA_EXCLUDEFROMCAPTURE = 0x11


class DesktopSource:
    """Desktop Duplication in a thread while the overlay is up."""

    def __init__(self, fps: int = 30):
        self.camera = dxcam.create(output_idx=0, output_color="BGRA")
        self.fps = fps
        self.frame: np.ndarray | None = None
        self.serial = 0
        self._lock = threading.Lock()
        self._active = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="desktop").start()

    def grab_now(self) -> None:
        # None means nothing changed since the previous grab, so the cached
        # frame is still current.
        with self._lock:
            frame = self.camera.grab()
            if frame is not None:
                self.frame, self.serial = frame, self.serial + 1

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self.frame, self.serial

    def set_active(self, active: bool) -> None:
        (self._active.set if active else self._active.clear)()

    def _run(self) -> None:
        period = 1.0 / self.fps
        while True:
            self._active.wait()
            t0 = time.perf_counter()
            self.grab_now()
            time.sleep(max(0.0, period - (time.perf_counter() - t0)))


class Overlay:
    def __init__(self, effect: str = "duo", blur: float = 0.7, shadow: float = 0.6):
        if not glfw.init():
            raise RuntimeError("GLFW init failed")
        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)
        self.width, self.height = mode.size.width, mode.size.height
        mx, my = glfw.get_monitor_pos(monitor)
        for hint, value in ((glfw.CONTEXT_VERSION_MAJOR, 3), (glfw.CONTEXT_VERSION_MINOR, 3),
                            (glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE),
                            (glfw.DECORATED, False), (glfw.FLOATING, True),
                            (glfw.VISIBLE, False), (glfw.FOCUS_ON_SHOW, False),
                            (glfw.RESIZABLE, False), (glfw.AUTO_ICONIFY, False)):
            glfw.window_hint(hint, value)
        # Windowed, not exclusive fullscreen: no mode switch, DWM composes it.
        self.window = glfw.create_window(self.width, self.height, "lidfold", None, None)
        glfw.set_window_pos(self.window, mx, my)
        glfw.make_context_current(self.window)
        glfw.swap_interval(1)

        self.hwnd = glfw.get_win32_window(self.window)
        ex = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        ex |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST
               | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE)
        win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, ex)
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 255, win32con.LWA_ALPHA)
        if not ctypes.windll.user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE):
            raise RuntimeError("SetWindowDisplayAffinity failed (needs Windows 10 2004+)")

        self.ctx = moderngl.create_context()
        self.program = self.ctx.program(vertex_shader=shaders.VERTEX,
                                        fragment_shader=shaders.FRAGMENT)
        self.vao = self.ctx.vertex_array(self.program, [])
        self.texture = self.ctx.texture((self.width, self.height), 4)
        self.texture.swizzle = "BGRA"
        self.texture.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        self.texture.repeat_x = self.texture.repeat_y = False
        self.program["desktop"] = 0
        self.program["aspect"] = self.width / self.height
        self.program["reference"] = math.radians(GHOST_REFERENCE_DEG)
        self.program["perspective"] = PERSPECTIVE
        self.set_style(effect, blur, shadow)

        self.source = DesktopSource()
        self.uploaded = -1
        self.visible = False

    def set_style(self, effect: str, blur: float, shadow: float) -> None:
        self.program["effect"] = shaders.EFFECT_INDEX.get(effect, 0)
        self.program["blur"] = blur
        self.program["shadow"] = shadow

    def _upload(self) -> None:
        frame, serial = self.source.latest()
        if frame is None or serial == self.uploaded:
            return
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            return   # resolution changed mid-run; keep the last good texture
        self.texture.write(np.ascontiguousarray(frame))
        self.texture.build_mipmaps()
        self.uploaded = serial

    def show(self) -> None:
        if self.visible:
            return
        # The first visible frame must match the desktop exactly, so take a
        # fresh capture before the window appears.
        self.source.grab_now()
        self._upload()
        self.source.set_active(True)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        self.visible = True

    def hide(self) -> None:
        if not self.visible:
            return
        self.source.set_active(False)
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
        self.visible = False

    def render(self, visual: Visual) -> None:
        self._upload()
        self.program["progress"] = visual.progress
        self.program["defocus"] = visual.defocus
        self.program["tilt"] = visual.tilt
        self.program["anchor"] = visual.anchor
        self.texture.use(0)
        self.ctx.viewport = (0, 0, self.width, self.height)
        self.vao.render(moderngl.TRIANGLES, vertices=3)
        glfw.swap_buffers(self.window)

    def pump(self) -> None:
        glfw.poll_events()

    def close(self) -> None:
        self.hide()
        glfw.terminate()
