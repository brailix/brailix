"""Unit tests for M6: tremolo (Table 14) + ornaments (Table 16).

Verifies that ornaments dispatched from inside ``<notations>`` reach
the BANA Tables 14 and 16 entities, that ``music.show_ornaments``
gates them as a group, and that the tremolo dual-mode behaviour
(``type="single"`` → repetition family, ``type="start"`` → alternation
family, ``type="stop"`` → no cell) routes correctly.
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


def _dots(cells):
    return [c.dots for c in cells]


def _roles(cells):
    return [c.role for c in cells]


def _note_with_ornaments(inner: str) -> ET.Element:
    """Build a quarter C4 note wrapped around the given ornaments XML."""
    return ET.fromstring(
        "<note>"
        "<pitch><step>C</step><octave>4</octave></pitch>"
        "<duration>1</duration><type>quarter</type>"
        f"<notations><ornaments>{inner}</ornaments></notations>"
        "</note>"
    )


# ---------------------------------------------------------------------------
# Tremolo (Table 14)
# ---------------------------------------------------------------------------


class TestTremoloRepetition:
    @pytest.mark.parametrize(
        "strokes, expected_dots",
        [
            # repetition_8ths = "^b" = (4,5)(1,2)
            (1, [(4, 5), (1, 2)]),
            # repetition_16ths = "^l" = (4,5)(1,2,3)
            (2, [(4, 5), (1, 2, 3)]),
            # repetition_32nds = "^1" = (4,5)(2,)
            (3, [(4, 5), (2,)]),
            # repetition_64ths = "^k" = (4,5)(1,3)
            (4, [(4, 5), (1, 3)]),
            # repetition_128ths = "^'" = (4,5)(3,)
            (5, [(4, 5), (3,)]),
        ],
    )
    def test_single_strokes_repetition(
        self, profile, ctx, strokes, expected_dots,
    ):
        note = _note_with_ornaments(f'<tremolo type="single">{strokes}</tremolo>')
        cells = emit_tree(note, ctx, profile)
        tremolo_cells = [c for c in cells if c.role == "music_tremolo"]
        assert _dots(tremolo_cells) == expected_dots

    def test_default_strokes_is_one(self, profile, ctx):
        # <tremolo type="single"/> with no body → default 1 stroke.
        note = _note_with_ornaments('<tremolo type="single"/>')
        cells = emit_tree(note, ctx, profile)
        tremolo_cells = [c for c in cells if c.role == "music_tremolo"]
        # repetition_8ths
        assert _dots(tremolo_cells) == [(4, 5), (1, 2)]

    def test_default_type_is_single(self, profile, ctx):
        # <tremolo>2</tremolo> with no type attribute defaults to single.
        note = _note_with_ornaments("<tremolo>2</tremolo>")
        cells = emit_tree(note, ctx, profile)
        # repetition_16ths
        tremolo_cells = [c for c in cells if c.role == "music_tremolo"]
        assert _dots(tremolo_cells) == [(4, 5), (1, 2, 3)]


class TestTremoloAlternation:
    def test_start_uses_alternation_family(self, profile, ctx):
        # 2 strokes alternation = "_l" wait — let me recompute.
        # alternation_8ths = ".b" = (4,6)(1,2)
        note = _note_with_ornaments('<tremolo type="start">1</tremolo>')
        cells = emit_tree(note, ctx, profile)
        tremolo_cells = [c for c in cells if c.role == "music_tremolo"]
        # alternation_8ths
        assert _dots(tremolo_cells) == [(4, 6), (1, 2)]

    def test_stop_emits_nothing(self, profile, ctx):
        # Alternation stop side — no cell.
        note = _note_with_ornaments('<tremolo type="stop">2</tremolo>')
        cells = emit_tree(note, ctx, profile)
        assert "music_tremolo" not in _roles(cells)


class TestTremoloEdgeCases:
    def test_six_strokes_warns(self, profile, ctx):
        # 6 strokes not in our table.
        note = _note_with_ornaments('<tremolo type="single">6</tremolo>')
        cells = emit_tree(note, ctx, profile)
        assert "music_tremolo" not in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_unmeasured_warns(self, profile, ctx):
        note = _note_with_ornaments('<tremolo type="unmeasured">3</tremolo>')
        cells = emit_tree(note, ctx, profile)
        assert "music_tremolo" not in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_unknown_type_warns(self, profile, ctx):
        note = _note_with_ornaments('<tremolo type="zigzag">2</tremolo>')
        emit_tree(note, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_non_integer_strokes_defaults_to_one(self, profile, ctx):
        # Malformed body — fall back to 1 stroke.
        note = _note_with_ornaments('<tremolo type="single">three</tremolo>')
        cells = emit_tree(note, ctx, profile)
        tremolo_cells = [c for c in cells if c.role == "music_tremolo"]
        # repetition_8ths
        assert _dots(tremolo_cells) == [(4, 5), (1, 2)]


# ---------------------------------------------------------------------------
# Ornaments (Table 16) — simple leaf tags
# ---------------------------------------------------------------------------


class TestOrnaments:
    @pytest.mark.parametrize(
        "tag, expected_entity, expected_dots",
        [
            # trill = "6" = (2,3,5)
            ("trill-mark", "trill", [(2, 3, 5)]),
            # turn_between_notes = "4" = (2,5,6)
            ("turn", "turn_between_notes", [(2, 5, 6)]),
            # inverted_turn_between_notes = "4l" = (2,5,6)(1,2,3)
            ("inverted-turn", "inverted_turn_between_notes",
             [(2, 5, 6), (1, 2, 3)]),
            # upper_mordent = '"6' = (5,)(2,3,5)
            ("mordent", "upper_mordent", [(5,), (2, 3, 5)]),
            # lower_mordent = '"6l' = (5,)(2,3,5)(1,2,3)
            ("inverted-mordent", "lower_mordent",
             [(5,), (2, 3, 5), (1, 2, 3)]),
            # glissando_line_between_notes = "@a" = (4,)(1,)
            ("glissando", "glissando_line_between_notes", [(4,), (1,)]),
        ],
    )
    def test_single_ornament(
        self, profile, ctx, tag, expected_entity, expected_dots,
    ):
        note = _note_with_ornaments(f"<{tag}/>")
        cells = emit_tree(note, ctx, profile)
        ornament_cells = [c for c in cells if c.role == "music_ornament"]
        assert _dots(ornament_cells) == expected_dots
        # source_text records the MusicXML tag.
        assert ornament_cells[0].source_text == tag

    def test_multiple_ornaments_on_one_note(self, profile, ctx):
        # Trill + turn — both emit, in document order.
        note = _note_with_ornaments("<trill-mark/><turn/>")
        cells = emit_tree(note, ctx, profile)
        ornament_cells = [c for c in cells if c.role == "music_ornament"]
        assert len(ornament_cells) == 2
        # trill = (2,3,5), turn = (2,5,6)
        assert _dots(ornament_cells) == [(2, 3, 5), (2, 5, 6)]

    def test_unknown_ornament_warns(self, profile, ctx):
        note = _note_with_ornaments("<schleifer/>")
        cells = emit_tree(note, ctx, profile)
        assert "music_ornament" not in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_accidental_mark_silently_skipped(self, profile, ctx):
        # <accidental-mark/> modifies a sibling trill/turn — M6
        # doesn't merge them but stays silent (real scores have it
        # frequently and we don't want noise).
        note = _note_with_ornaments(
            "<trill-mark/><accidental-mark>sharp</accidental-mark>"
        )
        cells = emit_tree(note, ctx, profile)
        # Trill still emits.
        ornament_cells = [c for c in cells if c.role == "music_ornament"]
        assert len(ornament_cells) == 1
        # No warning for the accidental-mark.
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" not in codes


# ---------------------------------------------------------------------------
# show_ornaments feature gate (covers tremolo + ornaments together)
# ---------------------------------------------------------------------------


class TestFeatureGate:
    def test_show_ornaments_false_suppresses_trill(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_ornaments",
            False,
        )
        note = _note_with_ornaments("<trill-mark/>")
        cells = emit_tree(note, ctx, profile)
        assert "music_ornament" not in _roles(cells)

    def test_show_ornaments_false_suppresses_tremolo(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_ornaments",
            False,
        )
        note = _note_with_ornaments('<tremolo type="single">2</tremolo>')
        cells = emit_tree(note, ctx, profile)
        assert "music_tremolo" not in _roles(cells)


# ---------------------------------------------------------------------------
# Note with combined ornament + tie + fingering — confirms emit order
# ---------------------------------------------------------------------------


class TestCombinedNote:
    def test_note_with_trill_tie_and_fingering(self, profile, ctx):
        note = ET.fromstring(
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<notations>"
            '<tied type="start"/>'
            "<ornaments><trill-mark/></ornaments>"
            "<technical><fingering>1</fingering></technical>"
            "</notations>"
            "</note>"
        )
        cells = emit_tree(note, ctx, profile)
        roles = _roles(cells)
        # Order in _emit_notations_post_note: tie → slur → ornaments → fingering.
        # Octave + note + (no dot) + tie + trill + fingering.
        assert roles == [
            "music_octave", "music_note",
            "music_tie", "music_tie",
            "music_ornament",
            "music_fingering",
        ]


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


SCORE_WITH_ORNAMENTS_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Piano</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    # C4 quarter with a trill
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type>"
    "<notations><ornaments><trill-mark/></ornaments></notations></note>"
    # D4 quarter with mordent
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type>"
    "<notations><ornaments><mordent/></ornaments></notations></note>"
    # E4 quarter with tremolo (single, 3 strokes = 32nd repetition)
    "<note><pitch><step>E</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type>"
    "<notations><ornaments>"
    '<tremolo type="single">3</tremolo>'
    "</ornaments></notations></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_score_with_ornaments(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_ORNAMENTS_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]

        # Note 1 (C4): octave + C + trill (1 cell)
        # Note 2 (D4, 2° away → no octave): D + upper_mordent (2 cells)
        # Note 3 (E4, 2° away → no octave): E + tremolo_repetition_32nds (2 cells)
        assert roles == [
            "music_octave", "music_note", "music_ornament",
            "music_note", "music_ornament", "music_ornament",
            "music_note", "music_tremolo", "music_tremolo",
        ]
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
