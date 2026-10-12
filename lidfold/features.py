"""Per-pixel colour + texture features at the analysis resolution.

Colour alone confuses a grey palm rest with a grey shirt. Texture at two
scales separates them: the base has a crisp front lip, key grid and flat
plastic, while fabric has soft folds. Everything is a few OpenCV filters on
a 160x120 image (well under 1 ms).
"""
from __future__ import annotations

import cv2
import numpy as np

# On images this small OpenCV's worker pool spends more time spinning than
# working: one thread cut whole-process CPU from ~30 % to ~7 % of a core.
cv2.setNumThreads(1)

WORK_W, WORK_H = 160, 120
N_FEATURES = 12


def prepare(frame_bgr: np.ndarray) -> np.ndarray:
    """Camera frame -> Lab float32 at WORK size."""
    small = cv2.resize(frame_bgr, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0


def _local_std(x: np.ndarray, k: int) -> np.ndarray:
    mean = cv2.blur(x, (k, k))
    return np.sqrt(np.maximum(cv2.blur(x * x, (k, k)) - mean * mean, 0.0))


def extract(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Returns (features HxWxN float32, vertical edge strength HxW in 0..1)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)
    lap = np.abs(cv2.Laplacian(L, cv2.CV_32F, ksize=3))
    chroma = np.sqrt((a - 0.5) ** 2 + (b - 0.5) ** 2)
    feats = np.stack([
        L, a, b,
        L - float(L.mean()),                       # robust to auto-exposure shifts
        _local_std(L, 5), _local_std(L, 15),       # fine / coarse texture energy
        cv2.blur(np.abs(gx), (5, 5)), cv2.blur(np.abs(gy), (5, 5)),
        cv2.blur(lap, (5, 5)), cv2.blur(lap, (15, 15)),
        _local_std(chroma, 7),
        cv2.blur(L, (31, 31)),                     # surrounding brightness
    ], axis=-1)
    edge = np.abs(cv2.GaussianBlur(gy, (1, 3), 0))
    edge /= max(float(np.percentile(edge, 99)), 1e-3)
    return feats.astype(np.float32), np.clip(edge, 0.0, 1.0)


def jitter(frame_bgr: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Photometric augmentation: exposure, contrast, white balance, blur."""
    img = frame_bgr.astype(np.float32)
    img = img * rng.uniform(0.6, 1.4) + rng.uniform(-20, 20)
    img = (img - img.mean()) * rng.uniform(0.75, 1.25) + img.mean()
    img *= rng.uniform(0.9, 1.1, size=3)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if rng.random() < 0.4:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 2.0))
    return img
