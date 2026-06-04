"""Unit tests for the M3.1 ``<attributes>`` handler suite.

Covers ``<clef>`` (Table 4) + ``<key>`` (Table 6, Par. 6.5) +
``<time>`` (Table 7) plus the per-element feature gates
(``show_clef`` / ``show_key_signature`` / ``show_time_signature``).

Per the §6.4 handler template, each handler is:
  feature gate → BANA resource lookup → emit_cells_for_entity.
The tests verify that the gate suppresses cleanly, the right entity
is selected for each MusicXML input shape, and unsupported variants
warn instead of silently vanishing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import MusicBrailleContext, emit_tree
from brailix.backend.music.dispatch import _emit_element
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


def _emit_one(profile, ctx, elem, **mctx_overrides):
    """Helper: emit one element through the dispatcher with a fresh
    MusicBrailleContext (lets us inject feature/octave overrides for
    individual tests)."""
    mctx = MusicBrailleContext(profile=profile, backend=ctx, **mctx_overrides)
    cells = []
    _emit_element(cells, mctx, elem)
    return cells


# ---------------------------------------------------------------------------
# <clef>
# ---------------------------------------------------------------------------


class TestClef:
    def test_treble_g2(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>G</sign><line>2</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        # G clef (treble) = "> / l" = [(3,4,5), (3,4), (1,2,3)]
        assert _dots(cells) == [(3, 4, 5), (3, 4), (1, 2, 3)]
        assert all(c.role == "music_clef" for c in cells)

    def test_bass_f4(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>F</sign><line>4</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        # F clef (bass) = ">#l" = (3,4,5)(3,4,5,6)(1,2,3)
        assert _dots(cells) == [(3, 4, 5), (3, 4, 5, 6), (1, 2, 3)]

    def test_alto_c3(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>C</sign><line>3</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        # C clef (alto) = ">+l" = (3,4,5)(3,4,6)(1,2,3)
        assert _dots(cells) == [(3, 4, 5), (3, 4, 6), (1, 2, 3)]

    def test_tenor_c4(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>C</sign><line>4</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        # C clef on 4th line (tenor) = ">+\"l" → 4 cells
        # > = 345, + = 346, " = 5, l = 123
        assert _dots(cells) == [(3, 4, 5), (3, 4, 6), (5,), (1, 2, 3)]

    def test_french_violin_g1(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>G</sign><line>1</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        # G clef on 1st line = ">/@l" → 4 cells
        # > / @ l
        assert _dots(cells) == [(3, 4, 5), (3, 4), (4,), (1, 2, 3)]

    def test_unknown_line_falls_back_to_sign_default(self, profile, ctx):
        # G clef on line 5 (doesn't exist in our table) → falls back
        # to g_clef_treble.
        clef = ET.fromstring("<clef><sign>G</sign><line>5</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        assert _dots(cells) == [(3, 4, 5), (3, 4), (1, 2, 3)]

    def test_no_line_falls_back_to_sign_default(self, profile, ctx):
        # Some exporters omit <line> for clef changes — fall back to
        # the sign's default entry.
        clef = ET.fromstring("<clef><sign>F</sign></clef>")
        cells = emit_tree(clef, ctx, profile)
        # f_clef_bass
        assert _dots(cells) == [(3, 4, 5), (3, 4, 5, 6), (1, 2, 3)]

    def test_lowercase_sign_normalised(self, profile, ctx):
        clef = ET.fromstring("<clef><sign>g</sign><line>2</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        assert _dots(cells) == [(3, 4, 5), (3, 4), (1, 2, 3)]

    def test_feature_gate_disables_output(self, profile, ctx, monkeypatch):
        # Flip the feature off in the profile dict; helper should
        # bail before emitting anything.
        monkeypatch.setitem(profile.features.setdefault("music", {}), "show_clef", False)
        clef = ET.fromstring("<clef><sign>G</sign><line>2</line></clef>")
        cells = emit_tree(clef, ctx, profile)
        assert cells == []
        assert not ctx.warnings.warnings  # no warning when gated off


# ---------------------------------------------------------------------------
# <key>
# ---------------------------------------------------------------------------


class TestKeySignature:
    def test_zero_fifths_emits_nothing(self, profile, ctx):
        # C major / A minor → no key signature shown (BANA convention).
        key = ET.fromstring("<key><fifths>0</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        assert cells == []

    def test_one_sharp(self, profile, ctx):
        key = ET.fromstring("<key><fifths>1</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Single sharp = "%" = (1,4,6)
        assert _dots(cells) == [(1, 4, 6)]
        assert all(c.role == "music_key_signature" for c in cells)

    def test_two_sharps(self, profile, ctx):
        key = ET.fromstring("<key><fifths>2</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Two sharps = "%%"
        assert _dots(cells) == [(1, 4, 6), (1, 4, 6)]

    def test_three_sharps(self, profile, ctx):
        key = ET.fromstring("<key><fifths>3</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Three sharps = "%%%"
        assert _dots(cells) == [(1, 4, 6), (1, 4, 6), (1, 4, 6)]

    def test_four_sharps_uses_named_entry(self, profile, ctx):
        key = ET.fromstring("<key><fifths>4</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Four sharps = "#d%" = (3,4,5,6) + (1,4,5) + (1,4,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 4, 5), (1, 4, 6)]

    def test_one_flat(self, profile, ctx):
        key = ET.fromstring("<key><fifths>-1</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Single flat = "<" = (1,2,6)
        assert _dots(cells) == [(1, 2, 6)]

    def test_two_flats(self, profile, ctx):
        key = ET.fromstring("<key><fifths>-2</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        assert _dots(cells) == [(1, 2, 6), (1, 2, 6)]

    def test_four_flats_uses_named_entry(self, profile, ctx):
        key = ET.fromstring("<key><fifths>-4</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # Four flats = "#d<"
        assert _dots(cells) == [(3, 4, 5, 6), (1, 4, 5), (1, 2, 6)]

    def test_five_sharps_synthesized(self, profile, ctx):
        # S1: 5 sharps = ``#e%`` = number_sign + digit-e + sharp.
        key = ET.fromstring("<key><fifths>5</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # # = (3,4,5,6); e = (1,5); % = (1,4,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 5), (1, 4, 6)]
        assert all(c.role == "music_key_signature" for c in cells)

    def test_seven_flats_synthesized(self, profile, ctx):
        key = ET.fromstring("<key><fifths>-7</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        # # + g (1,2,4,5) + flat (1,2,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 2, 4, 5), (1, 2, 6)]

    def test_synthesis_sources_cells_from_resources(self, profile, ctx):
        # The synthesized cells come from the ``numerals`` resource table +
        # ``accidentals_key`` (referencing cells.json), not dot literals in
        # code (music-design.md §10). Lock the sourcing, not just the dots.
        assert profile.music_topic("numerals"), "numerals table not loaded"
        key = ET.fromstring("<key><fifths>5</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        assert cells[0].dots == profile.music_cell("numerals", "number_sign")[0]
        assert cells[1].dots == profile.music_cell("numerals", "digit_upper_5")[0]
        assert cells[2].dots == profile.music_cell("accidentals_key", "sharp")[0]

    def test_feature_gate_disables_output(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_key_signature",
            False,
        )
        key = ET.fromstring("<key><fifths>2</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        assert cells == []

    def test_missing_fifths_skipped(self, profile, ctx):
        # <key> with no <fifths> child — nothing to emit; quietly skip.
        key = ET.fromstring("<key></key>")
        cells = emit_tree(key, ctx, profile)
        assert cells == []

    def test_non_integer_fifths_skipped(self, profile, ctx):
        key = ET.fromstring("<key><fifths>abc</fifths></key>")
        cells = emit_tree(key, ctx, profile)
        assert cells == []


# ---------------------------------------------------------------------------
# <time>
# ---------------------------------------------------------------------------


class TestTimeSignature:
    def test_four_four(self, profile, ctx):
        time = ET.fromstring("<time><beats>4</beats><beat-type>4</beat-type></time>")
        cells = emit_tree(time, ctx, profile)
        # four_four_time = "#d4" = (3,4,5,6) + (1,4,5) + (2,5,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 4, 5), (2, 5, 6)]
        assert all(c.role == "music_time_signature" for c in cells)

    def test_six_eight(self, profile, ctx):
        time = ET.fromstring("<time><beats>6</beats><beat-type>8</beat-type></time>")
        cells = emit_tree(time, ctx, profile)
        # six_eight_time = "#f8" = (3,4,5,6) + (1,2,4) + (2,3,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 2, 4), (2, 3, 6)]

    def test_common_via_symbol_attribute(self, profile, ctx):
        time = ET.fromstring('<time symbol="common"></time>')
        cells = emit_tree(time, ctx, profile)
        # common_time = ".c" = (4,6) + (1,4)
        assert _dots(cells) == [(4, 6), (1, 4)]

    def test_common_4_4_with_symbol_takes_symbol_form(self, profile, ctx):
        # 4/4 + symbol="common" → C, not #d4.
        time = ET.fromstring(
            '<time symbol="common">'
            '<beats>4</beats><beat-type>4</beat-type></time>'
        )
        cells = emit_tree(time, ctx, profile)
        assert _dots(cells) == [(4, 6), (1, 4)]

    def test_cut_via_symbol_attribute(self, profile, ctx):
        time = ET.fromstring('<time symbol="cut"></time>')
        cells = emit_tree(time, ctx, profile)
        # alla_breve_cut_time = "_c" = (4,5,6) + (1,4)
        assert _dots(cells) == [(4, 5, 6), (1, 4)]

    def test_three_four_synthesized(self, profile, ctx):
        # S1: 3/4 = ``#c4`` = number_sign + c(=3 upper) + 4(lower)
        time = ET.fromstring("<time><beats>3</beats><beat-type>4</beat-type></time>")
        cells = emit_tree(time, ctx, profile)
        # # = (3,4,5,6); c = (1,4); 4 lower = (2,5,6)
        assert _dots(cells) == [(3, 4, 5, 6), (1, 4), (2, 5, 6)]
        assert all(c.role == "music_time_signature" for c in cells)

    def test_twelve_eight_synthesized(self, profile, ctx):
        # 12/8 — multi-digit numerator
        time = ET.fromstring("<time><beats>12</beats><beat-type>8</beat-type></time>")
        cells = emit_tree(time, ctx, profile)
        # # + a(1) + b(12) + 8(236)
        assert _dots(cells) == [(3, 4, 5, 6), (1,), (1, 2), (2, 3, 6)]

    def test_feature_gate_disables_output(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_time_signature",
            False,
        )
        time = ET.fromstring("<time><beats>4</beats><beat-type>4</beat-type></time>")
        cells = emit_tree(time, ctx, profile)
        assert cells == []

    def test_malformed_skipped(self, profile, ctx):
        # Missing children, no symbol → quiet skip (the warning belongs
        # to upstream validators, not the cell emitter).
        time = ET.fromstring("<time></time>")
        cells = emit_tree(time, ctx, profile)
        assert cells == []


# ---------------------------------------------------------------------------
# <attributes> container
# ---------------------------------------------------------------------------


class TestAttributesContainer:
    def test_typical_attributes_block(self, profile, ctx):
        # Typical opening of a measure-1 score: divisions / key / time / clef.
        attrs = ET.fromstring(
            "<attributes>"
            "<divisions>4</divisions>"
            "<key><fifths>2</fifths></key>"
            "<time><beats>4</beats><beat-type>4</beat-type></time>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
        )
        cells = emit_tree(attrs, ctx, profile)
        # Expect: 2 sharps + 4/4 + treble clef.
        # 2 key cells + 3 time cells + 3 clef cells = 8 total.
        assert len(cells) == 8
        roles = _roles(cells)
        assert roles[:2] == ["music_key_signature"] * 2
        assert roles[2:5] == ["music_time_signature"] * 3
        assert roles[5:] == ["music_clef"] * 3

    def test_divisions_alone_emits_nothing(self, profile, ctx):
        # <divisions> is a parser-internal metric; no cells.
        attrs = ET.fromstring(
            "<attributes><divisions>4</divisions></attributes>"
        )
        cells = emit_tree(attrs, ctx, profile)
        assert cells == []

    def test_staves_and_instruments_skipped(self, profile, ctx):
        attrs = ET.fromstring(
            "<attributes>"
            "<staves>2</staves>"
            "<instruments>1</instruments>"
            "</attributes>"
        )
        cells = emit_tree(attrs, ctx, profile)
        assert cells == []

    def test_no_warnings_on_clean_attributes(self, profile, ctx):
        attrs = ET.fromstring(
            "<attributes>"
            "<divisions>4</divisions>"
            "<key><fifths>1</fifths></key>"
            "<time><beats>4</beats><beat-type>4</beat-type></time>"
            "<clef><sign>G</sign><line>2</line></clef>"
            "</attributes>"
        )
        emit_tree(attrs, ctx, profile)
        codes = [w.code for w in ctx.warnings.warnings]
        assert codes == []
