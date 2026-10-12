import numpy as np
import pytest

from lidfold.angle import AngleCalibration, LidAngle, LidModel
from lidfold.odometry import Motion
from lidfold.scenemap import SceneMap

FRAME = np.zeros((240, 320, 3), np.uint8)

CAL = AngleCalibration.from_poses(heights=[0.02, 0.30, 0.55], phis=[60.0, 75.0, 85.0],
                                  phi_start=50.0, phi_full=80.0)


class Base:
    def __init__(self, height=0.0, present=False, dark=0.0):
        self.height, self.present, self.dark_gated = height, present, dark
        self.fresh = height if present else None


class FakeDetector:
    def __init__(self):
        self.next = Base()

    def read(self, frame, now):
        return self.next

    def reset(self):
        pass


class FakeOdometry:
    def __init__(self):
        self.next = 0.0

    def update(self, frame, gray=None):
        return Motion(self.next, 8, 1.0)

    def reset(self):
        pass


@pytest.fixture
def lid():
    lid = LidAngle(LidModel(FakeDetector(), CAL, SceneMap.build([], [])))
    lid.odometry = FakeOdometry()
    return lid


def step(lid, deg=0.0, base=None, n=1):
    lid.odometry.next = deg
    lid.model.detector.next = base or Base()
    r = None
    for _ in range(n):
        r = lid.update(FRAME, 0.0)
    return r


def test_calibration_maps_and_extrapolates():
    assert CAL.phi_for_height(0.30) == pytest.approx(75.0)
    assert 60.0 < CAL.phi_for_height(0.15) < 75.0
    assert CAL.phi_for_height(0.70) > 85.0          # beyond the deepest pose
    assert CAL.phi_for_height(0.0) < 60.0
    assert CAL.phi_full == 80.0


def test_closure_ramps_from_start_angle():
    assert CAL.closure(40.0) == 0
    assert CAL.closure(50.0) == 0
    assert 0 < CAL.closure(60.0) < CAL.closure(75.0) < 1
    assert CAL.closure(CAL.phi_full + 10) == 1


def test_effect_starts_before_base_is_visible(lid):
    r = step(lid, deg=2.0, n=27)                     # tilt 54 deg, room only
    assert r.phi == pytest.approx(54.0)
    assert not r.base.present and r.closure > 0


def test_base_edge_cancels_drift(lid):
    step(lid, deg=1.0, n=40)                         # odometry says 40 deg...
    r = step(lid, base=Base(0.30, True), n=20)       # ...but the base says 75
    assert r.phi == pytest.approx(75.0, abs=0.5)


def test_darkness_drives_closure_near_shut(lid):
    r = step(lid, base=Base(dark=0.9))
    assert r.closure == pytest.approx(1.0)


def test_reset_returns_to_normal(lid):
    step(lid, deg=3.0, n=10)
    lid.reset()
    assert lid.phi == 0.0


def test_angle_relaxes_to_normal_when_nothing_moves(lid):
    r = step(lid, deg=2.0, n=27)                     # tilt to 54 deg (odometry only)
    assert r.closure > 0
    lid.odometry.next = 0.0
    t, r = 1.0, None
    while t < 12.0:                                  # 11 s with no motion, no base
        r = lid.update(FRAME, t)
        t += 1 / 30
    assert r.phi < 5.0 and r.closure == 0


def test_visible_base_holds_the_angle_at_rest(lid):
    step(lid, base=Base(0.30, True), n=5)
    t, r = 1.0, None
    while t < 12.0:
        lid.odometry.next = 0.0
        lid.model.detector.next = Base(0.30, True)
        r = lid.update(FRAME, t)
        t += 1 / 30
    assert r.phi == pytest.approx(75.0, abs=0.5)


def test_ghost_is_live_from_rest_and_peaks_at_the_full_stop(lid):
    r = step(lid, deg=1.0, n=20)                     # 20 deg closed: before the start angle
    assert r.closure == 0                            # duo & co. wait for the start angle
    assert r.ghost == pytest.approx(20.0 / CAL.phi_full, abs=1e-6)
    assert r.drive("ghost") == r.ghost and r.drive("duo") == r.closure
    r = step(lid, deg=1.0, n=60)                     # 80 deg: at/after the full stop (80)
    assert r.ghost == pytest.approx(1.0)


def test_ghost_does_not_go_negative_when_opened_past_normal(lid):
    r = step(lid, deg=-1.0, n=10)
    assert r.ghost == 0
