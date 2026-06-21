"""Tests for BANA Par. 3.2.2 octave-mark inference.

Exercises every branch of :func:`needs_octave_mark` and the
end-to-end emission path through :func:`emit_tree` so the rule is
verified both as a pure function and as observed in cell streams.

Rule:

* No previous pitch         -> always mark (Par. 3.2.1 line start).
* Interval < 4°  (≤ 3°)     -> never mark.
* Interval > 5°  (≥ 6°)     -> always mark.
* Interval = 4° or 5°       -> mark only when the BANA octave number
                               changed between the two notes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.backend.music.utils import needs_octave_mark
from brailix.core.config import load_profile
from brailix.core.context import BackendContext

# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestNeedsOctaveMarkPure:
    def test_first_note_always_marks(self):
        assert needs_octave_mark(None, ("C", 4)) is True
        assert needs_octave_mark(None, ("G", 5)) is True

    @pytest.mark.parametrize(
        "prev, curr",
        [
            (("C", 4), ("C", 4)),    # unison (1°)
            (("C", 4), ("D", 4)),    # 2°
            (("C", 4), ("E", 4)),    # 3°
            (("E", 4), ("C", 4)),    # 3° descending
            (("A", 4), ("C", 5)),    # 3° crossing octave but ≤ 3° -> still omit
        ],
    )
    def test_three_or_less_never_marks(self, prev, curr):
        assert needs_octave_mark(prev, curr) is False

    @pytest.mark.parametrize(
        "prev, curr",
        [
            (("C", 4), ("F", 4)),    # 4° same BANA octave
            (("C", 4), ("G", 4)),    # 5° same BANA octave
        ],
    )
    def test_four_or_five_same_octave_omits(self, prev, curr):
        # Same-octave 4° / 5° intervals omit the octave mark.  (The
        # cross-octave G4→D5 case is covered by the cross-octave test
        # below; it was a dead parametrize entry here — the if-guard made
        # its assertion never run.)
        assert needs_octave_mark(prev, curr) is False

    @pytest.mark.parametrize(
        "prev, curr",
        [
            (("G", 4), ("C", 5)),    # 4° crossing octave
            (("F", 4), ("C", 5)),    # 5° crossing octave
            (("C", 4), ("G", 3)),    # 4° descending crossing octave
        ],
    )
    def test_four_or_five_cross_octave_marks(self, prev, curr):
        assert needs_octave_mark(prev, curr) is True

    @pytest.mark.parametrize(
        "prev, curr",
        [
            (("C", 4), ("A", 4)),    # 6°
            (("C", 4), ("B", 4)),    # 7°
            (("C", 4), ("C", 5)),    # 8° (octave)
            (("C", 4), ("G", 5)),    # 12° (octave + 5)
        ],
    )
    def test_six_or_more_always_marks(self, prev, curr):
        assert needs_octave_mark(prev, curr) is True


# ---------------------------------------------------------------------------
# End-to-end: scan a measure and observe which notes get an octave prefix
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _make_measure(notes: list[tuple[str, int, str]]) -> ET.Element:
    """Build a one-part one-measure score from a list of
    (step, octave, type) tuples — handy shorthand for the tests below."""
    parts = ['<score-partwise><part id="P1"><measure number="1">']
    for step, octave, type_name in notes:
        parts.append(
            f"<note><pitch><step>{step}</step><octave>{octave}</octave>"
            f"</pitch><duration>1</duration><type>{type_name}</type></note>"
        )
    parts.append("</measure></part></score-partwise>")
    return ET.fromstring("".join(parts))


def _octave_roles(cells) -> list[bool]:
    """Boolean mask: True for cells emitted as an octave prefix.

    Skips inter-measure separator cells (``music_measure_sep``) —
    structural spacing between measures, not note-bearing cells, so
    they don't belong in an octave-marking mask."""
    return [
        c.role == "music_octave"
        for c in cells
        if c.role != "music_measure_sep"
    ]


class TestOctaveInferenceEndToEnd:
    def test_three_consecutive_close_notes_only_first_marks(self, profile, ctx):
        # C4 -> D4 (2°) -> E4 (2° again). Only the first note carries
        # an octave prefix; the others are within 3° and skip it.
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("D", 4, "quarter"),
            ("E", 4, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        # Expected: [octave, C, D, E] -> 4 cells, first one is octave.
        assert len(cells) == 4
        assert _octave_roles(cells) == [True, False, False, False]

    def test_six_or_more_re_marks(self, profile, ctx):
        # C4 -> A4 is a 6° leap, must re-mark octave.
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("A", 4, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        # Expected: [octave, C, octave, A]
        assert _octave_roles(cells) == [True, False, True, False]

    def test_four_within_octave_omits(self, profile, ctx):
        # C4 -> F4 is 4° but stays in octave 4 -> omit.
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("F", 4, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        assert _octave_roles(cells) == [True, False, False]

    def test_four_crossing_octave_marks(self, profile, ctx):
        # G4 -> C5 is 4° crossing octave 4→5 -> mark.
        tree = _make_measure([
            ("G", 4, "quarter"),
            ("C", 5, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        assert _octave_roles(cells) == [True, False, True, False]

    def test_rest_does_not_break_pitch_memory(self, profile, ctx):
        # C4 -> rest -> D4 should still be 2° (omit) — rests don't
        # disturb prev_pitch per Par. 3.2.2's "two consecutive notes".
        tree = ET.fromstring(
            '<score-partwise><part id="P1"><measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "<note><rest/><duration>1</duration><type>quarter</type></note>"
            "<note><pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        cells = emit_tree(tree, ctx, profile)
        # Expected sequence: [octave, C, rest, D] — only first cell
        # is the octave prefix; D doesn't re-mark.
        assert _octave_roles(cells) == [True, False, False, False]

    def test_part_boundary_resets_prev_pitch(self, profile, ctx):
        # Two parts each starting on a close pitch: the second part's
        # first note must still re-mark because part boundaries reset
        # prev_pitch (BANA Par. 3.2.1: "first note of a braille line").
        tree = ET.fromstring(
            '<score-partwise>'
            '<part id="P1"><measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part>"
            '<part id="P2"><measure number="1">'
            "<note><pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part>"
            "</score-partwise>"
        )
        cells = emit_tree(tree, ctx, profile)
        # The part boundary emits a structural ``music_part_sep`` cell
        # between the parts; drop it so the check is about the notes'
        # octave marks: [octave_P1, C, octave_P2, D].
        note_cells = [c for c in cells if c.role != "music_part_sep"]
        assert _octave_roles(note_cells) == [True, False, True, False]

    def test_pitch_memory_continues_across_normal_barline(self, profile, ctx):
        # Default octave rule: pitch memory is NOT reset at a normal barline —
        # octave memory carries across measures (BANA). M1 ends on E4; M2's
        # first note F4 is a 2° from it (same octave) → it must OMIT the octave
        # mark. A regression that reset prev_pitch every measure would wrongly
        # re-mark each measure's first note. (Measure separators are filtered
        # by _octave_roles, so this asserts on note cells only.)
        tree = ET.fromstring(
            '<score-partwise><part id="P1">'
            '<measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "<note><pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            '<measure number="2">'
            "<note><pitch><step>F</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part></score-partwise>"
        )
        cells = emit_tree(tree, ctx, profile)
        # [octave, C, E(omit), F(omit across barline)]
        assert _octave_roles(cells) == [True, False, False, False]

    def test_big_leap_across_barline_still_remarks(self, profile, ctx):
        # Contrast to the above: a ≥6° leap across the barline DOES re-mark,
        # proving cross-barline continuity isn't just "never mark after M1".
        tree = ET.fromstring(
            '<score-partwise><part id="P1">'
            '<measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            '<measure number="2">'
            "<note><pitch><step>A</step><octave>5</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part></score-partwise>"
        )
        cells = emit_tree(tree, ctx, profile)
        assert _octave_roles(cells) == [True, False, True, False]


# ---------------------------------------------------------------------------
# ``octave_rule`` overrides
# ---------------------------------------------------------------------------


class TestOctaveRuleOverrides:
    def test_always_rule_marks_every_note(self, profile, ctx):
        from brailix.backend.music import MusicBrailleContext
        from brailix.backend.music.dispatch import _emit_element

        tree = _make_measure([
            ("C", 4, "quarter"),
            ("D", 4, "quarter"),
            ("E", 4, "quarter"),
        ])
        mctx = MusicBrailleContext(
            profile=profile, backend=ctx, octave_rule="always"
        )
        cells = []
        _emit_element(cells, mctx, tree)
        # With ``always`` every note gets an octave prefix.
        assert _octave_roles(cells) == [True, False, True, False, True, False]

    def test_every_measure_rule_resets_per_measure(self, profile, ctx):
        from brailix.backend.music import MusicBrailleContext
        from brailix.backend.music.dispatch import _emit_element

        tree = ET.fromstring(
            '<score-partwise><part id="P1">'
            '<measure number="1">'
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "<note><pitch><step>D</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            '<measure number="2">'
            "<note><pitch><step>E</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure>"
            "</part></score-partwise>"
        )
        mctx = MusicBrailleContext(
            profile=profile, backend=ctx, octave_rule="every_measure"
        )
        cells = []
        _emit_element(cells, mctx, tree)
        # M1: [octave, C, D]  M2: [octave, E]  → re-marks at measure 2
        assert _octave_roles(cells) == [True, False, False, True, False]


# ---------------------------------------------------------------------------
# octave_rule wired through the profile (regression: was hard-coded to
# the "interval16" default and ignored features.music.octave_rule)
# ---------------------------------------------------------------------------


class TestOctaveRuleFromProfile:
    """End-to-end through ``emit_tree`` (not a hand-injected context):
    ``features.music.octave_rule`` must actually drive the backend."""

    def test_always_rule_from_profile_marks_every_note(
        self, profile, ctx, monkeypatch
    ):
        # feature() re-walks the live features dict, so setting it here
        # is observed by emit_tree's _resolve_octave_rule.
        monkeypatch.setitem(profile.features["music"], "octave_rule", "always")
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("D", 4, "quarter"),    # 2° — would normally skip the prefix
            ("E", 4, "quarter"),    # 2° — would normally skip the prefix
        ])
        cells = emit_tree(tree, ctx, profile)
        # Every note carries an octave prefix under "always".
        assert _octave_roles(cells) == [True, False, True, False, True, False]

    def test_default_profile_uses_interval16(self, profile, ctx):
        # Without overriding, the BANA default skips close intervals.
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("D", 4, "quarter"),
            ("E", 4, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        assert _octave_roles(cells) == [True, False, False, False]

    def test_invalid_octave_rule_falls_back(self, profile, ctx, monkeypatch):
        # A malformed profile value must not crash or violate the
        # context Literal — it degrades to interval16.
        monkeypatch.setitem(profile.features["music"], "octave_rule", "bogus")
        tree = _make_measure([
            ("C", 4, "quarter"),
            ("D", 4, "quarter"),
        ])
        cells = emit_tree(tree, ctx, profile)
        # interval16 behaviour: 2° skips the second prefix.
        assert _octave_roles(cells) == [True, False, False]
