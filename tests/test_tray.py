import pytest

from lidfold.config import Settings
from lidfold.icon import tray_icon


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setenv("LIDFOLD_HOME", str(tmp_path))
    from lidfold.tray import Controller
    return Controller(Settings())


def test_actions_persist_and_flag_changes(ctl, tmp_path):
    ctl.set_effect("ghost")
    ctl.set_blur(0.9)
    assert ctl.take_changes() == {"style", "tracker"}
    assert ctl.take_changes() == set()
    assert Settings.load().effect == "ghost" and Settings.load().blur == 0.9


def test_lock_turns_everything_off_and_persists(ctl):
    assert ctl.active
    ctl.toggle_lock()
    assert not ctl.active and Settings.load().locked
    ctl.toggle_lock()
    assert ctl.active


def test_menu_radio_items_drive_the_controller(ctl):
    from lidfold.tray import Tray
    tray = Tray(ctl)
    effect_menu = next(i for i in tray.icon.menu.items if i.text == "Effect").submenu
    ghost = next(i for i in effect_menu.items if i.text == "Ghost")
    ghost(tray.icon)
    assert ctl.settings.effect == "ghost" and ghost.checked
    clear = next(i for i in tray.icon.menu.items if i.text == "Clear when the lid is still").submenu
    next(i for i in clear.items if i.text == "Never")(tray.icon)
    assert ctl.settings.clear_after == 0.0


def test_icons_render_at_tray_sizes():
    for locked in (False, True):
        img = tray_icon(locked, 32)
        assert img.size == (32, 32) and img.getbbox() is not None


def test_never_clear_means_infinite_timeout():
    import math
    from lidfold.fold import tracker_for
    assert tracker_for("duo", 0.0, 0.025).clear_after == math.inf
    assert tracker_for("duo", 2.0, 0.025).clear_after == 2.0


def test_degree_of_action_menu(ctl):
    from lidfold.tray import Tray
    tray = Tray(ctl)
    menu = next(i for i in tray.icon.menu.items if i.text.startswith("Starts at")).submenu
    sixty = next(i for i in menu.items if i.text == "60 deg")
    sixty(tray.icon)
    assert ctl.settings.action_angle == 60.0 and "action" in ctl.take_changes()
    assert Settings.load().action_angle == 60.0
    ctl.settings.full_angle = 58.0                    # 60 is too close to a 58-deg full stop
    assert not sixty.enabled
    next(i for i in menu.items if i.text.startswith("Calibrated"))(tray.icon)
    assert ctl.settings.action_angle is None


def test_camera_frame_rate_menu(ctl):
    from lidfold.tray import Tray
    tray = Tray(ctl)
    menu = next(i for i in tray.icon.menu.items if i.text == "Camera frame rate").submenu
    assert [i.text.split()[0] for i in menu.items] == ["10", "15", "20", "30"]
    next(i for i in menu.items if i.text.startswith("15"))(tray.icon)
    assert ctl.settings.camera_fps == 15 and ctl.take_changes() == {"fps"}
    assert Settings.load().camera_fps == 15


def test_max_effect_menu(ctl):
    from lidfold.tray import Tray
    tray = Tray(ctl)
    menu = next(i for i in tray.icon.menu.items if i.text == "Max effect at").submenu
    next(i for i in menu.items if i.text == "35 deg")(tray.icon)
    assert ctl.settings.max_angle == 35.0 and "action" in ctl.take_changes()
    ctl.settings.action_angle = 50.0
    assert not next(i for i in menu.items if i.text == "50 deg").enabled   # must stay 5 below start
