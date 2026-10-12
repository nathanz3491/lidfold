"""User settings and calibration storage (%APPDATA%\\lidfold)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def data_dir() -> Path:
    root = os.environ.get("LIDFOLD_HOME") or Path(os.environ.get("APPDATA", Path.home())) / "lidfold"
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    camera: int = 0
    camera_fps: int = 30
    effect: str = "duo"            # duo | ghost | flat | iris
    # Lid held still this long while the effect shows -> clear and rebase (0 = never).
    clear_after: float = 2.0
    still_epsilon: float = 0.025
    blur: float = 0.65             # Mac Duo defaults
    shadow: float = 0.65
    locked: bool = False           # tray lock: effect and camera off

    @classmethod
    def load(cls) -> "Settings":
        path = data_dir() / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> Path:
        path = data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path
