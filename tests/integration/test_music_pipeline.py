"""End-to-end music pipeline test.

Drives :meth:`brailix.Pipeline.translate_document` over a single-part
MusicXML score wrapped in a :class:`ScoreBlock`, and verifies that
the Pipeline:

* populates ``ScoreBlock.children`` with a :class:`MusicInline` whose
  ``score`` is the parsed (and namespace-stripped) MusicXML tree;
* dispatches that MusicInline through the music backend;
* lands the BANA cells on a :class:`BrailleBlock` with
  ``block_type="score"``;
* survives soft failures (``<music-error>`` / missing adapter) with
  a populated warning collector and no crash.

This is the smoke test for the music pipeline — once it's green, a MusicXML payload
can become a BRF without going through unfamiliar plumbing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix import Pipeline
from brailix.ir.document import DocumentIR, ScoreBlock
from brailix.ir.inline import MusicInline

SIMPLE_SCORE_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "<note><pitch><step>E</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "<note><pitch><step>F</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


TWO_PART_SCORE_XML = (
    '<score-partwise version="4.0">'
    "<part-list>"
    '<score-part id="P1"><part-name>RH</part-name></score-part>'
    '<score-part id="P2"><part-name>LH</part-name></score-part>'
    "</part-list>"
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "</measure></part>"
    '<part id="P2"><measure number="1">'
    "<note><pitch><step>C</step><octave>3</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "</measure></part>"
    "</score-partwise>"
)


PIANO_TWO_STAFF_XML = (
    '<score-partwise version="4.0">'
    "<part-list>"
    '<score-part id="P1"><part-name>Piano</part-name></score-part>'
    "</part-list>"
    '<part id="P1"><measure number="1">'
    "<attributes><divisions>1</divisions><staves>2</staves>"
    '<clef number="1"><sign>G</sign><line>2</line></clef>'
    '<clef number="2"><sign>F</sign><line>4</line></clef>'
    "</attributes>"
    "<note><pitch><step>C</step><octave>5</octave></pitch>"
    "<duration>1</duration><type>quarter</type><staff>1</staff></note>"
    "<backup><duration>1</duration></backup>"
    "<note><pitch><step>C</step><octave>3</octave></pitch>"
    "<duration>1</duration><type>quarter</type><staff>2</staff></note>"
    "</measure></part>"
    "</score-partwise>"
)


@pytest.fixture
def pipe():
    return Pipeline(profile="cn_current")


def _dots(cells):
    return [c.dots for c in cells]


def _roles(cells):
    return [c.role for c in cells]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestScoreBlockTranslation:
    def test_score_block_round_trip_through_pipeline(self, pipe):
        # Build a one-block document with a ScoreBlock; Pipeline must
        # parse the MusicXML via the music frontend and dispatch the
        # resulting MusicInline through the music backend.
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SIMPLE_SCORE_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)

        # Frontend ran: ScoreBlock now carries one MusicInline child
        # with a parsed score tree.
        score_block = result.ir.blocks[0]
        assert isinstance(score_block, ScoreBlock)
        assert len(score_block.children) == 1
        child = score_block.children[0]
        assert isinstance(child, MusicInline)
        assert isinstance(child.score, ET.Element)
        assert child.score.tag == "score-partwise"

        # Backend ran: cells include one octave prefix + 4 note cells
        # (C-D-E-F sit within 3° of each other so only the first marks
        # octave per Par. 3.2.2).
        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 1
        assert bblocks[0].block_type == "score"
        cells = bblocks[0].cells
        assert _roles(cells) == [
            "music_octave", "music_note", "music_note",
            "music_note", "music_note",
        ]
        # Cells: fourth octave ('"'=5), then quarter C (1456),
        # quarter D (156), quarter E (1246), quarter F (12456).
        assert _dots(cells) == [
            (5,),                # fourth octave
            (1, 4, 5, 6),        # quarter C
            (1, 5, 6),           # quarter D
            (1, 2, 4, 6),        # quarter E
            (1, 2, 4, 5, 6),     # quarter F
        ]

    def test_no_warnings_on_clean_input(self, pipe):
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SIMPLE_SCORE_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        # Clean MusicXML should produce no warnings.
        codes = [w.code for w in result.warnings.warnings]
        assert codes == [], f"unexpected warnings: {codes}"

    def test_two_parts_separated_by_part_sep(self, pipe):
        # The backend marks the boundary between parts with a single
        # zero-width ``music_part_sep`` cell (bar-over-bar layout splits
        # on it; single-line treats it as a break).
        doc = DocumentIR(
            blocks=[ScoreBlock(text=TWO_PART_SCORE_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        # Exactly one separator, sitting between (not at either end of)
        # the two parts' cells.
        assert roles.count("music_part_sep") == 1
        idx = roles.index("music_part_sep")
        assert 0 < idx < len(roles) - 1
        assert cells[idx].dots == ()  # zero-width boundary marker

    def test_multi_staff_part_splits_into_hands(self, pipe):
        # A piano on one <part> with <staff>1 / <staff>2 is split into
        # two staff streams (right hand / left hand) joined by a single
        # music_part_sep, so bar-over-bar can align them as two hands.
        doc = DocumentIR(
            blocks=[ScoreBlock(text=PIANO_TWO_STAFF_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert roles.count("music_part_sep") == 1
        # Each staff stream carries its own clef (split by <clef number>).
        assert roles.count("music_clef") >= 2
        # Octave inference restarts per staff → both staves' first notes
        # mark octave (Par. 3.2.1).
        assert roles.count("music_octave") >= 2


# ---------------------------------------------------------------------------
# Soft failures: malformed XML → music-error in tree → MUSIC_PARSE_RECOVERY
# ---------------------------------------------------------------------------


class TestSoftFailure:
    def test_malformed_xml_produces_music_error_warning(self, pipe):
        # Adapter wraps a parse error into <music-error>; backend
        # surfaces that as MUSIC_PARSE_RECOVERY + an unknown cell.
        doc = DocumentIR(
            blocks=[ScoreBlock(text="<not closed>", source="musicxml")]
        )
        result = pipe.translate_document(doc)

        codes = [w.code for w in result.warnings.warnings]
        assert "MUSIC_PARSE_RECOVERY" in codes

        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 1
        assert bblocks[0].cells, "expected at least one fallback cell"

    def test_unknown_source_produces_adapter_missing_warning(self, pipe):
        # Pipeline routes to the music frontend with an unknown source
        # name; the frontend warns MUSIC_ADAPTER_MISSING and returns
        # None, so the populated MusicInline carries score=None and
        # the backend emits per-char unknown cells.
        doc = DocumentIR(
            blocks=[ScoreBlock(text="anything", source="midi")]
        )
        result = pipe.translate_document(doc)
        codes = [w.code for w in result.warnings.warnings]
        # Either of these is acceptable — adapter-missing happens at
        # frontend time, MUSIC_NO_IR follows from backend seeing
        # score=None.
        assert "MUSIC_ADAPTER_MISSING" in codes
        assert "MUSIC_NO_IR" in codes


# ---------------------------------------------------------------------------
# Block-level: ensure the music block plays nicely alongside other blocks
# ---------------------------------------------------------------------------


class TestMixedDocument:
    def test_paragraph_plus_score_blocks_translate_together(self, pipe):
        from brailix.ir.document import Paragraph

        doc = DocumentIR(
            blocks=[
                Paragraph(text="一段中文。"),
                ScoreBlock(text=SIMPLE_SCORE_XML, source="musicxml"),
                Paragraph(text="结束。"),
            ]
        )
        result = pipe.translate_document(doc)

        # Three BrailleBlocks — none of them empty, all with the right
        # block_type.
        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 3
        assert bblocks[0].block_type == "paragraph"
        assert bblocks[1].block_type == "score"
        assert bblocks[2].block_type == "paragraph"
        # The score block contains music cells.
        assert any(c.role and c.role.startswith("music_") for c in bblocks[1].cells)
        # The Chinese paragraphs are non-empty.
        assert bblocks[0].cells, "expected zh paragraph cells"
        assert bblocks[2].cells, "expected closing paragraph cells"


# ---------------------------------------------------------------------------
# Full score with <attributes> opening (clef + key + time)
# ---------------------------------------------------------------------------


SCORE_WITH_ATTRIBUTES_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<attributes>"
    "<divisions>4</divisions>"
    "<key><fifths>2</fifths></key>"  # D major (2 sharps)
    "<time><beats>4</beats><beat-type>4</beat-type></time>"
    "<clef><sign>G</sign><line>2</line></clef>"
    "</attributes>"
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestScoreWithAttributes:
    def test_score_with_attributes_emits_full_header(self, pipe):
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_ATTRIBUTES_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        bblocks = result.braille_ir.blocks
        assert len(bblocks) == 1
        cells = bblocks[0].cells

        # Expected role sequence at the measure opening:
        #   key (2 cells: %%) + time (3 cells: #d4) + clef (3: >/l)
        #   + octave (1: ", fourth) + note (1: D quarter)
        roles = [c.role for c in cells]
        assert roles == [
            "music_key_signature", "music_key_signature",
            "music_time_signature", "music_time_signature", "music_time_signature",
            "music_clef", "music_clef", "music_clef",
            "music_octave", "music_note",
        ]
        # No surprise warnings.
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []

    def test_feature_gates_can_strip_header(self, pipe):
        # Toggle off all three header features and re-run; only the
        # note (and its octave prefix) should remain.
        pipe._profile.features.setdefault("music", {}).update({
            "show_clef": False,
            "show_key_signature": False,
            "show_time_signature": False,
        })
        try:
            doc = DocumentIR(
                blocks=[ScoreBlock(text=SCORE_WITH_ATTRIBUTES_XML, source="musicxml")]
            )
            result = pipe.translate_document(doc)
            roles = [c.role for c in result.braille_ir.blocks[0].cells]
            assert roles == ["music_octave", "music_note"]
        finally:
            # Restore defaults for downstream tests.
            pipe._profile.features.setdefault("music", {}).update({
                "show_clef": True,
                "show_key_signature": True,
                "show_time_signature": True,
            })
