import time
from multiprocessing.connection import Client

import pytest

from lidfold.config import Settings


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("LIDFOLD_HOME", str(tmp_path))
    from lidfold.tray import Controller
    c = Controller(Settings())
    c.calibrated = True
    return c


@pytest.fixture
def bridge(ctl):
    from lidfold.ipc import UiBridge
    b = UiBridge(ctl, spawn=False)
    ctl.ui = b
    yield b
    b._stop.set()
    b.close()


def test_commands_change_settings(bridge, ctl):
    bridge.handle({"cmd": "set", "key": "effect", "value": "ghost"})
    bridge.handle({"cmd": "set", "key": "blur", "value": 0.3})
    bridge.handle({"cmd": "set", "key": "camera_fps", "value": 15})
    bridge.handle({"cmd": "set", "key": "action_angle", "value": 70})
    bridge.handle({"cmd": "set", "key": "clear_after", "value": 0})
    s = Settings.load()
    assert (s.effect, s.blur, s.camera_fps, s.action_angle, s.clear_after) == ("ghost", 0.3, 15, 70, 0)
    bridge.handle({"cmd": "set", "key": "action_angle", "value": None})
    assert ctl.settings.action_angle is None
    bridge.handle({"cmd": "toggle_lock"})
    assert ctl.settings.locked


@pytest.mark.parametrize("message", [
    {"cmd": "set", "key": "effect", "value": "explode"},
    {"cmd": "set", "key": "blur", "value": 7},
    {"cmd": "set", "key": "camera_fps", "value": 60},
    {"cmd": "set", "key": "action_angle", "value": 46},      # inside the 5-degree margin above full
    {"cmd": "set", "key": "locked", "value": True},          # not settable this way
    {"cmd": "launch", "tool": "format-c"},
    {"cmd": "rm"},
])
def test_bad_requests_are_rejected_and_change_nothing(bridge, ctl, message):
    before = vars(ctl.settings).copy()
    with pytest.raises((ValueError, KeyError)):
        bridge.handle(message)
    assert vars(ctl.settings) == before


@pytest.mark.parametrize("effect", ["card", "genie", "fold", "glass", "cube", "curl", "blinds",
                                    "doorway", "reflect", "stack", "twirl", "ripple", "aura",
                                    "island", "prism", "mosaic"])
def test_new_effects_are_accepted(bridge, ctl, effect):
    bridge.handle({"cmd": "set", "key": "effect", "value": effect})
    assert ctl.settings.effect == effect


def test_state_reports_live_values(bridge, ctl):
    ctl.lid_deg = 72.4
    state = bridge.state()
    assert state["lid_deg"] == 72 and state["calibrated"] and state["fps_choices"] == [10, 15, 20, 30]


def test_events_report_failure_without_a_ui(bridge, ctl):
    assert not bridge.toast("t", "b") and not bridge.hud("paused") and not bridge.show_panel()
    ctl.notify("hello")                                   # falls back to the tray balloon, no error


def test_round_trip_over_the_authenticated_pipe(bridge, ctl):
    bridge.start()
    client = Client(bridge.address, family="AF_PIPE", authkey=bridge.key)
    client.send({"cmd": "hello", "pid": 1234})
    deadline = time.monotonic() + 3
    state = None
    while time.monotonic() < deadline and state is None:
        if client.poll(0.2):
            message = client.recv()
            if message.get("event") == "state":
                state = message["state"]
    assert state is not None and state["effect"] == ctl.settings.effect
    client.send({"cmd": "set", "key": "effect", "value": "iris"})
    deadline = time.monotonic() + 2
    while ctl.settings.effect != "iris" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ctl.settings.effect == "iris"
    assert bridge.toast("Calibration", "saved")           # connected now
    client.close()
    bridge._stop.set()


def test_wrong_key_cannot_connect(bridge):
    from multiprocessing import AuthenticationError
    bridge.start()
    with pytest.raises(AuthenticationError):
        Client(bridge.address, family="AF_PIPE", authkey=b"x" * 32)
    bridge._stop.set()


def test_range_sets_both_ends(bridge, ctl):
    bridge.handle({"cmd": "set", "key": "range", "value": [80, 30]})
    assert (ctl.settings.action_angle, ctl.settings.max_angle) == (80, 30)
    bridge.handle({"cmd": "set", "key": "range", "value": [None, None]})
    assert (ctl.settings.action_angle, ctl.settings.max_angle) == (None, None)


@pytest.mark.parametrize("message", [
    {"cmd": "set", "key": "range", "value": [50, 48]},        # start not 5 deg above max
    {"cmd": "set", "key": "range", "value": [80, 5]},         # max implausibly low
    {"cmd": "set", "key": "max_angle", "value": 200},
])
def test_bad_ranges_are_rejected(bridge, ctl, message):
    before = vars(ctl.settings).copy()
    with pytest.raises((ValueError, TypeError)):
        bridge.handle(message)
    assert vars(ctl.settings) == before
