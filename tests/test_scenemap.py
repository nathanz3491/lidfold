import math

import cv2
import numpy as np

from lidfold.odometry import ODO_H, ODO_W, PitchOdometry
from lidfold.scenemap import SPACING, SceneMap

ROOM = cv2.GaussianBlur(np.random.default_rng(0).random((3000, ODO_W)).astype(np.float32), (0, 0), 2.5)
ROOM = (ROOM - ROOM.min()) / (ROOM.max() - ROOM.min()) * 255
ODO = PitchOdometry()


def view(phi: float) -> np.ndarray:
    """Grey camera view at lid angle phi (scene rises as the lid closes)."""
    off = ODO.focal * math.radians(phi)
    m = np.float32([[1, 0, 0], [0, 1, 1000 + off]])
    return cv2.warpAffine(ROOM, m, (ODO_W, ODO_H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)


def sweep_map():
    phis = list(np.arange(0, 90, 0.5))
    return SceneMap.build([view(p) for p in phis], phis)


def test_build_keeps_one_frame_per_spacing():
    m = sweep_map()
    assert abs(len(m) - 90 / SPACING) <= 1
    assert np.all(np.diff(np.sort(m.phis)) >= SPACING - 1e-9)


def test_locates_unseen_angles():
    m = sweep_map()
    for phi in (7.3, 31.9, 52.2, 80.6):
        located, _ = m.locate(view(phi), phi + 4.0, ODO)    # estimate 4 deg off
        assert located is not None and abs(located - phi) < 0.5


def test_far_off_estimate_does_not_match():
    # No search across all keyframes: a wrong far-away match is worse than none.
    m = sweep_map()
    assert m.locate(view(60.0), 20.0, ODO)[0] is None


def test_unrelated_scene_does_not_match():
    m = sweep_map()
    other = np.random.default_rng(5).random((ODO_H, ODO_W)).astype(np.float32) * 255
    assert m.locate(other, 30.0, ODO)[0] is None


def test_single_scene_match_is_not_applied_until_confirmed():
    from lidfold.angle import AngleCalibration, LidAngle, LidModel

    class Quiet:
        def read(self, frame, now):
            class R: present = False; fresh = None; dark_gated = 0.0
            return R()

        def reset(self):
            pass

    m = sweep_map()
    lid = LidAngle(LidModel(Quiet(), AngleCalibration.from_poses([0.3], [40.0], 30.0, 60.0), m))
    lid.phi = 40.0                                   # estimate; the view says 45
    frame = cv2.cvtColor(view(45.0).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    readings = [lid.update(frame, i / 30) for i in range(1, 7)]
    hits = [r.scene_hit for r in readings]
    assert hits.count(True) >= 1 and not hits[2]     # first lookup (frame 3) only arms
    assert abs(readings[-1].phi - 45.0) < abs(40.0 - 45.0)


def test_empty_map():
    assert SceneMap.build([], []).locate(view(0), 0.0, ODO) == (None, 0.0)
