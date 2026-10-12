"""Background webcam reader that keeps the latest frame and lid-angle reading."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from .angle import AngleReading, LidAngle


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
            ok, frame = cap.read()
            now = time.monotonic()
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
