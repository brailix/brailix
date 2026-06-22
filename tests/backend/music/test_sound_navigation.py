"""Tests for S5: D.C. / D.S. / Segno / Coda from <sound> attributes
(BANA Table 20 / Pars. 20.1-20.3).

MusicXML uses ``<sound>`` element attributes to mark navigation
directives. M S5 wires these to BANA Table 20 entities so they
appear in the cell stream alongside notes / dynamics.
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


# ---------------------------------------------------------------------------
# D.C. / Coda / Segno
# ---------------------------------------------------------------------------


class TestSoundDirectives:
    def test_dacapo_emits_print_da_capo(self, profile, ctx):
        s = ET.fromstring('<sound dacapo="yes"/>')
        cells = emit_tree(s, ctx, profile)
        # print_da_capo = ">d'c'>" = (3,4,5)(1,4,5)(3,)(1,4)(3,)(3,4,5)
        assert _dots(cells) == [
            (3, 4, 5), (1, 4, 5), (3,), (1, 4), (3,), (3, 4, 5),
        ]
        assert all(c.role == "music_dacapo" for c in cells)

    def test_segno_emits_segno_with_letter(self, profile, ctx):
        s = ET.fromstring('<sound segno="A"/>')
        cells = emit_tree(s, ctx, profile)
        # braille_segno_with_letter = "+a" = (3,4,6)(1,)
        assert _dots(cells) == [(3, 4, 6), (1,)]
        assert all(c.role == "music_segno" for c in cells)

    def test_dalsegno_emits_dal_segno(self, profile, ctx):
        s = ET.fromstring('<sound dalsegno="A"/>')
        cells = emit_tree(s, ctx, profile)
        # braille_dal_segno_letter_a = '"+a' = (5,)(3,4,6)(1,)
        assert _dots(cells) == [(5,), (3, 4, 6), (1,)]
        assert all(c.role == "music_dal_segno" for c in cells)

    def test_dalsegno_b_emits_letter_b(self, profile, ctx):
        # Regression: dal segno "B" used to be hardcoded to letter "a"; the
        # _b resource exists and must be used so "B" reads as a jump to B,
        # not a silent mislabel as A.
        s = ET.fromstring('<sound dalsegno="B"/>')
        cells = emit_tree(s, ctx, profile)
        # braille_dal_segno_letter_b = '"+b' = (5,)(3,4,6)(1,2)
        assert _dots(cells) == [(5,), (3, 4, 6), (1, 2)]
        assert all(c.role == "music_dal_segno" for c in cells)
        assert not ctx.warnings.warnings  # known letter → no warning

    def test_dalsegno_unknown_letter_falls_back_to_a_with_warning(
        self, profile, ctx
    ):
        # No per-letter marker beyond a/b ships: fall back to "a" but warn so
        # the mislabel is visible to the proofreader, not silent.
        s = ET.fromstring('<sound dalsegno="C"/>')
        cells = emit_tree(s, ctx, profile)
        assert _dots(cells) == [(5,), (3, 4, 6), (1,)]  # letter-a fallback
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_segno_non_a_letter_warns(self, profile, ctx):
        # Only the generic (letter-a) segno marker ships; a non-"a" label is
        # dropped, so warn rather than silently presenting it as segno "a".
        s = ET.fromstring('<sound segno="B"/>')
        cells = emit_tree(s, ctx, profile)
        assert _dots(cells) == [(3, 4, 6), (1,)]  # generic segno marker
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_coda_emits_print_encircled_cross(self, profile, ctx):
        s = ET.fromstring('<sound coda="X"/>')
        cells = emit_tree(s, ctx, profile)
        # print_encircled_cross_coda = "+l" = (3,4,6)(1,2,3)
        assert _dots(cells) == [(3, 4, 6), (1, 2, 3)]
        assert all(c.role == "music_coda" for c in cells)

    def test_tocoda_emits_print_encircled_cross(self, profile, ctx):
        s = ET.fromstring('<sound tocoda="X"/>')
        cells = emit_tree(s, ctx, profile)
        # Same entity as coda (no separate to-coda symbol in Table 20)
        assert _dots(cells) == [(3, 4, 6), (1, 2, 3)]
        assert all(c.role == "music_coda" for c in cells)


# ---------------------------------------------------------------------------
# Multiple directives + ignored attributes
# ---------------------------------------------------------------------------


class TestMultipleAndIgnored:
    def test_performance_only_attributes_ignored(self, profile, ctx):
        # tempo / dynamics / divisions are audio metadata — no cells.
        s = ET.fromstring(
            '<sound tempo="120" dynamics="100" divisions="4"/>'
        )
        cells = emit_tree(s, ctx, profile)
        assert cells == []

    def test_multiple_directives_emit_all(self, profile, ctx):
        s = ET.fromstring('<sound dacapo="yes" coda="X"/>')
        cells = emit_tree(s, ctx, profile)
        # D.C. (6 cells) + coda (2 cells)
        assert _roles(cells).count("music_dacapo") == 6
        assert _roles(cells).count("music_coda") == 2

    def test_empty_sound_emits_nothing(self, profile, ctx):
        s = ET.fromstring("<sound/>")
        cells = emit_tree(s, ctx, profile)
        assert cells == []


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------


class TestFeatureGate:
    def test_show_dynamics_false_suppresses_all(
        self, profile, ctx, monkeypatch,
    ):
        # S5 shares the dynamics gate (these are navigational hints
        # in the same conceptual layer).
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_dynamics",
            False,
        )
        s = ET.fromstring('<sound dacapo="yes" coda="X"/>')
        cells = emit_tree(s, ctx, profile)
        assert cells == []
