# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
