"""lidfold command line: run | calibrate | retrain | debug | record | replay | simulate | install | uninstall."""
from __future__ import annotations

import argparse

from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="lidfold", description=__doc__)
    parser.add_argument("--camera", type=int, help="camera index (default from settings, 0)")
    parser.add_argument("--action-angle", type=float, metavar="DEG",
                        help="degree of action: estimated lid angle at which the effect starts")
    parser.add_argument("--effect", choices=["duo", "ghost", "flat", "iris"],
                        help="fold style: duo / ghost (ports of Mac Duo's), flat, iris")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("calibrate", help="label the base edge at a few lid angles and train")
    sub.add_parser("retrain", help="retrain from the saved calibration frames")
    rec = sub.add_parser("record", help="record a lid-motion clip for tuning")
    rec.add_argument("--seconds", type=float, default=15.0)
    rep = sub.add_parser("replay", help="run the detector over a recording")
    rep.add_argument("path")
    sub.add_parser("debug", help="live camera view with detected base and fold value")
    run = sub.add_parser("run", help="run the effect")
    run.add_argument("-v", "--verbose", action="store_true", help="print every reading")
    run.add_argument("--no-tray", action="store_true", help="run without the system-tray icon")
    inst = sub.add_parser("install", help="install as a Windows app: own folder, CLI on PATH, "
                                          "Start Menu, start at sign-in")
    inst.add_argument("--no-start", action="store_true", help="do not launch it afterwards")
    uninst = sub.add_parser("uninstall", help="remove the installed app, shortcut, PATH entry, autostart")
    uninst.add_argument("--purge", action="store_true", help="also delete calibration and settings")
    sim = sub.add_parser("simulate", help="preview the effect with a scripted lid")
    sim.add_argument("--seconds", type=float, default=12.0)
    sim.add_argument("--still", action="store_true",
                     help="pause half-closed to show the hold-still clear")
    args = parser.parse_args()

    settings = Settings.load()
    if args.camera is not None:
        settings.camera = args.camera
    if args.effect:
        settings.effect = args.effect
    if args.action_angle is not None:
        settings.action_angle = args.action_angle

    if args.command == "calibrate":
        from .calibrate import calibrate
        calibrate(settings)
    elif args.command == "retrain":
        from .calibrate import load_dataset, train
        train(settings, *load_dataset())
    elif args.command == "record":
        from .calibrate import record
        record(settings, args.seconds)
    elif args.command == "replay":
        from pathlib import Path
        from .calibrate import replay
        replay(Path(args.path), settings)
    elif args.command == "install":
        from .install import install
        install(start=not args.no_start)
    elif args.command == "uninstall":
        from .install import uninstall
        uninstall(purge=args.purge)
    elif args.command == "debug":
        from .calibrate import debug
        debug(settings)
    elif args.command == "run":
        from .app import run as run_app
        run_app(settings, args.verbose, tray=not args.no_tray)
    else:
        from .app import simulate
        simulate(settings, args.seconds, args.still)


if __name__ == "__main__":
    main()
