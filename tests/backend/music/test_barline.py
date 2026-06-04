"""Unit tests for M3.3: ``<barline>`` handler suite.

Covers BANA Par. 1.10 (bar-line styles), Table 17 (print repeats),
and Par. 17.2 (volta endings). All cells funnel through the §6.4
template: feature gate → ``general``/``print_repeats`` resource
lookup → ``emit_cells_for_entity``.
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
# Bar styles (Par. 1.10)
# ---------------------------------------------------------------------------


class TestBarStyle:
    def test_final_double_bar(self, profile, ctx):
        bar = ET.fromstring(
            '<barline location="right"><bar-style>light-heavy</bar-style>'
            '</barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # final_double_bar = "<k" = (1,2,6) (1,3)
        assert _dots(cells) == [(1, 2, 6), (1, 3)]
        assert _roles(cells) == ["music_bar_line", "music_bar_line"]

    def test_sectional_double_bar(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><bar-style>light-light</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # sectional_double_bar = "<k'" = (1,2,6) (1,3) (3,)
        assert _dots(cells) == [(1, 2, 6), (1, 3), (3,)]

    def test_dotted_always_emits_dotted_cell(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><bar-style>dotted</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # print_dotted_bar_line = "k" = (1,3)
        assert _dots(cells) == [(1, 3)]

    def test_tick_falls_back_to_unusual(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><bar-style>tick</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # bar_line_unusual = "l" = (1,2,3)
        assert _dots(cells) == [(1, 2, 3)]

    def test_short_falls_back_to_unusual(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><bar-style>short</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert _dots(cells) == [(1, 2, 3)]

    def test_none_emits_nothing(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><bar-style>none</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []

    def test_regular_default_skip(self, profile, ctx):
        # bar_line_print default = "skip" → regular bar emits nothing.
        bar = ET.fromstring(
            '<barline><bar-style>regular</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []

    def test_no_bar_style_default_skip(self, profile, ctx):
        # No <bar-style> child → treated as regular → skip.
        bar = ET.fromstring('<barline/>')
        cells = emit_tree(bar, ctx, profile)
        assert cells == []

    def test_bar_line_print_dotted_mode(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "bar_line_print",
            "dotted",
        )
        bar = ET.fromstring(
            '<barline><bar-style>regular</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Dotted print bar
        assert _dots(cells) == [(1, 3)]

    def test_bar_line_print_space_mode(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "bar_line_print",
            "space",
        )
        bar = ET.fromstring(
            '<barline><bar-style>regular</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # One blank cell
        assert _dots(cells) == [()]
        assert _roles(cells) == ["music_bar_line"]

    def test_unknown_print_mode_warns(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "bar_line_print",
            "rocket-ship",
        )
        bar = ET.fromstring(
            '<barline><bar-style>regular</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_semantic_styles_unaffected_by_print_feature(
        self, profile, ctx, monkeypatch,
    ):
        # Even when bar_line_print = "skip" (the default), light-heavy
        # / light-light / dotted still emit their semantic cells.
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "bar_line_print",
            "skip",
        )
        bar = ET.fromstring(
            '<barline><bar-style>light-heavy</bar-style></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert _dots(cells) == [(1, 2, 6), (1, 3)]


# ---------------------------------------------------------------------------
# Repeats (Table 17)
# ---------------------------------------------------------------------------


class TestRepeat:
    def test_forward_repeat(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><repeat direction="forward"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # double_bar_dots_after = "<7" = (1,2,6) (2,3,5,6)
        assert _dots(cells) == [(1, 2, 6), (2, 3, 5, 6)]
        assert all(c.role == "music_repeat" for c in cells)

    def test_backward_repeat(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><repeat direction="backward"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # double_bar_dots_before = "<2" = (1,2,6) (2,3)
        assert _dots(cells) == [(1, 2, 6), (2, 3)]

    def test_default_direction_is_backward(self, profile, ctx):
        bar = ET.fromstring('<barline><repeat/></barline>')
        cells = emit_tree(bar, ctx, profile)
        # Default = backward
        assert _dots(cells) == [(1, 2, 6), (2, 3)]

    def test_unknown_direction_warns(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><repeat direction="sideways"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_backward_repeat_plus_final_bar(self, profile, ctx):
        # Common pattern at end of a repeated section:
        # bar-style=light-heavy + repeat backward
        bar = ET.fromstring(
            '<barline location="right">'
            '<bar-style>light-heavy</bar-style>'
            '<repeat direction="backward"/>'
            '</barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Order: repeat first, then bar-style.
        # backward repeat + final_double_bar
        assert _dots(cells) == [
            (1, 2, 6), (2, 3),     # <2
            (1, 2, 6), (1, 3),     # <k
        ]

    def test_expand_repeats_warns_but_still_emits_sign(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "expand_repeats",
            True,
        )
        bar = ET.fromstring(
            '<barline><repeat direction="backward"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Cells still produced (fallback to marker form)
        assert _dots(cells) == [(1, 2, 6), (2, 3)]
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Volta endings (Par. 17.2)
# ---------------------------------------------------------------------------


class TestVolta:
    def test_first_ending_start(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="1" type="start"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # prima_volta = "#1" = (3,4,5,6) (2,)
        assert _dots(cells) == [(3, 4, 5, 6), (2,)]
        assert all(c.role == "music_volta" for c in cells)

    def test_second_ending_start(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="2" type="start"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # seconda_volta = "#2" = (3,4,5,6) (2,3)
        assert _dots(cells) == [(3, 4, 5, 6), (2, 3)]

    def test_stop_emits_nothing(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="1" type="stop"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []

    def test_discontinue_emits_nothing(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="2" type="discontinue"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []

    def test_third_ending_warns(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="3" type="start"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_combined_number_warns(self, profile, ctx):
        bar = ET.fromstring(
            '<barline><ending number="1,2" type="start"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_letter_style_warns_but_fallback_numeric(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "volta_style",
            "letter",
        )
        bar = ET.fromstring(
            '<barline><ending number="1" type="start"/></barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Fallback: numeric form
        assert _dots(cells) == [(3, 4, 5, 6), (2,)]
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Combined: repeat + ending + bar style in one <barline>
# ---------------------------------------------------------------------------


class TestCombined:
    def test_first_ending_with_backward_repeat_and_final_bar(
        self, profile, ctx,
    ):
        # End of first volta: light-heavy bar + backward repeat + ending stop.
        # Stop emits nothing — only repeat + bar style cells appear.
        bar = ET.fromstring(
            '<barline location="right">'
            '<bar-style>light-heavy</bar-style>'
            '<repeat direction="backward"/>'
            '<ending number="1" type="stop"/>'
            '</barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Order: repeat → (volta stop = nothing) → bar style
        # backward repeat (2 cells) + final_double_bar (2 cells)
        assert _dots(cells) == [
            (1, 2, 6), (2, 3),       # <2
            (1, 2, 6), (1, 3),       # <k
        ]

    def test_volta_start_with_forward_repeat(self, profile, ctx):
        # Beginning of repeated volta-1 section
        bar = ET.fromstring(
            '<barline location="left">'
            '<repeat direction="forward"/>'
            '<ending number="1" type="start"/>'
            '</barline>'
        )
        cells = emit_tree(bar, ctx, profile)
        # Order: repeat first, then volta. No bar-style child.
        assert _dots(cells) == [
            (1, 2, 6), (2, 3, 5, 6),     # <7 (forward repeat)
            (3, 4, 5, 6), (2,),          # #1 (prima volta)
        ]


# ---------------------------------------------------------------------------
# Pipeline integration: score with repeats + volta
# ---------------------------------------------------------------------------


SCORE_WITH_REPEATS_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    '<barline location="left">'
    '<bar-style>heavy-light</bar-style>'
    '<repeat direction="forward"/>'
    "</barline>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    '<barline location="right">'
    '<bar-style>light-heavy</bar-style>'
    '<repeat direction="backward"/>'
    "</barline>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_score_with_repeats_emits_correct_role_order(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_REPEATS_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]

        # Left barline: forward repeat (2 cells) — no bar-style cells
        # because heavy-light isn't in our semantic map (regular fallback
        # under bar_line_print="skip" → no output).
        # Note: octave + note
        # Right barline: backward repeat (2 cells) + final_double_bar (2 cells)
        # Expected: 2 repeat + 1 octave + 1 note + 2 repeat + 2 bar = 8
        assert roles == [
            "music_repeat", "music_repeat",
            "music_octave", "music_note",
            "music_repeat", "music_repeat",
            "music_bar_line", "music_bar_line",
        ]
        # No surprise warnings (heavy-light isn't a semantic style in
        # our map and falls back to regular, which is silently skipped).
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
