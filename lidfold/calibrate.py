"""Calibration (sweep + click-to-label), retraining, recording/replay, debug view."""
from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .angle import AngleCalibration, AngleReading, LidAngle, LidModel
from .camera import open_camera
from .config import Settings, data_dir
from .detector import BaseDetector, Sample
from .features import WORK_H, WORK_W, prepare
from .fold import LidTracker, tracker_for
from .odometry import PitchOdometry, to_gray
from .scenemap import SceneMap

WINDOW = "lidfold"
VIEW_W, VIEW_H = 640, 480
SX, SY = VIEW_W / WORK_W, VIEW_H / WORK_H

SWEEP = [
    ("start", ["Stop 1/3: tilt the lid smoothly to where the effect should",
               "START (e.g. about 50 degrees), hold, SPACE."]),
    ("edge", ["Stop 2/3: keep tilting until the base's front edge JUST",
              "appears at the bottom (SPACE now if it already shows)."]),
    ("full", ["Stop 3/3: keep tilting to where the effect should be at",
              "MAXIMUM (e.g. about 30 degrees), hold, SPACE."]),
]
OUT_OF_VIEW = ["Stay out of the camera's view during the sweep: close the lid",
               "from the side, keep your body and arm out of the picture."]
MIN_START = 10.0     # a start stop closer to normal than this means the tilt was not tracked
MIN_SPAN = 8.0       # full must lie at least this far past start


def model_path() -> Path:
    return data_dir() / "base_model.npz"


def dataset_path() -> Path:
    return data_dir() / "calibration_set.npz"


def load_model() -> LidModel:
    if not model_path().exists():
        raise SystemExit("not calibrated yet: run `python -m lidfold calibrate` first")
    try:
        return LidModel.load(model_path())
    except (KeyError, ValueError):
        raise SystemExit("saved model is from an older lidfold: run `python -m lidfold calibrate` again")


# ------------------------------------------------------------------ UI bits
def _text(img, lines, y=24, color=(255, 255, 255)):
    for line in lines:
        if not line:
            continue
        cv2.putText(img, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        y += 22


def _view(frame):
    return cv2.resize(frame, (VIEW_W, VIEW_H), interpolation=cv2.INTER_AREA)


def _key():
    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        raise KeyboardInterrupt
    return key


class _TrackedCamera:
    """Camera reads that keep lid odometry running the whole session."""

    def __init__(self, cap):
        self.cap, self.odometry, self.phi = cap, PitchOdometry(), 0.0
        self.last = np.zeros((VIEW_H, VIEW_W, 3), np.uint8)
        self.sweep: list[tuple[np.ndarray, float]] | None = None   # recorded for the scene map

    def read(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok:
            return None
        gray = to_gray(frame)
        self.phi += self.odometry.update(frame, gray).degrees
        if self.sweep is not None:
            self.sweep.append((gray, self.phi))
        self.last = frame
        return frame

    def show(self, lines, frame=None, color=(255, 255, 255)) -> int:
        view = _view(self.last if frame is None else frame)
        _text(view, lines + [f"lid closed past normal: {self.phi:5.1f} deg"], color=color)
        cv2.imshow(WINDOW, view)
        return _key()


def _wait_space(cam: _TrackedCamera, lines) -> None:
    while True:
        cam.read()
        if cam.show(lines + ["SPACE: capture   ESC: quit"]) == 32:
            return


def _record(cam: _TrackedCamera, seconds: float, lines) -> tuple[list[np.ndarray], float]:
    frames, phis, t0 = [], [], time.monotonic()
    while time.monotonic() - t0 < seconds:
        frame = cam.read()
        if frame is None:
            continue
        frames.append(frame)
        phis.append(cam.phi)
        cam.show(lines + [f"recording... {seconds - (time.monotonic() - t0):0.1f}s"], color=(0, 220, 255))
    return frames, float(np.mean(phis))


def boundary_from_clicks(points: list[tuple[float, float]]) -> np.ndarray:
    """Clicked view points -> base rows-from-bottom per WORK column."""
    pts = sorted((x / SX, WORK_H - y / SY) for x, y in points)
    xs, bs = zip(*pts)
    return np.clip(np.interp(np.arange(WORK_W), xs, bs), 0, WORK_H).astype(np.float32)


def _draw_boundary(view, boundary, color, thickness=2):
    pts = np.stack([(np.arange(WORK_W) + 0.5) * SX, (WORK_H - boundary) * SY], axis=1)
    cv2.polylines(view, [pts.astype(np.int32)], False, color, thickness, cv2.LINE_AA)


def _label(cam: _TrackedCamera, frame, title: str) -> np.ndarray | None:
    """Click along the base edge on a frozen frame. None = no base / skip."""
    points: list[tuple[float, float]] = []
    cv2.setMouseCallback(WINDOW, lambda ev, x, y, *_: points.append((x, y)) if ev == cv2.EVENT_LBUTTONDOWN else None)
    try:
        while True:
            cam.read()   # keep odometry tracking while the user works
            view = _view(frame)
            if points:
                _draw_boundary(view, boundary_from_clicks(points), (0, 255, 0))
                for x, y in points:
                    cv2.circle(view, (int(x), int(y)), 4, (0, 255, 255), -1)
            _text(view, [title, "Click along the TOP edge of the base, left to right",
                         "(several points if it curves).",
                         "ENTER: accept   BACKSPACE: undo   S: no base visible / skip"])
            cv2.imshow(WINDOW, view)
            key = _key()
            if key == 13 and points:
                return boundary_from_clicks(points)
            if key == 8 and points:
                points.pop()
            if key in (ord("s"), ord("S")):
                return None
    finally:
        cv2.setMouseCallback(WINDOW, lambda *_: None)


def _pick(frames: list[np.ndarray], n: int) -> list[np.ndarray]:
    idx = np.linspace(0, len(frames) - 1, min(n, len(frames))).round().astype(int)
    return [frames[i] for i in idx]


# -------------------------------------------------------------- calibration
def calibrate(settings: Settings) -> None:
    cap = open_camera(settings.camera, settings.camera_fps)
    if not cap.isOpened():
        raise SystemExit(f"camera {settings.camera} could not be opened")
    cam = _TrackedCamera(cap)
    samples: list[Sample] = []
    cv2.namedWindow(WINDOW)
    try:
        intro = ["Step 0: lid in your normal position. When recording starts,",
                 "move naturally for 4 s: lean, shift, raise your hands."]
        _wait_space(cam, intro)
        frames, _ = _record(cam, 4.0, intro)
        samples += [Sample(f) for f in _pick(frames, 12)]
        while True:
            _wait_space(cam, ["Lid in its normal position. Now move OUT of the camera's",
                              "view, then SPACE. Next: one smooth tilt with 3 stops."] + OUT_OF_VIEW)
            cam.phi, cam.sweep = 0.0, []
            captures = []   # (name, frames, phi)
            for name, lines in SWEEP:
                _wait_space(cam, lines + OUT_OF_VIEW)
                frames, phi = _record(cam, 0.5, lines)
                captures.append((name, frames, phi))
            phi_start, phi_full = captures[0][2], captures[-1][2]
            scene = SceneMap.build([g for g, _ in cam.sweep], [p for _, p in cam.sweep])
            cam.sweep = None
            problem = None
            if phi_start < MIN_START:
                problem = f"start was measured only {phi_start:.0f} deg from normal"
            elif phi_full < phi_start + MIN_SPAN:
                problem = f"full ({phi_full:.0f}) is not clearly past start ({phi_start:.0f})"
            if problem is None:
                break
            while True:
                cam.read()
                if cam.show(["The tilt was not tracked properly:", problem + ".",
                             "Tilt smoothly, keep yourself out of view.",
                             "R: redo the sweep   ESC: quit"]) in (ord("r"), ord("R")):
                    break

        while True:
            for name, frames, phi in captures:
                boundary = _label(cam, frames[len(frames) // 2],
                                  f"Open the lid. Label '{name}' ({phi:.0f} deg past normal).")
                group = _pick(frames, 4)
                samples += [Sample(f, boundary, phi) if boundary is not None else Sample(f)
                            for f in group]
            captures = []
            choice = None
            while choice is None:
                cam.read()
                key = cam.show([f"{sum(s.boundary is not None for s in samples)} labelled frames.",
                                "A: add another angle (more angles = steadier)", "ENTER: train"])
                choice = "add" if key in (ord("a"), ord("A")) else "train" if key == 13 else None
            if choice == "train":
                break
            lines = ["Extra: tilt to an angle with the base visible, SPACE."]
            _wait_space(cam, lines)
            frames, phi = _record(cam, 0.5, lines)
            captures.append(("extra", frames, phi))
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if not any(s.boundary is not None for s in samples):
        raise SystemExit("no base edge was labelled; nothing to learn from")
    print(f"scene map: {len(scene)} keyframes over {scene.phis.min():.0f}..{scene.phis.max():.0f} deg")
    save_dataset(samples, phi_start, phi_full, scene)
    train(settings, samples, phi_start, phi_full, scene)


def save_dataset(samples: list[Sample], phi_start: float, phi_full: float,
                 scene: SceneMap) -> None:
    frames = np.stack([s.frame for s in samples])
    bounds = np.stack([s.boundary if s.boundary is not None else np.full(WORK_W, -1, np.float32)
                       for s in samples])
    phis = np.array([np.nan if s.phi is None else s.phi for s in samples])
    np.savez_compressed(dataset_path(), frames=frames, boundaries=bounds, phis=phis,
                        phi_start=phi_start, phi_full=phi_full,
                        scene_keys=scene.keys, scene_phis=scene.phis)


def load_dataset() -> tuple[list[Sample], float, float, SceneMap]:
    z = np.load(dataset_path())
    if "phi_full" not in z.files:
        raise SystemExit("calibration set is from an older lidfold: run `calibrate` again")
    samples = [Sample(f, None if b[0] < 0 else b, None if np.isnan(p) else float(p))
               for f, b, p in zip(z["frames"], z["boundaries"], z["phis"])]
    return (samples, float(z["phi_start"]), float(z["phi_full"]),
            SceneMap(z["scene_keys"], z["scene_phis"]))


def train(settings: Settings, samples: list[Sample], phi_start: float, phi_full: float,
          scene: SceneMap) -> LidModel:
    t0 = time.perf_counter()
    detector, accuracy = BaseDetector.fit(samples)
    print(f"trained on {len(samples)} frames in {time.perf_counter() - t0:.1f}s, "
          f"pixel accuracy {accuracy:.1%}")
    # Anchor angles on the heights the detector itself measures, so runtime
    # readings map through the same scale.
    poses: dict[float, list[float]] = {}
    for s in samples:
        m, score, *_ = detector.fit_edge(prepare(s.frame))
        truth = "none" if s.boundary is None else f"{s.boundary.mean():5.1f}"
        print(f"  labelled {truth:>5}  detected {m:3d} rows  score {score:6.2f}"
              + (f"  angle {s.phi:5.1f}" if s.phi is not None else ""))
        if s.boundary is not None and s.phi is not None:
            poses.setdefault(round(s.phi, 3), []).append(m / WORK_H)
    phis = np.array(sorted(poses))
    heights = np.array([float(np.median(poses[p])) for p in phis])
    angles = AngleCalibration.from_poses(heights, phis, phi_start, phi_full)
    model = LidModel(detector, angles, scene)
    model.save(model_path())
    print(f"effect starts {angles.phi_start:.1f} deg past normal, full at {angles.phi_full:.1f}")
    print(f"saved {model_path()}")
    return model


# ------------------------------------------------------- debugging & tuning
def annotate(frame: np.ndarray, r: AngleReading, extra: list[str]) -> np.ndarray:
    b = r.base
    view = _view(frame)
    _draw_boundary(view, b.boundary.astype(np.float32), (0, 255, 0) if b.present else (0, 0, 255))
    heat = cv2.applyColorMap((b.prob * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    view[-WORK_H:, -WORK_W:] = heat
    _text(view, [f"lid ~{r.lid_deg:5.1f} deg  closed {r.phi:5.1f}  closure {r.closure:.3f}  ghost {r.ghost:.3f}"
                 f"  {'MAP' if r.scene_hit else ''}",
                 f"base {b.height:.3f} {'SEEN' if b.present else '-'}  score {b.score:5.2f}  dark {b.darkness:.2f}"]
          + extra)
    return view


def _tracker(settings: Settings) -> LidTracker:
    return tracker_for(settings.effect, settings.clear_after, settings.still_epsilon)


def debug(settings: Settings) -> None:
    lid = LidAngle(load_model(), settings.action_angle, settings.full_angle)
    tracker = _tracker(settings)
    cap = open_camera(settings.camera, settings.camera_fps)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            now = time.monotonic()
            t0 = time.perf_counter()
            r = lid.update(frame, now)
            cost = (time.perf_counter() - t0) * 1000
            target, cleared = tracker.update(r.drive(settings.effect), now)
            view = annotate(frame, r, [f"fold {target:.2f}{' (cleared)' if cleared else ''}  {cost:.1f} ms",
                                       "R: reset angle to 0 (lid at normal position)"])
            cv2.rectangle(view, (0, VIEW_H - 8), (int(VIEW_W * target), VIEW_H), (0, 200, 255), -1)
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key in (ord("r"), ord("R")):
                lid.reset()
    finally:
        cap.release()
        cv2.destroyAllWindows()


def record(settings: Settings, seconds: float) -> Path:
    out_dir = data_dir() / "recordings"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"rec-{datetime.now():%Y%m%d-%H%M%S}.mp4"
    cap = open_camera(settings.camera, settings.camera_fps)
    cam = _TrackedCamera(cap)
    try:
        lines = [f"Recording {seconds:.0f}s: start at your normal position,",
                 "close the lid slowly, hold, reopen. Repeat at different speeds."]
        _wait_space(cam, lines)
        frames, _ = _record(cam, seconds, lines)
    finally:
        cap.release()
        cv2.destroyAllWindows()
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             len(frames) / seconds, (w, h))
    for f in frames:
        writer.write(f)
    writer.release()
    print(f"saved {len(frames)} frames to {path}")
    return path


def replay(path: Path, settings: Settings) -> Path:
    """Run the full estimator over a recording; write an annotated video and a CSV."""
    lid = LidAngle(load_model(), settings.action_angle, settings.full_angle)
    tracker = _tracker(settings)
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    out = path.with_name(path.stem + "-annotated.mp4")
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (VIEW_W, VIEW_H))
    with open(path.with_suffix(".csv"), "w", newline="") as fh:
        log = csv.writer(fh)
        log.writerow(["t", "angle", "motion", "scene_hit", "base_height", "base_seen", "darkness",
                      "closure", "ghost", "target"])
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i / fps
            r = lid.update(frame, t)
            target, cleared = tracker.update(r.drive(settings.effect), t)
            log.writerow([f"{t:.3f}", f"{r.phi:.2f}", f"{r.motion.degrees:.3f}", int(r.scene_hit),
                          f"{r.base.height:.4f}",
                          int(r.base.present), f"{r.base.darkness:.3f}", f"{r.closure:.4f}", f"{r.ghost:.4f}",
                          f"{0 if cleared else target:.3f}"])
            writer.write(annotate(frame, r, [f"t {t:5.2f}s  fold {target:.2f}"]))
            i += 1
    writer.release()
    print(f"{i} frames -> {out}")
    return out
