"""Unit tests for M3.5: tuplet marker + tie + slur + fingering.

Covers:

* BANA Par. 8.4 / 8.5 (Table 8): tuplet marker emitted before the
  first note of an N-tuplet; triplet form selectable via
  ``music.tuplet_form``.
* BANA Table 10: tie between consecutive notes (start side only).
* BANA Table 13: simple slur start.
* BANA Table 15: fingering 1–5; gated by ``music.show_fingering``.

All cells route through the §6.4 template (feature lookup → resource
lookup → ``emit_cells_for_entity``).
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


def _note_xml(
    step: str = "C",
    octave: int = 4,
    type_name: str = "quarter",
    notations: str = "",
    time_modification: str = "",
    extra: str = "",
) -> str:
    """Helper: build a <note> with optional notations + time-mod."""
    return (
        "<note>"
        f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        "<duration>1</duration>"
        f"<type>{type_name}</type>"
        f"{extra}"
        f"{time_modification}"
        f"{notations}"
        "</note>"
    )


# ---------------------------------------------------------------------------
# Tuplet marker (Par. 8.4 / 8.5)
# ---------------------------------------------------------------------------


class TestTupletMarker:
    def test_triplet_single_cell_default(self, profile, ctx):
        # default tuplet_form="single_cell" → triplet uses "2" = (2,3)
        note = ET.fromstring(_note_xml(
            type_name="eighth",
            time_modification=(
                "<time-modification>"
                "<actual-notes>3</actual-notes>"
                "<normal-notes>2</normal-notes>"
                "</time-modification>"
            ),
            notations='<notations><tuplet type="start" number="1"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # [tuplet, octave, note]
        assert cells[0].dots == (2, 3)
        assert cells[0].role == "music_tuplet"

    def test_triplet_three_cell_via_feature(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "tuplet_form",
            "three_cell",
        )
        note = ET.fromstring(_note_xml(
            type_name="eighth",
            time_modification=(
                "<time-modification><actual-notes>3</actual-notes>"
                "<normal-notes>2</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # triplet_three_cell = "_3'" = (4,5,6)(2,5)(3,)
        assert _dots(cells)[:3] == [(4, 5, 6), (2, 5), (3,)]
        assert all(c.role == "music_tuplet" for c in cells[:3])

    def test_group_of_two_notes(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            time_modification=(
                "<time-modification><actual-notes>2</actual-notes>"
                "<normal-notes>3</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # group_of_two_notes = "_2'" = (4,5,6)(2,3)(3,)
        assert _dots(cells)[:3] == [(4, 5, 6), (2, 3), (3,)]

    def test_group_of_ten_notes(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            time_modification=(
                "<time-modification><actual-notes>10</actual-notes>"
                "<normal-notes>8</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # group_of_ten_notes = "_10'" → 4 cells
        assert cells[0].role == "music_tuplet"

    def test_five_tuplet_synthesized(self, profile, ctx):
        # S1: 5-tuplet synthesized as ``_5'`` = (4,5,6)(2,6)(3,)
        note = ET.fromstring(_note_xml(
            time_modification=(
                "<time-modification><actual-notes>5</actual-notes>"
                "<normal-notes>4</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        tuplet_cells = [c for c in cells if c.role == "music_tuplet"]
        assert _dots(tuplet_cells) == [(4, 5, 6), (2, 6), (3,)]

    def test_eleven_tuplet_synthesized(self, profile, ctx):
        # 11-tuplet: ``_11'`` with multi-digit lower-row 11 = (2,)(2,)
        note = ET.fromstring(_note_xml(
            time_modification=(
                "<time-modification><actual-notes>11</actual-notes>"
                "<normal-notes>8</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        tuplet_cells = [c for c in cells if c.role == "music_tuplet"]
        assert _dots(tuplet_cells) == [(4, 5, 6), (2,), (2,), (3,)]

    def test_tuplet_stop_no_marker(self, profile, ctx):
        # type="stop" → no marker (only start side emits).
        note = ET.fromstring(_note_xml(
            notations='<notations><tuplet type="stop"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_tuplet" not in _roles(cells)

    def test_tuplet_without_time_modification_skipped(self, profile, ctx):
        # Malformed: <tuplet start> but no <time-modification>.
        # Don't crash, just skip.
        note = ET.fromstring(_note_xml(
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_tuplet" not in _roles(cells)

    def test_unsupported_form_warns_but_falls_back(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "tuplet_form",
            "weird-form",
        )
        note = ET.fromstring(_note_xml(
            time_modification=(
                "<time-modification><actual-notes>3</actual-notes>"
                "<normal-notes>2</normal-notes></time-modification>"
            ),
            notations='<notations><tuplet type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # Falls back to single_cell
        assert cells[0].dots == (2, 3)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Tie (Table 10)
# ---------------------------------------------------------------------------


class TestTie:
    def test_tie_start_emits_cells_after_note(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations='<notations><tied type="start"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # [octave, note, tie...]
        # tie_between_single_notes = "@c" = (4,)(1,4)
        assert _dots(cells)[-2:] == [(4,), (1, 4)]
        assert cells[-2].role == "music_tie"
        assert cells[-1].role == "music_tie"

    def test_tie_stop_no_cells(self, profile, ctx):
        # Stop side of a tie pair — no cells emitted.
        note = ET.fromstring(_note_xml(
            notations='<notations><tied type="stop"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_tie" not in _roles(cells)

    def test_both_tied_start_and_stop_only_emits_for_start(
        self, profile, ctx,
    ):
        # Middle note of three tied notes — has both start and stop.
        # Backend emits only one cell pair (start side).
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations>"
                '<tied type="stop"/>'
                '<tied type="start"/>'
                "</notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        assert _roles(cells).count("music_tie") == 2  # one start pair


# ---------------------------------------------------------------------------
# Slur (Table 13)
# ---------------------------------------------------------------------------


class TestSlur:
    def test_slur_start_emits_simple_short(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations='<notations><slur type="start" number="1"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        # simple_short_slur = "c" = (1,4)
        assert cells[-1].dots == (1, 4)
        assert cells[-1].role == "music_slur"

    def test_slur_stop_no_cells(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations='<notations><slur type="stop" number="1"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_slur" not in _roles(cells)

    def test_slur_continue_no_cells(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations='<notations><slur type="continue" number="1"/></notations>',
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_slur" not in _roles(cells)


# ---------------------------------------------------------------------------
# Fingering (Table 15)
# ---------------------------------------------------------------------------


class TestFingering:
    @pytest.mark.parametrize(
        "finger, expected_dots",
        [
            ("1", (1,)),          # first_finger = "a"
            ("2", (1, 2)),        # second_finger = "b"
            ("3", (1, 2, 3)),     # third_finger = "l"
            ("4", (2,)),          # fourth_finger = "1"
            ("5", (1, 3)),        # fifth_finger = "k"
        ],
    )
    def test_single_finger(self, profile, ctx, finger, expected_dots):
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                f"<fingering>{finger}</fingering>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        assert cells[-1].dots == expected_dots
        assert cells[-1].role == "music_fingering"

    def test_unknown_finger_warns(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                "<fingering>10</fingering>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_fingering" not in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_feature_gate_disables(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_fingering",
            False,
        )
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                "<fingering>1</fingering>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_fingering" not in _roles(cells)

    def test_multiple_fingerings_on_one_note(self, profile, ctx):
        # Two fingerings on the same note — both emit, in order.
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                "<fingering>1</fingering>"
                "<fingering>3</fingering>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        finger_cells = [c for c in cells if c.role == "music_fingering"]
        assert len(finger_cells) == 2
        assert finger_cells[0].dots == (1,)        # first_finger
        assert finger_cells[1].dots == (1, 2, 3)   # third_finger


# ---------------------------------------------------------------------------
# String techniques (Table 24, M-instr1)
# ---------------------------------------------------------------------------


class TestStringTechniques:
    def test_down_bow(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations="<notations><technical><down-bow/></technical></notations>",
        ))
        cells = emit_tree(note, ctx, profile)
        # down_bow = "<b" = (1,2,6)(1,2)
        assert _dots(cells)[-2:] == [(1, 2, 6), (1, 2)]
        assert cells[-1].role == "music_string_technique"

    def test_up_bow(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations="<notations><technical><up-bow/></technical></notations>",
        ))
        cells = emit_tree(note, ctx, profile)
        # up_bow = "<'" = (1,2,6)(3,)
        assert _dots(cells)[-2:] == [(1, 2, 6), (3,)]
        assert cells[-1].role == "music_string_technique"

    def test_open_string(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations="<notations><technical><open-string/></technical></notations>",
        ))
        cells = emit_tree(note, ctx, profile)
        assert cells[-1].dots == (1, 3)  # open_string = "k"
        assert cells[-1].role == "music_string_technique"

    def test_natural_harmonic_default(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations="<notations><technical><harmonic/></technical></notations>",
        ))
        cells = emit_tree(note, ctx, profile)
        assert cells[-1].dots == (1, 3)  # natural_harmonic = "k"
        assert cells[-1].role == "music_string_technique"

    def test_artificial_harmonic_via_child(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                "<harmonic><artificial/></harmonic>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        # artificial_harmonic = "*l" = (1,6)(1,2,3)
        assert _dots(cells)[-2:] == [(1, 6), (1, 2, 3)]

    def test_thumb_position(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical><thumb-position/>"
                "</technical></notations>"
            ),
        ))
        cells = emit_tree(note, ctx, profile)
        # left_hand_thumb = "*k" = (1,6)(1,3)
        assert _dots(cells)[-2:] == [(1, 6), (1, 3)]

    def test_feature_gate_disables(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_string_techniques",
            False,
        )
        note = ET.fromstring(_note_xml(
            notations="<notations><technical><down-bow/></technical></notations>",
        ))
        cells = emit_tree(note, ctx, profile)
        assert "music_string_technique" not in _roles(cells)

    def test_fingering_emits_before_bow(self, profile, ctx):
        note = ET.fromstring(_note_xml(
            notations=(
                "<notations><technical>"
                "<fingering>1</fingering><down-bow/>"
                "</technical></notations>"
            ),
        ))
        roles = _roles(emit_tree(note, ctx, profile))
        assert "music_fingering" in roles and "music_string_technique" in roles
        assert roles.index("music_fingering") < roles.index(
            "music_string_technique"
        )


# ---------------------------------------------------------------------------
# Combined: full note ordering
# ---------------------------------------------------------------------------


class TestCombined:
    def test_full_ordering_tuplet_accidental_octave_note_dot_tie_slur_finger(
        self, profile, ctx,
    ):
        # Maximum-decoration note: triplet start + sharp + dot + tie
        # start + slur start + fingering.
        note = ET.fromstring(
            "<note>"
            "<pitch><step>C</step><alter>1</alter><octave>4</octave></pitch>"
            "<duration>1</duration><type>eighth</type>"
            "<dot/>"
            "<accidental>sharp</accidental>"
            "<time-modification><actual-notes>3</actual-notes>"
            "<normal-notes>2</normal-notes></time-modification>"
            "<notations>"
            '<tuplet type="start"/>'
            '<tied type="start"/>'
            '<slur type="start" number="1"/>'
            "<technical><fingering>2</fingering></technical>"
            "</notations>"
            "</note>"
        )
        cells = emit_tree(note, ctx, profile)
        roles = _roles(cells)
        # Order per BANA combined rules:
        #   tuplet, accidental, octave, note, dot, tie(2), slur, finger
        assert roles == [
            "music_tuplet",
            "music_accidental",
            "music_octave",
            "music_note",
            "music_dot",
            "music_tie", "music_tie",
            "music_slur",
            "music_fingering",
        ]


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


SCORE_WITH_TUPLET_TIE_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    # Triplet of three 8th notes — first note has the marker.
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>eighth</type>"
    "<time-modification><actual-notes>3</actual-notes>"
    "<normal-notes>2</normal-notes></time-modification>"
    '<notations><tuplet type="start"/></notations></note>'
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>eighth</type>"
    "<time-modification><actual-notes>3</actual-notes>"
    "<normal-notes>2</normal-notes></time-modification></note>"
    "<note><pitch><step>E</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>eighth</type>"
    "<time-modification><actual-notes>3</actual-notes>"
    "<normal-notes>2</normal-notes></time-modification>"
    '<notations><tuplet type="stop"/></notations></note>'
    # Tied G4 to G4 with fingering 1 on first note.
    "<note><pitch><step>G</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    "<notations>"
    '<tied type="start"/>'
    "<technical><fingering>1</fingering></technical>"
    "</notations></note>"
    "<note><pitch><step>G</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    '<notations><tied type="stop"/></notations></note>'
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_full_score_with_tuplet_tie_fingering(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_TUPLET_TIE_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]

        # Expected:
        # Note 1: tuplet + octave + C
        # Note 2: D (within 3°, no octave)
        # Note 3: E
        # Note 4: octave (G4 is 4° from E4 crossing — wait, same octave so no?
        #         E4 -> G4 = 3° (E F G = 3 positions). 3° ≤ 3 → omit.
        # Actually E to G is 3rd interval (E F G = 3 notes including endpoints).
        # interval = abs(4 - 2) + 1 = 3 → omit.
        # So G note: just [G, tie..., fingering]
        # Note 5: G (same as prev, 1° → omit), no notations on start
        assert roles == [
            "music_tuplet",
            "music_octave", "music_note",
            "music_note",
            "music_note",
            "music_note", "music_tie", "music_tie", "music_fingering",
            "music_note",
        ]
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
