"""Estimate how far the lid is closed by tracking the laptop base's front edge.

The camera sits on the lid, and the base is rigid relative to it, so at any
lid angle the base occupies one specific region at the bottom of the frame
with one specific edge shape. Lid angle is therefore the *only* free
parameter, and the detector fits just that:

1. A tiny per-pixel MLP (colour + texture, see features.py) scores how
   base-like each pixel is.
2. Edge shapes clicked during calibration define a one-parameter family of
   boundaries, B_m(x), indexed by mean height m in rows.
3. For every m, score = (base logits below B_m) + lambda * (vertical edge
   strength along B_m). The argmax is the edge height. A shirt the same
   colour as the base, or a stray patch, cannot win against a rigid,
   full-width shape.
4. Hysteresis (on after 2 frames, off after 3) and a 3-frame median remove
   single-frame flicker. The effect starts as soon as about 2 rows of base
   are visible.

It also reports darkness for the last few degrees, when the camera sees
almost nothing. Darkness only counts once the base has been seen. angle.py
turns the edge height and darkness into a lid angle.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .features import N_FEATURES, WORK_H, WORK_W, extract, jitter, prepare
from .model import PixelMLP

EDGE_WEIGHT = 3.0
MIN_ROWS = 2                 # base rows needed before the effect starts
MIN_SCORE = 1.5              # mean per-column evidence for "base present"
MIN_MEAN_LOGIT = 0.5
ON_FRAMES, OFF_FRAMES = 2, 3
DARK_GATE_SECONDS = 1.5
DARK_GATE_HEIGHT = 0.06
LABEL_MARGIN = 2             # rows either side of a clicked edge left unlabelled


@dataclass
class Sample:
    """A calibration frame. boundary[x] = base rows from the bottom (WORK
    resolution), None for a frame with no base in view; phi = odometry angle
    when it was captured, if known."""
    frame: np.ndarray
    boundary: np.ndarray | None = None
    phi: float | None = None


@dataclass
class Reading:
    height: float               # base edge height, fraction of frame height
    score: float                # evidence for the best edge
    darkness: float
    dark_gated: float           # darkness that followed visible base, else 0
    fresh: float | None         # this frame's own edge height if detected now, unfiltered
    present: bool
    boundary: np.ndarray        # rows from bottom per column (WORK_W,)
    prob: np.ndarray = field(repr=False)   # per-pixel base probability, debug view


class ShapeFamily:
    """Boundaries B_m(x) for m = 0..WORK_H, interpolated between clicked edges."""

    def __init__(self, shapes: list[np.ndarray]):
        anchors = sorted(((float(s.mean()), np.asarray(s, np.float32)) for s in shapes),
                         key=lambda a: a[0])
        means = [0.0] + [m for m, _ in anchors]
        curves = [np.zeros(WORK_W, np.float32)] + [c for _, c in anchors]
        table = np.zeros((WORK_H + 1, WORK_W), np.float32)
        for m in range(WORK_H + 1):
            if m >= means[-1]:
                table[m] = curves[-1] + (m - means[-1])
                continue
            k = int(np.searchsorted(means, m, side="right"))
            lo, hi = means[k - 1], means[k]
            t = 0.0 if hi <= lo else (m - lo) / (hi - lo)
            table[m] = (1 - t) * curves[k - 1] + t * curves[k]
        self.shapes = [c for _, c in anchors]
        self.table = np.clip(np.rint(table), 0, WORK_H).astype(np.intp)


class BaseDetector:
    def __init__(self, net: PixelMLP, shapes: list[np.ndarray], open_luma: float):
        self.net = net
        self.family = ShapeFamily(shapes)
        self.open_luma = float(open_luma)
        self._cols = np.arange(WORK_W)
        self.reset()

    def reset(self) -> None:
        self._on = False
        self._streak = 0
        self._heights: deque[float] = deque(maxlen=3)
        self._recent: deque[tuple[float, float]] = deque()
        self._dark_latched = False

    # ------------------------------------------------------------- training
    @classmethod
    def fit(cls, samples: list[Sample], augment: int = 4, per_image: int = 2500,
            seed: int = 0) -> tuple["BaseDetector", float]:
        rng = np.random.default_rng(seed)
        xs, ys = [], []
        rows = np.arange(WORK_H)[:, None]
        for sample in samples:
            for flip in (False, True):
                frame = np.ascontiguousarray(sample.frame[:, ::-1] if flip else sample.frame)
                if sample.boundary is None:
                    label = np.zeros((WORK_H, WORK_W), np.int8)
                else:
                    b = sample.boundary[::-1] if flip else sample.boundary
                    top = WORK_H - b[None, :]                # first base row per column
                    label = np.where(rows >= top + LABEL_MARGIN, 1,
                                     np.where(rows < top - LABEL_MARGIN, 0, -1)).astype(np.int8)
                for i in range(augment):
                    img = frame if i == 0 else jitter(frame, rng)
                    feats, _ = extract(prepare(img))
                    for cls_value in (0, 1):
                        idx = np.flatnonzero(label.ravel() == cls_value)
                        if idx.size == 0:
                            continue
                        pick = rng.choice(idx, min(per_image // 2, idx.size), replace=False)
                        xs.append(feats.reshape(-1, N_FEATURES)[pick])
                        ys.append(np.full(pick.size, cls_value, np.float32))
        x, y = np.concatenate(xs), np.concatenate(ys)
        if y.min() == y.max():
            raise ValueError("calibration needs frames both with and without the base")
        net = PixelMLP(N_FEATURES)
        accuracy = net.fit(x, y)
        labelled = [s for s in samples if s.boundary is not None]
        open_frames = [s.frame for s in samples if s.boundary is None]
        open_luma = float(np.mean([prepare(f)[..., 0].mean() * 255 for f in open_frames])) if open_frames else 0.0
        return cls(net, [s.boundary for s in labelled], open_luma), accuracy

    def save(self, path: Path, **extra) -> None:
        state = {f"net_{k}": v for k, v in self.net.state().items()}
        np.savez_compressed(path, shapes=np.stack(self.family.shapes), open_luma=self.open_luma,
                 **state, **extra)

    @classmethod
    def load(cls, path: Path) -> "BaseDetector":
        z = np.load(path)
        net = PixelMLP.from_state({k[4:]: z[k] for k in z.files if k.startswith("net_")})
        return cls(net, list(z["shapes"]), float(z["open_luma"]))

    # -------------------------------------------------------------- reading
    def fit_edge(self, lab: np.ndarray) -> tuple[int, float, float, np.ndarray, np.ndarray]:
        """Best edge for one frame: (height m, score, mean logit, boundary, logits)."""
        feats, edge = extract(lab)
        s = np.clip(self.net.logits(feats), -4.0, 4.0)
        # C[b, x]: summed logits of the bottom b rows; E[b, x]: edge at that boundary.
        C = np.vstack([np.zeros((1, WORK_W), np.float32), np.cumsum(s[::-1], axis=0)])
        E = np.vstack([np.zeros((1, WORK_W), np.float32), edge[::-1]])
        T = self.family.table
        total = (C[T, self._cols] + EDGE_WEIGHT * E[T, self._cols]).sum(axis=1)
        m = int(np.argmax(total))
        rows = T[m]
        score = float(total[m]) / WORK_W
        mean_logit = float(C[rows, self._cols].sum() / max(int(rows.sum()), 1))
        return m, score, mean_logit, rows, s

    def darkness(self, lab: np.ndarray) -> float:
        if self.open_luma <= 1:
            return 0.0
        ratio = float(lab[..., 0].mean()) * 255 / self.open_luma
        return float(np.clip((0.5 - ratio) / 0.4, 0.0, 1.0))

    def read(self, frame_bgr: np.ndarray, now: float) -> Reading:
        lab = prepare(frame_bgr)
        m, score, mean_logit, rows, logits = self.fit_edge(lab)
        raw = m >= MIN_ROWS and score >= MIN_SCORE and mean_logit >= MIN_MEAN_LOGIT

        # Hysteresis: quick to start, slower to give up.
        if raw != self._on:
            self._streak += 1
            if self._streak >= (ON_FRAMES if raw else OFF_FRAMES):
                self._on, self._streak = raw, 0
                self._heights.clear()
        else:
            self._streak = 0
        if self._on and raw:
            self._heights.append(m / WORK_H)
        height = float(np.median(self._heights)) if self._on and self._heights else 0.0

        dark = self.darkness(lab)
        self._recent.append((now, height))
        while self._recent and now - self._recent[0][0] > DARK_GATE_SECONDS:
            self._recent.popleft()
        if dark <= 0:
            self._dark_latched = False
        elif max(h for _, h in self._recent) >= DARK_GATE_HEIGHT:
            self._dark_latched = True
        gated = dark if self._dark_latched else 0.0
        prob = 1 / (1 + np.exp(-logits))
        fresh = m / WORK_H if self._on and raw else None
        return Reading(height, score, dark, gated, fresh, self._on, rows, prob)
