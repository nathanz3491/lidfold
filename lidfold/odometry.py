"""Lid pitch from scene motion: the camera as a tilt sensor.

The camera rotates with the lid about the hinge axis, which is parallel to
the image rows, so tilting the lid slides the scene vertically. Closing the
lid tilts the camera down and moves the scene up.

Consecutive frames are compared with phase correlation on a 4x2 tile grid
over the upper two-thirds of a 320x240 frame (the base, rising from the
bottom, moves differently). A response-weighted median combines the tiles,
so a person moving in front of the camera, or the near desk sliding faster
than the far wall (parallax), cannot drag the estimate.

The thresholds were tuned on a recorded 15 s slow close (~1 px of shift per
frame). Stricter filters (bigger deadzone, run confirmation, 80 % tile
agreement) booked only 30 deg of the ~110 deg sweep; these book 112.
Accumulated drift is not handled here: scenemap.py and the base edge give
absolute corrections.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

ODO_W, ODO_H = 320, 240
GRID = (4, 2)               # tiles across, down (top two thirds of the frame)
MIN_RESPONSE = 0.2          # phase-correlation peak strength to trust a tile
MIN_TILES = 3
AGREEMENT = 0.5             # fraction of trusted tiles within tolerance of the median
TOLERANCE = 0.5             # ...relative (parallax), with a 0.5 px floor
DEADZONE_PX = 0.25
MOVING_PX = 0.6             # a tile shifting at least this much is "moving"


@dataclass
class Motion:
    degrees: float          # +: lid closed further since the previous frame
    tiles: int              # trusted tiles
    agree: float


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, cum[-1] / 2)])


def to_gray(frame_bgr: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame_bgr, (ODO_W, ODO_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)


class TileMatcher:
    """Vertical shift between two grey frames over the tile grid."""

    def __init__(self, vfov_deg: float = 55.0):
        # Units only need to be consistent: calibration maps them onto the
        # user's own start angle and base-edge heights.
        self.focal = (ODO_H / 2) / math.tan(math.radians(vfov_deg) / 2)
        tw, th = ODO_W // GRID[0], int(ODO_H * 2 / 3) // GRID[1]
        self.tiles = [(x * tw, y * th, tw, th) for y in range(GRID[1]) for x in range(GRID[0])]
        self.window = cv2.createHanningWindow((tw, th), cv2.CV_32F)

    def degrees(self, px: float) -> float:
        return -math.degrees(math.atan(px / self.focal))

    def match(self, a: np.ndarray, b: np.ndarray) -> tuple[float | None, int, float, float]:
        """(shift px or None, trusted tiles, agreement, median response)."""
        values, weights = [], []
        for x, y, w, h in self.tiles:
            ta, tb = a[y:y + h, x:x + w], b[y:y + h, x:x + w]
            if ta.std() < 2.0 or tb.std() < 2.0:      # blank wall / darkness: no signal
                continue
            # Copies: OpenCV 5's phaseCorrelate applies the window to its
            # inputs in place, which would corrupt stored frames and keyframes.
            (_, dy), response = cv2.phaseCorrelate(ta.copy(), tb.copy(), self.window)
            if response >= MIN_RESPONSE:
                values.append(dy)
                weights.append(response)
        if len(values) < MIN_TILES:
            return None, len(values), 0.0, 0.0
        v, w = np.array(values), np.array(weights)
        shift = weighted_median(v, w)
        moving = np.abs(v) >= MOVING_PX
        if abs(shift) < MOVING_PX and moving.sum() >= MIN_TILES:
            # The hand holding the lid moves *with* the camera and looks
            # static, outvoting the room. If the moving tiles agree among
            # themselves, they are the world.
            candidate = weighted_median(v[moving], w[moving])
            if np.mean(np.abs(v[moving] - candidate) <= max(0.5, TOLERANCE * abs(candidate))) >= 0.75:
                v, w, shift = v[moving], w[moving], candidate
        agree = float(np.mean(np.abs(v - shift) <= max(0.5, TOLERANCE * abs(shift))))
        return shift, len(values), agree, float(np.median(w))


def _half(gray: np.ndarray) -> np.ndarray:
    return cv2.resize(gray, (ODO_W // 2, ODO_H // 2), interpolation=cv2.INTER_AREA)


class HalfMatcher(TileMatcher):
    """The same tile grid at half resolution."""

    def __init__(self):
        super().__init__()
        tw, th = ODO_W // 2 // GRID[0], int(ODO_H // 2 * 2 / 3) // GRID[1]
        self.tiles = [(x * tw, y * th, tw, th) for y in range(GRID[1]) for x in range(GRID[0])]
        self.window = cv2.createHanningWindow((tw, th), cv2.CV_32F)


class PitchOdometry(TileMatcher):
    def __init__(self, vfov_deg: float = 55.0):
        super().__init__(vfov_deg)
        self.half = HalfMatcher()
        self.reset()

    def reset(self) -> None:
        self.prev: np.ndarray | None = None

    def update(self, frame_bgr: np.ndarray, gray: np.ndarray | None = None) -> Motion:
        gray = to_gray(frame_bgr) if gray is None else gray
        prev, self.prev = self.prev, gray
        if prev is None:
            return Motion(0.0, 0, 0.0)
        shift, tiles, agree, _ = self.match(prev, gray)
        if shift is None:
            # Fast moves (opening the lid briskly) blur the frame and can shift
            # it beyond a tile's reach; half resolution sees both better.
            half_shift, tiles, agree, _ = self.half.match(_half(prev), _half(gray))
            shift = None if half_shift is None else 2.0 * half_shift
        if shift is None or agree < AGREEMENT or abs(shift) < DEADZONE_PX:
            return Motion(0.0, tiles, agree)
        return Motion(self.degrees(shift), tiles, agree)
