# lidfold

The iPhone Duo / [Mac Duo](https://github.com/DhananjayBhosale/MacDuo) fold-away
effect for a Windows laptop that has **no lid-angle sensor**. As you close the
lid, the desktop tips away from the hinge, loses focus, and dissolves.
Reopening the lid plays it in reverse.

MacBooks have a hinge sensor that reports the lid angle in degrees. Most
Windows laptops only have an open/closed switch, so lidfold estimates the lid
angle from the **webcam** using three cues:

- **Room motion:** the camera rotates with the lid, so the room slides
  vertically in the image as the lid tilts. Measuring that shift tracks the
  lid from fully open, before any part of the base is visible. That makes a
  start angle around 50° possible.
- **Scene map:** the calibration sweep stores what the room looks like
  every 3°. Matching the current view against it gives an absolute angle,
  so the motion estimate cannot drift.
- **The base edge:** below about 40°, the laptop base's front edge enters
  the bottom of the frame. Its height gives the absolute angle even if the
  room has changed.

## Requirements

- Windows 10 2004+ / Windows 11, a webcam on the lid, Python 3.11+
- Tested on a Lenovo ThinkBook 21LF (Radeon 780M, 3072×1920 @ 60 Hz)

## Install (as a Windows app)

From this checkout:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install -e .
.venv\Scripts\lidfold.exe install
```

`install` sets up lidfold for the current user (no admin needed):

- `%LOCALAPPDATA%\Programs\lidfold\venv`: lidfold's own interpreter, with a
  regular (non-editable) install. Editing or switching branches in this
  checkout cannot break the running app; run `install` again to update it.
- `%LOCALAPPDATA%\Programs\lidfold\bin\lidfold.cmd`, added to your user
  PATH. `lidfold` works in any new terminal. Only this folder goes on PATH,
  not the venv's `python`/`pip`.
- A Start Menu shortcut (lidfold icon) and an `HKCU\…\Run` entry. Both start
  the windowless `lidfoldw.exe run`, so lidfold starts with the tray icon
  every time you sign in.
- Calibration and settings live in `%APPDATA%\lidfold`, shared with this
  checkout.

Run `lidfold install` again to update: it asks the running lidfold to quit
(`lidfold quit` does the same), replaces the program files, and starts the
new version.

`lidfold uninstall` removes the program folder, shortcut, PATH entry and
autostart, but keeps your calibration. `--purge` deletes that too.

## Development setup

The editable install in the steps above is enough for development:
`.venv\Scripts\lidfold.exe` runs this checkout directly.

## Run

```bash
lidfold calibrate   # once, ~1 min
lidfold debug       # optional: see what the camera detects
lidfold run         # the effect (the installed app starts this at sign-in)
```

**Effect range.** Two estimated lid angles, in real degrees:
- the **degree of action**, where the effect starts;
- the **max effect angle**, where it is full.

Absolute angles are anchored at your calibrated full stop, taken as 45°
(`full_angle` in `settings.json`; change it if your full stop is at a
different angle). The estimate is most accurate there, because the base
edge pins it down; above it, motion tracking adds real degrees. The panel
and the tray tooltip show the live estimate ("lid ~72 deg"), so you can
pick values. Set them with the panel's **Effect range** slider, the tray's
**Starts at** and **Max effect at** menus, `--action-angle 70 --max-angle
35`, or `action_angle` and `max_angle` in `settings.json`. The start stays
at least 5° above the max.

Both apply to every effect. Unset (Calibrated), duo runs from the
calibrated start stop to the full stop, and ghost tilts from rest to the
full stop. With only a max set, duo starts at the calibrated start and
ghost still starts from rest.

**The panel.** `run` puts a laptop icon in the tray: blue while active,
grey with a padlock while paused. **Left-click it** for the panel, a
macOS-style popover above the tray that closes when you click elsewhere:

- Header: on/off switch, and a status pill with the live lid angle
  ("Active · lid 72°", "Paused", "Calibration running…").
- **Effect** cards: Duo, Ghost, Flat, Iris.
- **Blur** and **Shading** sliders; changes apply live.
- **Effect range**: one track from open (left) to closed (right) with two
  handles: where the effect starts and where it is full (the blue one). An
  orange marker shows the lid's current angle, so you can tilt the lid and
  set either end right there. **Use calibration** clears both.
- **Analysis rate** (10–30 fps) and **Clear when the lid is still** (1–5 s
  or Never).
- **Preview** and **Calibrate…**; **Open at sign-in**; **Advanced** holds
  the camera index, Debug view, Record 15 s and the settings folder.

Also:
- **Ctrl+Alt+Shift+F** pauses and resumes from anywhere, after Mac Duo's
  ⌃⌥⌘F. A HUD confirms it in the middle of the screen, like the macOS volume
  HUD.
- Results (calibration saved, a tool stopped) appear as banners at the top
  right.
- The first start without a calibration opens a **Welcome** window that
  walks you through it. **About** is in the panel footer.
- Right-click the tray icon for the classic menu with the same options.
- Pausing also **releases the camera**, so the webcam light goes off. Only
  one lidfold runs at a time; `run --no-tray` runs without the tray and
  panel.

How the UI is built:
- The panel, HUD, banners and windows run in a **companion process**
  (`lidfold ui`, started automatically). They are web pages in the system
  WebView2, made frameless, with Windows 11 rounded corners and backdrop
  applied through DWM.
- A separate process is used because the effect's render loop needs the
  main thread for OpenGL, and so does any GUI toolkit. It also means a UI
  problem can never stop the effect.
- The two processes talk over a named pipe authenticated with a per-run
  random key. Every request from the UI is validated before it changes
  anything.
- All windows are created hidden at startup, so the panel opens instantly.
- Lessons from making the tray click reliable (each one froze the UI or
  left windows misbehaving):
  - The pipe reader never blocks. Page updates are queued until a page
    loads, then run on short-lived threads.
  - Window operations go through pywebview's own API (raw `ShowWindow` left
    the hidden forms invisible), on one ordered worker thread.
  - pywebview event handlers only queue work: styling a window inside the
    `loaded` handler deadlocked pywebview's own load sequence.
  - Window calls use `ctypes`, which releases the GIL. pywin32's
    `SetWindowLong` holds it while waiting on the window thread, and that
    froze the whole process.
  - Windows are not transparent: pywebview ignores `hidden=True` for
    transparent windows, so they popped up as empty grey boxes. Each window
    background matches its surface colour instead.
  - `easy_drag` is off: pywebview lets you drag a frameless window from any
    point by default, so dragging a slider moved the panel instead.
  - Pages are served from a free port, not pywebview's fixed 42001. Two
    processes can both bind 42001 on Windows, and the panel then loaded
    whichever process's pages answered, such as an old install's.
  - The UI exits with its app, by watching the app's process handle, so a
    stuck UI can't linger as an orphan.
  - Both processes log to `%APPDATA%\lidfold\lidfold.log`.

**Calibration** (one slow sweep, then click-to-label):

0. With the lid in its normal position, move naturally for 4 s. These frames
   teach the model what is *not* base: you, your shirt, the room.
1. Move **out of the camera's view**. The normal lid position becomes 0°.
2. Make one smooth tilt forward, reaching from the side so your body and arm
   stay out of view. Press SPACE at three stops:
   - where the effect should **start** (for example about 50°);
   - where the base's front edge **just** appears;
   - where the effect should be at **maximum** (for example about 30°).

   The preview shows the measured tilt live. This sweep also records the
   scene map, which is why you need to stay out of view: a hand on the lid
   moves with the camera and hides the motion, and a map of your shirt only
   matches if you sit exactly the same way. If the stops come out too close
   to normal or too close together, calibration asks you to redo the sweep.
3. Open the lid. For each frozen stop, click along the top edge of the base
   (several points if it curves) and press ENTER, or press S if no base is
   visible. Press A to add more angles.

**Other commands:**
- `retrain`: refit from the saved calibration frames.
- `record --seconds N`: save a lid-motion clip.
- `replay <clip.mp4>`: write an annotated video and a CSV trace for tuning.
- `simulate [--seconds N] [--still]`: preview the effect with a scripted lid
  and no camera.
- Global `--effect duo|ghost|flat|iris`, `--camera N`, `--action-angle DEG`
  and `--max-angle DEG`. These, plus `clear_after`, `blur` and `shadow`, can
  be saved in `%APPDATA%\lidfold\settings.json`.

## Test

```bash
.venv\Scripts\python -m pytest
```

## How it works

```
webcam 30 fps ─┬─► pitch odometry ─┐
               ├─► scene map ──────┼─► lid angle ─► LidTracker ─► FoldAnimator ─► overlay
               └─► edge tracker ───┘   (closure 0..1)
```

**Pitch odometry** (`odometry.py`, about 2 ms per frame):
- The lid's hinge axis is parallel to the image rows, so tilting the lid
  slides the scene vertically. Closing it moves the scene up.
- Consecutive frames are compared with phase correlation on a 4×2 grid of
  tiles in the upper two-thirds of a 320×240 frame.
- A response-weighted median combines the tiles, so a person moving, or the
  near desk sliding faster than the far wall, cannot drag it.
- Thresholds were tuned on a recorded slow close, about 1 px per frame.
  Stricter filters (large deadzone, run confirmation) booked only 30° of a
  ~100° sweep.
- A hand holding the lid moves with the camera and looks static. When most
  tiles look static but at least 3 agree on a real shift, the moving tiles
  win.

**Scene map** (`scenemap.py`):
- Keeps one keyframe every 3° from the calibration sweep.
- About 10 times a second, the view is matched against the 3 keyframes
  nearest the current estimate. Angle = keyframe angle + residual shift.
- A match needs response ≥ 0.45 and 75% tile agreement, and is applied only
  when two lookups in a row agree.
- There is deliberately **no** search across all keyframes. On a real
  recording it locked onto a wrong keyframe and jumped the angle from 0° to
  44° with the lid at normal, which is the "triggers at normal" bug.

**Lid angle** (`angle.py`):
- Odometry accumulates the angle, "degrees closed past normal".
- The scene map, and the base edge when it is detected this frame, pull the
  estimate toward their absolute angles.
- If nothing has moved for 3 s and neither absolute cue is available, the
  angle relaxes back to 0 (normal). Drift therefore cannot leave the effect
  showing at the normal position.
- The effect runs from the calibrated start stop to the full stop. Darkness
  takes over in the last few degrees.
- After startup or sleep/resume, the angle resets to 0.

**`phaseCorrelate` gotcha:** OpenCV 5 applies the window to its *input*
arrays in place. Tiles are copied before matching; otherwise stored frames
and keyframes get corrupted.

**Edge tracker** (`features.py`, `model.py`, `detector.py`, about 5 ms per
frame at 160×120). The base is rigid relative to the camera, so at a given
lid angle it always occupies the same region with the same edge shape. Lid
angle is the only unknown, and that is the only thing the tracker fits:

1. A tiny MLP (12 → 24 → 1, trained in NumPy during calibration in about
   2 s) scores each pixel from colour **and** texture at two scales:
   gradients, local contrast, Laplacian. A grey shirt and a grey palm rest
   have the same colour but different texture. Training uses exposure,
   white-balance and blur jitter, plus mirroring.
2. The clicked edges define a one-parameter family of boundary curves. For
   every candidate height, the score is the base evidence below the curve
   plus the vertical edge strength along it, and the best height wins. A
   patch the same colour in mid-frame cannot win against a rigid, full-width
   shape.
3. **Immediate onset:** the effect starts once about 2 rows of base are seen
   in 2 consecutive frames. It switches off after 3 frames without base, and
   a 3-frame median smooths the height.
4. **Darkness:** takes over for the last few degrees, when the camera sees
   almost nothing. It only counts after the base has been seen.

**LidTracker** (`fold.py`, adapted from Mac Duo): if you hold the lid still
for `clear_after` seconds, the effect clears, and later movement is measured
from that resting position. Working with the lid lowered therefore never
leaves the desktop folded.

**Overlay** (`overlay.py`, `shaders.py`):
- **duo** (default): a port of Mac Duo's default "Duo". There is no 3D
  projection: each row is magnified by `1 + p·(0.12 + 0.55·height)` around
  the bottom-centre hinge. Icons swell, upper content leaves through the
  top, and nothing ever shrinks. Blur grows with height, the edges get dark
  feathering, then it dissolves. As Mac Duo's shader puts it, "the physical
  lid already supplies the camera's trapezoid".
- **ghost**: a port of Mac Duo's "Ghost", shader and behaviour. A fixed
  viewer's ray through the tilting panel meets the resting plane, so the
  desktop looks anchored where the lid rested. As in Mac Duo, it does not
  wait for the start angle:
  - it is live as soon as the lid leaves rest, with a 1° deadband against
    jitter;
  - its tilt is the real rotation since rest (the angle tracker's "degrees
    closed past normal");
  - it peaks at your calibrated full stop (where the effect should be
    biggest, ~45°), instead of over Mac Duo's 100° sweep;
  - the viewer is placed for a 105° resting angle, Mac Duo's default;
  - holding the lid still clears it and rebases, like every effect.
- Duo and ghost are driven by Mac Duo's curves from `FoldVisualState.at`: progress,
  a defocus that is barely visible for the first few degrees, and tilt. They
  run over Mac Duo's 105°→5° sweep. For duo that sweep is mapped onto your
  calibrated start→full range; ghost uses real degrees from rest, exactly as
  Mac Duo does. Defaults follow Mac Duo: perspective 0.7, blur 0.65, shadow 0.65.
- **flat**: the original look, where the desktop lies down toward the
  keyboard. **iris**: aperture blades.
- The desktop is captured with Desktop Duplication (dxcam) and redrawn by a
  GLSL shader in a click-through, topmost window.
- The window is marked `WDA_EXCLUDEFROMCAPTURE`, so the capture keeps seeing
  the live desktop under it and never captures itself.
- It is hidden while the effect is clear.
- Blur comes from the mipmap chain plus 5 taps.

**Cost:**
- At rest: about 22% of one core, for the webcam, odometry, scene map and
  edge tracker.
- While folding: 60 fps, with about 2 ms of GPU time for the shader.

**Camera frame rate.** Webcams often ignore frame-rate requests: this one
streams 30 fps over both MSMF and DirectShow, whatever is asked. lidfold
therefore enforces the rate itself. Frames are scheduled evenly, and skipped
frames are grabbed without decoding. The camera keeps streaming at 30 fps
(same power, same light); what changes is lidfold's own CPU use. Measured
live on this laptop, as a share of one core:

| Setting | lidfold CPU | Tracking vs 30 fps on recorded closes |
|---|---|---|
| 30 fps (default) | 22% | reference |
| 20 fps | 19% | ~1° mean difference |
| 15 fps | 16% | ~1° mean, ≤4.5° max |
| 10 fps | 9% | up to 3° mean, 9° max |

15 fps is the sweet spot. Faster lid motion favours higher rates.

**Memory.** By default NumPy's OpenBLAS reserves a buffer per CPU thread
as soon as NumPy is imported: about 480 MB on this 16-thread laptop, in
every lidfold process. lidfold's matrices are tiny, so `lidfold/__init__.py`
pins it to one thread before NumPy loads. Measured on the installed app,
this cut the effect + UI from 1.8 GB to 610 MB private. The UI process also
never imports the tracking stack.

The UI's WebView2 needs its GPU process: with `--disable-gpu` the
transparent panel renders blank.

**Why `cv2.setNumThreads(1)`?** On images this small, OpenCV's thread pool
spun most of the time. Pinning it to one thread cut CPU use about 4×.

## Limitations / notes

- **Turn off camera effects.** *Settings → Bluetooth & devices → Cameras →
  Integrated Camera*: disable background blur and automatic framing (Windows
  Studio Effects or the vendor's equivalent). Blur smears the base's texture,
  and auto-framing moves the crop, which breaks the fixed-geometry model.
  Recalibrate after changing these.
- Above the point where the base becomes visible, the angle comes from the
  room, so it needs some visible texture: a featureless wall in the dark
  gives nothing to track.
- The scene map is tied to where the laptop sat during calibration. After
  moving it to a new spot, lidfold falls back to motion plus base edge, which
  drifts more. Recalibrate at the new spot for the best start-angle accuracy.
- `debug` shows the live angle and MAP when the scene map matches. Press R
  there to re-zero.
- The webcam stays on, so its privacy LED stays lit. On Windows 11, enable
  *Allow multiple apps* on the same camera settings page so video calls can
  share the camera.
- Windows still owns lid-close sleep. If the loop detects a gap (sleep and
  resume), it resets and never wakes up to a folded desktop.
- Only the primary display is animated.
- On this machine, camera index 1 is a virtual camera (letterboxed). Use
  index 0.
- Your screenshots will not show the overlay, because it is excluded from
  capture on purpose.

## Credits

The fold-tracking design (rebasing on the resting angle, clearing when the
lid is held still, timed ease-out) follows
[Mac Duo](https://github.com/DhananjayBhosale/MacDuo) by Dhananjay Bhosale
(MIT). The shaders here are independent GLSL implementations.
