"""Tests for S6: chord intervals (BANA Par. 9.1 / Table 9).

When a ``<note>`` has a ``<chord/>`` child, MusicXML is telling us
this note sounds simultaneously with the previous ``<note>`` (the
chord root). BANA represents chord notes as **interval cells**
relative to the root (2nd / 3rd / ... / octave), not as full note
cells. S6 replaces the chord notes' octave + note + dot emission
with an interval cell from Table 9.
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


def _chord_measure(*notes_xml: str) -> ET.Element:
    return ET.fromstring(
        '<measure number="1">' + "".join(notes_xml) + "</measure>"
    )


def _note(step: str, octave: int, *, chord: bool = False, dur: int = 4,
          type_name: str = "quarter") -> str:
    chord_tag = "<chord/>" if chord else ""
    return (
        f"<note>{chord_tag}"
        f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        f"<duration>{dur}</duration><type>{type_name}</type></note>"
    )


# ---------------------------------------------------------------------------
# Basic intervals (≤ 7°)
# ---------------------------------------------------------------------------


class TestChordIntervals:
    def test_c_major_triad_emits_third_fifth(self, profile, ctx):
        # C4 + E4 (3°) + G4 (5°)
        m = _chord_measure(
            _note("C", 4),
            _note("E", 4, chord=True),
            _note("G", 4, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        roles = _roles(cells)
        # Root: octave + note
        # Chord notes: each emits an interval cell only.
        assert roles == [
            "music_octave", "music_note",          # C4 root
            "music_interval",                       # E4 = 3rd
            "music_interval",                       # G4 = 5th
        ]
        # interval entities: third = (3,4,6); fifth = (3,5)
        interval_cells = [c for c in cells if c.role == "music_interval"]
        assert _dots(interval_cells) == [(3, 4, 6), (3, 5)]

    def test_second_third_fourth_fifth_sixth_seventh(self, profile, ctx):
        # C4 + D4 (2°) + E4 (3°) + F4 (4°) + G4 (5°) + A4 (6°) + B4 (7°)
        m = _chord_measure(
            _note("C", 4),
            _note("D", 4, chord=True),
            _note("E", 4, chord=True),
            _note("F", 4, chord=True),
            _note("G", 4, chord=True),
            _note("A", 4, chord=True),
            _note("B", 4, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        expected = [
            (3, 4),       # 2nd "/"
            (3, 4, 6),    # 3rd "+"
            (3, 4, 5, 6), # 4th "#"
            (3, 5),       # 5th "9"
            (3, 5, 6),    # 6th "0"
            (2, 5),       # 7th "3"
        ]
        assert _dots(intervals) == expected

    def test_octave_above_root(self, profile, ctx):
        # C4 + C5 (octave above)
        m = _chord_measure(
            _note("C", 4),
            _note("C", 5, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        # octave entity = "-" = (3,6)
        assert _dots(intervals) == [(3, 6)]


# ---------------------------------------------------------------------------
# Compound intervals (≥ 8°)
# ---------------------------------------------------------------------------


class TestCompoundIntervals:
    def test_ninth(self, profile, ctx):
        # C4 + D5 (9° = octave + 2°)
        m = _chord_measure(
            _note("C", 4),
            _note("D", 5, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        # octave + second
        assert _dots(intervals) == [(3, 6), (3, 4)]

    def test_two_octaves(self, profile, ctx):
        # C4 + C6 (15° = two octaves)
        m = _chord_measure(
            _note("C", 4),
            _note("C", 6, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        assert _dots(intervals) == [(3, 6), (3, 6)]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_chord_without_root_warns(self, profile, ctx):
        # A bare <chord/> note without a preceding root — unusual but
        # tolerated with a warning + fallback unknown cell.
        m = _chord_measure(
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>"
        )
        emit_tree(m, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_doubled_pitch_warns_emits_second(self, profile, ctx):
        # C4 + C4 (unison / doubled) — BANA has no unison cell; we
        # warn + emit a second cell as placeholder.
        m = _chord_measure(
            _note("C", 4),
            _note("C", 4, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        # 2nd "/" = (3,4)
        assert _dots(intervals) == [(3, 4)]
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_consecutive_chords_isolate_roots(self, profile, ctx):
        # Chord 1: C E G   Chord 2: D F A
        # Second chord's root (D) should reset chord_root, so F is 3°
        # from D, not from C.
        m = _chord_measure(
            _note("C", 4),
            _note("E", 4, chord=True),
            _note("G", 4, chord=True),
            _note("D", 4),
            _note("F", 4, chord=True),
            _note("A", 4, chord=True),
        )
        cells = emit_tree(m, ctx, profile)
        intervals = [c for c in cells if c.role == "music_interval"]
        # 3rd, 5th, 3rd, 5th
        assert _dots(intervals) == [
            (3, 4, 6),    # E from C = 3rd
            (3, 5),       # G from C = 5th
            (3, 4, 6),    # F from D = 3rd
            (3, 5),       # A from D = 5th
        ]


# ---------------------------------------------------------------------------
# Chord notations / lyrics still suppressed on chord notes (S3 still
# applies)
# ---------------------------------------------------------------------------


class TestChordNotationsStillSuppressed:
    def test_chord_note_tie_not_emitted(self, profile, ctx):
        m = _chord_measure(
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "</note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "</note>",
        )
        cells = emit_tree(m, ctx, profile)
        # Only one tie pair (root's), chord note's tied is silently
        # ignored — S6 returns before reaching _emit_notations_post_note.
        assert _roles(cells).count("music_tie") == 2
