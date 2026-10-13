"""Absolute lid angle from what the room looks like.

During calibration, one sweep passes through every lid angle. A keyframe is
kept every SPACING degrees, tagged with its angle. At runtime, matching the
current view against the keyframes nearest the current estimate gives an
absolute angle: keyframe angle + residual shift.

Matches are only trusted when strong. A search across *all* keyframes is
deliberately not done: on a real recording it locked onto a wrong keyframe
(a calibration frame full of the user's shirt) and jumped the angle from
0 to 44 deg with the lid at its normal position. This only works while the
room looks as it did at calibration, so the calibration sweep should be
made with the user out of view.
"""
from __future__ import annotations

import numpy as np

from .odometry import TileMatcher

SPACING = 3.0          # degrees between keyframes
LOCAL_WINDOW = 12.0    # search keyframes within this many degrees of the estimate
MAX_CANDIDATES = 3
MIN_AGREE = 0.6
MIN_RESPONSE = 0.35
MAX_SHIFT_PX = 30.0


class SceneMap:
    def __init__(self, keys: np.ndarray, phis: np.ndarray):
        self.keys = np.asarray(keys, np.uint8)          # (K, H, W) greyscale
        self.phis = np.asarray(phis, np.float64)        # (K,)
        self._float = [k.astype(np.float32) for k in self.keys]

    def __len__(self) -> int:
        return len(self.phis)

    @classmethod
    def build(cls, grays: list[np.ndarray], phis: list[float]) -> "SceneMap":
        """Keep one frame per SPACING degrees of the sweep."""
        keys, kept = [], []
        for gray, phi in zip(grays, phis):
            if all(abs(phi - k) >= SPACING for k in kept):
                keys.append(np.clip(gray, 0, 255).astype(np.uint8))
                kept.append(phi)
        if not keys:
            return cls(np.zeros((0, 1, 1), np.uint8), np.zeros(0))
        return cls(np.stack(keys), np.array(kept))

    def locate(self, gray: np.ndarray, phi_estimate: float,
               matcher: TileMatcher) -> tuple[float | None, float]:
        """(absolute angle or None, match response), near the current estimate."""
        if not len(self):
            return None, 0.0
        distance = np.abs(self.phis - phi_estimate)
        candidates = [i for i in np.argsort(distance)[:MAX_CANDIDATES] if distance[i] <= LOCAL_WINDOW]
        best, best_response = None, 0.0
        for i in candidates:
            shift, _, agree, response = matcher.match(self._float[i], gray)
            if (shift is None or agree < MIN_AGREE or response < MIN_RESPONSE
                    or abs(shift) > MAX_SHIFT_PX):
                continue
            if response > best_response:
                best, best_response = float(self.phis[i] + matcher.degrees(shift)), response
        return best, best_response
