"""Tests for the input-layer size budget — :class:`brailix.input.InputLimits`.

The gate refuses an oversized file with a ``stat()`` BEFORE any read, so an
untrusted upload can't spike process memory the instant it's loaded (P1-3).
These pin: the pre-read ordering, the two ceilings (file bytes / decoded
characters), the ``unlimited()`` opt-out, that binary formats are gated too,
and that the gate never masks a genuine ``FileNotFoundError``.
"""

from __future__ import annotations

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
