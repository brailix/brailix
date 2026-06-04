"""Tests for M8: cell-level source provenance.

Every cell emitted by the music backend within a ``<part>`` /
``<measure>`` container should carry that container's identifier in
its ``source_text`` (``[p=<part_id>,m=<measure_number>]`` suffix).
This lets proofread / front-end tooling map any cell back to its measure
without re-parsing the MusicXML tree.

Bare emit_tree() calls (no surrounding <part>/<measure>) leave
source_text untouched so existing low-level tests stay valid.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


# ---------------------------------------------------------------------------
# Bare element: no annotation
# ---------------------------------------------------------------------------


class TestBareElementUnchanged:
    def test_lone_note_no_provenance_suffix(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # No <part>/<measure> wrapper → no [p=...,m=...] suffix.
        for c in cells:
            text = c.source_text or ""
            assert "[p=" not in text
            assert "[m=" not in text


# ---------------------------------------------------------------------------
# Inside <measure> only
# ---------------------------------------------------------------------------


class TestMeasureAnnotation:
    def test_measure_only_annotates_m(self, profile, ctx):
        m = ET.fromstring(
            '<measure number="3">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        # Every emitted cell carries [m=3] but no [p=...].
        for c in cells:
            text = c.source_text or ""
            assert "[m=3]" in text
            assert "[p=" not in text

    def test_measure_without_number_no_annotation(self, profile, ctx):
        # If <measure> has no number attribute, current_measure_number
        # falls back to whatever was set on entry (None at top-level),
        # so no annotation is added.
        m = ET.fromstring(
            "<measure>"
            "<note><pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        for c in cells:
            text = c.source_text or ""
            assert "[m=" not in text

    def test_measure_number_restored_after_exit(self, profile, ctx):
        # Two measures back-to-back inside a <part> — each carries its
        # own number, not the previous one's.
        part = ET.fromstring(
            '<part id="P1">'
            '<measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            '<measure number="2">'
            "<note><pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part>"
        )
        cells = emit_tree(part, ctx, profile)
        note_cells = [c for c in cells if c.role == "music_note"]
        assert len(note_cells) == 2
        assert "[p=P1,m=1]" in (note_cells[0].source_text or "")
        assert "[p=P1,m=2]" in (note_cells[1].source_text or "")


# ---------------------------------------------------------------------------
# Inside <part> + <measure>
# ---------------------------------------------------------------------------


class TestPartAndMeasureAnnotation:
    def test_part_measure_both_annotated(self, profile, ctx):
        part = ET.fromstring(
            '<part id="RH">'
            '<measure number="5">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part>"
        )
        cells = emit_tree(part, ctx, profile)
        for c in cells:
            text = c.source_text or ""
            assert "[p=RH,m=5]" in text

    def test_part_without_id_only_m_annotated(self, profile, ctx):
        # MusicXML technically requires <part id="..."> but tolerate it.
        part = ET.fromstring(
            "<part>"
            '<measure number="1">'
            "<note><pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part>"
        )
        cells = emit_tree(part, ctx, profile)
        for c in cells:
            text = c.source_text or ""
            assert "[m=1]" in text
            assert "[p=" not in text

    def test_multiple_parts_carry_distinct_ids(self, profile, ctx):
        score = ET.fromstring(
            "<score-partwise>"
            '<part id="P1">'
            '<measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part>"
            '<part id="P2">'
            '<measure number="1">'
            "<note><pitch><step>G</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part>"
            "</score-partwise>"
        )
        cells = emit_tree(score, ctx, profile)
        note_cells = [c for c in cells if c.role == "music_note"]
        assert len(note_cells) == 2
        assert "[p=P1," in (note_cells[0].source_text or "")
        assert "[p=P2," in (note_cells[1].source_text or "")


# ---------------------------------------------------------------------------
# Different cell kinds all get annotated (not just notes)
# ---------------------------------------------------------------------------


class TestAllKindsAnnotated:
    def test_clef_key_time_all_annotated(self, profile, ctx):
        part = ET.fromstring(
            '<part id="P1">'
            '<measure number="1">'
            "<attributes>"
            "<key><fifths>2</fifths></key>"
            "<time><beats>4</beats><beat-type>4</beat-type></time>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
            "</measure>"
            "</part>"
        )
        cells = emit_tree(part, ctx, profile)
        # 2 key + 3 time + 3 clef = 8 cells, all carry the suffix.
        assert len(cells) == 8
        for c in cells:
            text = c.source_text or ""
            assert "[p=P1,m=1]" in text

    def test_barline_in_measure_annotated(self, profile, ctx):
        m = ET.fromstring(
            '<measure number="2">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            '<barline location="right">'
            "<bar-style>light-heavy</bar-style></barline>"
            "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        bar_cells = [c for c in cells if c.role == "music_bar_line"]
        assert len(bar_cells) == 2
        for c in bar_cells:
            assert "[m=2]" in (c.source_text or "")


# ---------------------------------------------------------------------------
# Pipeline integration: proofread_json carries the annotations
# ---------------------------------------------------------------------------


SCORE_FOR_PROOFREAD_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<attributes>"
    "<key><fifths>0</fifths></key>"
    "<time><beats>4</beats><beat-type>4</beat-type></time>"
    "<clef><sign>G</sign><line>2</line></clef>"
    "</attributes>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure>"
    '<measure number="2">'
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestProofreadJson:
    def test_proofread_json_contains_provenance(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_FOR_PROOFREAD_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        pf = result.proofread_json()

        # proofread_json includes the braille_ir; every music cell's
        # source_text should carry [p=P1,m=N] for some N.
        braille = pf["braille_ir"]
        all_cells = [
            c for block in braille["blocks"]
            for c in block.get("cells", [])
        ]
        music_cells = [
            c for c in all_cells
            if isinstance(c.get("role"), str)
            and c["role"].startswith("music_")
            # Inter-measure separators are structural spacing — they have
            # no single (part, measure) home, so carry no [p,m] provenance.
            and c["role"] != "music_measure_sep"
        ]
        assert music_cells, "expected at least one music cell"
        for c in music_cells:
            st = c.get("source_text", "") or ""
            assert "[p=P1," in st, f"missing part annotation on {c}"
            assert "m=" in st, f"missing measure annotation on {c}"

    def test_cells_split_by_measure(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_FOR_PROOFREAD_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        # Source-text format is "<entity> [p=<part>,m=<measure>]",
        # so the measure suffix matches "m=N]".
        m1_cells = [c for c in cells if "m=1]" in (c.source_text or "")]
        m2_cells = [c for c in cells if "m=2]" in (c.source_text or "")]
        # Measure 1: 0 fifths emits no key cells; 3 time + 3 clef +
        # 1 octave + 1 C quarter = 8 cells.
        # Measure 2: D quarter only (2° from C → no octave re-mark) = 1 cell.
        assert len(m1_cells) == 8
        assert len(m2_cells) == 1
