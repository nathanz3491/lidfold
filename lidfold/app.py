"""Main loops: `run` (camera-driven) and `simulate` (scripted lid motion)."""
from __future__ import annotations

import ctypes
import math
import time

from .angle import LidAngle
from .calibrate import load_model
from .camera import CameraWorker
from .config import Settings
from .fold import FoldAnimator, LidTracker, tracker_for
from .overlay import Overlay
from .tray import Controller, Tray

STALE_READING = 1.0   # s without a camera reading -> clear the effect
RESUME_GAP = 2.0      # loop stalled this long -> assume sleep/resume, reset
STATUS_EVERY = 0.5    # s between live lid-angle updates in the tray tooltip
QUIT_EVENT = r"Local\lidfold-quit"   # set by `lidfold install` to stop a running copy


def _single_instance() -> object | None:
    """A named mutex; None if another lidfold is already running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, r"Local\lidfold-single-instance")
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        return None
    return handle


def _quit_event() -> tuple[object, object]:
    """Named event another process (the installer) can set to make us quit."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    return kernel32, kernel32.CreateEventW(None, True, False, QUIT_EVENT)


def run(settings: Settings, verbose: bool = False, tray: bool = True,
        ctl: Controller | None = None) -> None:
    if _single_instance() is None:
        raise SystemExit("lidfold is already running (see the tray icon).")
    ctl = ctl or Controller(settings)
    icon = Tray(ctl) if tray else None
    if icon:
        icon.start()
    overlay = Overlay(settings.effect, settings.blur, settings.shadow)
    print("lidfold running - close the lid slowly. Ctrl+C or the tray's Quit to exit.")

    kernel32, quit_event = _quit_event()
    model, camera, lid = None, None, None
    tracker = tracker_for(settings.effect, settings.clear_after, settings.still_epsilon)
    animator = FoldAnimator()
    changes = {"model"}
    last_stamp, last_loop, target, clear = 0.0, time.monotonic(), 0.0, False
    last_status = 0.0
    try:
        while not ctl.quit_requested.is_set():
            if quit_event and kernel32.WaitForSingleObject(ctypes.c_void_p(quit_event), 0) == 0:
                break
            changes |= ctl.take_changes()
            if "style" in changes:
                overlay.set_style(settings.effect, settings.blur, settings.shadow)
            if "model" in changes:
                try:
                    model = load_model()
                except SystemExit as exc:
                    model = None
                    ctl.set_status("not calibrated")
                    ctl.notify(f"{exc} Use Calibrate... in the tray menu.")
            want_camera = ctl.active and model is not None
            if camera is not None and (not want_camera or changes & {"camera", "model"}):
                camera.stop()
                camera.join(timeout=2.0)            # release the device before a tool opens it
                camera = None
            if want_camera and camera is None:
                ctl.camera_idle.clear()
                lid = LidAngle(model, settings.action_angle, settings.full_angle)
                ctl.calibrated_start_deg = lid.lid_degrees(model.angles.phi_start)
                camera = CameraWorker(settings.camera, settings.camera_fps, lid)
                camera.start()
                changes.add("tracker")
            if "fps" in changes and camera is not None:
                camera.set_fps(settings.camera_fps)
            if "action" in changes and lid is not None:
                lid.configure(settings.action_angle, settings.full_angle)
                changes.add("tracker")
            if camera is None:
                ctl.camera_idle.set()
            if "tracker" in changes and model is not None:
                tracker = tracker_for(settings.effect, settings.clear_after, settings.still_epsilon)
                animator = FoldAnimator(tilt_span=lid.ghost_tilt_span if lid else model.angles.ghost_span)
                target, clear = 0.0, False
            changes = set()

            if camera is None:
                overlay.hide()
                overlay.pump()
                if model is not None:
                    ctl.set_status(f"{ctl.busy.lower()}..." if ctl.busy else "locked")
                time.sleep(0.1)
                continue

            now = time.monotonic()
            if now - last_loop > RESUME_GAP:
                # Never wake up to a folded-away desktop.
                tracker.reset(); animator.reset(); overlay.hide()
                target, clear = 0.0, False
            last_loop = now

            _, reading, stamp = camera.latest()
            if reading is None or now - stamp > STALE_READING:
                target, clear = 0.0, True
                ctl.set_status("on - waiting for camera")
            elif stamp != last_stamp:
                last_stamp = stamp
                target, clear = tracker.update(reading.drive(settings.effect), stamp)
                if now - last_status >= STATUS_EVERY:
                    ctl.set_status(f"on - lid ~{reading.lid_deg:.0f} deg")
                    last_status = now
                if verbose:
                    print(f"angle {reading.phi:6.1f} base {reading.base.height:.3f} "
                          f"dark {reading.base.darkness:.2f} "
                          f"closure {reading.closure:.3f} ghost {reading.ghost:.3f} "
                          f"ref {tracker.reference:.3f} "
                          f"target {target:.2f}{' clear' if clear else ''}")

            visual = animator.sample(target, clear, now)
            overlay.pump()
            if visual.is_clear:
                overlay.hide()
                camera.wait(0.25)
            else:
                overlay.show()
                overlay.render(visual)   # blocks on vsync
    except KeyboardInterrupt:
        pass
    finally:
        if camera is not None:
            camera.stop()
        overlay.close()
        if icon:
            icon.stop()


def lid_script(t: float, still: bool) -> float:
    """Simulated lid closure: rest open, close over 1.8 s, hold, reopen.

    `still` stops at 60 % and holds past the clear timeout."""
    top, hold = (0.6, 3.0) if still else (1.0, 0.8)
    t %= 0.8 + 1.8 + hold + 1.8 + 0.8
    if t < 0.8:
        return 0.0
    if t < 2.6:
        return top * (0.5 - 0.5 * math.cos(math.pi * (t - 0.8) / 1.8))
    if t < 2.6 + hold:
        return top
    if t < 4.4 + hold:
        return top * (0.5 + 0.5 * math.cos(math.pi * (t - 2.6 - hold) / 1.8))
    return 0.0


def simulate(settings: Settings, seconds: float, still: bool = False) -> None:
    """Drive the real overlay from a scripted lid; no camera involved."""
    tracker = LidTracker(0.0, 1.0, settings.clear_after, settings.still_epsilon)
    animator = FoldAnimator()
    overlay = Overlay(settings.effect, settings.blur, settings.shadow)
    start = time.monotonic()
    frames, worst = 0, 0.0
    try:
        while (now := time.monotonic()) - start < seconds:
            target, clear = tracker.update(lid_script(now - start, still), now)
            visual = animator.sample(target, clear, now)
            overlay.pump()
            if visual.is_clear:
                overlay.hide()
                time.sleep(1 / 60)
                continue
            overlay.show()
            t0 = time.perf_counter()
            overlay.render(visual)
            worst = max(worst, time.perf_counter() - t0)
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        overlay.close()
    print(f"rendered {frames} frames, slowest {worst * 1000:.1f} ms")
