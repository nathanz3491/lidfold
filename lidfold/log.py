"""Shared log file: %APPDATA%\\lidfold\\lidfold.log (rotated at 1 MB).

The app and its UI run windowless (pythonw), so without this their errors
vanish. Each process tags its lines with its role.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import data_dir

_configured = False


def get(role: str) -> logging.Logger:
    global _configured
    if not _configured:
        handler = RotatingFileHandler(data_dir() / "lidfold.log", maxBytes=1_000_000, backupCount=2,
                                      encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(asctime)s %(process)d %(name)s %(levelname)s %(message)s"))
        root = logging.getLogger("lidfold")
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        _configured = True
    return logging.getLogger(f"lidfold.{role}")
