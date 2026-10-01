"""Command-line entry: gui (default), service/watch, analyze, convert-baseline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .logutil import attach_console_if_needed, configure_logging, ensure_stdio, stream_isatty

KNOWN_COMMANDS = {
    "analyze",
    "watch",
    "service",
    "gui",
    "convert-baseline",
}
PARENT_FLAGS = {"-v", "--verbose", "-h", "--help"}
ENV_SETTINGS = "CTQA_CATPHAN_SETTINGS"


def _insert_command(tokens: list[str], command: str) -> list[str]:
    i = 0
    while i < len(tokens) and tokens[i] in PARENT_FLAGS:
        i += 1
    return tokens[:i] + [command] + tokens[i:]


def _env_path(value: str) -> str:
    path = Path(value).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    return str(path)


def wants_help(argv: list[str]) -> bool:
    return any(tok in ("-h", "--help") or tok.startswith("--help") for tok in argv)


def prepare_argv(argv: list[str] | None = None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    settings: str | None = None
    mode: str | None = None
    out: list[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok in ("--settings", "-s", "--config") and i + 1 < len(raw):
            settings = raw[i + 1]
            i += 2
            continue
        if tok.startswith("--settings=") or tok.startswith("--config="):
            settings = tok.split("=", 1)[1]
            i += 1
            continue
        if tok == "--mode" and i + 1 < len(raw):
            mode = raw[i + 1].strip().lower()
            i += 2
            continue
        if tok.startswith("--mode="):
            mode = tok.split("=", 1)[1].strip().lower()
            i += 1
            continue
        out.append(tok)
        i += 1
    if settings:
        os.environ[ENV_SETTINGS] = _env_path(settings)
    existing = next((tok for tok in out if tok in KNOWN_COMMANDS), None)
    if existing == "service":
        out = ["watch" if tok == "service" else tok for tok in out]
        existing = "watch"
    wanted: str | None = None
    if mode in ("service", "watch"):
        wanted = "watch"
    elif mode == "gui":
        wanted = "gui"
    elif existing is None and not (out and out[0] in ("-h", "--help")):
        wanted = "gui"
    if wanted and existing is None:
        out = _insert_command(out, wanted)
    elif wanted and existing and existing != wanted:
        out = [wanted if tok == existing else tok for tok in out]
    return out


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if wants_help(raw):
        if getattr(sys, "frozen", False) and not stream_isatty(sys.stdout) and not stream_isatty(
            sys.stderr
        ):
            attach_console_if_needed()
    else:
        ensure_stdio()
    argv = prepare_argv(argv)
    parser = argparse.ArgumentParser(
        prog="CTQA-CatPhan",
        epilog=(
            "With no command, opens the GUI. Use --mode service (or the 'watch' command) "
            "for the DailyQA folder watcher. --settings FILE selects settings.json; "
            "if omitted, settings.json next to the executable is used."
        ),
    )
    parser.add_argument(
        "--settings",
        "-s",
        "--config",
        metavar="FILE",
        help="path to settings.json (default: next to this executable)",
    )
    parser.add_argument(
        "--mode",
        choices=("gui", "service"),
        help="gui (default) or service (folder watcher)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_case = sub.add_parser("analyze", help="analyze one case folder")
    p_case.add_argument("case_dir")
    p_case.add_argument("--machine", default="")
    p_case.add_argument("--email", action="store_true")

    p_watch = sub.add_parser("watch", help="watch DailyQA import folders")
    p_watch.add_argument("--watch-path", default="")

    sub.add_parser("gui", help="open the CatPhan viewer / analysis app")

    p_conv = sub.add_parser("convert-baseline", help="convert baseline nrrd/mhd to compressed .mha")
    p_conv.add_argument("baseline_dir", nargs="?", help="defaults to MACHINES[0].baseline_dir")

    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, console=True)

    if args.cmd == "analyze":
        from .pipeline import run_case

        run_case(args.case_dir, machine_name=args.machine, send_email=args.email)
        return 0

    if args.cmd == "watch":
        from .watcher import WatchPathUnavailable, watch

        try:
            watch(args.watch_path)
        except WatchPathUnavailable as exc:
            print(exc, file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Watcher stopped.", file=sys.stderr)
            return 0
        return 0

    if args.cmd == "gui":
        from .gui import run_app

        return run_app()

    if args.cmd == "convert-baseline":
        from .app_settings import default_machine, load_settings
        from .masks import convert_nrrd_dir

        folder = args.baseline_dir
        if not folder:
            machine = default_machine(load_settings())
            if machine is None:
                print("no machine / baseline_dir in settings.json", file=sys.stderr)
                return 2
            folder = str(machine.get("baseline_dir") or "")
        written = convert_nrrd_dir(folder)
        print(f"converted {len(written)} images in {folder}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
