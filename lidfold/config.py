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


FPS_CHOICES = (10, 15, 20, 30)   # camera analysis rates offered in the UI
# Effects in menu order: (setting value, label). Shader indices live in
# shaders.EFFECT_INDEX.
EFFECTS = (("duo", "Duo"), ("ghost", "Ghost"), ("flat", "Flat"), ("iris", "Iris"),
           ("card", "Card"), ("genie", "Genie"), ("fold", "Fold"), ("glass", "Glass"),
           ("cube", "Cube"), ("curl", "Curl"), ("blinds", "Blinds"), ("doorway", "Doorway"),
           ("reflect", "Reflect"), ("stack", "Stack"), ("twirl", "Twirl"), ("ripple", "Ripple"),
           ("aura", "Aura"), ("island", "Island"), ("prism", "Prism"), ("mosaic", "Mosaic"))
EFFECT_NAMES = tuple(name for name, _ in EFFECTS)


@dataclass
class Settings:
    camera: int = 0
    camera_fps: int = 30           # frames analysed per second (10/15/20/30)
    effect: str = "duo"            # one of EFFECT_NAMES
    # Lid held still this long while the effect shows -> clear and rebase (0 = never).
    clear_after: float = 2.0
    still_epsilon: float = 0.025
    blur: float = 0.65             # Mac Duo defaults
    shadow: float = 0.65
    locked: bool = False           # tray lock: effect and camera off
    # Degree of action: estimated lid angle (deg) at which the effect starts.
    # None = calibrated start stop (ghost: from rest).
    action_angle: float | None = None
    full_angle: float = 45.0       # real lid angle at the calibrated full stop
    # Max effect angle: estimated lid angle (deg) at which the effect is full.
    # None = calibrated full stop.
    max_angle: float | None = None

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
