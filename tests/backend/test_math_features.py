"""Feature-toggle tests for the math backend.

Each ``profile.feature(...)`` switch the backend reads is exercised in
both states (on/off) to confirm the behaviour is properly gated.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import translate
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.frontend.math.normalizer import normalize
from brailix.ir.inline import MathInline


def mml(xml: str) -> ET.Element:
    return normalize(xml)


def emit(tree, profile):
    wc = WarningCollector(mode=RunMode.NORMAL)
    ctx = BackendContext(profile="cn_current", warnings=wc)
    node = MathInline(surface="", source="mathml", math=tree)
    cells = translate(node, ctx, profile)
    return cells, wc


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


def roles(cells):
    return [c.role for c in cells]


# ---------------------------------------------------------------------------
# math.simplify_fraction
# ---------------------------------------------------------------------------


class TestSimplifyFractionFeature:
    def test_on_by_default(self, profile):
        assert profile.feature("math.simplify_fraction", False) is True

    def test_legacy_alias_works(self, profile):
        # ``math_simplify_fraction`` is the old flat name.
        assert profile.feature("math_simplify_fraction", False) is True

    def test_atomic_letter_pair_simplified(self, profile):
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mi>y</mi></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_off_forces_open_close(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "simplify_fraction", False
        )
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mi>y</mi></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r


# ---------------------------------------------------------------------------
# math.simplify_script
# ---------------------------------------------------------------------------


class TestSimplifyScriptFeature:
    def test_on_by_default(self, profile):
        assert profile.feature("math.simplify_script", False) is True

    def test_atomic_sub_no_close_when_on(self, profile):
        cells, _ = emit(
            mml("<math><msub><mi>x</mi><mn>1</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_script_close" not in r

    def test_off_forces_close(self, profile, monkeypatch):
        # Use a non-digit subscript so atomic_script_lower_digit doesn't
        # preempt the simplify_script test (lower-digit path skips close
        # by its own rule, independent of simplify_script).
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "simplify_script", False
        )
        cells, _ = emit(
            mml("<math><msub><mi>x</mi><mi>n</mi></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_script_close" in r

    def test_complex_content_always_closes(self, profile):
        cells, _ = emit(
            mml(
                "<math><msup><mi>x</mi>"
                "<mrow><mi>n</mi><mo>+</mo><mn>1</mn></mrow>"
                "</msup></math>"
            ),
            profile,
        )
        r = roles(cells)
        # Non-atomic content always emits close.
        assert "math_script_close" in r


# ---------------------------------------------------------------------------
# math.op_spacing
# ---------------------------------------------------------------------------


class TestOpSpacingFeature:
    def test_on_by_default(self, profile):
        assert profile.feature("math.op_spacing", False) is True

    def test_spacing_inserts_blank(self, profile):
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"), profile
        )
        assert any(c.is_blank for c in cells)

    def test_off_drops_all_blanks(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "op_spacing", False
        )
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"), profile
        )
        assert all(not c.is_blank for c in cells)


# ---------------------------------------------------------------------------
# math.atomic_script_lower_digit (applies to big-op AND regular scripts)
# ---------------------------------------------------------------------------


class TestAtomicScriptLowerDigitFeature:
    def test_on_by_default(self, profile):
        assert profile.feature("math.atomic_script_lower_digit", False) is True

    def test_integral_with_atomic_0_uses_lower(self, profile):
        # Big-op script side: ∫_0
        cells, _ = emit(
            mml("<math><msub><mo>∫</mo><mn>0</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" in r
        assert "number_sign" not in r
        assert "math_script_close" not in r

    def test_regular_sup_with_atomic_digit_uses_lower(self, profile):
        # Regular superscript: x^2 should become x + sup_indicator + lower_2
        # (no number_sign, no script_close).
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mn>2</mn></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" in r
        assert "number_sign" not in r
        assert "math_script_close" not in r

    def test_regular_sub_with_atomic_digit_uses_lower(self, profile):
        # Regular subscript: x_3
        cells, _ = emit(
            mml("<math><msub><mi>x</mi><mn>3</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" in r
        assert "number_sign" not in r
        assert "math_script_close" not in r

    def test_regular_subsup_both_atomic_digits_use_lower(self, profile):
        # x_2^3: both sides use lower-form
        cells, _ = emit(
            mml("<math><msubsup><mi>x</mi><mn>2</mn><mn>3</mn></msubsup></math>"),
            profile,
        )
        r = roles(cells)
        assert r.count("math_digit_lower") == 2
        assert "number_sign" not in r

    def test_multi_digit_sup_does_not_lower(self, profile):
        # x^{12}: multi-digit content falls back to normal number_sign + digits
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mn>12</mn></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r
        assert "number_sign" in r

    def test_non_digit_sup_does_not_lower(self, profile):
        # x^n: identifier exponent stays as identifier
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mi>n</mi></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r

    def test_off_disables_lower_for_regular(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}),
            "atomic_script_lower_digit",
            False,
        )
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mn>2</mn></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r
        assert "number_sign" in r

    def test_off_disables_lower_for_big_op(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}),
            "atomic_script_lower_digit",
            False,
        )
        cells, _ = emit(
            mml("<math><msub><mo>∫</mo><mn>0</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r
        assert "number_sign" in r
        assert "math_script_close" in r


# ---------------------------------------------------------------------------
# zh.number_sign
# ---------------------------------------------------------------------------


class TestMathNumberSignFeature:
    """``math.number_sign`` and ``zh.number_sign`` are independent —
    math has its own opinion on whether to emit ⠼ before a digit run."""

    def test_math_on_by_default(self, profile):
        assert profile.feature("math.number_sign", False) is True

    def test_zh_on_by_default(self, profile):
        assert profile.feature("zh.number_sign", False) is True

    def test_math_off_drops_sign_in_math(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "number_sign", False
        )
        cells, _ = emit(mml("<math><mn>5</mn></math>"), profile)
        assert all(c.role != "number_sign" for c in cells)

    def test_zh_off_does_not_affect_math(self, profile, monkeypatch):
        # Turning off zh.number_sign should leave math number sign alone.
        monkeypatch.setitem(
            profile.features.setdefault("zh", {}), "number_sign", False
        )
        cells, _ = emit(mml("<math><mn>5</mn></math>"), profile)
        assert any(c.role == "number_sign" for c in cells)


# ---------------------------------------------------------------------------
# Profile lookup defaults
# ---------------------------------------------------------------------------


class TestProfileLookupDefaults:
    def test_unknown_symbol_role_none(self, profile):
        assert profile.math_symbol_role("???") is None

    def test_unknown_symbol_script_prefix_false(self, profile):
        assert profile.math_symbol_script_prefix("???") is False

    def test_unknown_function_big_op_false(self, profile):
        assert profile.math_function_big_op("not_a_function") is False

    def test_unknown_function_script_prefix_false(self, profile):
        assert profile.math_function_script_prefix("not_a_function") is False

    def test_unknown_structure_returns_empty(self, profile):
        assert profile.math_structure("nonexistent.marker") == ()


# ---------------------------------------------------------------------------
# Letter lookup (profile.letter)
# ---------------------------------------------------------------------------


class TestLetterLookup:
    """``profile.letter(ch)`` returns the letter + script-class prefix
    cell sequence."""

    def test_letter_lower_latin_uses_prefix(self, profile):
        seq = profile.letter("x")
        assert seq is not None
        # latin_lower prefix (56) followed by x cell (1346).
        assert seq == ((5, 6), (1, 3, 4, 6))

    def test_letter_upper_latin_uses_prefix(self, profile):
        seq = profile.letter("A")
        assert seq is not None
        # latin_upper prefix (6) + A cell (1).
        assert seq[0] == (6,)

    def test_letter_greek_uses_prefix(self, profile):
        seq = profile.letter("π")
        assert seq is not None
        # greek_lower prefix (46) + π cell.
        assert seq[0] == (4, 6)

    def test_letter_unknown_char_returns_none(self, profile):
        assert profile.letter("好") is None

    def test_repeated_lookup_returns_same_result(self, profile):
        # profile.letter maintains a per-instance cache.
        a1 = profile.letter("x")
        a2 = profile.letter("x")
        assert a1 == a2


class TestBareLetter:
    """``profile.bare_letter(ch)`` returns just the letter cell — no
    script-class prefix. Used by the Latin backend (word-initial prefix, V3)
    and any caller that emits the prefix itself."""

    def test_bare_latin_lower(self, profile):
        # 'x' bare cell is (1, 3, 4, 6) — same as second tuple of letter('x').
        assert profile.bare_letter("x") == (1, 3, 4, 6)

    def test_bare_latin_upper(self, profile):
        # Latin upper letters share cells with lower in cn_current;
        # the case info lives in the prefix only.
        assert profile.bare_letter("A") == (1,)

    def test_bare_greek_lower(self, profile):
        # π bare cell is (1, 2, 3, 4) — same as second tuple of letter('π').
        # This exercises the greek_letters branch of bare_letter.
        assert profile.bare_letter("π") == (1, 2, 3, 4)

    def test_bare_greek_upper(self, profile):
        assert profile.bare_letter("Π") == (1, 2, 3, 4)

    def test_bare_unknown_returns_none(self, profile):
        assert profile.bare_letter("好") is None
        assert profile.bare_letter("?") is None
