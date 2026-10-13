"""Tray icon artwork, drawn with Pillow (no image files to ship).

A laptop seen from the front with its lid half closed toward the viewer:
the lid's screen glows in a blue-violet gradient with a soft highlight
band, the "folding" desktop. The locked variant greys the lid out and adds
a padlock. Drawn at 256 px and downsampled, so it stays crisp at 16-32 px
on both light and dark taskbars (dark outline, bright fill).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 256
OUTLINE = (24, 27, 33, 255)


def _gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        for x in range(w):
            px[x, y] = c
    return img


def _laptop(locked: bool) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Base: a shallow slab in perspective.
    base = [(22, 196), (234, 196), (250, 228), (6, 228)]
    d.polygon(base, fill=(214, 219, 227, 255), outline=OUTLINE, width=8)
    d.line([(96, 214), (160, 214)], fill=(150, 156, 166, 255), width=8)      # touchpad lip

    # Lid tilted toward the viewer: wider at the top than at the hinge.
    lid = [(40, 190), (216, 190), (238, 30), (18, 30)]
    if locked:
        top, bottom = (132, 138, 150, 255), (88, 93, 104, 255)
    else:
        top, bottom = (118, 92, 255, 255), (64, 148, 255, 255)
    fill = _gradient((S, S), top, bottom)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(lid, fill=255)
    img.paste(fill, (0, 0), mask)

    # A soft highlight band: the desktop swelling and blurring as it folds.
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((60, 70, 196, 132), fill=(255, 255, 255, 120 if not locked else 60))
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    img.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S)), mask))
    d.polygon(lid, outline=OUTLINE, width=10)

    if locked:
        # Padlock badge, bottom-right.
        d.rounded_rectangle((150, 132, 246, 222), radius=14, fill=(255, 170, 40, 255), outline=OUTLINE, width=8)
        d.arc((168, 86, 228, 150), start=180, end=360, fill=OUTLINE, width=26)
        d.arc((168, 86, 228, 150), start=180, end=360, fill=(255, 170, 40, 255), width=12)
        d.ellipse((188, 162, 208, 182), fill=OUTLINE)
    return img


@lru_cache(maxsize=8)   # the tray refreshes often; drawing takes ~50 ms
def tray_icon(locked: bool = False, size: int = 64) -> Image.Image:
    return _laptop(locked).resize((size, size), Image.LANCZOS)


def write_ico(path: Path) -> Path:
    """Multi-size .ico for the Start Menu shortcut."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _laptop(False).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path
