"""Tests for the ``brailix`` command-line interface.

Every test drives :func:`brailix.cli.main` in-process and is **dependency
free**: inputs are digits / ASCII (which never reach the Chinese or
Japanese frontends) or use the ``char`` analyzer + ``null`` resolver, both
of which are built in and need no optional package. So the suite passes on
a bare install — CI runs it without any extra — and never loads a tokenizer
model, which would also pollute captured stdout.

The oracle for each translation is the library producing the *same* output
the CLI builds (the exact ``Pipeline`` + renderer), so the tests assert the
CLI is a faithful frontend rather than pinning braille byte-for-byte.
"""

from __future__ import annotations

import io
import json

import pytest

from brailix import Pipeline, __version__
from brailix.cli import main
from brailix.core.config import iter_builtin_profiles
from brailix.frontend.ja.analyzer import list_analyzers as ja_list_analyzers
from brailix.frontend.zh.analyzer import list_analyzers as zh_list_analyzers
from brailix.frontend.zh.pinyin import list_resolvers
from brailix.renderer import (
    LayoutOptions,
    LayoutRenderer,
    braille_renderer_names,
    renderer_registry,
)

# --------------------------------------------------------------------------
# Oracles + stdin fakes
# --------------------------------------------------------------------------


def _braille(text: str, *, fmt: str = "plain", **pipe_kw: str):
    """The BrailleDocument the CLI builds for ``text`` (same Pipeline path)."""
    pipe = Pipeline(profile="cn_current", **pipe_kw)
    return pipe.translate_document(pipe.parse_text(text, format=fmt)).braille_ir


class _FakeBufferStdin:
    """stdin exposing a binary ``.buffer`` (the real-stdin read path)."""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)

    def isatty(self) -> bool:
        return False


class _FakeTTYStdin:
    """stdin that reports it's an interactive terminal (no piped input)."""

    def isatty(self) -> bool:
        return True

    def read(self) -> str:  # pragma: no cover - never reached (isatty short-circuits)
        return ""


# --------------------------------------------------------------------------
# Translation: encodings
# --------------------------------------------------------------------------


def test_translate_digits_unicode(capsys):
    rc = main(["123", "-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(_braille("123"))
    assert capsys.readouterr().out == expected + "\n"
    assert expected  # digits really produced braille, dependency-free


def test_translate_to_brf_file(tmp_path):
    out = tmp_path / "out.brf"
    rc = main(["123", "--to", "brf", "-o", str(out), "-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("brf").render(_braille("123"))
    assert isinstance(expected, bytes)
    assert out.read_bytes() == expected


def test_translate_cells_is_json(capsys):
    rc = main(["123", "--to", "cells", "-p", "cn_current"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "braille_document"
    assert payload["blocks"][0]["cells"]  # has cells


def test_unicode_to_file(tmp_path):
    out = tmp_path / "out.txt"
    rc = main(["123", "-o", str(out), "-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(_braille("123"))
    assert out.read_text(encoding="utf-8") == expected + "\n"


# --------------------------------------------------------------------------
# Translation: layout pass
# --------------------------------------------------------------------------


def test_width_triggers_layout(capsys):
    rc = main(["ab cd ef gh ij", "-w", "5", "-p", "cn_current"])
    assert rc == 0
    expected = LayoutRenderer(
        options=LayoutOptions(line_width=5), format="unicode"
    ).render(_braille("ab cd ef gh ij"))
    assert capsys.readouterr().out == expected + "\n"


def test_to_layout_uses_default_width(capsys):
    rc = main(["ab cd ef", "--to", "layout", "-p", "cn_current"])
    assert rc == 0
    expected = LayoutRenderer(
        options=LayoutOptions(line_width=40), format="unicode"
    ).render(_braille("ab cd ef"))
    assert capsys.readouterr().out == expected + "\n"


def test_brf_with_width_is_wrapped_bytes(tmp_path):
    out = tmp_path / "out.brf"
    rc = main(["ab cd ef gh ij", "--to", "brf", "-w", "5", "-o", str(out), "-p", "cn_current"])
    assert rc == 0
    expected = LayoutRenderer(
        options=LayoutOptions(line_width=5), format="brf"
    ).render(_braille("ab cd ef gh ij"))
    assert isinstance(expected, bytes)
    assert out.read_bytes() == expected


# --------------------------------------------------------------------------
# Input sources
# --------------------------------------------------------------------------


def test_file_input_plain(tmp_path, capsys):
    src = tmp_path / "in.txt"
    src.write_text("123", encoding="utf-8")
    rc = main(["-f", str(src), "-p", "cn_current"])
    assert rc == 0
    pipe = Pipeline(profile="cn_current")
    expected = pipe.translate_file(str(src)).render("unicode")
    assert capsys.readouterr().out == expected + "\n"


def test_file_input_markdown_by_suffix(tmp_path, capsys):
    src = tmp_path / "in.md"
    src.write_text("# Title\n\nbody text\n", encoding="utf-8")
    rc = main(["-f", str(src), "-p", "cn_current"])
    assert rc == 0
    pipe = Pipeline(profile="cn_current")
    expected = pipe.translate_file(str(src)).render("unicode")
    assert capsys.readouterr().out == expected + "\n"


def test_stdin_buffer(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeBufferStdin(b"123"))
    rc = main(["-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(_braille("123"))
    assert capsys.readouterr().out == expected + "\n"


def test_stdin_text_fallback(monkeypatch, capsys):
    # A stdin without a .buffer (e.g. io.StringIO) takes the text-read path.
    monkeypatch.setattr("sys.stdin", io.StringIO("123"))
    rc = main(["-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(_braille("123"))
    assert capsys.readouterr().out == expected + "\n"


def test_in_format_markdown_for_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Title\n\nbody\n"))
    rc = main(["--in-format", "markdown", "-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(
        _braille("# Title\n\nbody\n", fmt="markdown")
    )
    assert capsys.readouterr().out == expected + "\n"


def test_positional_text_wins_over_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeBufferStdin(b"456"))
    rc = main(["123", "-p", "cn_current"])
    assert rc == 0
    expected = renderer_registry.get("unicode").render(_braille("123"))
    assert capsys.readouterr().out == expected + "\n"


def test_file_wins_over_stdin(monkeypatch, tmp_path, capsys):
    # The remaining precedence, and the one that is unambiguous: stdin is the
    # fallback for "no source given", so an explicit --file is the source.
    monkeypatch.setattr("sys.stdin", _FakeBufferStdin(b"456"))
    src = tmp_path / "in.txt"
    src.write_text("123", encoding="utf-8")
    rc = main(["-f", str(src), "-p", "cn_current"])
    assert rc == 0
    expected = Pipeline(profile="cn_current").translate_file(str(src)).render(
        "unicode"
    )
    assert capsys.readouterr().out == expected + "\n"


def test_omitting_in_format_reads_text_as_plain(capsys):
    # --in-format now defaults to None at the parser (so "not given" can be
    # told from "given"); the format it resolves to must still be plain.
    rc = main(["# 123", "-p", "cn_current"])
    assert rc == 0
    explicit = capsys.readouterr().out
    rc = main(["# 123", "--in-format", "plain", "-p", "cn_current"])
    assert rc == 0
    assert capsys.readouterr().out == explicit


# --------------------------------------------------------------------------
# Chinese path (dependency-free via char + null)
# --------------------------------------------------------------------------


def test_chinese_char_null_matches_library(capsys):
    args = ["中文", "--analyzer", "char", "--resolver", "null", "-p", "cn_current"]
    rc = main(args)
    assert rc == 0
    expected = renderer_registry.get("unicode").render(
        _braille("中文", analyzer="char", resolver="null")
    )
    assert capsys.readouterr().out == expected + "\n"


def test_warnings_go_to_stderr(capsys):
    rc = main(["中", "--analyzer", "char", "--resolver", "null", "-p", "cn_current"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "MISSING_PINYIN" in err  # a real warning surfaced


def test_quiet_suppresses_warnings(capsys):
    rc = main(["中", "--analyzer", "char", "--resolver", "null", "-q", "-p", "cn_current"])
    assert rc == 0
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_version(capsys):
    rc = main(["-V"])
    assert rc == 0
    assert capsys.readouterr().out == f"brailix {__version__}\n"


def test_list_profiles(capsys):
    rc = main(["--list-profiles"])
    assert rc == 0
    out = capsys.readouterr().out.split()
    assert out == iter_builtin_profiles()
    assert "cn_current" in out


def test_list_renderers(capsys):
    rc = main(["--list-renderers"])
    assert rc == 0
    # The CLI is text→braille, so it lists only the braille renderers; the
    # tactile-graphics renderers (bmp / png / tactile_preview) share
    # renderer_registry but consume a raster (reached via GraphicResult.render),
    # so they're filtered out of --to / --list-renderers.
    out = capsys.readouterr().out.split()
    assert out == braille_renderer_names()
    assert "unicode" in out
    assert "bmp" not in out
    assert "bmp" in renderer_registry.names()  # present, just not CLI-listed


def test_list_resolvers(capsys):
    # Grouped like the analyzers, and for the same reason: a reading engine is
    # a language's own. Only Chinese offers one today (a Japanese reading comes
    # out of its analyzer), so there is exactly one group.
    rc = main(["--list-resolvers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.split() == ["Chinese:", *list_resolvers()]


def test_list_analyzers_groups_languages(capsys):
    rc = main(["--list-analyzers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Chinese:" in out and "Japanese:" in out
    assert "char" in out  # a Chinese analyzer
    assert "kana" in out  # a Japanese analyzer


def test_language_listings_follow_the_registry(capsys, monkeypatch):
    """A language nobody wrote into the CLI still shows up in it.

    The whole point of the seam: registering a frontend is what makes a
    language selectable, so the listing has to come from the registry rather
    than from two imports and two headings — which is what it used to be, and
    what would have left a third language invisible with everything else
    about it correctly registered.
    """
    from brailix.core.protocols import LanguageFrontend
    from brailix.frontend import language_frontend_registry

    class _KlingonFrontend(LanguageFrontend):
        prose_types = frozenset({"tlh_text"})
        display_name = "Klingon"
        adapters = {"analyzer": lambda: ["pIqaD"]}

        def process(self, surface, base, ctx):  # pragma: no cover - not run
            return []

    with language_frontend_registry.overriding("tlh", _KlingonFrontend):
        rc = main(["--list-analyzers"])
        out = capsys.readouterr().out
    assert rc == 0
    assert "Klingon:" in out
    assert "pIqaD" in out


def test_an_unregistered_analyzer_name_is_still_refused(capsys):
    # The other half: a name no language offers is refused, so dropping the
    # hard-coded language list didn't turn validation into "anything goes".
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-p", "cn_current", "--analyzer", "no_such_engine"])
    assert excinfo.value.code == 2
    assert "unknown analyzer" in capsys.readouterr().err


def test_a_language_that_cannot_load_does_not_break_the_listing(capsys):
    """Discovery is what a user reaches for *because* they don't know what is
    installed; it must not be what fails.

    A third-party language may ship behind an optional package of its own, and
    asking it what engines it offers resolves its frontend. That failure is
    isolated per language: the built-ins still list on stdout, the broken one is
    named on stderr with its pip hint, and the exit code stays 0. It used to
    abort the whole listing — and with a traceback, since discovery runs before
    ``main``'s error boundary.
    """
    from brailix.frontend import language_frontend_registry

    def _needs_a_wheel():
        # The exception a real absent package raises — the registry rewrites
        # only this one into the install hint the assertions below read.
        raise ModuleNotFoundError(
            "No module named 'klingonlib'", name="klingonlib"
        )

    with language_frontend_registry.overriding(
        "tlh", _needs_a_wheel, extra="klingon"
    ):
        rc = main(["--list-analyzers"])
        captured = capsys.readouterr()
    assert rc == 0
    assert "Chinese:" in captured.out and "char" in captured.out
    assert "'tlh'" in captured.err and "klingon" in captured.err
    assert "Traceback" not in captured.err


def test_another_languages_analyzer_is_a_usage_error(capsys):
    """``kana`` is a Japanese analyzer, so a Chinese profile must refuse it.

    The union over every registered language accepted it here and let the run
    fail later in the Chinese registry: a usage error the parser could name,
    reported instead as exit 1 after startup.
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-p", "cn_current", "--analyzer", "kana"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "unknown analyzer" in err and "cn_current" in err
    assert "char" in err  # the message lists what Chinese does offer


def test_a_chinese_analyzer_is_a_usage_error_for_a_japanese_profile(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["ABC", "-p", "ja_current", "--analyzer", "char"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "unknown analyzer" in err and "ja_current" in err
    assert "kana" in err


def test_a_reading_engine_for_a_language_without_one_is_a_usage_error(capsys):
    """Japanese has no ``resolver`` family — a reading comes out of its
    analyzer. The option used to be accepted and then read by nobody: a legal
    flag with no effect on the output."""
    with pytest.raises(SystemExit) as excinfo:
        main(["ABC", "-p", "ja_current", "--resolver", "null"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--resolver" in err and "Japanese" in err


def test_a_language_only_offers_its_own_engines_for_validation(capsys):
    """A newly registered language's engine is selectable under *its* profile
    and nowhere else — the same seam the listing rides, applied to validation."""
    from brailix.core.protocols import LanguageFrontend
    from brailix.frontend import language_frontend_registry

    class _KlingonFrontend(LanguageFrontend):
        prose_types = frozenset({"tlh_text"})
        display_name = "Klingon"
        adapters = {"analyzer": lambda: ["pIqaD"]}

        def process(self, surface, base, ctx):  # pragma: no cover - not run
            return []

    with language_frontend_registry.overriding("tlh", _KlingonFrontend):
        with pytest.raises(SystemExit) as excinfo:
            main(["123", "-p", "cn_current", "--analyzer", "pIqaD"])
        assert excinfo.value.code == 2
        assert "unknown analyzer" in capsys.readouterr().err


def test_a_default_run_needs_no_profile_load_for_validation(monkeypatch):
    """The language lookup only happens when the user actually named an engine,
    so a plain run does not load the profile a second time."""
    from brailix import cli

    def _fail(name, *args, **kwargs):
        raise AssertionError("validation loaded the profile")

    monkeypatch.setattr(cli, "load_profile", _fail)
    assert main(["123", "-p", "cn_current", "-q"]) == 0


# --------------------------------------------------------------------------
# Errors / exit codes
# --------------------------------------------------------------------------


def test_missing_file_exits_1(tmp_path, capsys):
    rc = main(["-f", str(tmp_path / "nope.md"), "-p", "cn_current"])
    assert rc == 1
    assert "brailix:" in capsys.readouterr().err


def test_unknown_profile_exits_1(capsys):
    rc = main(["123", "-p", "does-not-exist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does-not-exist" in err
    assert "cn_current" in err  # the error lists what's available


def test_unknown_analyzer_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-a", "bogus", "-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "unknown analyzer" in capsys.readouterr().err


def test_unknown_resolver_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-r", "bogus", "-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "unknown resolver" in capsys.readouterr().err


def test_bad_mode_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-m", "loud"])
    assert excinfo.value.code == 2


def test_nonpositive_width_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-w", "0"])
    assert excinfo.value.code == 2


def test_cells_with_layout_option_exits_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "--to", "cells", "-w", "10"])
    assert excinfo.value.code == 2
    assert "cells" in capsys.readouterr().err


def test_missing_profile_is_usage_error():
    # A translation run without --profile is a usage error (the flag is
    # required); the CLI must exit 2 rather than silently picking a default.
    with pytest.raises(SystemExit) as excinfo:
        main(["123"])
    assert excinfo.value.code == 2


def test_text_and_file_together_is_usage_error(tmp_path, capsys):
    # Both name an input, and which one wins was never a decision anyone
    # wrote down: the implementation translated the file, the CLI guide
    # promised the positional string, and the text the user typed was
    # silently not what came out. Neither reading is obviously right, so the
    # command is refused instead of resolved.
    src = tmp_path / "lesson.md"
    src.write_text("# Title\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["123", "-f", str(src), "-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_in_format_with_file_is_usage_error(tmp_path, capsys):
    # A --file is dispatched by its suffix, so --in-format cannot do what it
    # says; it used to be accepted and ignored, which is how the CLI guide
    # came to print an example that promised to read a .txt as MusicXML and
    # translated it as prose.
    src = tmp_path / "score-fragment.txt"
    src.write_text("123", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["--in-format", "musicxml", "-f", str(src), "-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "--in-format" in capsys.readouterr().err


def test_no_input_is_usage_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeTTYStdin())
    with pytest.raises(SystemExit) as excinfo:
        main(["-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "no input" in capsys.readouterr().err


def test_closed_stdin_is_a_clean_usage_error(monkeypatch, capsys):
    # A closed stdin raises ValueError from isatty() — already tolerated —
    # and again from the read, which was not: main() catches BrailixError /
    # OSError / UnicodeDecodeError, so "I/O operation on closed file" escaped
    # as a traceback. Nothing could be read, so the answer is the same one a
    # terminal gets: no input, exit 2, with the usage line that says what to
    # pass instead.
    closed = io.StringIO("123")
    closed.close()
    monkeypatch.setattr("sys.stdin", closed)
    with pytest.raises(SystemExit) as excinfo:
        main(["-p", "cn_current"])
    assert excinfo.value.code == 2
    assert "no input" in capsys.readouterr().err


def test_invalid_utf8_stdin_exits_1(monkeypatch, capsys):
    # Invalid UTF-8 on the pipe (e.g. a GBK file piped on a Windows
    # console) must surface as a clean exit-1 error, not an uncaught
    # UnicodeDecodeError traceback. b"\xc4\xe3\xba\xc3" is 你好 in GBK.
    monkeypatch.setattr("sys.stdin", _FakeBufferStdin(b"\xc4\xe3\xba\xc3"))
    rc = main(["-p", "cn_current"])
    assert rc == 1
    assert "brailix:" in capsys.readouterr().err


def test_page_numbers_without_height_warns(capsys):
    rc = main(["123", "--page-numbers", "-p", "cn_current"])
    assert rc == 0
    assert "--page-numbers" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Core symmetry: the JA analyzer enumerator the CLI relies on
# --------------------------------------------------------------------------


def test_ja_list_analyzers_from_registry():
    names = ja_list_analyzers()
    assert "kana" in names and "auto" in names
    assert names == sorted(names)


def test_zh_list_analyzers_from_registry():
    names = zh_list_analyzers()
    assert "char" in names and "auto" in names


def test_internal_keyerror_is_not_masked(monkeypatch):
    # cli-keyerror: a genuine internal KeyError (a programming bug deep in the
    # pipeline) must crash with a traceback, NOT be reported as a clean exit-1
    # user error. Registry / auto "unknown name" failures are now
    # UnknownAdapterError (a BrailixError) and stay caught; a bare KeyError
    # propagates so the bug is debuggable.
    import brailix.cli as cli_mod

    def _boom(*args, **kwargs):
        raise KeyError("internal-bug-key")

    monkeypatch.setattr(cli_mod, "_translate", _boom)
    with pytest.raises(KeyError, match="internal-bug-key"):
        main(["123", "-p", "cn_current"])
