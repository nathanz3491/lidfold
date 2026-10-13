"""Fuse odometry, the scene map, the base edge and darkness into one lid angle.

phi is "degrees closed" relative to the lid position at calibration (larger
= more closed). Three cues:

* Frame-to-frame odometry: smooth and immediate, but drifts.
* The scene map (~10 Hz): absolute, from what the room looks like at each
  angle of the calibration sweep. A match is applied only when two lookups
  in a row agree.
* The base edge, when visible: absolute, independent of the room.

The two absolute cues pull the odometry estimate toward them, and darkness
drives the last few degrees. When nothing has moved for REST_AFTER seconds
and neither absolute cue is available, the angle relaxes back to "normal"
(0), so drift can never leave the effect showing at the normal position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .detector import BaseDetector, Reading
from .fold import GHOST_DEFAULT_SPAN
from .odometry import Motion, PitchOdometry, to_gray
from .scenemap import SceneMap

FULL_ANGLE = 45.0         # real lid angle at the calibrated full stop (user-measured)
MIN_ACTION_SPAN = 5.0     # the degree of action stays at least this far above full

# Per-frame pull toward the edge-derived angle: firm at rest, gentle while
# the lid moves fast (edge detection then lags odometry slightly).
ANCHOR_GAIN, ANCHOR_GAIN_MOVING = 0.35, 0.12
MAP_EVERY = 3            # scene-map lookup every n-th frame (~10 Hz)
MAP_GAIN = 0.4
MAP_CONFIRM_DEG = 2.0    # two consecutive scene matches must agree within this
REST_AFTER = 3.0         # s with no motion and no absolute cue before relaxing to 0
REST_TAU = 1.5           # s, time constant of that relaxation
# Reopening: once the base has slid out of view *going down*, or odometry
# reports a brisk opening, the lid is heading back to normal. Fast opening
# blurs frames and odometry under-counts, so relax quickly instead of
# waiting REST_AFTER with the effect still showing.
OPEN_RATE = -15.0        # deg/s of smoothed odometry that counts as opening
OPEN_WINDOW = 1.5        # s after the base left view while falling
OPEN_TAU = 0.4           # s, time constant of the reopening relaxation
DEFAULT_SLOPE = 55.0     # degrees per frame-height of edge travel, if only one pose
SLOPE_RANGE = (20.0, 200.0)
MERGE_HEIGHT = 0.03      # poses closer than this (fraction of frame height) are merged


@dataclass
class AngleCalibration:
    heights: np.ndarray      # base edge height (fraction) at labelled poses
    phis: np.ndarray         # odometry angle at those poses
    phi_start: float         # effect begins here
    phi_full: float          # effect complete here

    @classmethod
    def from_poses(cls, heights, phis, phi_start: float, phi_full: float) -> "AngleCalibration":
        order = np.argsort(heights)
        hs = np.asarray(heights, np.float64)[order]
        ps = np.asarray(phis, np.float64)[order]
        # Merge poses whose edges sit at almost the same height: their angle
        # difference is odometry noise, and would make a near-vertical segment.
        merged_h, merged_p = [], []
        for h, p in zip(hs, ps):
            if merged_h and h - merged_h[-1][-1] < MERGE_HEIGHT:
                merged_h[-1].append(h); merged_p[-1].append(p)
            else:
                merged_h.append([h]); merged_p.append([p])
        hs = np.array([np.mean(g) for g in merged_h])
        ps = np.array([np.mean(g) for g in merged_p])
        # Base height grows with lid angle, so keep the map monotonic.
        ps = np.maximum.accumulate(ps)
        return cls(hs, ps, phi_start, max(phi_full, phi_start + 1.0))

    def slope(self) -> float:
        """Degrees per frame-height of edge travel, least squares over all poses."""
        if self.heights.size < 2:
            return DEFAULT_SLOPE
        fit = float(np.polyfit(self.heights, self.phis, 1)[0])
        return float(np.clip(fit, *SLOPE_RANGE))

    def phi_for_height(self, h: float) -> float:
        hs, ps = self.heights, self.phis
        if hs.size == 1 or h <= hs[0] or h >= hs[-1]:
            i = 0 if h <= hs[0] else -1
            return float(ps[i] + self.slope() * (h - hs[i]))
        return float(np.interp(h, hs, ps))

    @property
    def ghost_span(self) -> float:
        """Degrees from rest to the ghost's maximum: the calibrated full stop."""
        return self.phi_full if self.phi_full >= 15.0 else GHOST_DEFAULT_SPAN

    def closure(self, phi: float) -> float:
        return float(np.clip((phi - self.phi_start) / (self.phi_full - self.phi_start), 0.0, 1.0))


@dataclass
class AngleReading:
    phi: float
    closure: float           # 0 at the start angle .. 1 at full; what the fold follows
    base: Reading
    motion: Motion
    scene_hit: bool = False
    ghost: float = 0.0       # Ghost drive: degrees closed since rest / ghost span
    lid_deg: float = 0.0     # estimated absolute lid angle, degrees

    def drive(self, effect: str) -> float:
        """Ghost follows the rotation since rest, as in Mac Duo; every other
        effect follows the calibrated start->full closure."""
        return self.ghost if effect == "ghost" else self.closure


class LidModel:
    """Everything calibration produces: base detector, angle calibration and
    scene map."""

    def __init__(self, detector: BaseDetector, angles: AngleCalibration, scene: SceneMap):
        self.detector, self.angles, self.scene = detector, angles, scene

    def save(self, path: Path) -> None:
        self.detector.save(path, angle_heights=self.angles.heights, angle_phis=self.angles.phis,
                           phi_start=self.angles.phi_start, phi_full=self.angles.phi_full,
                           scene_keys=self.scene.keys, scene_phis=self.scene.phis)

    @classmethod
    def load(cls, path: Path) -> "LidModel":
        z = np.load(path)
        angles = AngleCalibration(z["angle_heights"], z["angle_phis"],
                                  float(z["phi_start"]), float(z["phi_full"]))
        return cls(BaseDetector.load(path), angles, SceneMap(z["scene_keys"], z["scene_phis"]))


class LidAngle:
    def __init__(self, model: LidModel, action_angle: float | None = None,
                 full_angle: float = FULL_ANGLE, max_angle: float | None = None):
        self.model = model
        self.odometry = PitchOdometry()
        self.configure(action_angle, full_angle, max_angle)
        self.reset()

    # ------------------------------------------------- degree of action
    # Absolute degrees are anchored at the calibrated full stop, whose real
    # angle the user knows (~45 deg). That is where the estimate is most
    # accurate (the base edge pins phi down there); above it, phi adds real
    # degrees from odometry and the scene map.
    def configure(self, action_angle: float | None, full_angle: float = FULL_ANGLE,
                  max_angle: float | None = None) -> None:
        """Degree of action (start) and max effect angle (full), in estimated
        lid degrees; None keeps the calibrated stop. Called from the render
        loop; the attribute stores are atomic."""
        self.full_angle = float(full_angle)
        self.max_phi = self.model.angles.phi_full if max_angle is None else self._phi(max_angle)
        self.action_phi = (None if action_angle is None
                           else min(self.max_phi - MIN_ACTION_SPAN, self._phi(action_angle)))
        self.custom = action_angle is not None or max_angle is not None

    def lid_degrees(self, phi: float) -> float:
        return self.full_angle + (self.model.angles.phi_full - phi)

    def _phi(self, degrees: float) -> float:
        """phi where the estimated lid angle equals `degrees`."""
        return self.model.angles.phi_full + self.full_angle - degrees

    def _span(self, start: float) -> tuple[float, float]:
        start = min(start, self.max_phi - MIN_ACTION_SPAN)
        return start, self.max_phi - start

    @property
    def ghost_tilt_span(self) -> float:
        """Real degrees the ghost tilts over from start to maximum."""
        if not self.custom:
            return self.model.angles.ghost_span
        return self._span(self.action_phi if self.action_phi is not None else 0.0)[1]

    def reset(self) -> None:
        self.phi = 0.0
        self.frame = 0
        self.pending: float | None = None     # last unconfirmed scene match
        self.last_time: float | None = None
        self.last_activity: float | None = None   # motion or an absolute cue
        self.rate = 0.0                       # smoothed odometry, deg/s
        self.base_height: float | None = None # last seen base height (smoothed)
        self.base_falling = False
        self.base_left_at = -1e9              # when the base left view while falling
        self.odometry.reset()
        self.model.detector.reset()

    def _scene_fix(self, gray: np.ndarray) -> bool:
        """Pull phi toward a confirmed scene-map angle. True when applied."""
        self.frame += 1
        if self.frame % MAP_EVERY:
            return False
        located, _ = self.model.scene.locate(gray, self.phi, self.odometry)
        confirmed = (located is not None and self.pending is not None
                     and abs(located - self.pending) <= MAP_CONFIRM_DEG)
        self.pending = located
        if confirmed:
            self.phi += MAP_GAIN * (located - self.phi)
        return confirmed

    def update(self, frame_bgr: np.ndarray, now: float) -> AngleReading:
        dt = 0.0 if self.last_time is None else max(0.0, now - self.last_time)
        self.last_time = now
        base = self.model.detector.read(frame_bgr, now)
        gray = to_gray(frame_bgr)
        motion = self.odometry.update(frame_bgr, gray)
        self.phi += motion.degrees
        scene_hit = self._scene_fix(gray)
        if self.last_activity is None or motion.degrees or scene_hit or base.present:
            self.last_activity = now
        elif now - self.last_activity > REST_AFTER:
            self.phi *= math.exp(-dt / REST_TAU)
        if self._opening(base, motion, dt, now) and not scene_hit:
            self.phi *= math.exp(-dt / OPEN_TAU)
        # Only this frame's own detection anchors: the filtered height lags
        # and is held for a few frames after the base leaves view.
        if base.fresh is not None:
            anchor = self.model.angles.phi_for_height(base.fresh)
            gain = ANCHOR_GAIN_MOVING if abs(motion.degrees) > 0.5 else ANCHOR_GAIN
            self.phi += gain * (anchor - self.phi)
        dark = min(1.0, base.dark_gated * 1.15)
        ramp = lambda start, span: min(1.0, max(0.0, (self.phi - start) / span))
        if not self.custom:
            closure = max(self.model.angles.closure(self.phi), dark)
        else:
            start = self.action_phi if self.action_phi is not None else self.model.angles.phi_start
            closure = max(ramp(*self._span(start)), dark)
        # Ghost follows Mac Duo: it starts the moment the lid leaves rest and
        # tilts by the real rotation since then (phi, in degrees; LidTracker
        # rebases on wherever the lid rests). It peaks at the calibrated full
        # stop, where the user wants the effect biggest (~45 deg), instead of
        # Mac Duo's 100-degree sweep.
        ghost = max(min(1.0, max(0.0, self.phi) / self.model.angles.ghost_span), dark)
        if self.custom:
            # A set start applies to every effect; with only a max angle set,
            # ghost still starts from rest, as in Mac Duo.
            ghost = (closure if self.action_phi is not None
                     else max(ramp(*self._span(0.0)), dark))
        return AngleReading(self.phi, closure, base, motion, scene_hit, ghost,
                            self.lid_degrees(self.phi))

    def _opening(self, base, motion: Motion, dt: float, now: float) -> bool:
        """True while the lid is evidently heading back up with the base out of view."""
        if dt > 0:
            self.rate += (motion.degrees / dt - self.rate) * (1 - math.exp(-dt / 0.25))
        if base.present:
            if self.base_height is not None:
                self.base_falling = base.height < self.base_height - 1e-3
            self.base_height = base.height
            return False
        if self.base_height is not None:          # just left view
            if self.base_falling:
                self.base_left_at = now
            self.base_height, self.base_falling = None, False
        return self.rate < OPEN_RATE or now - self.base_left_at < OPEN_WINDOW
