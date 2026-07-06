"""Tests for the music file input adapters.

Covers ``parse_musicxml`` directly + suffix dispatch through
``parse_file``: ``.musicxml`` / ``.xml`` (UTF-8 text) and ``.mxl``
(ZIP container, unzipped through the frontend MxlSourceAdapter),
``parse_score_file`` for the eager binary score formats (``.mid`` /
``.midi`` as bytes), and ``parse_deferred_score`` for the deferred text
dialect (``.abc``, kept raw for the frontend).
"""

from __future__ import annotations

import importlib.util
import io
import zipfile

import pytest

from brailix.core import MissingExtraError
from brailix.core.registry import Registry
from brailix.input import (
    parse_deferred_score,
    parse_file,
    parse_musicxml,
    parse_score_file,
)
from brailix.ir.document import DocumentIR, ScoreBlock


def _has(pkg: str) -> bool:
    return importlib.util.find_spec(pkg) is not None


class _RecordingAdapter:
    """Stand-in music source adapter that records what it was handed and
    returns a fixed MusicXML string — lets the dispatch tests run without
    the optional ``midi`` / ``abc`` packages installed."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.received: str | bytes | None = None
        self.ctx_source: str | None = None

    def to_musicxml(self, src, ctx=None) -> str:
        self.received = src
        self.ctx_source = getattr(ctx, "source", None)
        return SIMPLE_XML

SIMPLE_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>V</part-name></score-part>'
    "</part-list>"
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure></part>"
    "</score-partwise>"
)


def _make_mxl_bytes(score_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container><rootfiles>'
            '<rootfile full-path="score.musicxml" '
            'media-type="application/vnd.recordare.musicxml+xml"/>'
            '</rootfiles></container>',
        )
        zf.writestr("score.musicxml", score_xml)
    return buf.getvalue()


class TestMxlZipBombCap:
    """A small .mxl whose inner member inflates past the decompression cap
    must soft-fail (music-error), not OOM the process."""

    def test_oversized_member_soft_fails_not_oom(self, monkeypatch):
        import brailix.frontend.music.adapters.mxl as mxl_mod
        from brailix.frontend.music.adapters.mxl import MxlSourceAdapter

        # Shrink the cap instead of building a 64 MB payload: a normal inner
        # score then exceeds it and exercises the same abort path a real zip
        # bomb (tiny on disk, gigabytes inflated) would hit.
        monkeypatch.setattr(mxl_mod, "_MAX_MEMBER_BYTES", 512)
        big_inner = (
            "<score-partwise><part-list/>"
            + "<part><measure/></part>" * 60
            + "</score-partwise>"
        )
        assert len(big_inner) > 512
        out = MxlSourceAdapter().to_musicxml(_make_mxl_bytes(big_inner))
        assert "zip bomb" in out  # soft-failed with the cap reason


# ---------------------------------------------------------------------------
# parse_musicxml direct
# ---------------------------------------------------------------------------


class TestParseMusicxml:
    def test_musicxml_text_file(self, tmp_path):
        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_musicxml(p, profile="cn_current", language="zh-CN")
        assert isinstance(doc, DocumentIR)
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert doc.blocks[0].source == "musicxml"
        assert doc.blocks[0].text == SIMPLE_XML

    def test_xml_suffix_also_works(self, tmp_path):
        p = tmp_path / "song.xml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_musicxml(p, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].source == "musicxml"

    def test_utf16_musicxml_loads(self, tmp_path):
        # Finale / some Windows exporters write UTF-16 (with a BOM); a flat
        # utf-8-sig read used to raise UnicodeDecodeError on those valid files.
        p = tmp_path / "song.musicxml"
        p.write_bytes(SIMPLE_XML.encode("utf-16"))  # encodes with a BOM
        doc = parse_musicxml(p, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert "<step>C</step>" in doc.blocks[0].text

    def test_mxl_zip_file(self, tmp_path):
        p = tmp_path / "song.mxl"
        p.write_bytes(_make_mxl_bytes(SIMPLE_XML))
        doc = parse_musicxml(p, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)
        # Source is normalised to musicxml after unzipping (the inner
        # XML is plain text by this point).
        assert doc.blocks[0].source == "musicxml"
        # The inner XML is preserved (modulo MxlSourceAdapter normalisation).
        assert "<step>C</step>" in doc.blocks[0].text

    def test_unsupported_suffix_raises(self, tmp_path):
        p = tmp_path / "song.midi"
        p.write_bytes(b"\x00")
        with pytest.raises(ValueError, match="unsupported"):
            parse_musicxml(p, profile="cn_current", language="zh-CN")

    def test_metadata_records_language_profile(self, tmp_path):
        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_musicxml(p, language="en-US", profile="cn_current")
        assert doc.metadata["language"] == "en-US"
        assert doc.metadata["profile"] == "cn_current"


# ---------------------------------------------------------------------------
# parse_file dispatch
# ---------------------------------------------------------------------------


class TestParseFileDispatch:
    def test_musicxml_via_parse_file(self, tmp_path):
        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_file(p, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert doc.blocks[0].source == "musicxml"

    def test_mxl_via_parse_file(self, tmp_path):
        p = tmp_path / "song.mxl"
        p.write_bytes(_make_mxl_bytes(SIMPLE_XML))
        doc = parse_file(p, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert "<step>C</step>" in doc.blocks[0].text


# ---------------------------------------------------------------------------
# End-to-end Pipeline.translate_file
# ---------------------------------------------------------------------------


class TestPipelineTranslateFile:
    def test_musicxml_round_trip(self, tmp_path):
        from brailix import Pipeline

        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")

        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_file(p)

        # Score block → MusicInline child → backend emits note cells.
        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 1
        assert bblocks[0].block_type == "score"
        roles = [c.role for c in bblocks[0].cells]
        assert "music_note" in roles
        assert "music_octave" in roles

    def test_mxl_round_trip(self, tmp_path):
        from brailix import Pipeline

        p = tmp_path / "song.mxl"
        p.write_bytes(_make_mxl_bytes(SIMPLE_XML))

        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_file(p)
        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 1
        assert any(c.role == "music_note" for c in bblocks[0].cells)


# ---------------------------------------------------------------------------
# parse_score_file — adapter-converted score formats (.mid / .midi / .abc)
# ---------------------------------------------------------------------------


class TestParseScoreFileDispatch:
    """The read-and-route logic, exercised with a stand-in adapter so it
    runs without partitura / abc-xml-converter installed."""

    def test_mid_reads_bytes_and_normalises_to_musicxml(
        self, tmp_path, monkeypatch
    ):
        fake = _RecordingAdapter("midi")
        # Registry uses __slots__, so patch the class method (auto-reverts,
        # no instance-cache pollution) rather than the bound get.
        monkeypatch.setattr(Registry, "get", lambda self, name: fake)

        p = tmp_path / "song.mid"
        p.write_bytes(b"MThd\x00\x00\x00\x06")
        doc = parse_file(p, profile="cn_current", language="zh-CN")

        assert isinstance(doc.blocks[0], ScoreBlock)
        # Conversion is eager: source is normalised to musicxml and the
        # block carries the adapter's MusicXML output as text.
        assert doc.blocks[0].source == "musicxml"
        assert "<step>C</step>" in doc.blocks[0].text
        # MIDI is binary — the adapter must receive raw bytes, not text.
        assert fake.received == b"MThd\x00\x00\x00\x06"
        assert fake.ctx_source == "midi"

    def test_parse_score_file_rejects_abc(self, tmp_path):
        # ABC is a text dialect handled by parse_deferred_score now; the
        # binary parse_score_file rejects it.
        p = tmp_path / "tune.abc"
        p.write_text("X:1\nK:C\nCDEF|", encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported binary score"):
            parse_score_file(p, profile="cn_current", language="zh-CN")

    def test_midi_long_suffix_maps_to_midi_source(self, tmp_path, monkeypatch):
        captured: list[str] = []

        def fake_get(self, name):
            captured.append(name)
            return _RecordingAdapter(name)

        monkeypatch.setattr(Registry, "get", fake_get)

        p = tmp_path / "song.midi"
        p.write_bytes(b"\x00")
        parse_file(p, profile="cn_current", language="zh-CN")
        assert captured == ["midi"]

    def test_parse_score_file_rejects_musicxml_family(self, tmp_path):
        # The MusicXML family is parse_musicxml's job; parse_score_file
        # only handles the adapter-converted formats.
        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported"):
            parse_score_file(p, profile="cn_current", language="zh-CN")


class TestParseScoreFileMissingExtra:
    """Without the optional dependency, file input fails loudly with a
    MissingExtraError naming the extra — the same contract as .docx."""

    @pytest.mark.skipif(
        _has("partitura"),
        reason="partitura installed — can't test the missing-extra path",
    )
    def test_mid_without_midi_extra_raises(self, tmp_path):
        p = tmp_path / "song.mid"
        p.write_bytes(b"MThd")
        with pytest.raises(MissingExtraError):
            parse_file(p, profile="cn_current", language="zh-CN")


class TestParseDeferredScore:
    """.abc is a text dialect: stored raw at input and deferred to the
    frontend (ARCHITECTURE §1 rule 1), exactly as a LaTeX MathBlock — the
    input layer runs no adapter and imports no frontend for it."""

    def test_abc_via_parse_file_stored_raw(self, tmp_path):
        p = tmp_path / "tune.abc"
        p.write_text("X:1\nK:C\nCDEF|", encoding="utf-8")
        doc = parse_file(p, profile="cn_current", language="zh-CN")

        block = doc.blocks[0]
        assert isinstance(block, ScoreBlock)
        # Deferred, not converted: source stays the dialect name and text is
        # the verbatim ABC (a converted block would be source="musicxml"
        # carrying <score-partwise>).
        assert block.source == "abc"
        assert block.text == "X:1\nK:C\nCDEF|"

    def test_parse_deferred_score_direct(self, tmp_path):
        p = tmp_path / "tune.abc"
        p.write_text("X:1\nK:C\nCDEF|", encoding="utf-8")
        doc = parse_deferred_score(p, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].source == "abc"
        assert doc.blocks[0].text == "X:1\nK:C\nCDEF|"

    def test_abc_touches_no_source_adapter(self, tmp_path, monkeypatch):
        # The deferral must not reach the registry at all: a get() that
        # explodes proves parse_file never resolves an adapter for .abc
        # (so a missing abc extra can't raise at input time either).
        def boom(self, name):
            raise AssertionError(
                f"input must not resolve adapter {name!r} for a deferred .abc"
            )

        monkeypatch.setattr(Registry, "get", boom)
        p = tmp_path / "tune.abc"
        p.write_text("X:1\nK:C\nCDEF|", encoding="utf-8")
        doc = parse_file(p, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].source == "abc"

    def test_parse_deferred_score_rejects_binary(self, tmp_path):
        # .mid is parse_score_file's job (binary, eager); the deferred path
        # rejects it.
        p = tmp_path / "song.mid"
        p.write_bytes(b"MThd")
        with pytest.raises(ValueError, match="unsupported deferred score"):
            parse_deferred_score(p, profile="cn_current", language="zh-CN")
