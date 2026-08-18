"""Command line: start the interface, or work without one.

Four sub-commands are available. ``ui`` starts the local web interface and is
what runs when no sub-command is given; ``show``, ``export`` and ``strip``
process files straight from a terminal or a script.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .core.exporter import EXPORT_FORMATS, metadata_to_string
from .core.reader import read_metadata
from .core.tags import GROUP_ORDER, REMOVABLE_KINDS
from .core.provenance import detect as detect_provenance
from .core.utils import human_size, scan_folder
from .core.writer import DEFAULT_KINDS, plan_removal, strip_metadata
from .i18n import detect_language, group_label, set_language, t

DEFAULT_PORT = 7860


def make_output_printable() -> None:
    """Never let an unencodable character abort the output.

    A Windows console redirected to a file falls back to the ANSI code page,
    where the status icons have no representation. Replacing them beats raising
    ``UnicodeEncodeError`` halfway through a report.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):  # pragma: no cover
            pass

#: How many ports to try after the default one before giving up.
PORT_SCAN_RANGE = 64


# ------------------------------------------------------------------------ ports
def port_is_free(host: str, port: int) -> bool:
    """Tell whether the server could bind this TCP port on this host.

    The probe mirrors what uvicorn does, so that its answer matches what the
    real bind will do:

    - On Unix, uvicorn sets ``SO_REUSEADDR``, which lets it reuse a port still
      holding connections in ``TIME_WAIT``. Without the option here, restarting
      the application right after closing a browser tab would needlessly move it
      to the next port.
    - On Windows, ``SO_REUSEADDR`` means something else entirely: it allows
      binding a port another process actively holds. Setting it there would make
      every check succeed, so the option stays off.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(host: str, start: int = DEFAULT_PORT,
                   attempts: int = PORT_SCAN_RANGE) -> Optional[int]:
    """Return the first free port at or after ``start``, or ``None`` if none is."""
    for port in range(start, start + attempts):
        if port_is_free(host, port):
            return port
    return None


def resolve_port(host: str, requested: Optional[int]) -> Optional[int]:
    """Pick the port to listen on, reporting the reason when none works.

    An explicit ``--port`` is honoured as given: if it is busy, the caller hears
    about it instead of silently landing somewhere else. Without one, the
    default port is used when free and the next free port otherwise, which keeps
    the application usable next to another Gradio app.
    """
    if requested is not None:
        if port_is_free(host, requested):
            return requested
        print(t("cli_port_busy", port=requested, host=host), file=sys.stderr)
        return None
    port = find_free_port(host)
    if port is None:
        print(t("cli_no_free_port", start=DEFAULT_PORT,
                end=DEFAULT_PORT + PORT_SCAN_RANGE - 1), file=sys.stderr)
        return None
    if port != DEFAULT_PORT:
        print(t("cli_port_auto", default=DEFAULT_PORT, port=port))
    return port


# ------------------------------------------------------------------- file input
def collect_paths(inputs: Sequence[str], recursive: bool = False) -> List[Path]:
    """Expand a mix of files and folders into a flat list of image files.

    Paths that do not exist are reported on stderr and skipped, so one bad
    argument never aborts a batch.
    """
    paths: List[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            paths.extend(scan_folder(path, recursive))
        elif path.is_file():
            paths.append(path)
        else:
            print(f"⚠️  {t('cli_skipped', path=path)}", file=sys.stderr)
    return paths


def _labels() -> dict:
    """Return the group labels used by the human-readable export formats."""
    return {key: group_label(key) for key in GROUP_ORDER}


# --------------------------------------------------------------------- commands
def cmd_ui(args: argparse.Namespace) -> int:
    """Start the local web interface and block until it is stopped.

    Sharing the interface publicly turns off local folder access and disables
    overwriting originals, so a public link cannot reach the filesystem.
    """
    import gradio as gr

    from . import gr_compat as gc
    from .app import WORK_DIR, build_interface

    port = resolve_port(args.host, args.port)
    if port is None:
        return 1

    allow_local = not args.share and not args.no_local
    roots = [WORK_DIR]
    if allow_local:
        roots.append(Path.home())
    demo = build_interface(allow_local=allow_local, allowed_roots=roots)
    _, launch_style = gc.place_style(theme=gr.themes.Soft())
    try:
        demo.launch(
            server_name=args.host,
            server_port=port,
            share=args.share,
            inbrowser=not args.no_browser,
            show_error=True,
            allowed_paths=[str(root) for root in roots],
            quiet=args.quiet,
            **launch_style,
        )
    except OSError as exc:
        # The port can be taken between the probe and the actual bind.
        print(t("cli_launch_failed", error=exc), file=sys.stderr)
        return 1
    return 0


def _collect_provenance(metas: Sequence, args: argparse.Namespace) -> Optional[dict]:
    """Run the provenance detectors when the flags ask for them."""
    if not (args.provenance or args.watermark):
        return None
    return {
        meta.path: detect_provenance(meta.path, check_watermark=args.watermark, meta=meta)
        for meta in metas
    }


def cmd_show(args: argparse.Namespace) -> int:
    """Print the metadata of the given files to standard output."""
    paths = collect_paths(args.files, args.recursive)
    if not paths:
        print(t("cli_no_input_show"), file=sys.stderr)
        return 2
    metas = [read_metadata(path, with_hash=not args.no_hash) for path in paths]
    provenance = _collect_provenance(metas, args)
    print(metadata_to_string(
        metas, args.format, groups=args.groups or None, query=args.search or "",
        sensitive_only=args.sensitive, group_labels=_labels(), provenance=provenance,
    ))
    if provenance and args.format != "json":
        # JSON already carries the block; the readable formats get it appended.
        for meta in metas:
            print()
            print(f"[{t('prov_section').upper()}] {meta.filename}")
            for line in provenance[meta.path].as_lines():
                print(line)
    return 0 if all(meta.ok for meta in metas) else 1


def cmd_export(args: argparse.Namespace) -> int:
    """Write the metadata of the given files into one export document."""
    paths = collect_paths(args.files, args.recursive)
    if not paths:
        print(t("cli_no_input_export"), file=sys.stderr)
        return 2
    metas = [read_metadata(path, with_hash=not args.no_hash) for path in paths]
    text = metadata_to_string(
        metas, args.format, groups=args.groups or None, query=args.search or "",
        sensitive_only=args.sensitive, group_labels=_labels(),
        provenance=_collect_provenance(metas, args),
    )
    destination = Path(args.output).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if args.format == "csv" else "utf-8"
    destination.write_text(text, encoding=encoding, newline="")
    print(f"✅ {t('cli_exported', count=len(metas), path=destination)}")
    return 0


def cmd_strip(args: argparse.Namespace) -> int:
    """Remove metadata from the given files and report the result per file."""
    paths = collect_paths(args.files, args.recursive)
    if not paths:
        print(t("cli_no_input_strip"), file=sys.stderr)
        return 2
    kinds = args.remove or ["all"]
    if args.out and len(paths) > 1:
        Path(args.out).expanduser().mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in paths:
        before = read_metadata(path, with_hash=False)
        if args.dry_run:
            plan = plan_removal(before, kinds)
            detail = ", ".join(f"{group_label(k)} ({n})" for k, n in plan.items()) \
                or t("cli_nothing")
            print(f"🔍 {path.name}: {detail}")
            continue
        report = strip_metadata(
            path, target=args.out, kinds=kinds,
            in_place=args.in_place, backup=not args.no_backup, suffix=args.suffix,
        )
        if not report.ok:
            print(f"⛔ {path.name}: {report.error}", file=sys.stderr)
            failures += 1
            continue
        after = read_metadata(report.target, with_hash=False)
        mode = t("cli_lossless") if report.lossless else t("cli_reencoded")
        removed = ", ".join(group_label(k) for k in report.removed_kinds) or t("value_none")
        print(f"✅ {path.name} → {report.target}")
        print(f"   {before.metadata_count} → {after.metadata_count} "
              f"{t('sum_entries')} · {human_size(report.size_before)} → "
              f"{human_size(report.size_after)} · {mode}")
        print(f"   {t('cli_removed')}: {removed}")
        for warning in report.warnings:
            print(f"   ⚠️  {warning}")
        if report.backup:
            print(f"   {t('cli_backup')}: {report.backup}")
    return 1 if failures else 0


# ----------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every sub-command."""
    parser = argparse.ArgumentParser(
        prog="imgmetamanager",
        description="Read, export and remove image metadata, from a local web "
                    "interface or from the command line.",
    )
    parser.add_argument("--version", action="version", version=f"ImgMetaManager {__version__}")
    parser.add_argument("--lang", choices=tuple(sorted(("en", "fr"))), default=None,
                        help="interface and label language (default: from the environment)")
    subparsers = parser.add_subparsers(dest="command")

    ui = subparsers.add_parser("ui", help="start the web interface (the default action)")
    ui.add_argument("--host", default="127.0.0.1", help="network interface to listen on")
    ui.add_argument("--port", type=int, default=None,
                    help=f"port to listen on (default: {DEFAULT_PORT}, "
                         "or the next free one)")
    ui.add_argument("--share", action="store_true",
                    help="create a temporary public link (disables local folder access)")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ui.add_argument("--no-local", action="store_true",
                    help="forbid reading and writing outside the uploaded files")
    ui.add_argument("--quiet", action="store_true", help="reduce Gradio's own output")
    ui.set_defaults(func=cmd_ui)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("files", nargs="+", help="files or folders to process")
        sub.add_argument("-r", "--recursive", action="store_true",
                         help="walk into sub-folders")

    def add_provenance(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--provenance", action="store_true",
                         help="look for AI provenance signals: C2PA manifest and "
                              "declarative metadata")
        sub.add_argument("--watermark", action="store_true",
                         help="also decode the invisible TrustMark watermark, which "
                              "downloads a 40 MB model on first use (implies "
                              "--provenance)")

    show = subparsers.add_parser("show", help="print metadata to the terminal")
    add_common(show)
    show.add_argument("-f", "--format", choices=list(EXPORT_FORMATS), default="txt")
    show.add_argument("-g", "--groups", nargs="*", choices=list(GROUP_ORDER),
                      help="restrict the output to these groups")
    show.add_argument("-s", "--search", default="", help="filter on a tag or a value")
    show.add_argument("--sensitive", action="store_true",
                      help="show only the potentially identifying entries")
    show.add_argument("--no-hash", action="store_true", help="skip the SHA-256 digest")
    add_provenance(show)
    show.set_defaults(func=cmd_show)

    export = subparsers.add_parser("export", help="write metadata to a file")
    add_common(export)
    export.add_argument("-o", "--output", required=True, help="output file")
    export.add_argument("-f", "--format", choices=list(EXPORT_FORMATS), default="json")
    export.add_argument("-g", "--groups", nargs="*", choices=list(GROUP_ORDER))
    export.add_argument("-s", "--search", default="")
    export.add_argument("--sensitive", action="store_true")
    export.add_argument("--no-hash", action="store_true")
    add_provenance(export)
    export.set_defaults(func=cmd_export)

    strip = subparsers.add_parser("strip", help="remove metadata")
    add_common(strip)
    strip.add_argument("--remove", nargs="*", choices=list(REMOVABLE_KINDS) + ["all"],
                       default=["all"],
                       help=f"categories to remove (default: all = {', '.join(DEFAULT_KINDS)})")
    strip.add_argument("--out", help="destination file or folder "
                                     "(a path with no extension is treated as a folder)")
    strip.add_argument("--suffix", default="-clean", help="suffix of the cleaned file")
    strip.add_argument("--in-place", action="store_true", help="rewrite the original file")
    strip.add_argument("--no-backup", action="store_true",
                       help="skip the .bak copy when rewriting in place")
    strip.add_argument("--dry-run", action="store_true", help="report without writing")
    strip.set_defaults(func=cmd_strip)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command line.

    Returns:
        ``0`` on success, ``1`` when at least one file failed, ``2`` when no
        input file was found.
    """
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    make_output_printable()
    args = parser.parse_args(arguments)
    if not hasattr(args, "func"):
        # No sub-command: start the interface, keeping the global options.
        args = parser.parse_args(arguments + ["ui"])
    set_language(args.lang or detect_language())
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
