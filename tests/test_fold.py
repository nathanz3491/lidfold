from lidfold.fold import FoldAnimator, LidTracker, Visual, CLEAR_DURATION


def feed(tracker, values, t0=0.0, dt=1 / 30):
    out = None
    for i, v in enumerate(values):
        out = tracker.update(v, t0 + i * dt)
    return out


def test_visual_is_exact_passthrough_at_rest():
    v = Visual.at(0.0)
    assert v.is_clear and v.tilt == 0


def test_visual_monotonic():
    a, b = Visual.at(0.3), Visual.at(0.7)
    assert a.progress < b.progress and a.defocus < b.defocus and a.tilt < b.tilt


def test_open_lid_gives_no_fold():
    t = LidTracker(start=0.05, full=0.55)
    target, clear = feed(t, [0.02] * 30)
    assert target == 0 and not clear


def test_closing_raises_target_and_full_reaches_one():
    t = LidTracker(start=0.05, full=0.55)
    ramp = [0.05 + 0.6 * i / 30 for i in range(31)] + [0.65] * 10
    target, _ = feed(t, ramp)
    assert target == 1.0


def test_holding_still_clears_and_rebases():
    t = LidTracker(start=0.05, full=0.55, clear_after=1.0)
    feed(t, [0.05 + 0.25 * i / 15 for i in range(16)])          # close to 0.30
    target, clear = feed(t, [0.30] * 45, t0=16 / 30)               # hold 1.5 s
    assert clear and target == 0
    assert abs(t.reference - 0.30) < 0.02
    # Moving further from the new rest starts small again, not at 0.30's strength.
    target, clear = feed(t, [0.30 + 0.06 * i / 5 for i in range(6)], t0=2.0)
    assert not clear and 0 < target < 0.3


def test_reopening_lowers_reference():
    t = LidTracker(start=0.05, full=0.55, clear_after=0.5)
    feed(t, [0.4] * 30)
    assert t.reference > 0.3
    feed(t, [0.1] * 30, t0=2.0)
    assert t.reference < 0.15


def test_noise_below_epsilon_does_not_count_as_motion():
    t = LidTracker(start=0.0, full=1.0, clear_after=1.0, epsilon=0.025)
    feed(t, [0.5] * 10)
    jitter = [0.5 + (0.01 if i % 2 else -0.01) for i in range(40)]
    _, clear = feed(t, jitter, t0=10 / 30)
    assert clear


def test_animator_follows_and_clears_in_fixed_time():
    a = FoldAnimator()
    for i in range(60):
        v = a.sample(0.8, False, i / 60)
    assert abs(a.progress - 0.8) < 1e-3
    t0 = 1.0
    a.sample(0.0, True, t0)
    mid = a.sample(0.0, True, t0 + CLEAR_DURATION / 2)
    assert 0 < mid.progress < 0.8
    end = a.sample(0.0, True, t0 + CLEAR_DURATION + 0.01)
    assert end.is_clear


def test_mac_duo_curves():
    import math
    from lidfold.fold import MAC_SPAN
    assert Visual.at(0.0).is_clear
    # Defocus is barely visible over the first few Mac-equivalent degrees.
    assert Visual.at(3 / MAC_SPAN).defocus < 0.05
    values = [Visual.at(c / 20) for c in range(21)]
    for a, b in zip(values, values[1:]):
        assert b.progress >= a.progress and b.defocus >= a.defocus and b.anchor >= a.anchor
    assert values[-1].progress == 1.0
    assert math.isclose(values[-1].anchor, math.radians(85.0))


def test_ghost_tilt_is_real_degrees_over_its_span():
    import math
    v = Visual.at(0.5, tilt_span=60.0)
    assert math.isclose(v.anchor, math.radians(30.0))
    assert Visual.at(0.5).progress == v.progress     # curve shape independent of span
