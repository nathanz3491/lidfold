"""Install lidfold as a Windows app for the current user (no admin needed).

    %LOCALAPPDATA%\\Programs\\lidfold\\
        venv\\        its own interpreter with a regular (non-editable) install,
                     so editing or switching branches in the source checkout
                     cannot break the running app
        bin\\lidfold.cmd   the CLI; only this folder goes on PATH, not the venv's
                         python/pip
        lidfold.ico

plus a Start Menu shortcut and an HKCU Run entry (start at logon), both
launching the windowless lidfoldw.exe. Calibration and settings stay in
%APPDATA%\\lidfold and are shared with a development checkout.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from .icon import write_ico
from .tray import RUN_KEY, RUN_NAME

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "lidfold"
VENV = APP_DIR / "venv"
BIN = APP_DIR / "bin"
SHORTCUT = (Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows"
            / "Start Menu" / "Programs" / "lidfold.lnk")
ENV_KEY = "Environment"
SHIM = '@"%~dp0..\\venv\\Scripts\\lidfold.exe" %*\r\n'


# ------------------------------------------------------------ PATH helpers
def with_entry(path_value: str, entry: str) -> str:
    """PATH string with `entry` appended once (case-insensitive)."""
    parts = [p for p in path_value.split(";") if p]
    if any(os.path.normcase(p.rstrip("\\")) == os.path.normcase(entry.rstrip("\\")) for p in parts):
        return ";".join(parts)
    return ";".join(parts + [entry])


def without_entry(path_value: str, entry: str) -> str:
    target = os.path.normcase(entry.rstrip("\\"))
    return ";".join(p for p in path_value.split(";") if p and os.path.normcase(p.rstrip("\\")) != target)


def _user_path() -> tuple[str, int]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY) as key:
        try:
            return winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "", winreg.REG_EXPAND_SZ


def _set_user_path(value: str, kind: int) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)
    # Tell Explorer (and so newly opened terminals) that the environment changed.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)


# --------------------------------------------------------------- pieces
def _source_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "pyproject.toml").exists():
        raise SystemExit("run `lidfold install` from the lidfold source checkout")
    return root


def _base_python() -> str:
    return getattr(sys, "_base_executable", sys.executable)


def _run(*cmd: str) -> None:
    subprocess.run(cmd, check=True)


def _shortcut(target: Path, args: str, icon: Path) -> None:
    import win32com.client
    SHORTCUT.parent.mkdir(parents=True, exist_ok=True)
    link = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(SHORTCUT))
    link.TargetPath = str(target)
    link.Arguments = args
    link.WorkingDirectory = str(APP_DIR)
    link.IconLocation = str(icon)
    link.Description = "Fold-away desktop effect when the laptop lid closes"
    link.save()


def _set_autostart(command: str | None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def _already_running() -> bool:
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\lidfold-single-instance")
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


# ------------------------------------------------------------- commands
def install(start: bool = True) -> None:
    source = _source_root()
    python = VENV / "Scripts" / "python.exe"
    if not python.exists():
        print(f"creating {VENV}")
        _run(_base_python(), "-m", "venv", str(VENV))
    print("installing dependencies and lidfold (this takes a minute the first time)")
    _run(str(python), "-m", "pip", "install", "-q", "--upgrade", "pip")
    _run(str(python), "-m", "pip", "install", "-q", "-r", str(source / "requirements.lock"))
    _run(str(python), "-m", "pip", "install", "-q", "--upgrade", "--force-reinstall", "--no-deps",
         str(source))

    BIN.mkdir(parents=True, exist_ok=True)
    (BIN / "lidfold.cmd").write_text(SHIM, encoding="ascii")
    value, kind = _user_path()
    if with_entry(value, str(BIN)) != value:
        _set_user_path(with_entry(value, str(BIN)), kind)
        print(f"added {BIN} to your PATH (new terminals will see `lidfold`)")

    icon = write_ico(APP_DIR / "lidfold.ico")
    gui = VENV / "Scripts" / "lidfoldw.exe"
    _shortcut(gui, "run", icon)
    _set_autostart(f'"{gui}" run')
    print(f"Start Menu shortcut: {SHORTCUT}")
    print("starts automatically when you sign in")

    if start:
        if _already_running():
            print("lidfold is already running: Quit it from the tray, then start "
                  "lidfold from the Start Menu to switch to the installed copy.")
        else:
            subprocess.Popen([str(gui), "run"], creationflags=0x00000008,   # DETACHED_PROCESS
                             cwd=str(APP_DIR), close_fds=True)
            print("started lidfold (see the tray icon)")


def uninstall(purge: bool = False) -> None:
    _set_autostart(None)
    if SHORTCUT.exists():
        SHORTCUT.unlink()
    value, kind = _user_path()
    if without_entry(value, str(BIN)) != value:
        _set_user_path(without_entry(value, str(BIN)), kind)
    if _already_running():
        print("lidfold is running: Quit it from the tray first, then run uninstall again "
              "to remove the program files.")
    elif APP_DIR.exists():
        shutil.rmtree(APP_DIR, ignore_errors=True)
    if purge:
        from .config import data_dir
        shutil.rmtree(data_dir(), ignore_errors=True)
        print("removed calibration and settings")
    print("lidfold uninstalled" + ("" if purge else " (calibration and settings kept)"))
