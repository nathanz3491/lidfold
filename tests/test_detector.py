"""Synthetic scenes for the hard case: a shirt the same grey as the base.

Frames are 320x240 (2x the analysis size); boundaries are given in analysis
rows from the bottom.
"""
import numpy as np
import pytest

from lidfold.detector import BaseDetector, Sample
from lidfold.features import WORK_H, WORK_W, prepare

H, W = 240, 320
GREY = 150


def _curve(height):
    """Base edge in analysis rows from bottom, slightly bowed like a lens would."""
    x = np.linspace(-1, 1, WORK_W)
    return (height - 3 * x * x).astype(np.float32).clip(0)


def scene(rng, shirt=True, blob=False):
    img = np.empty((H, W, 3), np.float32)
    img[:] = np.linspace(200, 120, H)[:, None, None]          # lit wall
    img[..., 0] += 20                                          # bluish
    if shirt:   # same grey as the base, but smooth folds instead of keys
        yy, xx = np.mgrid[0:H, 0:W]
        folds = 12 * np.sin(xx / 23.0 + yy / 41.0)
        mask = (yy > 150) & (np.abs(xx - W / 2) < 110)
        img[mask] = (GREY + folds[mask])[:, None]
    if blob:
        img[60:110, 100:220] = GREY
        img[60:110:6, 100:220] = GREY - 70
    img += rng.normal(0, 4, img.shape)
    return img


def with_base(img, height_rows):
    img = img.copy()
    edge = (WORK_H - _curve(height_rows)) * (H / WORK_H)       # full-res row of the lip
    yy, xx = np.mgrid[0:H, 0:W]
    top = edge[(xx * WORK_W // W)]
    base = yy >= top
    keys = ((yy - top) % 14 < 3) | (xx % 16 < 3)               # key grid
    img[base] = GREY
    img[base & keys & (yy > top + 8)] = GREY - 75
    img[(yy >= top) & (yy < top + 4)] = 40                     # dark front lip
    return img


def u8(img):
    return np.clip(img, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def detector():
    rng = np.random.default_rng(1)
    samples = [Sample(u8(scene(rng, shirt=i % 2 == 0))) for i in range(6)]
    for h in (6, 40, 75):
        for _ in range(3):
            samples.append(Sample(u8(with_base(scene(rng), h)), _curve(h)))
    det, accuracy = BaseDetector.fit(samples, augment=2)
    assert accuracy > 0.9
    return det


def run(det, frames):
    det.reset()
    out = None
    for i, f in enumerate(frames):
        out = det.read(f, i / 30)
    return out


def test_shirt_alone_is_not_base(detector):
    rng = np.random.default_rng(7)
    r = run(detector, [u8(scene(rng)) for _ in range(5)])
    assert not r.present and r.height == 0


def test_floating_lookalike_is_ignored(detector):
    rng = np.random.default_rng(8)
    r = run(detector, [u8(scene(rng, blob=True)) for _ in range(5)])
    assert not r.present


@pytest.mark.parametrize("rows", [4, 25, 55, 90])
def test_edge_height_is_tracked(detector, rows):
    rng = np.random.default_rng(rows)
    r = run(detector, [u8(with_base(scene(rng), rows)) for _ in range(5)])
    assert r.present
    assert abs(r.height * WORK_H - _curve(rows).mean()) <= 4


def test_onset_needs_two_frames(detector):
    rng = np.random.default_rng(3)
    open_f = u8(scene(rng))
    edge_f = u8(with_base(scene(rng), 6))
    detector.reset()
    detector.read(open_f, 0.0)
    assert not detector.read(edge_f, 0.033).present
    assert detector.read(edge_f, 0.066).present


def test_single_dropout_does_not_flicker(detector):
    rng = np.random.default_rng(4)
    frames = [u8(with_base(scene(rng), 40)) for _ in range(4)]
    run(detector, frames)
    r = detector.read(u8(scene(rng)), 1.0)       # one bad frame
    assert r.present and r.height > 0.2


def test_darkness_needs_preceding_base(detector):
    rng = np.random.default_rng(5)
    dark = u8(scene(rng) * 0.08)
    assert run(detector, [dark] * 3).dark_gated == 0
    r = run(detector, [u8(with_base(scene(rng), 80))] * 4 + [dark])
    assert r.dark_gated > 0.5


def test_model_roundtrip(detector, tmp_path):
    path = tmp_path / "m.npz"
    detector.save(path)
    other = BaseDetector.load(path)
    f = u8(with_base(scene(np.random.default_rng(9)), 30))
    a = detector.fit_edge(prepare(f))
    b = other.fit_edge(prepare(f))
    assert a[0] == b[0] and abs(a[1] - b[1]) < 1e-4
