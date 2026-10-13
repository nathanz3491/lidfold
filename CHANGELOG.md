# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Memory: NumPy's OpenBLAS reserved ~480 MB per process (a buffer per CPU
  thread). It is now pinned to one thread, and the UI process no longer
  imports the tracking stack. Effect + UI went from 1.8 GB to 610 MB
  private.

### Added
- macOS-style UI:
  - a popover panel on tray left-click: on/off switch, live status, effect
    cards, sliders, a "Starts at" slider with a live lid-angle marker,
    segmented controls, Preview and Calibrate;
  - a pause/resume HUD and Ctrl+Alt+Shift+F hotkey;
  - banners for results, a first-run Welcome window, and an About window.

  It runs as a companion WebView2 process over an authenticated named pipe,
  and every UI request is validated.
- Camera frame rate (10/15/20/30 fps) in the tray, applied live. The camera
  ignores rate requests, so lidfold schedules which frames to analyse and
  grabs the rest without decoding. Measured CPU: 22% at 30 fps, 9% at 10.
- Degree of action: the estimated lid angle at which the effect starts. It
  is anchored at the calibrated full stop (45°) and applies to every effect.
  Set it from the tray's "Starts at" menu, `--action-angle`, or settings.
  The tray tooltip shows the live estimated lid angle.
- `lidfold quit`. `lidfold install` now stops a running copy (named quit
  event, with a narrowly matched process fallback), updates it and restarts
  it; `uninstall` stops it too.

## [0.1.0] - 2026-10-12

### Added
- Webcam lid-angle tracking for laptops without a hinge sensor:
  - Pitch odometry: phase correlation over a tile grid with a
    response-weighted median, robust to a hand on the lid and to parallax.
  - Scene map: keyframes every 3° from the calibration sweep give an
    absolute angle. A match is applied only when two lookups in a row agree.
  - Base-edge tracker: a per-pixel colour and texture MLP plus a
    one-parameter fit over the calibrated edge-shape family, with hysteresis.
  - Fusion with darkness for the last degrees, relaxation back to normal at
    rest, and fast relaxation when reopening.
- Calibration: out-of-view sweep with start, edge and full stops,
  click-to-label edges, validation, plus `retrain`, `debug`, `record` and
  `replay` for tuning on real footage.
- Fold overlay: Desktop Duplication capture, a click-through window excluded
  from capture, and GLSL effects:
  - `duo` and `ghost`: ports of Mac Duo's shaders and angle curves. Ghost
    is live from rest and peaks at the full stop.
  - `flat` and `iris`.
- System-tray app: lock/unlock (which also releases the camera); live Effect,
  Blur, Shading, Clear-when-still and Camera menus; Calibrate, Debug, Record
  and Preview tools; Start with Windows; single-instance guard.
- `lidfold install` / `uninstall`: a per-user Windows app in
  `%LOCALAPPDATA%\Programs\lidfold` with its own venv, a `lidfold` command
  on PATH, a Start Menu shortcut, and start at sign-in.
