"""Command-line interface for brailix.

``brailix`` (or ``python -m brailix``) compiles text, Markdown, Word, and
MusicXML sources into braille from a terminal. It is a thin wrapper over
:class:`brailix.Pipeline` and the renderer registry — every pluggable
choice (profile, segmentation engine, reading resolver, braille renderer)
is enumerated from the core registries, so ``--list-*`` and the accepted
values always reflect what the installed build actually provides rather
than a hand-kept list.

This command is **braille-only**, which is what its renderer listings mean:
the registry it reads holds the tactile-graphics renderers too, and those are
filtered out rather than offered, because a graphic is not something this CLI
can take as input. Compiling a figure is
:func:`brailix.translate_graphic` plus
:meth:`~brailix.GraphicResult.render` from Python; there is no graphic input
mode here.

That includes the *languages*: the engine listings walk
``language_frontend_registry`` and ask each language what it offers
(:func:`brailix.frontend.list_language_adapters`), so a third language that
registers a segmenter, a frontend and a backend is discoverable here without
an edit. This module used to import ``frontend.zh`` / ``frontend.ja``
directly and print two hard-coded headings, which made "adding a language is
registration" true everywhere except at the surface a user actually looks
through to find one.

Examples::

    brailix "我在重庆。" -p cn_current                 # Unicode braille to stdout
    brailix -f lesson.md -w 32 -p cn_current          # wrap Markdown at 32 cells
    brailix "123" --to brf -o out.brf -p cn_current   # NABCC for an embosser
    echo "# 标题" | brailix --in-format markdown -p cn_current
    brailix --list-profiles                           # discovery needs no profile

``-p/--profile`` is required for a translation: more than one braille
standard ships, and the CLI never picks one for you (``--list-profiles``
prints the names). The discovery flags run without it.

The translation surface mirrors the library: input is dispatched the same
way :meth:`brailix.Pipeline.translate_file` dispatches it (by suffix for
``--file``; ``--in-format`` for text / stdin), and output goes through the
same renderers :meth:`brailix.TranslationResult.render` exposes.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import TYPE_CHECKING, Any, Literal

from brailix import Pipeline, __version__
from brailix.core import RunMode
from brailix.core.config import iter_builtin_profiles, load_profile
from brailix.core.errors import BrailixError
from brailix.frontend import (
    language_display_name,
    language_frontend_registry,
    list_language_adapters,
)
from brailix.renderer import (
    LayoutOptions,
    LayoutRenderer,
    braille_renderer_names,
    renderer_registry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from brailix.pipeline import TranslationResult

# The library's own defaults, read off :class:`~brailix.Pipeline`'s field
# declarations rather than copied. A CLI default that drifted from the API's
# would make ``--help`` describe a run the library doesn't do — and the two
# are the same decision, so only one of them should be allowed to state it.
_PIPELINE_DEFAULTS: dict[str, Any] = {
    f.name: f.default for f in dataclasses.fields(Pipeline)
}

# Formats the ``--in-format`` flag (text / stdin) accepts. These mirror
# :meth:`brailix.Pipeline.parse_text`'s contract; the input layer keeps no
# registry for them because the choice is static (a file's suffix, or this
# flag) — see ``brailix/input/__init__.py``. Files passed with ``--file``
# are dispatched by suffix instead, so combining the two is refused rather
# than silently ignored (see :func:`_validate`).
IN_FORMATS = ("plain", "markdown", "musicxml")

# What TEXT / stdin is read as when ``--in-format`` is omitted. The flag's
# argparse default is ``None`` instead, so "not given" stays distinguishable
# from "given" — that is what lets ``--in-format`` with ``--file`` be an
# error while a plain ``--file`` run is not.
DEFAULT_IN_FORMAT = "plain"

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

    Only the braille renderers are offered: the CLI translates text to
    braille, so the tactile-graphics renderers (``bmp`` / ``png`` / ``pdf`` /
    ``tactile_preview``) — which share ``renderer_registry`` but consume a
    raster, reached via the library's :meth:`~brailix.pipeline.GraphicResult.render`
    — are filtered out by :func:`~brailix.renderer.braille_renderer_names`.
    That list is derived from each renderer's own ``consumes``, so a fifth
    tactile renderer needs no edit here; this sentence naming four of them is
    prose, and ``tests/test_output_domains.py`` is what keeps it honest.
    """
    renderers = braille_renderer_names()
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
        "(.md / .docx / .musicxml / ...); needs the matching extra. "
        "not combinable with TEXT",
    )
    src.add_argument(
        "--in-format",
        dest="in_format",
        choices=IN_FORMATS,
        default=None,
        help=f"format for TEXT / stdin (default: {DEFAULT_IN_FORMAT}; "
        "not for --file, which dispatches by suffix)",
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
        default=_PIPELINE_DEFAULTS["default_renderer"],
        help="output renderer: " + " / ".join(renderers) + f" (default: {_PIPELINE_DEFAULTS['default_renderer']}). "
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
        default=None,
        metavar="NAME",
        help="braille profile to use, required (see --list-profiles)",
    )
    tr.add_argument(
        "-a",
        "--analyzer",
        default=_PIPELINE_DEFAULTS["analyzer"],
        metavar="NAME",
        help="word-segmentation engine for the profile's language "
        f"(default: {_PIPELINE_DEFAULTS['analyzer']}; see --list-analyzers)",
    )
    tr.add_argument(
        "-r",
        "--resolver",
        default=_PIPELINE_DEFAULTS["resolver"],
        metavar="NAME",
        help="reading engine for the profile's language, where it has one "
        f"(Chinese pinyin today; default: {_PIPELINE_DEFAULTS['resolver']}; "
        "see --list-resolvers)",
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
        help="list word-segmentation engines, by language",
    )
    disc.add_argument(
        "--list-resolvers", dest="list_resolvers", action="store_true",
        help="list reading engines, by language",
    )
    disc.add_argument(
        "--list-renderers", dest="list_renderers", action="store_true",
        help="list the braille renderers this command accepts for --to",
    )
    disc.add_argument(
        "-V", "--version", action="store_true", help="print the brailix version",
    )

    return parser


_EPILOG = """\
examples:
  brailix "我在重庆。" -p cn_current            translate a string to Unicode braille
  brailix -f lesson.md -w 32 -p cn_current     wrap a Markdown file at 32 cells
  brailix "123" --to brf -o out.brf -p cn_current   write NABCC for an embosser
  echo "# 标题" | brailix --in-format markdown -p cn_current
  brailix --list-analyzers                     discovery needs no profile

-p/--profile is required to translate: more than one braille standard ships
and the choice is always the caller's (--list-profiles prints the names).

The text comes from exactly one place: TEXT, or --file, or piped stdin when
neither is given. TEXT together with --file is a usage error rather than a
silent winner.

A profile, engine, or resolver name shown by the --list-* flags is always
valid even before its optional dependency is installed; selecting one whose
package is missing reports which `pip install brailix[...]` extra to add.

An engine belongs to one language: --list-analyzers / --list-resolvers group
the names by language, and --analyzer / --resolver accept the ones the
profile's own language offers. Picking another language's engine — or a
reading engine for a language that has none — is a usage error, not a run
that quietly ignores the flag.
"""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _registered_languages() -> list[str]:
    """Every language with a registered frontend, in listing order.

    Ordered by the name a reader sees, with the subtag as tiebreak — the
    listings below print the label, so sorting by the key behind it would look
    arbitrary on the page.
    """
    return sorted(
        language_frontend_registry.names(),
        key=lambda lang: (language_display_name(lang), lang),
    )


def _print_by_language(family: str) -> None:
    """Print one adapter family, grouped by the language that offers it.

    Grouped because these are language-scoped choices and a flat list is
    ambiguous — ``auto`` means a different engine per language. The groups and
    their labels come from the registry (see
    :func:`brailix.frontend.list_language_adapters`), so a newly registered
    language appears here on its own, and one that offers nothing in this
    family is skipped rather than printed as an empty heading.

    Asking a language what it offers *resolves that language's frontend*, and a
    third-party language may ship behind an optional package of its own. That
    failure is isolated per language: the ones that answered are still listed,
    the one that could not is reported on stderr with its ``pip install`` hint,
    and the command still exits 0. Discovery is the surface a user reaches for
    precisely *because* they don't know what is installed yet — it must not be
    the surface that fails, and least of all with a traceback (it runs before
    ``main``'s error boundary). The names stay on stdout alone, so piping the
    listing still yields names and nothing else.
    """
    for lang in _registered_languages():
        try:
            names = list_language_adapters(lang, family)
        except BrailixError as exc:
            print(
                f"brailix: language {lang!r} is registered but unavailable: "
                f"{_format_error(exc)}",
                file=sys.stderr,
            )
            continue
        if not names:
            continue
        print(f"{language_display_name(lang)}:")
        for name in names:
            print(f"  {name}")


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
        _print_by_language("analyzer")
        return 0
    if args.list_resolvers:
        _print_by_language("resolver")
        return 0
    if args.list_renderers:
        for name in braille_renderer_names():
            print(name)
        return 0
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _profile_language(name: str) -> str | None:
    """The primary language subtag of profile ``name``, or ``None`` when the
    profile cannot be read.

    Unreadable covers every reason — no such name, a malformed file, a path
    that is not a name — and all of them are reported by the real load inside
    :func:`main`'s error boundary, as one clean exit-1 message. Reporting them
    from here as well would turn "which engines may I pick?" into the place a
    bad profile name is first announced, in a second wording, at a different
    exit code.
    """
    try:
        return load_profile(name).language.split("-")[0]
    except (BrailixError, OSError, ValueError):
        return None


def _validate_language_adapters(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> None:
    """Check ``--analyzer`` / ``--resolver`` against the *profile's* language.

    An analyzer name is language-scoped: ``thulac`` is a Chinese segmenter and
    ``janome`` a Japanese one, and the runtime routes the choice to the frontend
    of the active profile's language — the ``"{lang}_analyzer"`` option key that
    :class:`~brailix.pipeline.frontend_driver.FrontendDriver` builds from it.
    Validating against the *union* over every registered language accepted
    ``-p cn_current --analyzer janome`` here and then failed in the Chinese
    registry — turning a usage error the parser could have named into a
    run-time one, with exit 1 instead of 2.

    The same scoping answers a case a union cannot express at all: a language
    with no ``resolver`` family (a Japanese reading comes out of its analyzer)
    would take ``--resolver g2pw`` as valid, and the option would then be
    carried into the run and read by nobody — a legal-looking flag with no
    effect on the output.

    Only reached when the user actually set one of the two, so a plain run
    neither loads the profile twice nor resolves a language frontend it has no
    use for.
    """
    lang = _profile_language(args.profile)
    if lang is None:
        return
    for flag, value, default, family in (
        ("--analyzer", args.analyzer, _PIPELINE_DEFAULTS["analyzer"], "analyzer"),
        ("--resolver", args.resolver, _PIPELINE_DEFAULTS["resolver"], "resolver"),
    ):
        if value == default:
            continue
        try:
            valid = list_language_adapters(lang, family)
        except BrailixError:
            # This language ships behind an optional package of its own and it
            # is not installed, so what it offers is unknowable here. The run
            # says so itself, with the pip hint; refusing the name on top of
            # that would blame the engine for a missing language.
            continue
        label = language_display_name(lang)
        if not valid:
            parser.error(
                f"{flag} does not apply to profile {args.profile!r}: "
                f"{label} has no {family} to choose from "
                f"(see --list-{family}s)"
            )
        if value not in valid:
            parser.error(
                f"unknown {family} {value!r} for profile {args.profile!r} "
                f"({label}); choose from: {', '.join(valid)}"
            )


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject combinations argparse can't express, with clean exit-2 errors.

    ``--analyzer`` / ``--resolver`` are validated against the live registry of
    the *profile's own language* (the no-hardcode source of truth) — but only
    when the user set a non-default value, so a plain run never resolves a
    frontend it doesn't need. See :func:`_validate_language_adapters`.

    ``--profile`` is required (there is no built-in default braille
    standard); it is checked here rather than via argparse ``required=True``
    so the ``--list-*`` discovery flags still run without it.

    The input source is checked here too. ``TEXT`` and ``--file`` are both
    accepted by the parser and both name an input, so passing both used to
    resolve by an implicit precedence nobody had written down — ``--file``
    won, while the guide said the positional argument did, and the text the
    user typed was silently not the thing translated. There is no reading of
    that command that is obviously right, so it is refused. Likewise
    ``--in-format`` with ``--file``: a file is dispatched by its suffix, so
    the flag cannot do what it says on the tin and quietly did nothing.
    """
    if args.profile is None:
        parser.error(
            "the following arguments are required: -p/--profile "
            "(see --list-profiles for available names)"
        )
    if args.text is not None and args.file is not None:
        parser.error(
            "TEXT and --file are mutually exclusive: pass the text to "
            "translate or a file to read it from, not both"
        )
    if args.in_format is not None and args.file is not None:
        parser.error(
            "--in-format applies to TEXT / stdin; --file is dispatched by "
            "the file's suffix (pipe the file in to force a format)"
        )
    if (
        args.analyzer != _PIPELINE_DEFAULTS["analyzer"]
        or args.resolver != _PIPELINE_DEFAULTS["resolver"]
    ):
        _validate_language_adapters(args, parser)
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
    """Read piped stdin as UTF-8 text, or ``None`` when there is nothing to read.

    Reads the raw byte buffer and decodes UTF-8 explicitly (rather than
    trusting the locale) so piped Chinese / braille survives a non-UTF-8
    console codepage on Windows.

    ``None`` covers both "stdin is a terminal" and "stdin cannot be read at
    all", which is the same answer to the only question the caller asks: is
    there piped input here? A **closed** stream raises ``ValueError`` — from
    ``isatty()``, which was already tolerated, and then again from the read,
    which was not. ``main``'s user-error handler catches ``BrailixError`` /
    ``OSError`` / ``UnicodeDecodeError``, so that second one escaped as a
    traceback and broke the CLI's no-traceback contract in exactly the
    environments that produce it: a service host, a detached process, an
    application spawning brailix as a subprocess with its std handles
    closed. Answering "no input" instead lands on the usage error that
    already tells the user what to do.
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
    try:
        raw = buffer.read() if buffer is not None else stdin.read()
    except UnicodeDecodeError:
        # A text-mode stream that fails to decode is a real input error with
        # its own clean exit-1 message; it must not be reported as "no input"
        # by the wider ``ValueError`` below (UnicodeDecodeError IS one).
        raise
    except ValueError:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _translate(
    pipe: Pipeline, args: argparse.Namespace, source_text: str | None
) -> TranslationResult:
    """Run the pipeline on the selected input source.

    Exactly one source is possible by the time this runs: ``_validate``
    rejected TEXT together with ``--file``, and ``main`` only falls back to
    stdin when neither was given.
    """
    if args.file is not None:
        return pipe.translate_file(args.file)
    assert source_text is not None  # guaranteed by the caller
    doc = pipe.parse_text(
        source_text, format=args.in_format or DEFAULT_IN_FORMAT
    )
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
    except (BrailixError, OSError, UnicodeDecodeError) as exc:
        # Registry / auto "unknown name" failures are UnknownAdapterError, a
        # BrailixError, so they're still caught here as clean exit-1 messages.
        # Bare KeyError is deliberately NOT caught: a genuine internal dict-miss
        # is a programming bug and should surface as a crash + traceback, not be
        # masked as a user-facing error.
        print(f"brailix: {_format_error(exc)}", file=sys.stderr)
        return 1

    _emit_warnings(result, args.quiet)

    try:
        payload = _produce_output(result, args)
        _write_output(payload, args.output)
    except (BrailixError, OSError) as exc:  # see the translate try above re KeyError
        print(f"brailix: {_format_error(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
