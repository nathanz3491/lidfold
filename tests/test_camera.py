import numpy as np
import pytest

from lidfold.camera import FrameGate


def analysed(target, seconds=10.0, source=30.0, jitter=0.004, seed=0):
    """Feed a jittery `source`-fps stream through the gate; frames kept per second."""
    rng = np.random.default_rng(seed)
    gate, t, kept = FrameGate(target), 0.0, 0
    for _ in range(int(seconds * source)):
        t += 1.0 / source + rng.uniform(-jitter, jitter)
        kept += gate.offer(t)
    return kept / seconds


@pytest.mark.parametrize("target", [10, 15, 20, 30])
def test_gate_delivers_the_chosen_rate(target):
    assert analysed(target) == pytest.approx(target, abs=0.5)


def test_gate_never_drops_frames_at_the_source_rate_despite_jitter():
    for jitter in (0.004, 0.012, 0.016):
        assert analysed(30, jitter=jitter) == pytest.approx(30, abs=0.2)


def test_gate_spreads_frames_evenly():
    gate, t, kept = FrameGate(15), 0.0, []
    for i in range(30):
        t += 1 / 30
        kept.append(gate.offer(t))
    assert all(a != b for a, b in zip(kept[1:], kept[2:]))   # strictly alternating


def test_gate_rate_changes_live():
    gate, t = FrameGate(30), 0.0
    gate.target = 10.0
    kept = 0
    for _ in range(300):
        t += 1 / 30
        kept += gate.offer(t)
    assert kept == pytest.approx(100, abs=2)
