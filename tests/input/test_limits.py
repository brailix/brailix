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


class TestEveryRouteBindsTheCallersCeilingToTheBytesRead:
    """The caller's ``max_file_bytes`` must reach the read that actually
    happens, on every route.

    ``check_file_size`` is a fast reject on *path metadata*; the binding
    promise is :meth:`InputLimits.read_bounded`, on the descriptor. A route
    that read through a helper whose ``limits`` parameter was *defaulted*
    quietly swapped the caller's policy for ``DEFAULT_INPUT_LIMITS`` — the
    file-byte ceiling was then effectively absent, since the caller only ever
    tightens it. Two routes did: the generic ``.xml`` sniff and the ``.abc``
    deferred score. Both are exercised through the replace-after-stat window,
    which is the only way to tell a real read ceiling from an absent one.

    :meth:`InputLimits.read_bounded_text` is why this can't come back: it is a
    method, so there is no default to fall back to — a caller without an
    ``InputLimits`` in hand cannot read at all.
    """

    @staticmethod
    def _swap_after_stat(monkeypatch, target: Path, replacement: bytes) -> None:
        """Rewrite ``target`` the first time its ``stat()`` is taken — the
        window between ``parse_file``'s gate and the adapter's read."""
        import pathlib

        real_stat = pathlib.Path.stat
        fired = {"n": 0}

        def growing_stat(self, **kwargs):  # noqa: ANN001, ANN202
            result = real_stat(self, **kwargs)
            if self == target and fired["n"] == 0:
                fired["n"] += 1
                target.write_bytes(replacement)
            return result

        monkeypatch.setattr(pathlib.Path, "stat", growing_stat)

    def test_generic_xml_route(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "doc.xml"
        target.write_bytes(b"<a/>")
        self._swap_after_stat(monkeypatch, target, b"<a>" + b"x" * 4096 + b"</a>")
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                target,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )
        assert exc.value.kind == "file_bytes"

    def test_score_xml_route(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "score.musicxml"
        target.write_bytes(b"<score-partwise/>")
        self._swap_after_stat(monkeypatch, target, b"<score-partwise>" + b"x" * 4096)
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                target,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )
        assert exc.value.kind == "file_bytes"

    def test_deferred_abc_route(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "tune.abc"
        target.write_bytes(b"X:1\nK:C\n")
        self._swap_after_stat(monkeypatch, target, b"X:1\nK:C\n" + b"a" * 4096)
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                target,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )
        assert exc.value.kind == "file_bytes"

    def test_plain_route_still_bound(self, tmp_path: Path, monkeypatch) -> None:
        # The route that already had it — kept so a refactor of the shared
        # reader can't lose the one case that was right.
        target = _write(tmp_path / "doc.txt", "ab")
        self._swap_after_stat(monkeypatch, target, b"x" * 4096)
        with pytest.raises(InputTooLargeError):
            parse_file(
                target,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )


class TestWordRoutesOwnTheirInput:
    """A ``.docx`` is checked and then parsed; both must see the same bytes.

    The archive used to be opened twice by path — once by the zip-bomb
    preflight, once by python-docx — so the object that passed the caps and
    the object that was parsed could be two different files, and the caller's
    ``max_file_bytes`` never reached the second open at all.
    """

    @staticmethod
    def _docx(path: Path, text: str) -> Path:
        docx = pytest.importorskip("docx")
        doc = docx.Document()
        doc.add_paragraph(text)
        doc.save(str(path))
        return path

    def test_callers_ceiling_reaches_the_docx_read(self, tmp_path: Path) -> None:
        path = self._docx(tmp_path / "doc.docx", "hello")
        with pytest.raises(InputTooLargeError) as exc:
            parse_file(
                path,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )
        assert exc.value.kind == "file_bytes"

    def test_parse_consumes_the_preflighted_bytes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Replace the archive the instant the preflight has passed: the parse
        must still yield the document that was checked, not the substitute."""
        import brailix.input.docx as docx_adapter

        path = self._docx(tmp_path / "doc.docx", "原始文档")
        substitute = self._docx(tmp_path / "other.docx", "掉包文档").read_bytes()

        real_preflight = docx_adapter._preflight_docx_archive

        def swapping_preflight(data: bytes, p: Path) -> None:
            real_preflight(data, p)
            path.write_bytes(substitute)  # the window, now closed

        monkeypatch.setattr(
            docx_adapter, "_preflight_docx_archive", swapping_preflight
        )
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        texts = [b.text for b in doc.blocks if getattr(b, "text", None)]
        assert "原始文档" in texts
        assert "掉包文档" not in texts

    def test_doc_conversion_hands_libreoffice_a_bounded_private_copy(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """``soffice`` takes a path, so it must be pointed at the bytes that
        were read under the caller's ceiling — not at the caller's path, which
        can resolve to something else by the time the converter opens it."""
        import brailix.input.docx as docx_adapter

        original = tmp_path / "legacy.doc"
        original.write_bytes(b"\xd0\xcf\x11\xe0 legacy doc bytes")
        converted_from: list[Path] = []

        def fake_run(cmd, *, check, capture_output, timeout):  # noqa: ANN001
            source = Path(cmd[-1])
            converted_from.append(source)
            # The converter is handed a private copy, so swapping the caller's
            # path now must not change what gets converted.
            original.write_bytes(b"\xd0\xcf\x11\xe0 swapped")
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            TestWordRoutesOwnTheirInput._docx(
                out_dir / (source.stem + ".docx"), "转换结果"
            )

            class Result:
                returncode = 0

            return Result()

        monkeypatch.setattr(
            docx_adapter, "_resolve_doc_converter", lambda override: "soffice"
        )
        monkeypatch.setattr(docx_adapter.subprocess, "run", fake_run)

        doc = parse_file(original, profile="cn_current", language="zh-CN")
        assert len(converted_from) == 1
        source = converted_from[0]
        assert source != original, "LibreOffice was pointed at the caller's path"
        assert source.name == original.name
        assert [b.text for b in doc.blocks if getattr(b, "text", None)] == [
            "转换结果"
        ]

    def test_doc_conversion_respects_the_callers_ceiling(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import brailix.input.docx as docx_adapter

        original = tmp_path / "legacy.doc"
        original.write_bytes(b"\xd0\xcf\x11\xe0" + b"x" * 4096)
        monkeypatch.setattr(
            docx_adapter, "_resolve_doc_converter", lambda override: "soffice"
        )

        def never(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("LibreOffice must not see an oversized input")

        monkeypatch.setattr(docx_adapter.subprocess, "run", never)
        with pytest.raises(InputTooLargeError):
            parse_file(
                original,
                profile="cn_current",
                language="zh-CN",
                limits=InputLimits(max_file_bytes=64),
            )


class TestTheGenericXmlRouteOwnsItsInput:
    """``.xml`` is sniffed and then parsed; both must see the same bytes.

    The generic container has to be classified before it can be routed, so the
    route reads the head to find the root element — and then handed
    :func:`parse_musicxml` the *path*, which opened and decoded the file again.
    Two reads of one path are two different documents the moment something
    replaces it in between: the sniff classifies one and the parse consumes the
    other. Exactly the window the ``.docx`` route is already held closed
    against, and the wasted second decode of a large score was the lesser half.
    """

    @staticmethod
    def _swap_after_each_read(monkeypatch, path: Path, substitute: str) -> list[Path]:
        """Rewrite ``path`` after every bounded read; return the read log."""
        real = InputLimits.read_bounded
        reads: list[Path] = []

        def counting_read(self: InputLimits, p: object) -> bytes:
            reads.append(Path(p))  # type: ignore[arg-type]
            data = real(self, p)  # type: ignore[arg-type]
            path.write_text(substitute, encoding="utf-8")
            return data

        monkeypatch.setattr(InputLimits, "read_bounded", counting_read)
        return reads

    def test_a_score_xml_is_read_once_and_parsed_from_that_snapshot(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        original = "<score-partwise><part-list/></score-partwise>"
        path = tmp_path / "score.xml"
        path.write_text(original, encoding="utf-8")
        reads = self._swap_after_each_read(
            monkeypatch, path, "<score-partwise><!--swapped--></score-partwise>"
        )

        doc = parse_file(path, language="zh-CN", profile="cn_current")

        assert [p for p in reads if p == path] == [path], (
            f"the .xml route read the file {len(reads)} times — the sniff and "
            f"the parse must share one snapshot"
        )
        assert doc.blocks[0].source == "musicxml"
        assert doc.blocks[0].text == original
        assert "swapped" not in doc.blocks[0].text

    def test_a_non_score_xml_is_read_once_too(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The other branch: a generic ``.xml`` falls back to plain text off
        the same snapshot, so it never re-reads either."""
        original = "<notes>hello</notes>"
        path = tmp_path / "doc.xml"
        path.write_text(original, encoding="utf-8")
        reads = self._swap_after_each_read(monkeypatch, path, "<notes>swapped</notes>")

        doc = parse_file(path, language="zh-CN", profile="cn_current")

        assert [p for p in reads if p == path] == [path]
        assert "swapped" not in doc.blocks[0].text


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


class TestConstructionRefusesANonsenseCeiling:
    """A ceiling that no input can satisfy is a mistake at the construction
    site, and that is where it should be reported.

    Left unvalidated it surfaced as a nonsensical rejection ("4096 bytes > -2
    bytes") from whichever read ran first, and ``True`` / ``False`` — ``int``
    subclasses both — became a silent one-byte / zero-byte budget. The memory
    bound also stopped resting on an accident: ``read_bounded`` computes
    ``read(max_file_bytes + 1)``, which for a negative ceiling is ``read(-1)``
    — read to EOF — and was unreachable only because the ``fstat`` gate above
    it happens to reject first.
    """

    @pytest.mark.parametrize("bad", [-1, -2, -(10**9)])
    def test_negative_ceilings_are_refused(self, bad: int) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            InputLimits(max_file_bytes=bad)
        with pytest.raises(ValueError, match="must be >= 0"):
            InputLimits(max_text_chars=bad)

    @pytest.mark.parametrize("bad", [True, False])
    def test_booleans_are_refused(self, bad: bool) -> None:
        """``bool`` passes an ``isinstance(..., int)`` test, so it has to be
        rejected by name or it means "one byte" / "zero bytes"."""
        with pytest.raises(ValueError, match="must be an int"):
            InputLimits(max_file_bytes=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must be an int"):
            InputLimits(max_text_chars=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [1.5, "1024", None, 10**6 + 0.0])
    def test_non_integers_are_refused(self, bad: object) -> None:
        with pytest.raises(ValueError, match="must be an int"):
            InputLimits(max_file_bytes=bad)  # type: ignore[arg-type]

    def test_zero_is_a_legitimate_ceiling(self, tmp_path: Path) -> None:
        """"Only an empty file passes" is extreme but coherent, and the gates
        already read it that way — so it must not be swept up by the check."""
        limits = InputLimits(max_file_bytes=0, max_text_chars=0)
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"")
        assert limits.read_bounded(empty) == b""
        with pytest.raises(InputTooLargeError):
            limits.read_bounded(_write(tmp_path / "one.txt", "x"))

    def test_the_shipped_defaults_and_unlimited_still_construct(self) -> None:
        assert InputLimits().max_file_bytes > 0
        assert InputLimits.unlimited().max_text_chars > 0


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
