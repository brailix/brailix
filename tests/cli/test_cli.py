"""Tests for the ``brailix`` command-line interface.

Every test drives :func:`brailix.cli.main` in-process and is **dependency
free**: inputs are digits / ASCII (which never reach the Chinese or
Japanese frontends) or use the ``char`` analyzer + ``null`` resolver, both
of which are built in and need no optional package. So the suite passes on
a bare install (the public mirror's extra-agnostic CI) and never loads a
tokenizer model — which would also pollute captured stdout.

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
    rc = main(["--list-resolvers"])
    assert rc == 0
    assert capsys.readouterr().out.split() == list_resolvers()


def test_list_analyzers_groups_languages(capsys):
    rc = main(["--list-analyzers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Chinese:" in out and "Japanese:" in out
    assert "char" in out  # a Chinese analyzer
    assert "kana" in out  # a Japanese analyzer


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


def test_no_input_is_usage_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeTTYStdin())
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
