"""Per-part / per-staff reading-state reset (``_emit_part``).

Reading state — octave inference, value-sign baseline, hairpin, AND the
Par. 9.2 clef — must restart at every part boundary, and at every staff
boundary of a multi-staff part, so one part's / staff's context never
leaks into the next. Both paths funnel through
``_reset_part_reading_state``; the two clef-reset cases below are
regression guards for that leak (a leaked clef silently reverses chord
written-note direction). The single-staff cross-part case had no coverage
before — the multi-staff branch reset the clef but the single-staff branch
didn't.
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


def _quarter_note_dots(profile, step: str, octave: int):
    """Note-cell dots of a lone quarter note — used to identify which pitch
    a chord picked as its written (first-emitted) note. The note cell
    encodes pitch-letter + value only (octave is a separate cell), so C and
    E differ here regardless of register."""
    m = ET.fromstring(
        '<score-partwise><part id="P1"><measure number="1">'
        f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        "<duration>1</duration><type>quarter</type></note>"
        "</measure></part></score-partwise>"
    )
    cells = emit_tree(m, BackendContext(profile="cn_current", block_type="score"), profile)
    return next(c.dots for c in cells if c.role == "music_note")


class TestClefResetsAtStaffBoundary:
    def test_unnumbered_clef_does_not_leak_into_staff_two(self, profile, ctx):
        # An unnumbered <clef> belongs to staff 1, so staff 2 declares no
        # clef of its own. The G (treble) clef must NOT leak into staff 2:
        # leaked, staff 2's chord reads treble (uppermost = written, so E3);
        # reset at the staff boundary, staff 2 falls back to the clef-less
        # default (source order, first note written, so C3). The chord is
        # authored low→high (C3 root, E3 chord) precisely so the two
        # behaviours pick different written notes.
        score = ET.fromstring(
            '<score-partwise version="4.0"><part id="P1">'
            '<measure number="1">'
            "<attributes><divisions>1</divisions>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
            # staff 1: a single treble note
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type><staff>1</staff></note>"
            # staff 2: a chord, source order low (C3) then high (E3)
            "<note><pitch><step>C</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type><staff>2</staff></note>"
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type><staff>2</staff></note>"
            "</measure></part></score-partwise>"
        )
        cells = emit_tree(score, ctx, profile)
        roles = _roles(cells)
        # Staff 1 and staff 2 streams are separated by a part-sep.
        sep_idx = roles.index("music_part_sep")
        staff2_notes = [c for c in cells[sep_idx:] if c.role == "music_note"]
        assert staff2_notes, "staff 2 should emit notes after the separator"
        # The written (first-emitted) note of staff 2's chord must be C3
        # (clef-less default), not E3 (which a leaked treble clef picks).
        assert staff2_notes[0].dots == _quarter_note_dots(profile, "C", 3)
        assert staff2_notes[0].dots != _quarter_note_dots(profile, "E", 3)


class TestClefResetsAtPartBoundary:
    def test_clef_does_not_leak_into_next_single_staff_part(self, profile, ctx):
        # Two single-staff parts. P1 declares a G (treble) clef; P2 declares
        # no clef of its own. The clef must NOT leak across the part
        # boundary: leaked, P2's chord reads treble (uppermost = written, so
        # E3); reset at the part boundary, P2 falls back to the clef-less
        # default (source order, first note written, so C3). The chord is
        # authored low→high (C3 root, E3 chord) so the two behaviours pick
        # different written notes. Before the fix the single-staff branch of
        # _emit_part didn't reset the clef (only the multi-staff branch did),
        # so P2 inherited P1's treble clef and read its chord upside down.
        score = ET.fromstring(
            '<score-partwise version="4.0">'
            '<part id="P1"><measure number="1">'
            "<attributes><divisions>1</divisions>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part>"
            '<part id="P2"><measure number="1">'
            "<attributes><divisions>1</divisions></attributes>"
            # chord authored low (C3 root) then high (E3)
            "<note><pitch><step>C</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        cells = emit_tree(score, ctx, profile)
        roles = _roles(cells)
        # Single-staff parts → exactly one part-sep, between P1 and P2.
        sep_idx = roles.index("music_part_sep")
        part2_notes = [c for c in cells[sep_idx:] if c.role == "music_note"]
        assert part2_notes, "part 2 should emit notes after the separator"
        # P2's chord must pick C3 (clef-less default) as its written note,
        # not E3 (which a leaked treble clef would pick).
        assert part2_notes[0].dots == _quarter_note_dots(profile, "C", 3)
        assert part2_notes[0].dots != _quarter_note_dots(profile, "E", 3)


class TestChordMemberNotationWarning:
    def test_slur_on_clef_demoted_member_warns_not_silent(self, profile, ctx):
        # In treble clef the uppermost note is the written one; the lower
        # member (here the source root C4) is demoted to an interval, which
        # emits only a size cell. A slur authored on that demoted member is
        # dropped — it must surface a MUSIC_UNSUPPORTED_NOTATION warning
        # rather than vanish silently.
        score = ET.fromstring(
            '<score-partwise version="4.0"><part id="P1">'
            '<measure number="1">'
            "<attributes><divisions>1</divisions>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
            # source order low (C4, carries the slur) then high (E4)
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<notations><slur number=\"1\" type=\"start\"/></notations></note>"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        emit_tree(score, ctx, profile)
        assert "MUSIC_UNSUPPORTED_NOTATION" in [w.code for w in ctx.warnings]
