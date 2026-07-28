"""Tests for the input-layer size budget — :class:`brailix.input.InputLimits`.

The gate refuses an oversized file with a ``stat()`` BEFORE any read, so an
untrusted upload can't spike process memory the instant it's loaded (P1-3).
These pin: the pre-read ordering, the two ceilings (file bytes / decoded
characters), the ``unlimited()`` opt-out, that binary formats are gated too,
and that the gate never masks a genuine ``FileNotFoundError``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from brailix import Pipeline
from brailix.input import (
    DEFAULT_INPUT_LIMITS,
    InputLimits,
    InputTooLargeError,
    parse_file,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestFileSizeGate:
    def test_small_file_under_default_limit_parses(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "ok.txt", "你好世界")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "你好世界"

    def test_oversized_file_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "big.txt", "x" * 4096)
        limits = InputLimits(max_file_bytes=1024)
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                path, profile="cn_current", language="zh-CN", limits=limits
            )
        assert exc.value.kind == "file_bytes"
        assert exc.value.limit == 1024
        assert exc.value.actual == 4096

    def test_rejection_happens_before_any_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gate is a stat(), not a read: an oversized file is refused
        without a single byte loaded. Make every read explode and prove the
        InputTooLargeError still fires — the DoS guard's whole point."""
        path = _write(tmp_path / "big.txt", "x" * 4096)

        def _boom(*_a: object, **_k: object) -> bytes:
            raise AssertionError("parse_file must not read an oversized file")

        monkeypatch.setattr(Path, "read_bytes", _boom)
        with pytest.raises(InputTooLargeError):
            parse_file(
                path,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=1024),
            )

    def test_binary_format_is_gated_too(self, tmp_path: Path) -> None:
        """The gate is suffix-agnostic — it runs before the adapter is even
        chosen, so an oversized ``.mxl`` (whole compressed archive otherwise
        read into memory) is refused before the zip adapter touches it."""
        path = (tmp_path / "big.mxl")
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 4096)
        with pytest.raises(InputTooLargeError):
            parse_file(
                path,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=512),
            )

    def test_missing_file_raises_filenotfound_not_toolarge(
        self, tmp_path: Path
    ) -> None:
        """A missing path must raise FileNotFoundError as before — the gate
        stat()s and must not swallow it into a size error."""
        with pytest.raises(FileNotFoundError):
            parse_file(
                tmp_path / "nope.txt", profile="cn_current", language="zh-CN"
            )


class TestTheGateBindsToTheBytesRead:
    """The ``stat()`` gate describes the *path*; the read opens it again.

    Between those two the file can grow, be atomically replaced, or be a
    symlink repointed — so the check and the consumption were about different
    bytes. Measured before ``read_bounded``: a 2-byte file under a 16-byte
    ceiling, replaced with 4096 bytes after the gate, parsed all 4096.

    The binding promise is on the handle now: ``fstat`` on the open descriptor,
    and a read of one byte past the ceiling.
    """

    def test_growth_after_the_stat_gate_is_still_refused(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import pathlib

        target = _write(tmp_path / "doc.txt", "ab")
        limits = InputLimits(max_file_bytes=16, max_text_chars=10_000_000)

        real_stat = pathlib.Path.stat
        fired = {"n": 0}

        def growing_stat(self, **kwargs):  # noqa: ANN001, ANN202
            result = real_stat(self, **kwargs)
            if self == target and fired["n"] == 0:
                fired["n"] += 1
                # The window: the gate has looked, the read has not happened.
                target.write_bytes(b"x" * 4096)
            return result

        monkeypatch.setattr(pathlib.Path, "stat", growing_stat)
        with pytest.raises(InputTooLargeError) as excinfo:
            parse_file(
                target, language="zh-CN", profile="cn_current", limits=limits
            )
        assert excinfo.value.kind == "file_bytes"

    def test_read_bounded_stops_at_one_past_the_ceiling(
        self, tmp_path: Path
    ) -> None:
        """It must not load the whole oversized file just to discover it is
        oversized — that is the denial of service the ceiling exists to stop."""
        path = tmp_path / "big.bin"
        path.write_bytes(b"y" * 10_000)
        limits = InputLimits(max_file_bytes=100)

        with pytest.raises(InputTooLargeError) as excinfo:
            limits.read_bounded(path)
        # Reported from the fstat, so the caller learns the real size...
        assert excinfo.value.limit == 100

    def test_read_bounded_accepts_a_file_exactly_at_the_ceiling(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "exact.bin"
        path.write_bytes(b"z" * 64)
        assert InputLimits(max_file_bytes=64).read_bounded(path) == b"z" * 64

    def test_non_regular_files_are_refused(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """``st_size`` is meaningless for a FIFO or a device: a named pipe
        reports 0 and then delivers without end, so the size gate reads as
        "empty, go ahead" on the one input it can never bound."""
        import stat as stat_mod

        path = tmp_path / "pipe"
        path.write_bytes(b"data")

        real_fstat = os.fstat

        def fifo_fstat(fd):  # noqa: ANN001, ANN202
            info = real_fstat(fd)
            fields = list(info)
            # Re-label the mode as a FIFO, keeping everything else.
            fields[0] = (info.st_mode & ~stat_mod.S_IFMT(info.st_mode)) | stat_mod.S_IFIFO
            return os.stat_result(fields)

        monkeypatch.setattr(os, "fstat", fifo_fstat)
        with pytest.raises(InputTooLargeError, match="not a regular file"):
            InputLimits(max_file_bytes=1000).read_bounded(path)

    def test_unlimited_reads_a_whole_file(self, tmp_path: Path) -> None:
        """``unlimited()`` spells its ceiling ``sys.maxsize``; ``read(maxsize +
        1)`` overflows an index-sized argument, so that path must not compute
        one."""
        path = tmp_path / "any.bin"
        path.write_bytes(b"q" * 5000)
        assert InputLimits.unlimited().read_bounded(path) == b"q" * 5000


class TestTextCharGate:
    def test_decoded_char_limit_fires(self, tmp_path: Path) -> None:
        """A file whose bytes squeak under the byte gate but whose decoded
        character count exceeds ``max_text_chars`` is still refused."""
        path = _write(tmp_path / "long.txt", "a" * 5000)
        limits = InputLimits(max_file_bytes=1_000_000, max_text_chars=1000)
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                path, profile="cn_current", language="zh-CN", limits=limits
            )
        assert exc.value.kind == "text_chars"
        assert exc.value.limit == 1000

    def test_xml_route_is_char_gated(self, tmp_path: Path) -> None:
        """The ``.xml`` route reads directly (not via the shared text cache),
        so it enforces the char gate on its own path too."""
        path = tmp_path / "big.xml"
        path.write_text("<notes>" + "a" * 5000 + "</notes>", encoding="utf-8")
        limits = InputLimits(max_file_bytes=1_000_000, max_text_chars=1000)
        with pytest.raises(InputTooLargeError):
            parse_file(
                path, profile="cn_current", language="zh-CN", limits=limits
            )


class TestScoreRoutesAreCharGated:
    """A **score** suffix must not be a way around the character gate.

    The threat the two ceilings exist for: a service allows large binary
    uploads (``max_file_bytes`` high) while bounding the per-character work
    the frontend then does (``max_text_chars`` low). ``.abc`` and
    ``.musicxml`` route straight to their own adapters, so if those don't
    gate the text they read, renaming a large text file to one of those
    suffixes buys the whole byte budget with no character ceiling at all.
    """

    LIMITS = InputLimits(max_file_bytes=100_000_000, max_text_chars=1000)

    def test_abc_is_char_gated(self, tmp_path: Path) -> None:
        # .abc is kept RAW at input (deferred to the frontend), so its whole
        # decoded text is what the frontend later walks.
        path = tmp_path / "big.abc"
        path.write_text("X:1\nK:C\n" + "abc " * 2000, encoding="utf-8")
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                path,
                profile="cn_current",
                language="zh-CN",
                limits=self.LIMITS,
            )
        assert exc.value.kind == "text_chars"

    def test_musicxml_is_char_gated(self, tmp_path: Path) -> None:
        path = tmp_path / "big.musicxml"
        path.write_text(
            "<score-partwise>" + "<!--" + "a" * 5000 + "-->" + "</score-partwise>",
            encoding="utf-8",
        )
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                path,
                profile="cn_current",
                language="zh-CN",
                limits=self.LIMITS,
            )
        assert exc.value.kind == "text_chars"

    def test_adapters_gate_when_called_directly(self, tmp_path: Path) -> None:
        """The gate lives in the adapter, not in ``parse_file``'s routing —
        a caller reaching for ``parse_musicxml`` / ``parse_deferred_score``
        directly (the documented way to bypass suffix dispatch) is covered
        too, instead of silently losing its policy."""
        from brailix.input import parse_deferred_score, parse_musicxml

        abc = tmp_path / "big.abc"
        abc.write_text("X:1\n" + "abc " * 2000, encoding="utf-8")
        with pytest.raises(InputTooLargeError):
            parse_deferred_score(
                abc, language="zh-CN", profile="cn_current", limits=self.LIMITS
            )

        xml = tmp_path / "big.musicxml"
        xml.write_text("<score-partwise>" + "a" * 5000, encoding="utf-8")
        with pytest.raises(InputTooLargeError):
            parse_musicxml(
                xml, language="zh-CN", profile="cn_current", limits=self.LIMITS
            )

    def test_score_routes_pass_under_a_generous_limit(
        self, tmp_path: Path
    ) -> None:
        # The gate must not fire on a normal score: the default policy is
        # generous, and a small file passes with room to spare.
        path = tmp_path / "small.abc"
        path.write_text("X:1\nT:Tune\nK:C\nCDEF|\n", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].source == "abc"


class TestUnlimited:
    def test_unlimited_never_rejects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``InputLimits.unlimited()`` opts a trusted local caller out: even a
        (patched) astronomically large file passes the gate."""
        path = _write(tmp_path / "ok.txt", "你好")

        real_stat = Path.stat

        def _huge_stat(self: Path, *a: object, **k: object):  # type: ignore[no-untyped-def]
            st = real_stat(self, *a, **k)

            class _S:
                st_size = 10**15

                def __getattr__(self, name: str) -> object:
                    return getattr(st, name)

            return _S()

        monkeypatch.setattr(Path, "stat", _huge_stat)
        doc = parse_file(
            path,
            profile="cn_current",
            language="zh-CN",
            limits=InputLimits.unlimited(),
        )
        assert doc.blocks[0].text == "你好"


class TestPipelineForwardsLimits:
    def test_pipeline_parse_file_enforces_limits(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "big.txt", "x" * 4096)
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="null")
        with pytest.raises(InputTooLargeError):
            pipe.parse_file(path, limits=InputLimits(max_file_bytes=1024))

    def test_pipeline_translate_file_enforces_limits(
        self, tmp_path: Path
    ) -> None:
        path = _write(tmp_path / "big.txt", "x" * 4096)
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="null")
        with pytest.raises(InputTooLargeError):
            pipe.translate_file(path, limits=InputLimits(max_file_bytes=1024))


class TestDefaults:
    def test_default_is_generous(self) -> None:
        # Sanity: the shipped default won't bite a normal document (hundreds of
        # MB of headroom), so desktop use never trips it.
        assert DEFAULT_INPUT_LIMITS.max_file_bytes >= 256 * 1024 * 1024
        assert DEFAULT_INPUT_LIMITS.max_text_chars >= 1_000_000

    def test_limits_are_frozen(self) -> None:
        import dataclasses

        limits = InputLimits()
        with pytest.raises(dataclasses.FrozenInstanceError):
            limits.max_file_bytes = 1  # type: ignore[misc]
