"""Closure signal -> fold animation state. Pure logic, no I/O.

Adapted from the approach in Mac Duo (github.com/DhananjayBhosale/MacDuo,
MIT): the effect is measured from where the lid last *rested*, not from
fully open, so working with the lid half-closed does not leave the desktop
permanently folded; holding the lid still clears the effect and rebases.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

CLEAR_DURATION = 0.6
MIN_SPAN = 0.15
# Mac Duo's own curves (FoldVisualState.at) run over its whole sweep, from
# the resting angle (105 deg by default) down to 5 deg: a 100-degree span.
# lidfold maps its calibrated start->full range onto that sweep, so every
# Mac Duo curve keeps its shape, compressed into the user's range.
MAC_SPAN = 100.0
PERSPECTIVE = 0.7        # Mac Duo default
# Ghost holds the desktop on the resting plane; Mac Duo's default resting
# angle (its "clears at" setting), clamped to 90-140 deg in the shader.
GHOST_REFERENCE_DEG = 105.0
GHOST_DEADBAND = 0.02     # of the ghost span: ignore ~1 deg of jitter around rest
# Degrees from rest to the ghost's maximum when no calibration says otherwise
# (rest ~105 deg, biggest effect ~45 deg). Normally the calibrated full stop.
GHOST_DEFAULT_SPAN = 60.0


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


@dataclass(frozen=True)
class Visual:
    progress: float = 0.0   # Mac Duo progress: eased 0 .. 1
    defocus: float = 0.0    # optical blur, barely visible at first
    tilt: float = 0.0       # radians, flat effect: desktop lying back
    anchor: float = 0.0     # radians, ghost: lid rotation away from the held plane

    @property
    def is_clear(self) -> bool:
        return self.progress <= 1e-4 and self.defocus <= 1e-4

    @staticmethod
    def at(closure: float, tilt_span: float = MAC_SPAN) -> "Visual":
        """closure 0 .. 1 -> shader parameters. Mac Duo's curve shapes run over
        the closure; ghost's tilt is real degrees: closure * tilt_span."""
        c = min(1.0, max(0.0, closure))
        delta = c * MAC_SPAN                     # Mac-equivalent degrees past rest
        defocus = (0.16 * ease(delta / 15) * ease(delta / 6)
                   + 0.84 * ease((delta - 15) / (MAC_SPAN - 15)))
        geometry = 1 - (1 - c) ** 2
        return Visual(ease(c), min(1.0, defocus), math.radians(80.0 * geometry),
                      math.radians(min(85.0, c * tilt_span)))


class LidTracker:
    """Turns noisy closure readings into a target fold progress."""

    def __init__(self, start: float, full: float, clear_after: float = 2.0,
                 epsilon: float = 0.025, tau: float = 0.06, deadband: float = 0.0):
        self.start, self.full, self.deadband = start, full, deadband
        self.clear_after, self.epsilon, self.tau = clear_after, epsilon, tau
        self.reset()

    def reset(self) -> None:
        self.smoothed: float | None = None
        self.reference = self.start
        self.anchor = 0.0
        self.last_motion = 0.0
        self.last_time: float | None = None
        self.cleared = False

    def update(self, closure: float, now: float) -> tuple[float, bool]:
        """Returns (target progress, clear) where clear asks for a timed fade."""
        dt = 0.0 if self.last_time is None else max(0.0, min(0.5, now - self.last_time))
        self.last_time = now
        if self.smoothed is None:
            self.smoothed = closure
            self.anchor, self.last_motion = closure, now
        else:
            self.smoothed += (closure - self.smoothed) * (1 - math.exp(-dt / self.tau))
        s = self.smoothed

        if abs(s - self.anchor) > self.epsilon:
            self.anchor, self.last_motion, self.cleared = s, now, False
        # Lid opened past where it last rested: follow it back down.
        if s < self.reference:
            self.reference = max(self.start, s)

        progress = self.progress_for(s)
        if progress > 0 and not self.cleared and now - self.last_motion >= self.clear_after:
            self.reference = max(self.start, s)
            self.cleared = True
        if self.cleared:
            return 0.0, True
        return progress, False

    def progress_for(self, s: float) -> float:
        full = min(1.0, max(self.full, self.reference + MIN_SPAN))
        return max(0.0, min(1.0, (s - self.reference - self.deadband) / (full - self.reference)))


def tracker_for(effect: str, clear_after: float, epsilon: float) -> LidTracker:
    """Ghost gets a 1-degree deadband: it is live from rest, unlike the
    effects that wait for the calibrated start angle."""
    deadband = GHOST_DEADBAND if effect == "ghost" else 0.0
    clear_after = clear_after if clear_after > 0 else math.inf     # 0 = never clear
    return LidTracker(0.0, 1.0, clear_after, epsilon, deadband=deadband)


class FoldAnimator:
    """Smooths the target per render frame; timed ease-out when clearing."""

    def __init__(self, tau: float = 0.045, tilt_span: float = MAC_SPAN):
        self.tau, self.tilt_span = tau, tilt_span
        self.progress = 0.0
        self.last_time: float | None = None
        self.clear_from: float | None = None
        self.clear_start = 0.0

    def reset(self) -> None:
        self.__init__(self.tau, self.tilt_span)

    def sample(self, target: float, clear: bool, now: float) -> Visual:
        dt = 0.0 if self.last_time is None else max(0.0, min(0.1, now - self.last_time))
        self.last_time = now
        if clear and self.progress > 0:
            if self.clear_from is None:
                self.clear_from, self.clear_start = self.progress, now
            t = (now - self.clear_start) / CLEAR_DURATION
            self.progress = 0.0 if t >= 1 else self.clear_from * (1 - ease(t))
            return Visual.at(self.progress, self.tilt_span)
        if not clear and self.clear_from is not None:
            self.clear_from = None   # retarget from the current pixels, no jump
        if clear:
            return Visual()
        self.progress += (target - self.progress) * (1 - math.exp(-dt / self.tau))
        if abs(self.progress - target) < 1e-4:
            self.progress = target
        return Visual.at(self.progress, self.tilt_span)
