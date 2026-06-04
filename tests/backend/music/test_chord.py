"""Tests for S3: chord-aware notation suppression.

MusicXML represents chords as a series of ``<note>`` elements where
the second and subsequent ones carry a ``<chord/>`` marker. Per BANA
Pars. 9.1 / 10.2 / 13, ties / slurs / lyrics belong on the chord
root (the first note), not on every interval below it. S3 makes
``_emit_note`` skip the post-note notation / lyric emission on
chord notes so the cell stream matches BANA conventions.
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


def _chord_block(notes_xml: list[str]) -> ET.Element:
    """Wrap a list of <note> fragments in a parent so emit_tree can
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
    def test_chord_root_emits_tie(self, profile, ctx):
        # C major triad: C-E-G; the root C carries the tie, chord
        # notes E and G suppress their own tied/slur markers.
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
        cells = emit_tree(tree, ctx, profile)
        # Only the root's tie cells appear — chord notes' ties are
        # suppressed.
        tie_count = _roles(cells).count("music_tie")
        # tie_between_single_notes = 2 cells, one occurrence.
        assert tie_count == 2

    def test_chord_root_emits_lyric_marker_chord_notes_dont(
        self, profile, ctx,
    ):
        # Lyric on root, lyrics on chord notes should be silently
        # dropped (BANA doesn't put lyrics on interval cells).
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
        cells = emit_tree(tree, ctx, profile)
        marker_count = _roles(cells).count("music_lyric_marker")
        assert marker_count == 1, (
            "expected one lyric marker (on chord root only)"
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
        cells = emit_tree(tree, ctx, profile)
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
        cells = emit_tree(tree, ctx, profile)
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
        cells = emit_tree(tree, ctx, profile)
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
        cells = emit_tree(tree, ctx, profile)
        tie_count = _roles(cells).count("music_tie")
        assert tie_count == 4  # 2 cells × 2 notes
