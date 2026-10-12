import math

import cv2
import numpy as np

from lidfold.odometry import ODO_H, ODO_W, PitchOdometry

ROOM = cv2.GaussianBlur(np.random.default_rng(0).random((2400, ODO_W)).astype(np.float32), (0, 0), 2.5)
ROOM = (ROOM - ROOM.min()) / (ROOM.max() - ROOM.min()) * 255


def view(offset: float) -> np.ndarray:
    """Camera view of the room, the scene shifted up by `offset` px (sub-pixel)."""
    m = np.float32([[1, 0, 0], [0, 1, offset + 1000]])
    img = cv2.warpAffine(ROOM, m, (ODO_W, ODO_H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    return cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_GRAY2BGR)


def integrate(odo, offsets):
    return sum(odo.update(view(o)).degrees for o in offsets)


def expected_degrees(odo, px):
    return math.degrees(math.atan(px / odo.focal))


def test_closing_lid_is_measured():
    odo = PitchOdometry()
    offsets = np.arange(0, 60 * 4.0, 4.0)        # scene rises 4 px/frame for 2 s
    total = integrate(odo, offsets)
    # Per-frame rotation is atan(step / f); the steps add up as angles.
    expect = (len(offsets) - 1) * expected_degrees(odo, 4.0)
    assert total > 0
    assert abs(total - expect) / expect < 0.05


def test_opening_is_negative_and_symmetric():
    odo = PitchOdometry()
    down = integrate(odo, np.arange(0, 120, 3.0))
    odo.reset()
    up = integrate(odo, np.arange(120, 0, -3.0))
    assert down > 0 > up
    assert abs(down + up) < 0.1 * down


def test_subpixel_jitter_at_rest_does_not_drift():
    odo = PitchOdometry()
    rng = np.random.default_rng(1)
    assert integrate(odo, rng.uniform(-0.1, 0.1, 300)) == 0


def test_slow_close_is_fully_measured():
    # ~1 px per frame, as in a real recorded slow close.
    odo = PitchOdometry()
    offsets = np.arange(0, 200, 1.0)
    total = integrate(odo, offsets)
    expect = (len(offsets) - 1) * expected_degrees(odo, 1.0)
    # Sub-pixel phase correlation under-reads ~1 px shifts by about 11 %
    # (4 px shifts are within 5 %). The scene map and base edge correct the
    # absolute angle, so odometry only bridges short gaps.
    assert 0.8 < total / expect < 1.05


def test_object_moving_in_one_tile_is_ignored():
    odo = PitchOdometry()
    total = 0.0
    for i in range(40):
        img = view(0)
        y = 10 + 4 * i
        cv2.rectangle(img, (20, y), (60, y + 30), (255, 255, 255), -1)   # hand in one tile
        total += odo.update(img).degrees
    assert total == 0


def test_hand_on_lid_does_not_hide_the_motion():
    # Tiles covered by the hand holding the lid move with the camera and look
    # static; the remaining (world) tiles still carry the lid motion.
    odo = PitchOdometry()
    hand = np.full((100, 170, 3), 200, np.uint8)
    cv2.randu(hand, 0, 255)
    total = 0.0
    for i, off in enumerate(np.arange(0, 80, 2.0)):
        img = view(off)
        img[0:100, 0:170] = hand                     # fixed in the image: 5 of 8 tiles
        total += odo.update(img).degrees
    expect = 39 * expected_degrees(odo, 2.0)
    assert total / expect > 0.8
