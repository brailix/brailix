"""Unit tests for M3.2: note modifications.

Covers ``<dot/>`` (BANA Par. 2.3 / 5.4), ``<accidental>``
(BANA Par. 6.1), and the Par. 6.2 within-measure persistence rule
(``music.accidental_persist_in_measure``).

Per §6.4 template:
* dot emission funnels through the BANA ``notes.dot_added_value``
  entity — no inline ``BrailleCell(dots=(3,))``.
* accidental emission funnels through ``accidentals_key.*`` entries
  via :func:`accidental_entity_name` (no hard-coded MusicXML→cell
  mapping inside the handler).
* Both features go through ``profile.feature("music.<name>", default)``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.backend.music.utils import accidental_entity_name
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


# ---------------------------------------------------------------------------
# Dots on notes and rests
# ---------------------------------------------------------------------------


class TestDot:
    def test_single_dot_on_quarter_note(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>3</duration><type>quarter</type><dot/></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # [fourth octave, quarter C, dot]
        # dot_added_value = "'" = (3,)
        assert _dots(cells) == [(5,), (1, 4, 5, 6), (3,)]
        assert _roles(cells) == ["music_octave", "music_note", "music_dot"]

    def test_double_dot_on_half_note(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>G</step><octave>4</octave></pitch>"
            "<duration>7</duration><type>half</type><dot/><dot/></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # [fourth octave, half G, dot, dot]
        # half G = "R" = (1,2,3,5)
        assert _dots(cells) == [(5,), (1, 2, 3, 5), (3,), (3,)]
        assert _roles(cells) == [
            "music_octave", "music_note", "music_dot", "music_dot",
        ]

    def test_dot_on_rest(self, profile, ctx):
        # Dotted half rest — BANA Par. 5.4
        rest = ET.fromstring(
            "<note><rest/><duration>3</duration><type>quarter</type><dot/></note>"
        )
        cells = emit_tree(rest, ctx, profile)
        # quarter rest = "v" = (1,2,3,6); + dot
        assert _dots(cells) == [(1, 2, 3, 6), (3,)]
        assert _roles(cells) == ["music_rest", "music_dot"]

    def test_no_dot_no_dot_cell(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Just octave + note.
        assert "music_dot" not in _roles(cells)

    def test_unsupported_dot_form_warns_but_falls_back(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "dot_form",
            "combined",
        )
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>3</duration><type>quarter</type><dot/></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Falls back to separate form — dot cell still present.
        assert _dots(cells)[-1] == (3,)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Accidentals
# ---------------------------------------------------------------------------


class TestAccidentalMapping:
    """The mapping helper alone — caller-side concerns (gating /
    measure-persist) are exercised below."""

    @pytest.mark.parametrize(
        "musicxml_value, expected",
        [
            ("sharp",                "sharp"),
            ("flat",                 "flat"),
            ("natural",              "natural"),
            ("double-sharp",         "double_sharp"),
            ("sharp-sharp",          "double_sharp"),
            ("double-flat",          "double_flat"),
            ("flat-flat",            "double_flat"),
            ("quarter-sharp",        "quarter_step_sharp"),
            ("quarter-flat",         "quarter_step_flat"),
            ("three-quarters-sharp", "three_quarter_step_sharp"),
            ("three-quarters-flat",  "three_quarter_step_flat"),
            ("SHARP",                "sharp"),       # case-insensitive
            ("  natural  ",          "natural"),     # strip-friendly
        ],
    )
    def test_known_values(self, musicxml_value, expected):
        assert accidental_entity_name(musicxml_value) == expected

    def test_unknown_value(self):
        assert accidental_entity_name("sharp-up") is None  # MusicXML 4.0 micro
        assert accidental_entity_name("") is None


class TestAccidentalEmission:
    def test_sharp_before_note(self, profile, ctx):
        # Sharp precedes octave + note per Par. 6.1 + 3.2.
        note = ET.fromstring(
            "<note><pitch><step>F</step><alter>1</alter><octave>4</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>sharp</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # [sharp, fourth octave, quarter F]
        # sharp = "%" = (1,4,6); fourth octave = (5,); quarter F = "]" = (1,2,4,5,6)
        assert _dots(cells) == [(1, 4, 6), (5,), (1, 2, 4, 5, 6)]
        assert _roles(cells) == [
            "music_accidental", "music_octave", "music_note",
        ]

    def test_flat_before_note(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>B</step><alter>-1</alter><octave>4</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>flat</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # [flat, fourth octave, quarter B]
        # flat = "<" = (1,2,6); quarter B = "W" = (2,4,5,6)
        assert _dots(cells) == [(1, 2, 6), (5,), (2, 4, 5, 6)]

    def test_natural_before_note(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>F</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<accidental>natural</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # natural = "*" = (1,6)
        assert _dots(cells)[0] == (1, 6)
        assert _roles(cells)[0] == "music_accidental"

    def test_double_sharp_emits_two_cells(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><alter>2</alter><octave>4</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>double-sharp</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # double_sharp = "%%" = (1,4,6) twice
        assert _dots(cells)[:2] == [(1, 4, 6), (1, 4, 6)]
        assert _roles(cells)[:2] == [
            "music_accidental", "music_accidental",
        ]

    def test_accidental_with_dot(self, profile, ctx):
        # Sharp + dotted-quarter — exercises both ends of the
        # ordering [accidental][octave][note][dot].
        note = ET.fromstring(
            "<note><pitch><step>C</step><alter>1</alter><octave>4</octave>"
            "</pitch><duration>3</duration><type>quarter</type>"
            "<dot/><accidental>sharp</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        assert _roles(cells) == [
            "music_accidental", "music_octave", "music_note", "music_dot",
        ]

    def test_unknown_accidental_warns(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<accidental>sharp-up</accidental></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Note still rendered; accidental skipped + warning.
        roles = _roles(cells)
        assert "music_accidental" not in roles
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_missing_accidental_no_cell(self, profile, ctx):
        # No <accidental> child — nothing emitted, no warning.
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        assert "music_accidental" not in _roles(cells)
        assert ctx.warnings.warnings == []


# ---------------------------------------------------------------------------
# BANA Par. 6.2 within-measure persistence
# ---------------------------------------------------------------------------


def _make_measure(notes_xml: list[str]) -> ET.Element:
    """Wrap a list of note-element strings into one measure."""
    parts = ['<score-partwise><part id="P1"><measure number="1">']
    parts.extend(notes_xml)
    parts.append("</measure></part></score-partwise>")
    return ET.fromstring("".join(parts))


def _make_two_measures(notes_m1: list[str], notes_m2: list[str]) -> ET.Element:
    parts = ['<score-partwise><part id="P1">']
    parts.append('<measure number="1">')
    parts.extend(notes_m1)
    parts.append("</measure>")
    parts.append('<measure number="2">')
    parts.extend(notes_m2)
    parts.append("</measure>")
    parts.append("</part></score-partwise>")
    return ET.fromstring("".join(parts))


_SHARP_C4 = (
    "<note><pitch><step>C</step><alter>1</alter><octave>4</octave>"
    "</pitch><duration>1</duration><type>quarter</type>"
    "<accidental>sharp</accidental></note>"
)
_C4 = (
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
)


class TestAccidentalPersistInMeasure:
    def test_duplicate_in_same_measure_suppressed_by_default(
        self, profile, ctx,
    ):
        # Two C#4 in a row — the second's accidental cell is dropped
        # under default ``accidental_persist_in_measure=true``.
        tree = _make_measure([_SHARP_C4, _SHARP_C4])
        cells = emit_tree(tree, ctx, profile)
        roles = _roles(cells)
        # First note: [accidental, octave, note]
        # Second note: [note] (octave skipped by interval rule; accidental
        # suppressed by Par. 6.2)
        assert roles.count("music_accidental") == 1

    def test_persist_off_emits_every_time(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "accidental_persist_in_measure",
            False,
        )
        tree = _make_measure([_SHARP_C4, _SHARP_C4])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2

    def test_different_pitch_not_suppressed(self, profile, ctx):
        # Sharp on C4 then sharp on D4 (different pitch) — both emit.
        sharp_d4 = (
            "<note><pitch><step>D</step><alter>1</alter><octave>4</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>sharp</accidental></note>"
        )
        tree = _make_measure([_SHARP_C4, sharp_d4])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2

    def test_different_octave_not_suppressed(self, profile, ctx):
        sharp_c5 = (
            "<note><pitch><step>C</step><alter>1</alter><octave>5</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>sharp</accidental></note>"
        )
        tree = _make_measure([_SHARP_C4, sharp_c5])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2

    def test_different_accidental_kind_not_suppressed(self, profile, ctx):
        # C#4 followed by Cb4 — different accidental types on the same
        # (pitch, octave) — both must print.
        flat_c4 = (
            "<note><pitch><step>C</step><alter>-1</alter><octave>4</octave>"
            "</pitch><duration>1</duration><type>quarter</type>"
            "<accidental>flat</accidental></note>"
        )
        tree = _make_measure([_SHARP_C4, flat_c4])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2

    def test_cross_measure_resets(self, profile, ctx):
        # C#4 in measure 1, C#4 in measure 2 — both print
        # (Par. 6.2 expires at the bar line).
        tree = _make_two_measures([_SHARP_C4], [_SHARP_C4])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2

    def test_natural_after_sharp_in_same_measure(self, profile, ctx):
        # C#4 then C-natural4 — these are different accidental entities
        # so both print (matches BANA convention: a natural sign cancels
        # a prior sharp explicitly).
        natural_c4 = (
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<accidental>natural</accidental></note>"
        )
        tree = _make_measure([_SHARP_C4, natural_c4])
        cells = emit_tree(tree, ctx, profile)
        assert _roles(cells).count("music_accidental") == 2
