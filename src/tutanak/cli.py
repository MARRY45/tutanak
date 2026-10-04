"""Command line: ``tutanak`` starts the app; see ``tutanak --help`` for the rest."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .config import Config, default_home


def _services(home: str | None):
    from .pipeline import Services

    return Services(Config(Path(home).expanduser() if home else None))


def _serve(services, host: str, port: int, open_browser: bool) -> int:
    import uvicorn

    from .audio import capture
    from .netguard import is_local_host_header
    from .server import create_app

    if not is_local_host_header(host):
        print(
            f"Refusing to listen on {host}: Tutanak has no login and holds private recordings.\n"
            "Keep it on 127.0.0.1 and use an SSH tunnel if you need remote access.",
            file=sys.stderr,
        )
        return 2
    capture.preload()  # the audio backend must first be imported on the main thread
    url = f"http://{host}:{port}"
    print(f"Tutanak {__version__}  ·  {url}  ·  data: {services.cfg.home}")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        create_app(services), host=host, port=port, log_level="warning", timeout_graceful_shutdown=2
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    return _serve(_services(args.home), args.host, args.port, not args.no_browser)


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import seed_demo

    home = args.home or str(default_home().with_name(default_home().name + "-demo"))
    services = _services(home)
    seed_demo(services)
    print("Demo data: two sample meetings (no microphone, model or network needed).")
    return _serve(services, args.host, args.port, not args.no_browser)


def cmd_import(args: argparse.Namespace) -> int:
    from .audio.files import ffmpeg_path
    from .importer import create_import, run_import

    source = Path(args.file).expanduser()
    if not source.is_file():
        print(f"File not found: {source}", file=sys.stderr)
        return 2
    if ffmpeg_path() is None:
        print("ffmpeg is required to import recordings and was not found on PATH.", file=sys.stderr)
        return 2
    services = _services(args.home)

    def show(event: dict) -> None:
        if event["type"] == "progress" and event.get("stage"):
            fraction = event.get("fraction")
            percent = f" {fraction * 100:3.0f}%" if fraction is not None else ""
            print(f"\r  {event['stage']}{percent}".ljust(40), end="", flush=True)

    services.bus.add_listener(show)
    meeting_id = create_import(services, source, title=args.title)
    run_import(services, meeting_id, source, language=args.language)
    meeting = services.store.get_meeting(meeting_id)
    print(f"\r{'':40}\r", end="")
    if meeting["status"] == "failed":
        print(f"Import failed: {meeting['error']}", file=sys.stderr)
        return 1
    print(f"{meeting_id}  {meeting['title']}")
    print(
        f"  {len(services.store.segments(meeting_id))} segments, "
        f"{len(services.store.slides(meeting_id))} slides, "
        f"{len(services.store.notes(meeting_id))} note blocks"
    )
    if meeting["error"]:
        print(f"  notes were not written: {meeting['error']}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    from .asr import MODEL_SIZE_MB, AsrUnavailable

    transcriber = _services(args.home).transcriber
    info = transcriber.info()
    if not info["available"]:
        print("Speech recognition is not installed: pip install 'tutanak[asr]'", file=sys.stderr)
        return 1
    if info["cached"]:
        print(f"{info['model']} is already on this machine.")
        return 0
    size = MODEL_SIZE_MB.get(info["model"])
    print(f"Downloading {info['model']}" + (f" (about {size} MB)" if size else "") + " ...")
    try:
        transcriber.load()
    except AsrUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Ready: {transcriber.model_name} on {transcriber.device}.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from .textutil import fmt_ts

    for meeting in _services(args.home).store.list_meetings():
        print(
            f"{meeting['id']}  {meeting['started_at'][:16].replace('T', ' ')}  "
            f"{fmt_ts(meeting['duration']):>8}  {meeting['status']:<10}  {meeting['title']}"
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import to_markdown

    services = _services(args.home)
    text = to_markdown(services.store, args.id, language=services.settings.ui_language)
    if text is None:
        print(f"No meeting with id {args.id}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .asr import cuda_ready
    from .audio import capture
    from .audio.files import ffmpeg_path
    from .screen import capture as screen

    services = _services(args.home)
    settings = services.settings
    rows: list[tuple[str, bool, str]] = []

    try:
        devices = capture.list_devices()
        detail = (
            f"{len(devices['microphones'])} microphone(s), {len(devices['speakers'])} speaker(s)"
        )
        rows.append(("audio capture", True, detail))
    except Exception as exc:
        rows.append(("audio capture", False, str(exc)))
    try:
        rows.append(("screen capture", True, f"{len(screen.list_monitors())} monitor(s)"))
    except Exception as exc:
        rows.append(("screen capture", False, str(exc)))

    asr = services.transcriber.info()
    if asr["available"]:
        gpu = "GPU" if cuda_ready() else "no usable CUDA libraries: pip install 'tutanak[cuda]'"
        cached = "" if asr["cached"] else "  ->  not downloaded yet: tutanak download"
        rows.append(
            ("speech recognition", True, f"{asr['model']} on {asr['device']}  [{gpu}]{cached}")
        )
    else:
        rows.append(("speech recognition", False, "pip install 'tutanak[asr]'"))

    ocr = services.ocr
    rows.append(("slide OCR", ocr is not None, ocr.name if ocr else "pip install 'tutanak[ocr]'"))
    rows.append(
        ("ffmpeg", ffmpeg_path() is not None, ffmpeg_path() or "not on PATH (needed for import)")
    )

    llm = services.llm.health()
    if services.llm_error:
        rows.append(("local model server", False, services.llm_error))
    elif not llm["reachable"]:
        rows.append(("local model server", False, f"nothing listening at {settings.llm_base_url}"))
    else:
        rows.append(("local model server", True, settings.llm_base_url))
        hint = "" if llm["model_available"] else f"  ->  ollama pull {settings.llm_model}"
        rows.append(("  notes model", llm["model_available"], settings.llm_model + hint))
        if settings.embed_model:
            hint = "" if llm["embed_available"] else f"  ->  ollama pull {settings.embed_model}"
            rows.append(("  embedding model", llm["embed_available"], settings.embed_model + hint))

    print(f"Tutanak {__version__}\ndata directory: {services.cfg.home}\n")
    for name, ok, detail in rows:
        print(f"  [{'ok' if ok else '--'}] {name:<20} {detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    def options(*, top: bool, web: bool) -> argparse.ArgumentParser:
        # Options are accepted before or after the command. On subcommands the
        # defaults are suppressed so they do not overwrite a value given up front.
        def default(value):
            return value if top else argparse.SUPPRESS

        group = argparse.ArgumentParser(add_help=False)
        group.add_argument(
            "--home",
            default=default(None),
            help="data directory (default: ~/.tutanak or $TUTANAK_HOME)",
        )
        if web:
            group.add_argument("--host", default=default("127.0.0.1"))
            group.add_argument("--port", type=int, default=default(8765))
            group.add_argument(
                "--no-browser",
                action="store_true",
                default=default(False),
                help="do not open the browser",
            )
        return group

    common, web = options(top=False, web=False), options(top=False, web=True)
    parser = argparse.ArgumentParser(
        prog="tutanak",
        parents=[options(top=True, web=True)],
        description="Local-first meeting memory. Run without arguments to start the app.",
    )
    parser.add_argument("--version", action="version", version=f"tutanak {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.set_defaults(func=cmd_serve)
    sub = parser.add_subparsers(metavar="command")

    sub.add_parser("serve", parents=[web], help="start the app (default)").set_defaults(
        func=cmd_serve
    )
    sub.add_parser(
        "demo", parents=[web], help="start with two sample meetings; needs no model"
    ).set_defaults(func=cmd_demo)
    imp = sub.add_parser("import", parents=[common], help="import an audio or video recording")
    imp.add_argument("file")
    imp.add_argument("--title")
    imp.add_argument("--language", help="spoken language, e.g. tr or en (default: detect)")
    imp.set_defaults(func=cmd_import)
    sub.add_parser(
        "download", parents=[common], help="fetch the speech model now instead of on first use"
    ).set_defaults(func=cmd_download)
    sub.add_parser("list", parents=[common], help="list meetings").set_defaults(func=cmd_list)
    exp = sub.add_parser("export", parents=[common], help="export a meeting as Markdown")
    exp.add_argument("id")
    exp.add_argument("-o", "--output")
    exp.set_defaults(func=cmd_export)
    sub.add_parser(
        "doctor", parents=[common], help="check what works on this machine"
    ).set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
