"""Tests for the M7.2 music file input adapter.

Covers ``parse_musicxml`` directly + suffix dispatch through
``parse_file``: ``.musicxml`` / ``.xml`` (UTF-8 text) and ``.mxl``
(ZIP container, unzipped through the frontend MxlSourceAdapter).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from brailix.input import parse_file, parse_musicxml
from brailix.ir.document import DocumentIR, ScoreBlock

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


# ---------------------------------------------------------------------------
# parse_musicxml direct
# ---------------------------------------------------------------------------


class TestParseMusicxml:
    def test_musicxml_text_file(self, tmp_path):
        p = tmp_path / "song.musicxml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_musicxml(p)
        assert isinstance(doc, DocumentIR)
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert doc.blocks[0].source == "musicxml"
        assert doc.blocks[0].text == SIMPLE_XML

    def test_xml_suffix_also_works(self, tmp_path):
        p = tmp_path / "song.xml"
        p.write_text(SIMPLE_XML, encoding="utf-8")
        doc = parse_musicxml(p)
        assert doc.blocks[0].source == "musicxml"

    def test_mxl_zip_file(self, tmp_path):
        p = tmp_path / "song.mxl"
        p.write_bytes(_make_mxl_bytes(SIMPLE_XML))
        doc = parse_musicxml(p)
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
            parse_musicxml(p)

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
        doc = parse_file(p)
        assert isinstance(doc.blocks[0], ScoreBlock)
        assert doc.blocks[0].source == "musicxml"

    def test_mxl_via_parse_file(self, tmp_path):
        p = tmp_path / "song.mxl"
        p.write_bytes(_make_mxl_bytes(SIMPLE_XML))
        doc = parse_file(p)
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
