"""Unit tests for the M2.3 music backend handlers.

Verifies single-note and single-rest cell emission per BANA Table 2
/ Table 3 / Table 5 — no octave-inference complexity here, that lives
in test_octave_inference.py.
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


# ---------------------------------------------------------------------------
# Single note in isolation — emit_tree wraps the element in a fresh
# MusicBrailleContext (prev_pitch=None), so the first call always
# emits an octave prefix.
# ---------------------------------------------------------------------------


class TestSingleNote:
    def test_quarter_C4(self, profile, ctx):
        # C4 = middle C → BANA fourth octave + quarter C cell.
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # First cell = octave prefix (fourth_octave = ", a.k.a. dot 5)
        # Second cell = quarter C = "?" = (1,4,5,6)
        assert _dots(cells) == [(5,), (1, 4, 5, 6)]
        assert _roles(cells) == ["music_octave", "music_note"]

    def test_whole_D5(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>D</step><octave>5</octave></pitch>"
            "<duration>16</duration><type>whole</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Fifth octave prefix = "." = (4,6)
        # Whole D = "Z" = (1,3,5,6)
        assert _dots(cells) == [(4, 6), (1, 3, 5, 6)]

    def test_eighth_G3(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>G</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>eighth</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Third octave prefix = "_" = (4,5,6); eighth G = "H" = (1,2,5)
        assert _dots(cells) == [(4, 5, 6), (1, 2, 5)]

    def test_default_type_when_missing(self, profile, ctx):
        # No <type> child → backend treats it as quarter (BANA default
        # safe pick); warning is the caller's concern at M3+.
        note = ET.fromstring(
            "<note><pitch><step>F</step><octave>4</octave></pitch>"
            "<duration>1</duration></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # Quarter F = "]" = (1,2,4,5,6)
        assert _dots(cells)[-1] == (1, 2, 4, 5, 6)

    def test_breve_C4(self, profile, ctx):
        # BANA Table 2: breve (double whole note) = whole-form + breve
        # suffix cell. breve_a_C = ["c_13456", "c_13"]. Regression: the
        # type used to fall through to the quarter default silently.
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>32</duration><type>breve</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # [octave fourth = (5,), whole C = (1,3,4,5,6), breve suffix = (1,3)]
        assert _dots(cells) == [(5,), (1, 3, 4, 5, 6), (1, 3)]
        assert _roles(cells) == ["music_octave", "music_note", "music_note"]
        # breve is a known type → no ambiguity warning.
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_DURATION_AMBIGUOUS" not in codes


class TestSingleRest:
    def test_quarter_rest(self, profile, ctx):
        rest = ET.fromstring(
            "<note><rest/><duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(rest, ctx, profile)
        # Quarter rest = "v" = (1,2,3,6)
        assert _dots(cells) == [(1, 2, 3, 6)]
        assert _roles(cells) == ["music_rest"]

    def test_whole_rest(self, profile, ctx):
        rest = ET.fromstring(
            "<note><rest/><duration>16</duration><type>whole</type></note>"
        )
        cells = emit_tree(rest, ctx, profile)
        # Whole rest = "m" = (1,3,4)
        assert _dots(cells) == [(1, 3, 4)]

    def test_eighth_rest(self, profile, ctx):
        rest = ET.fromstring(
            "<note><rest/><duration>1</duration><type>eighth</type></note>"
        )
        cells = emit_tree(rest, ctx, profile)
        # 8th rest = "x" = (1,3,4,6)
        assert _dots(cells) == [(1, 3, 4, 6)]


class TestMalformedNote:
    def test_note_missing_pitch_and_rest_warns(self, profile, ctx):
        note = ET.fromstring("<note><duration>1</duration></note>")
        cells = emit_tree(note, ctx, profile)
        assert len(cells) == 1
        assert cells[0].dots == ()  # unknown cell
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_pitch_missing_step(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes
        # One unknown cell as a marker.
        assert cells == [c for c in cells if c.dots == ()]

    def test_octave_not_integer(self, profile, ctx):
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>middle</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        emit_tree(note, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_unknown_type_warns_not_silent(self, profile, ctx):
        # A bogus <type> degrades to the quarter-note shape, but must
        # warn MUSIC_DURATION_AMBIGUOUS instead of mistranslating
        # silently (regression: any unknown type used to map to quarter
        # with no signal at all).
        note = ET.fromstring(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>fortnight</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_DURATION_AMBIGUOUS" in codes
        # Still emits the fallback quarter C so the score renders.
        # Quarter C = "?" = (1,4,5,6).
        assert _dots(cells)[-1] == (1, 4, 5, 6)


# ---------------------------------------------------------------------------
# music-error fallback from the frontend
# ---------------------------------------------------------------------------


class TestMusicError:
    def test_music_error_emits_unknown_cell_with_warning(self, profile, ctx):
        err = ET.fromstring(
            '<score-partwise><music-error data-reason="bad input">x</music-error>'
            '</score-partwise>'
        )
        cells = emit_tree(err, ctx, profile)
        assert len(cells) == 1
        assert cells[0].dots == ()
        assert cells[0].role == "music_error"
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_PARSE_RECOVERY" in codes


# ---------------------------------------------------------------------------
# Unsupported tags warn but don't crash
# ---------------------------------------------------------------------------


class TestUnsupported:
    def test_unknown_element_warns(self, profile, ctx):
        weird = ET.fromstring("<somefuturetag/>")
        cells = emit_tree(weird, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes
