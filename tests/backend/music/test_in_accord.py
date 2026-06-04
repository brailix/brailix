"""Unit tests for M4: full-measure in-accord (BANA Par. 11.1).

Verifies that ``_emit_measure`` switches to multi-voice mode when
notes carry distinct ``<voice>`` tags, emits the in-accord marker
between voices, resets ``prev_pitch`` per voice (so each voice's
first note re-marks octave), and shares ``measure_accidentals``
across voices in the same bar (Par. 6.2).
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


def _roles(cells):
    return [c.role for c in cells]


def _dots(cells):
    return [c.dots for c in cells]


def _note(step: str, octave: int, voice: str | None = None,
          accidental: str | None = None) -> str:
    """Build one <note> XML fragment."""
    inner = (
        f"<pitch><step>{step}</step>"
        + ("<alter>1</alter>" if accidental == "sharp" else "")
        + ("<alter>-1</alter>" if accidental == "flat" else "")
        + f"<octave>{octave}</octave></pitch>"
        "<duration>1</duration><type>quarter</type>"
    )
    if voice is not None:
        inner += f"<voice>{voice}</voice>"
    if accidental:
        inner += f"<accidental>{accidental}</accidental>"
    return f"<note>{inner}</note>"


def _measure(notes_xml: list[str]) -> ET.Element:
    return ET.fromstring(
        '<measure number="1">' + "".join(notes_xml) + "</measure>"
    )


# ---------------------------------------------------------------------------
# Single-voice fast path (no <voice> elements)
# ---------------------------------------------------------------------------


class TestSingleVoice:
    def test_no_voice_tags_takes_fast_path(self, profile, ctx):
        m = _measure([
            _note("C", 4),
            _note("D", 4),
        ])
        cells = emit_tree(m, ctx, profile)
        # Single voice → no in-accord marker
        assert "music_in_accord" not in _roles(cells)

    def test_all_same_voice_takes_fast_path(self, profile, ctx):
        m = _measure([
            _note("C", 4, voice="1"),
            _note("E", 4, voice="1"),
        ])
        cells = emit_tree(m, ctx, profile)
        assert "music_in_accord" not in _roles(cells)


# ---------------------------------------------------------------------------
# Multi-voice in-accord
# ---------------------------------------------------------------------------


class TestMultiVoice:
    def test_two_voices_separated_by_marker(self, profile, ctx):
        # Voice 1: C4; Voice 2: E4. In real MusicXML there'd be a
        # <backup> between them; M4 ignores cursor controls and
        # groups by voice tag.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + "<backup><duration>1</duration></backup>"
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        roles = _roles(cells)
        # Expected: [octave, C, in-accord, octave, E]
        # full_measure_in_accord = "<>" = (1,2,6)(3,4,5)
        assert roles == [
            "music_octave", "music_note",
            "music_in_accord", "music_in_accord",
            "music_octave", "music_note",
        ]

    def test_voice_marker_cells(self, profile, ctx):
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        in_accord_cells = [c for c in cells if c.role == "music_in_accord"]
        # full_measure_in_accord = "<>" = (1,2,6)(3,4,5)
        assert [c.dots for c in in_accord_cells] == [(1, 2, 6), (3, 4, 5)]

    def test_three_voices_get_two_markers(self, profile, ctx):
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + _note("G", 4, voice="3")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        # 3 voices => 2 separator marker pairs
        assert _roles(cells).count("music_in_accord") == 4  # 2 markers × 2 cells

    def test_voice_order_follows_first_appearance(self, profile, ctx):
        # Voice 2 appears before voice 1 in the XML stream — voice
        # ordering follows source order, not voice number.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="2")
            + _note("G", 4, voice="1")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        # First emitted note should be C (voice 2 emitted first)
        # quarter C = "?" = (1,4,5,6); quarter G = "\\" = (1,2,5,6)
        note_cells = [c for c in cells if c.role == "music_note"]
        assert note_cells[0].dots == (1, 4, 5, 6)
        assert note_cells[1].dots == (1, 2, 5, 6)


# ---------------------------------------------------------------------------
# State across voices
# ---------------------------------------------------------------------------


class TestStateAcrossVoices:
    def test_prev_pitch_resets_each_voice(self, profile, ctx):
        # Voice 1: C4 D4 (D4 omits octave, within 2°).
        # Voice 2: E4 — even though E4 is within 3° of voice-1's
        # last D4, the voice switch resets prev_pitch so E4 still
        # marks octave.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("D", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        roles = _roles(cells)
        # [octave, C, D (no octave), in-accord(2), octave, E]
        assert roles == [
            "music_octave", "music_note", "music_note",
            "music_in_accord", "music_in_accord",
            "music_octave", "music_note",
        ]

    def test_measure_accidentals_shared_across_voices(self, profile, ctx):
        # Voice 1 sharps C4. Voice 2 also has a sharp C4 — under
        # Par. 6.2 (accidental_persist_in_measure default true),
        # the second C# inherits the first, so no second accidental
        # cell. measure_accidentals is shared across voices.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1", accidental="sharp")
            + _note("C", 4, voice="2", accidental="sharp")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        accidental_cells = [c for c in cells if c.role == "music_accidental"]
        # Only the first voice's sharp prints.
        assert len(accidental_cells) == 1


# ---------------------------------------------------------------------------
# Feature gates
# ---------------------------------------------------------------------------


class TestFeatureGates:
    def test_marker_disabled_concatenates_voices(
        self, profile, ctx, monkeypatch,
    ):
        # Lossy debug mode: in_accord_marker=false drops the marker.
        # Voices still emit but the reader has no way to tell where
        # the boundary is.
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "in_accord_marker",
            False,
        )
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        assert "music_in_accord" not in _roles(cells)
        # Both notes still present.
        assert _roles(cells).count("music_note") == 2

    def test_part_measure_form_warns_falls_back_to_full(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "in_accord_form",
            "part_measure",
        )
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        # Fallback: full-measure in-accord output still produced.
        assert "music_in_accord" in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Globals (attributes / barline) shared across voices
# ---------------------------------------------------------------------------


class TestGlobals:
    def test_attributes_emitted_once_before_voices(self, profile, ctx):
        # <attributes> applies to the whole measure regardless of voice.
        m = ET.fromstring(
            '<measure number="1">'
            "<attributes>"
            "<key><fifths>2</fifths></key>"   # D major
            "</attributes>"
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        # Key signature cells appear once at the top.
        key_cells = [c for c in cells if c.role == "music_key_signature"]
        assert len(key_cells) == 2  # 2 sharps

    def test_barline_at_end_emits_once(self, profile, ctx):
        # Right barline goes after both voices.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + '<barline location="right"><bar-style>light-heavy</bar-style></barline>'
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        bar_cells = [c for c in cells if c.role == "music_bar_line"]
        # final_double_bar = 2 cells, one occurrence
        assert len(bar_cells) == 2

    def test_right_barline_emits_after_voices(self, profile, ctx):
        # Regression for the M4 globals-split bug: a trailing
        # ``<barline location="right">`` was being emitted *before*
        # the voices because the original implementation lumped all
        # non-note children into one "globals" list. The bar must
        # appear after the final voice's notes.
        m = ET.fromstring(
            '<measure number="1">'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + '<barline location="right"><bar-style>light-heavy</bar-style></barline>'
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        roles = _roles(cells)
        # Bar cells must come *after* the voice-2 note (which is the
        # last music_note in the stream).
        last_note_idx = max(i for i, r in enumerate(roles) if r == "music_note")
        first_bar_idx = next(i for i, r in enumerate(roles) if r == "music_bar_line")
        assert first_bar_idx > last_note_idx

    def test_left_barline_emits_before_voices(self, profile, ctx):
        # The mirror case: a ``<barline location="left">`` (opening
        # repeat) must precede the voice content.
        m = ET.fromstring(
            '<measure number="1">'
            + '<barline location="left">'
            + '<bar-style>heavy-light</bar-style>'
            + '<repeat direction="forward"/>'
            + '</barline>'
            + _note("C", 4, voice="1")
            + _note("E", 4, voice="2")
            + "</measure>"
        )
        cells = emit_tree(m, ctx, profile)
        roles = _roles(cells)
        # repeat cells come before any music_note.
        first_note_idx = next(i for i, r in enumerate(roles) if r == "music_note")
        last_repeat_idx = max(
            (i for i, r in enumerate(roles) if r == "music_repeat"),
            default=-1,
        )
        assert last_repeat_idx >= 0
        assert last_repeat_idx < first_note_idx


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


SCORE_WITH_TWO_VOICES_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Piano</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<attributes><key><fifths>0</fifths></key></attributes>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type><voice>1</voice></note>"
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type><voice>1</voice></note>"
    "<backup><duration>8</duration></backup>"
    "<note><pitch><step>E</step><octave>3</octave></pitch>"
    "<duration>4</duration><type>quarter</type><voice>2</voice></note>"
    "<note><pitch><step>G</step><octave>3</octave></pitch>"
    "<duration>4</duration><type>quarter</type><voice>2</voice></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_two_voice_piano_measure(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_TWO_VOICES_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]

        # Voice 1 (C4 → D4 within 2°, no octave re-mark on D):
        #   [octave, C, D]
        # in-accord marker (2 cells)
        # Voice 2 (E3 → G3 within 3°, no octave re-mark on G):
        #   [octave, E, G]
        assert roles == [
            "music_octave", "music_note", "music_note",
            "music_in_accord", "music_in_accord",
            "music_octave", "music_note", "music_note",
        ]
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
