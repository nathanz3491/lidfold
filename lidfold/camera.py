"""Background webcam reader that keeps the latest frame and lid-angle reading."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from .angle import AngleReading, LidAngle


FPS_CHOICES = (10, 15, 20, 30)


class FrameGate:
    """Decimates a camera stream to `target` frames per second.

    Webcams commonly ignore frame-rate requests: this one streams 30 fps
    over both MSMF and DirectShow whatever is asked (DirectShow even reports
    the requested rate). So the rate is enforced here, as a schedule: a frame
    is analysed when it is the nearest one to the next due time (within half
    a source interval), and the due time then advances by exactly 1/target,
    so the average rate is exact and the kept frames are evenly spread.
    Within 20 % of the source rate every frame is kept, so arrival jitter
    can never drop one. (Credit-counting variants dropped frames at 30 fps under
    jitter and scrambled the 15 fps pattern through float rounding.)
    """

    def __init__(self, target: float):
        self.target = float(target)
        self.interval: float | None = None   # smoothed source frame interval
        self.last: float | None = None
        self.next_due: float | None = None

    def offer(self, now: float) -> bool:
        if self.last is not None:
            dt = max(1e-4, now - self.last)
            self.interval = dt if self.interval is None else self.interval + 0.1 * (dt - self.interval)
        self.last = now
        period = 1.0 / max(self.target, 1e-3)
        if self.interval is None or self.next_due is None:
            self.next_due = now + period
            return True
        if self.target * self.interval >= 0.8:             # within 20 % of the source rate
            self.next_due = now + period
            return True
        if self.next_due < now - period:                   # stalled (e.g. resume): resync
            self.next_due = now
        if now >= self.next_due - 0.5 * self.interval:
            self.next_due += period
            return True
        return False


def open_camera(index: int, fps: int) -> cv2.VideoCapture:
    # MSMF opens ~4x faster than DirectShow on this class of hardware.
    cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


class CameraWorker(threading.Thread):
    def __init__(self, index: int, fps: int, lid: LidAngle | None):
        super().__init__(daemon=True, name="camera")
        self.index, self.fps, self.lid = index, fps, lid
        self.gate = FrameGate(fps)
        self._lock = threading.Lock()
        self._fresh = threading.Event()
        self._stop = threading.Event()
        self.frame: np.ndarray | None = None
        self.reading: AngleReading | None = None
        self.stamp = 0.0
        self.healthy = False

    def latest(self) -> tuple[np.ndarray | None, AngleReading | None, float]:
        with self._lock:
            return self.frame, self.reading, self.stamp

    def wait(self, timeout: float) -> None:
        """Block until a new reading arrives (or timeout)."""
        self._fresh.wait(timeout)
        self._fresh.clear()

    def stop(self) -> None:
        self._stop.set()

    def set_fps(self, fps: int) -> None:
        """Change the analysis rate live (a float store; safe across threads)."""
        self.fps = fps
        self.gate.target = float(fps)

    def run(self) -> None:
        cap: cv2.VideoCapture | None = None
        failures = 0
        while not self._stop.is_set():
            if cap is None:
                cap = open_camera(self.index, self.fps)
                if not cap.isOpened():
                    cap.release(); cap = None
                    self.healthy = False
                    self._stop.wait(1.0)
                    continue
            # grab() takes the frame without decoding it; only frames the gate
            # lets through are decoded and analysed.
            ok = cap.grab()
            now = time.monotonic()
            frame = None
            if ok:
                if not self.gate.offer(now):
                    failures = 0
                    continue
                ok, frame = cap.retrieve()
            if not ok or frame is None:
                failures += 1
                if failures >= 10:   # e.g. after sleep/resume: reopen
                    cap.release(); cap = None; failures = 0
                    self.healthy = False
                    with self._lock:
                        self.reading = None
                self._stop.wait(0.05)
                continue
            failures = 0
            self.healthy = True
            if self.lid and now - self.stamp > 2.0:
                # First frame, or the first after sleep/resume: the lid may have
                # moved unseen, so start from "normal" and let the base re-anchor.
                self.lid.reset()
            reading = self.lid.update(frame, now) if self.lid else None
            with self._lock:
                self.frame, self.reading, self.stamp = frame, reading, now
            self._fresh.set()
        if cap is not None:
            cap.release()
