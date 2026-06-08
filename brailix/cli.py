"""Command-line interface for brailix.

``brailix`` (or ``python -m brailix``) compiles text, Markdown, Word, and
MusicXML sources into braille from a terminal. It is a thin wrapper over
:class:`brailix.Pipeline` and the renderer registry — every pluggable
choice (profile, segmentation engine, pinyin resolver, output renderer)
is enumerated from the core registries, so ``--list-*`` and the accepted
values always reflect what the installed build actually provides rather
than a hand-kept list.

Examples::

    brailix "我在重庆。"                  # Unicode braille to stdout
    brailix -f lesson.md -w 32           # wrap a Markdown file at 32 cells
    brailix "123" --to brf -o out.brf    # NABCC bytes for an embosser
    echo "# 标题" | brailix --in-format markdown
    brailix --list-profiles

The translation surface mirrors the library: input is dispatched the same
way :meth:`brailix.Pipeline.translate_file` dispatches it (by suffix for
``--file``; ``--in-format`` for text / stdin), and output goes through the
same renderers :meth:`brailix.TranslationResult.render` exposes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Literal

from brailix import Pipeline, __version__
from brailix.core import RunMode
from brailix.core.config import iter_builtin_profiles
from brailix.core.defaults import (
    DEFAULT_PINYIN_RESOLVER,
    DEFAULT_PROFILE,
    DEFAULT_RENDERER,
    DEFAULT_ZH_ANALYZER,
)
from brailix.core.errors import BrailixError
from brailix.frontend.ja.analyzer import list_analyzers as list_ja_analyzers
from brailix.frontend.zh.analyzer import list_analyzers as list_zh_analyzers
from brailix.frontend.zh.pinyin import list_resolvers
from brailix.renderer import LayoutOptions, LayoutRenderer, renderer_registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from brailix.pipeline import TranslationResult

# Formats the ``--in-format`` flag (text / stdin) accepts. These mirror
# :meth:`brailix.Pipeline.parse_text`'s contract; the input layer keeps no
# registry for them because the choice is static (a file's suffix, or this
# flag) — see ``brailix/input/__init__.py``. Files passed with ``--file``
# are dispatched by suffix instead and ignore this flag.
IN_FORMATS = ("plain", "markdown", "musicxml")

# Default page width (in cells) for the layout pass when ``--width`` is
# omitted but a layout pass is requested. Matches
# :attr:`brailix.renderer.LayoutOptions.line_width`.
DEFAULT_LAYOUT_WIDTH = 40


def _positive_int(value: str) -> int:
    """argparse ``type`` for cell counts: a base-10 integer >= 1."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected an integer, got {value!r}"
        ) from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {n}")
    return n


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Choices for ``--to`` come from the live renderer registry and ``--mode``
    from the :class:`~brailix.core.RunMode` enum, so they never drift from
    what the core actually supports. ``--profile`` / ``--analyzer`` /
    ``--resolver`` stay free-form strings (validated at run time against
    their registries) so language- and third-party adapters that register a
    name are selectable without changing this parser.
    """
    renderers = renderer_registry.names()
    parser = argparse.ArgumentParser(
        prog="brailix",
        description="Compile text, Markdown, Word, and MusicXML into braille.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )

    parser.add_argument(
        "text",
        nargs="?",
        help="text to translate; omit to read --file or piped stdin",
    )

    src = parser.add_argument_group("input")
    src.add_argument(
        "-f",
        "--file",
        metavar="PATH",
        help="read input from a file, dispatched by suffix "
        "(.md / .docx / .musicxml / ...); needs the matching extra",
    )
    src.add_argument(
        "--in-format",
        dest="in_format",
        choices=IN_FORMATS,
        default="plain",
        help="format for TEXT / stdin (default: plain; ignored for --file)",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="write output to a file (default: stdout)",
    )
    out.add_argument(
        "-t",
        "--to",
        choices=renderers,
        default=DEFAULT_RENDERER,
        help="output renderer: " + " / ".join(renderers) + f" (default: {DEFAULT_RENDERER}). "
        "unicode/brf/cells are encodings; layout is laid-out Unicode braille",
    )
    out.add_argument(
        "-w",
        "--width",
        type=_positive_int,
        metavar="N",
        help="line width in cells; turns on the layout pass (wrap + indent)",
    )
    out.add_argument(
        "--page-height",
        dest="page_height",
        type=_positive_int,
        metavar="N",
        help="lines per page; turns on pagination (layout pass)",
    )
    out.add_argument(
        "--page-numbers",
        dest="page_numbers",
        action="store_true",
        help="print page numbers (needs --page-height)",
    )

    tr = parser.add_argument_group("translation")
    tr.add_argument(
        "-p",
        "--profile",
        default=DEFAULT_PROFILE,
        metavar="NAME",
        help=f"braille profile (default: {DEFAULT_PROFILE}; see --list-profiles)",
    )
    tr.add_argument(
        "-a",
        "--analyzer",
        default=DEFAULT_ZH_ANALYZER,
        metavar="NAME",
        help="word-segmentation engine "
        f"(default: {DEFAULT_ZH_ANALYZER}; see --list-analyzers)",
    )
    tr.add_argument(
        "-r",
        "--resolver",
        default=DEFAULT_PINYIN_RESOLVER,
        metavar="NAME",
        help=f"pinyin resolver (default: {DEFAULT_PINYIN_RESOLVER}; see --list-resolvers)",
    )
    tr.add_argument(
        "-m",
        "--mode",
        choices=[m.value for m in RunMode],
        default=RunMode.NORMAL.value,
        help="diagnostic strictness (default: normal)",
    )

    diag = parser.add_argument_group("diagnostics")
    diag.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress translation warnings on stderr",
    )

    disc = parser.add_argument_group("discovery (print and exit)")
    disc.add_argument(
        "--list-profiles", dest="list_profiles", action="store_true",
        help="list available braille profiles",
    )
    disc.add_argument(
        "--list-analyzers", dest="list_analyzers", action="store_true",
        help="list word-segmentation engines",
    )
    disc.add_argument(
        "--list-resolvers", dest="list_resolvers", action="store_true",
        help="list pinyin resolvers",
    )
    disc.add_argument(
        "--list-renderers", dest="list_renderers", action="store_true",
        help="list output renderers",
    )
    disc.add_argument(
        "-V", "--version", action="store_true", help="print the brailix version",
    )

    return parser


_EPILOG = """\
examples:
  brailix "我在重庆。"                 translate a string to Unicode braille
  brailix -f lesson.md -w 32          wrap a Markdown file at 32 cells
  brailix "123" --to brf -o out.brf   write NABCC bytes for an embosser
  echo "# 标题" | brailix --in-format markdown
  brailix --list-analyzers

A profile, engine, or resolver name shown by the --list-* flags is always
valid even before its optional dependency is installed; selecting one whose
package is missing reports which `pip install brailix[...]` extra to add.
"""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _handle_discovery(args: argparse.Namespace) -> int | None:
    """Run a ``--list-*`` / ``--version`` action if requested.

    Returns the exit code to use, or ``None`` if no discovery flag was set
    (so the caller proceeds to translate).
    """
    if args.version:
        print(f"brailix {__version__}")
        return 0
    if args.list_profiles:
        for name in iter_builtin_profiles():
            print(name)
        return 0
    if args.list_analyzers:
        # Analyzers are language-scoped, so group them by language rather
        # than flattening into one ambiguous list.
        print("Chinese:")
        for name in list_zh_analyzers():
            print(f"  {name}")
        print("Japanese:")
        for name in list_ja_analyzers():
            print(f"  {name}")
        return 0
    if args.list_resolvers:
        for name in list_resolvers():
            print(name)
        return 0
    if args.list_renderers:
        for name in renderer_registry.names():
            print(name)
        return 0
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject combinations argparse can't express, with clean exit-2 errors.

    ``--analyzer`` / ``--resolver`` are validated against their live
    registries (the no-hardcode source of truth) — but only when the user
    set a non-default value, so a plain run never imports a registry it
    doesn't need.
    """
    if args.analyzer != DEFAULT_ZH_ANALYZER:
        valid = set(list_zh_analyzers()) | set(list_ja_analyzers())
        if args.analyzer not in valid:
            parser.error(
                f"unknown analyzer {args.analyzer!r}; "
                f"choose from: {', '.join(sorted(valid))}"
            )
    if args.resolver != DEFAULT_PINYIN_RESOLVER:
        valid = set(list_resolvers())
        if args.resolver not in valid:
            parser.error(
                f"unknown resolver {args.resolver!r}; "
                f"choose from: {', '.join(sorted(valid))}"
            )
    if args.to == "cells" and (args.width or args.page_height or args.page_numbers):
        parser.error(
            "--to cells emits structural cell data and cannot be combined "
            "with layout options (--width / --page-height / --page-numbers)"
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _produce_output(
    result: TranslationResult, args: argparse.Namespace
) -> str | bytes:
    """Render ``result`` to the payload the user asked for.

    Two orthogonal axes: ``--to`` picks the encoding (``unicode`` / ``brf``
    / ``cells``), and the layout knobs (``--width`` / ``--page-height`` /
    ``--page-numbers``, or ``--to layout``) decide whether the encoding is
    wrapped + paginated. ``cells`` is structural JSON and never laid out.
    """
    if args.to == "cells":
        return json.dumps(
            result.render("cells"), indent=2, ensure_ascii=False
        ) + "\n"

    encoding: Literal["unicode", "brf"] = "brf" if args.to == "brf" else "unicode"
    layout_on = (
        args.to == "layout"
        or bool(args.width)
        or bool(args.page_height)
        or args.page_numbers
    )
    if layout_on:
        options = LayoutOptions(
            line_width=args.width or DEFAULT_LAYOUT_WIDTH,
            page_height=args.page_height,
            show_page_numbers=args.page_numbers,
        )
        return LayoutRenderer(options=options, format=encoding).render(
            result.braille_ir
        )
    return renderer_registry.get(encoding).render(result.braille_ir)


def _write_output(payload: str | bytes, output_path: str | None) -> None:
    """Write the payload to a file (``-o``) or stdout, in the right mode."""
    if isinstance(payload, bytes):
        if output_path is not None:
            with open(output_path, "wb") as fh:
                fh.write(payload)
            return
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(payload)
        else:  # text-only stream (e.g. a captured test stdout): BRF is ASCII
            sys.stdout.write(payload.decode("ascii"))
        return

    text = payload if payload.endswith("\n") else payload + "\n"
    if output_path is not None:
        with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return
    sys.stdout.write(text)


def _reconfigure_utf8_streams() -> None:
    """Best-effort: make stdout + stderr emit UTF-8.

    Unicode braille (stdout) and warnings carrying Chinese / Japanese
    surface text (stderr) would otherwise raise on a non-UTF-8 Windows
    console codepage. A no-op for streams that can't be reconfigured — a
    captured test stream, or a closed std stream.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def _read_stdin() -> str | None:
    """Read piped stdin as UTF-8 text, or ``None`` when stdin is a terminal.

    Reads the raw byte buffer and decodes UTF-8 explicitly (rather than
    trusting the locale) so piped Chinese / braille survives a non-UTF-8
    console codepage on Windows.
    """
    stdin = sys.stdin
    if stdin is None:
        return None
    try:
        if stdin.isatty():
            return None
    except (ValueError, OSError):
        pass  # unusual / closed stream — attempt the read anyway
    buffer = getattr(stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8")
    return stdin.read()


def _translate(
    pipe: Pipeline, args: argparse.Namespace, source_text: str | None
) -> TranslationResult:
    """Run the pipeline on the selected input source."""
    if args.file is not None:
        return pipe.translate_file(args.file)
    assert source_text is not None  # guaranteed by the caller
    doc = pipe.parse_text(source_text, format=args.in_format)
    return pipe.translate_document(doc)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _emit_warnings(result: TranslationResult, quiet: bool) -> None:
    if quiet:
        return
    for warning in result.warnings:
        print(f"[{warning.code}] {warning.message}", file=sys.stderr)


def _format_error(exc: Exception) -> str:
    """A one-line, user-facing message for ``exc`` (no traceback)."""
    if isinstance(exc, KeyError):
        # KeyError.__str__ wraps its arg in quotes; the registry packs a
        # full "no adapter named ...; available: [...]" message in args[0].
        return str(exc.args[0]) if exc.args else str(exc)
    return str(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the brailix CLI. Returns a process exit code.

    Exit codes: ``0`` success; ``1`` a translation / IO error (printed to
    stderr without a traceback); ``2`` a usage error (argparse, or an
    invalid flag combination — raised as :class:`SystemExit`).
    """
    _reconfigure_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    discovery = _handle_discovery(args)
    if discovery is not None:
        return discovery

    _validate(args, parser)

    if args.page_numbers and args.page_height is None:
        print(
            "brailix: --page-numbers has no effect without --page-height",
            file=sys.stderr,
        )

    try:
        # Resolve the input source inside the try so a non-UTF-8 pipe
        # (common when a GBK-encoded file is piped on a Windows console)
        # decodes to a clean exit-1 error instead of an uncaught
        # UnicodeDecodeError traceback. ``parser.error`` raises
        # ``SystemExit`` (a BaseException), which the ``except`` below does
        # NOT catch, so "no input" stays an exit-2 usage error.
        source_text: str | None = None
        if args.file is None:
            source_text = args.text if args.text is not None else _read_stdin()
            if source_text is None:
                parser.error(
                    "no input: pass TEXT, use --file, or pipe text via stdin"
                )
        pipe = Pipeline(
            profile=args.profile,
            mode=args.mode,
            analyzer=args.analyzer,
            resolver=args.resolver,
        )
        result = _translate(pipe, args, source_text)
    except (BrailixError, OSError, UnicodeDecodeError, KeyError) as exc:
        print(f"brailix: {_format_error(exc)}", file=sys.stderr)
        return 1

    _emit_warnings(result, args.quiet)

    try:
        payload = _produce_output(result, args)
        _write_output(payload, args.output)
    except (BrailixError, OSError, KeyError) as exc:
        print(f"brailix: {_format_error(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
