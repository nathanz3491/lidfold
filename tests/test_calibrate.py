import numpy as np

from lidfold.calibrate import SX, SY, boundary_from_clicks
from lidfold.features import WORK_H, WORK_W


def test_single_click_gives_flat_edge():
    b = boundary_from_clicks([(300, VIEW_Y := 400)])
    assert b.shape == (WORK_W,)
    assert np.allclose(b, WORK_H - VIEW_Y / SY)


def test_clicks_interpolate_and_extend():
    b = boundary_from_clicks([(0, 440), (639, 400)])
    assert b[0] < b[-1]                       # right side clicked higher
    assert np.isclose(b[0], WORK_H - 440 / SY)
    assert np.all(np.diff(b) >= 0)


def test_click_order_does_not_matter():
    pts = [(500, 420), (100, 430), (320, 400)]
    assert np.array_equal(boundary_from_clicks(pts), boundary_from_clicks(pts[::-1]))
    assert SX > 0
