# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- After a forced stop or crash, tray clicks could do nothing until restart.
  The killed UI's WebView2 browser process stayed alive, holding the
  profile folder. A new UI on that folder attached to the orphan, and its
  pages never loaded. Now:
  - each UI gets its own profile folder;
  - orphaned lidfold browsers are ended in the background (they also held
    memory);
  - a UI whose pages have not loaded within 20 s exits, and the app starts a
    fresh one.

  Verified by force-killing the app, then running end-to-end trials straight
  after (4/4 pass; before, every one failed).
- The app logs why it exits, and logs errors with their traceback. It runs
  windowless, so an exit used to leave no trace.
- The panel could show another process's pages. Outside private mode
  pywebview serves every app on the fixed port 42001, and Windows lets a
  second server bind it too, so after an update the panel showed the old
  install's markup. Any other pywebview app could also have served pages
  with access to the panel's API. The UI now serves from its own free port.
- Dragging a slider in the panel moved the whole panel instead. pywebview
  makes frameless windows draggable from any point by default; that is now
  off for every lidfold window.
- The panel grew taller with every live update. It measured its
  window-filling container, so the window's own height fed back, adding
  2 px per update. It now measures the content and resizes only on change.
  Each UI starts with a fresh WebView2 profile, so updated pages are never
  served stale from cache.
- Clicking the tray icon did nothing. The UI froze through several
  blocking paths: page updates waiting on unloaded pages, styling inside
  pywebview's load sequence, pywin32 holding the GIL in `SetWindowLong`,
  and raw `ShowWindow` on pywebview's hidden forms. Transparent windows
  also ignored `hidden` and appeared as grey boxes. Now clicks are queued
  and non-blocking, window calls use ctypes, and windows are opaque. Early
  clicks open the panel once it is ready. Verified with 5 back-to-back
  end-to-end runs (panel, HUD, banner, hidden at rest).
- The UI exits with its app (orphaned UIs could linger), both processes log
  to `lidfold.log`, and the test suite uses its own data folder.
- Memory: NumPy's OpenBLAS reserved ~480 MB per process (a buffer per CPU
  thread). It is now pinned to one thread, and the UI process no longer
  imports the tracking stack. Effect + UI went from 1.8 GB to 610 MB
  private.

### Added
- Four effects in Apple's motion language, in the panel, tray and
  `--effect`:
  - **Card**: the iPhone app-close card over a blurred, dimmed desktop;
  - **Genie**: the Mac minimize funnel, pouring into the hinge;
  - **Fold**: the top half folds down like a page, showing its frosted back;
  - **Glass**: liquid glass rises from the hinge, refracting at the rim.

  The existing effects render pixel-identically, and each new one is an
  exact copy of the desktop at rest. The effect list now lives in one place
  (`config.EFFECTS`).
- Max effect angle: the lid angle at which the effect is full, beside the
  degree of action where it starts. Set both on the panel's new **Effect
  range** slider (two handles on one open-to-closed track, with the live
  lid marker), the tray's **Max effect at** menu, `--max-angle`, or
  `max_angle` in `settings.json`. Unset, the calibrated full stop is used,
  so the current behaviour is unchanged.
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
