"""Tests for chord-aware notation handling.

MusicXML represents chords as a series of ``<note>`` elements where
the second and subsequent ones carry a ``<chord/>`` marker.  Per BANA
Pars. 9.1 / 9.2 / 10.2 / 13: chord members render as interval cells
from the written note (which the clef may reorder); a tied chord takes
ONE chord-tie sign (``tie_between_chords``) after the intervals — any
member may carry the source ``<tied>``; lyrics ride with the chord
(every authored ``<lyric>`` in the run lands once, wherever the
reorder put its note); slurs / fingering stay on the written note.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import translate_tree
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


def _chord_block(notes_xml: list[str]) -> ET.Element:
    """Wrap a list of <note> fragments in a parent so translate_tree can
    walk them in order."""
    return ET.fromstring(
        '<part id="P1"><measure number="1">'
        + "".join(notes_xml)
        + "</measure></part>"
    )


# ---------------------------------------------------------------------------
# Chord root vs chord notes
# ---------------------------------------------------------------------------


class TestChordNoteSuppression:
    def test_tied_chord_uses_one_chord_tie_sign(self, profile, ctx):
        # C major triad, every member tied: ONE chord-tie sign for the
        # whole chord (Par. 10.2), using the Table 10 chord entity —
        # not the single-note tie, and not one sign per tied member.
        notes = [
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
            "<note><chord/>"
            "<pitch><step>G</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "</note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        tie_cells = [c for c in cells if c.role == "music_tie"]
        # tie_between_chords = c_46 + c_14 (Table 10).
        assert [c.dots for c in tie_cells] == [(4, 6), (1, 4)]

    def test_every_authored_lyric_in_the_run_lands_once(
        self, profile, ctx,
    ):
        # Lyrics ride with the chord: the written note's own lyric is
        # emitted by its single-note path, the other members' by the
        # chord run — each authored <lyric> lands exactly once (they
        # used to be dropped from non-written members, which after a
        # clef reorder silently lost the syllable).
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            "<lyric><text>la</text></lyric>"
            "</note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            "<lyric><text>la</text></lyric>"
            "</note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        marker_count = _roles(cells).count("music_lyric_marker")
        assert marker_count == 2, (
            "every authored <lyric> in the chord run lands exactly once"
        )

    def test_chord_root_emits_slur_chord_notes_dont(self, profile, ctx):
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><slur type="start" number="1"/></notations>'
            "</note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><slur type="start" number="1"/></notations>'
            "</note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        slur_count = _roles(cells).count("music_slur")
        assert slur_count == 1

    def test_chord_notes_emit_interval_cells(self, profile, ctx):
        # S6 (BANA Par. 9.1): chord notes don't emit full note cells —
        # they're represented as interval markers from the root.
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            "<note><chord/>"
            "<pitch><step>G</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        # Root emits a music_note, chord notes emit music_interval.
        assert _roles(cells).count("music_note") == 1
        assert _roles(cells).count("music_interval") == 2

    def test_chord_notes_dot_suppressed(self, profile, ctx):
        # S6: BANA Par. 9.1 — chord intervals don't carry duration
        # modifiers (the root's dot covers the whole chord). Only the
        # root's <dot/> child emits a dot cell.
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>6</duration><type>quarter</type><dot/></note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>6</duration><type>quarter</type><dot/></note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        dot_count = _roles(cells).count("music_dot")
        assert dot_count == 1  # root only — chord interval has no dot


# ---------------------------------------------------------------------------
# Non-chord notes unaffected
# ---------------------------------------------------------------------------


class TestNonChordUnaffected:
    def test_plain_consecutive_notes_each_emit_tie(self, profile, ctx):
        # Without <chord/>, each note carries its own notations.
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "</note>",
            "<note>"
            "<pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "</note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        tie_count = _roles(cells).count("music_tie")
        assert tie_count == 4  # 2 cells × 2 notes


# ---------------------------------------------------------------------------
# Clef reorder keeps chord-level data (regression)
# ---------------------------------------------------------------------------


class TestChordReorderKeepsTieAndLyric:
    """BANA Par. 9.2 reorders the chord by clef.  The tie and the lyric
    belong to the chord, not to whichever member became the written
    note — regression: under a treble clef, C4(tied, lyric)-E4-G4 lost
    both, because G4 became the written note and the C4 carrying them
    was demoted to an interval whose notations were skipped."""

    def _chord_with_clef(self, sign: str, line: int) -> ET.Element:
        return ET.fromstring(
            '<part id="P1"><measure number="1">'
            "<attributes><clef>"
            f"<sign>{sign}</sign><line>{line}</line>"
            "</clef></attributes>"
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            '<notations><tied type="start"/></notations>'
            "<lyric><text>la</text></lyric>"
            "</note>"
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            "</note>"
            "<note><chord/>"
            "<pitch><step>G</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type>"
            "</note>"
            "</measure></part>"
        )

    def test_treble_reorder_keeps_tie_and_lyric(self, profile, ctx):
        # Treble: written note = uppermost (G4); the source C4 carrying
        # the tie + lyric becomes an interval member.
        tree = self._chord_with_clef("G", 2)
        cells = translate_tree(tree, ctx, profile)
        tie_cells = [c for c in cells if c.role == "music_tie"]
        assert [c.dots for c in tie_cells] == [(4, 6), (1, 4)]
        assert _roles(cells).count("music_lyric_marker") == 1

    def test_bass_written_note_keeps_tie_and_lyric(self, profile, ctx):
        # Bass: written note = lowermost (C4 itself) — the chord-level
        # outcome is identical either way.
        tree = self._chord_with_clef("F", 4)
        cells = translate_tree(tree, ctx, profile)
        tie_cells = [c for c in cells if c.role == "music_tie"]
        assert [c.dots for c in tie_cells] == [(4, 6), (1, 4)]
        assert _roles(cells).count("music_lyric_marker") == 1


# ---------------------------------------------------------------------------
# Unreadable chord root must not reuse a stale root (regression)
# ---------------------------------------------------------------------------


class TestChordRootUnreadablePitch:
    """Regression: when a chord's root ``<note>`` has a missing/malformed
    ``<pitch>``, its interval members used to silently measure against a
    STALE ``chord_root`` left over from an earlier note in the same
    measure — emitting a real-but-wrong interval cell with no warning.
    The root's parse failure now leaves ``chord_root`` None, so members
    take the orphan-warning branch instead of writing wrong braille."""

    def test_unreadable_root_does_not_reuse_prior_note_as_root(
        self, profile, ctx,
    ):
        notes = [
            # A normal melodic note first: sets chord_root=(G,4).
            "<note>"
            "<pitch><step>G</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            # Chord root with a malformed pitch (octave but no <step>):
            # this <note> starts a run because the next sibling carries
            # <chord/>.
            "<note>"
            "<pitch><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        # The member must NOT emit an interval measured from the stale
        # (G,4) root; it orphans instead.
        assert _roles(cells).count("music_interval") == 0
        assert any(
            w.code == "MUSIC_UNSUPPORTED_NOTATION"
            and "without a prior root note" in w.message
            for w in ctx.warnings.warnings
        ), "chord member with an unreadable root must orphan-warn"


# ---------------------------------------------------------------------------
# A <direction> between a chord root and its members must not split the chord
# ---------------------------------------------------------------------------


class TestChordNotSplitByInterposedDirection:
    """The chord interval must directly follow the written note. A
    ``<direction>`` (dynamic / wedge) wedged between the root and its
    ``<chord/>`` member used to detach the interval on the single-voice
    path (the in-accord path already buffered such inserts). Regression
    guard: the interval stays adjacent to the note; the direction's cells
    land *after* the whole chord."""

    def test_dynamic_between_root_and_member_keeps_chord_intact(
        self, profile, ctx,
    ):
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            # A dynamic wedged between the root and the chord member.
            "<direction><direction-type>"
            "<dynamics><f/></dynamics>"
            "</direction-type></direction>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        roles = _roles(cells)
        # Chord kept whole (note + interval), dynamic after it — not split.
        assert roles == [
            "music_octave",
            "music_note",
            "music_interval",
            "music_dynamic",
            "music_dynamic",
        ]
        # The defining regression assertion: interval before any dynamic.
        assert roles.index("music_interval") < roles.index("music_dynamic")
        assert ctx.warnings.warnings == []

    def test_direction_after_complete_chord_unchanged(self, profile, ctx):
        # A direction that follows a *complete* chord stays after it
        # (this path was always correct; pin it so the fix doesn't regress
        # the ordering of a trailing direction).
        notes = [
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            "<note><chord/>"
            "<pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>4</duration><type>quarter</type></note>",
            "<direction><direction-type>"
            "<dynamics><f/></dynamics>"
            "</direction-type></direction>",
        ]
        tree = _chord_block(notes)
        cells = translate_tree(tree, ctx, profile)
        roles = _roles(cells)
        assert roles == [
            "music_octave",
            "music_note",
            "music_interval",
            "music_dynamic",
            "music_dynamic",
        ]
        assert ctx.warnings.warnings == []
