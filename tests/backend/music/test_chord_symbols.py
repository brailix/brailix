"""Tests for S7: chord symbols (BANA Table 23, MusicXML <harmony>).

Covers root letter + accidental + kind suffix + slash-bass forms.
S7 doesn't try to render <degree> alterations (most exporters fold
them into <kind> text); MusicXML <function> roman-numeral harmony
warns + skips.
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


def _harmony(root_step: str, *, root_alter: int = 0, kind: str = "major",
             bass_step: str | None = None, bass_alter: int = 0) -> ET.Element:
    root = f"<root-step>{root_step}</root-step>"
    if root_alter:
        root += f"<root-alter>{root_alter}</root-alter>"
    bass = ""
    if bass_step is not None:
        bass = f"<bass><bass-step>{bass_step}</bass-step>"
        if bass_alter:
            bass += f"<bass-alter>{bass_alter}</bass-alter>"
        bass += "</bass>"
    return ET.fromstring(
        f"<harmony>"
        f"<root>{root}</root>"
        f"<kind>{kind}</kind>"
        f"{bass}"
        "</harmony>"
    )


# ---------------------------------------------------------------------------
# Root + accidental
# ---------------------------------------------------------------------------


class TestRoot:
    def test_bare_major_chord_root_only(self, profile, ctx):
        # C major → just lowercase "c" letter cell.
        cells = emit_tree(_harmony("C"), ctx, profile)
        assert _dots(cells) == [(1, 4)]   # 'c' lowercase
        assert _roles(cells) == ["music_chord_symbol"]

    def test_root_sharp(self, profile, ctx):
        # F# major → f + sharp
        cells = emit_tree(_harmony("F", root_alter=1), ctx, profile)
        assert _dots(cells) == [(1, 2, 4), (1, 4, 6)]   # f, sharp

    def test_root_flat(self, profile, ctx):
        # Bb major → b + flat (Table 23 flat = (1,2,6))
        cells = emit_tree(_harmony("B", root_alter=-1), ctx, profile)
        assert _dots(cells) == [(1, 2), (1, 2, 6)]

    def test_root_natural_zero_alter_no_accidental(self, profile, ctx):
        # alter=0 → no accidental cell.
        cells = emit_tree(_harmony("C", root_alter=0), ctx, profile)
        assert len(cells) == 1


# ---------------------------------------------------------------------------
# Kind suffix
# ---------------------------------------------------------------------------


class TestKind:
    def test_minor_appends_m(self, profile, ctx):
        cells = emit_tree(_harmony("D", kind="minor"), ctx, profile)
        # 'd' + 'm'
        assert _dots(cells) == [(1, 4, 5), (1, 3, 4)]

    def test_dominant_appends_7(self, profile, ctx):
        # G7 → 'g' + lower-row '7'
        cells = emit_tree(_harmony("G", kind="dominant"), ctx, profile)
        assert _dots(cells) == [(1, 2, 4, 5), (2, 3, 5, 6)]

    def test_diminished_uses_circle_entity(self, profile, ctx):
        # B° → 'b' + circle (Table 23 circle_diminished = (2,5,6))
        cells = emit_tree(_harmony("B", kind="diminished"), ctx, profile)
        assert _dots(cells) == [(1, 2), (2, 5, 6)]

    def test_augmented_uses_plus_entity(self, profile, ctx):
        # C+ → 'c' + plus (Table 23 plus = (3,4,6))
        cells = emit_tree(_harmony("C", kind="augmented"), ctx, profile)
        assert _dots(cells) == [(1, 4), (3, 4, 6)]

    def test_major_seventh_appends_maj7(self, profile, ctx):
        # Cmaj7 → 'c' + 'm' + 'a' + 'j' + '7'
        cells = emit_tree(_harmony("C", kind="major-seventh"), ctx, profile)
        assert _dots(cells) == [
            (1, 4),         # c
            (1, 3, 4),      # m
            (1,),           # a
            (2, 4, 5),      # j
            (2, 3, 5, 6),   # 7 (lower)
        ]

    def test_half_diminished_uses_half_diminished_entity(
        self, profile, ctx,
    ):
        # Cø → 'c' + half_diminished (= (2,5,6)(3,))
        cells = emit_tree(_harmony("C", kind="half-diminished"), ctx, profile)
        # 'c' + circle + dot
        assert _dots(cells) == [(1, 4), (2, 5, 6), (3,)]

    def test_unknown_kind_emits_bare_root_with_warning(self, profile, ctx):
        # Unknown <kind> falls back to bare root + warning.
        cells = emit_tree(_harmony("C", kind="exotic-chord"), ctx, profile)
        assert _dots(cells) == [(1, 4)]
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Slash-bass
# ---------------------------------------------------------------------------


class TestBass:
    def test_c_over_g(self, profile, ctx):
        # C/G → 'c' + slash + 'g'
        cells = emit_tree(_harmony("C", bass_step="G"), ctx, profile)
        assert _dots(cells) == [
            (1, 4),         # c
            (3, 4),         # slash
            (1, 2, 4, 5),   # g
        ]

    def test_d_minor_over_a_flat(self, profile, ctx):
        # Dm/Ab → 'd' + 'm' + slash + 'a' + flat
        cells = emit_tree(
            _harmony("D", kind="minor", bass_step="A", bass_alter=-1),
            ctx, profile,
        )
        assert _dots(cells) == [
            (1, 4, 5),      # d
            (1, 3, 4),      # m
            (3, 4),         # slash
            (1,),           # a
            (1, 2, 6),      # flat
        ]


# ---------------------------------------------------------------------------
# Feature gate + edge cases
# ---------------------------------------------------------------------------


class TestFeatureGate:
    def test_show_chord_symbols_false(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_chord_symbols",
            False,
        )
        cells = emit_tree(_harmony("C", kind="major"), ctx, profile)
        assert cells == []


class TestEdgeCases:
    def test_harmony_without_root_warns(self, profile, ctx):
        h = ET.fromstring(
            "<harmony><function>I</function><kind>major</kind></harmony>"
        )
        cells = emit_tree(h, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_unusual_root_letter_warns(self, profile, ctx):
        # MusicXML allows root-step to be one of A-G; anything else
        # is malformed. We warn + skip.
        h = ET.fromstring(
            "<harmony><root><root-step>H</root-step></root>"
            "<kind>major</kind></harmony>"
        )
        cells = emit_tree(h, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes
