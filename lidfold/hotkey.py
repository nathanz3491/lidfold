"""Global pause/resume hotkey (Ctrl+Alt+Shift+F, after Mac Duo's ctrl-opt-cmd-F).

RegisterHotKey delivers WM_HOTKEY to the registering thread's message
queue, so this runs its own thread with a message loop.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0x1F01


class Hotkey(threading.Thread):
    def __init__(self, callback, key: str = "F",
                 modifiers: int = MOD_CONTROL | MOD_ALT | MOD_SHIFT):
        super().__init__(daemon=True, name="hotkey")
        self.callback, self.vk, self.modifiers = callback, ord(key.upper()), modifiers
        self.registered = threading.Event()
        self.failed = threading.Event()
        self._thread_id = 0

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, self.modifiers | MOD_NOREPEAT, self.vk):
            self.failed.set()                 # taken by another app
            return
        self.registered.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
