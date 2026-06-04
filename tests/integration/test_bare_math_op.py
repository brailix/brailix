"""End-to-end tests for bare half-width math operators in Chinese prose.

Setting: textbook exercises commonly mix Chinese sentences with bare
half-width ``()`` (fill-in-the-blank slots, sub-question numbers like
``(1)`` ``(2)``) and other math operators (``+`` ``-`` ``=`` ...) that
sit **outside** any explicit ``$...$`` wrap.

The project's profile design treats half-width = math semantics,
full-width = Chinese punctuation. So a bare ``(`` in prose is a
one-character math operator, not a Chinese paren — it must:

* segment as a dedicated ``math_op`` segment (one char per segment);
* normalise into a tiny :class:`MathInline` with pre-filled MathML tree
  (no latex2mathml round-trip needed);
* render through the math backend, picking up the profile's
  ``math_symbol['(']`` cells rather than the prose punctuation table.

These tests assert the **observable** behaviour end-to-end: no
``UNKNOWN_PUNCT`` warnings for ``(`` / ``)`` / ``+`` etc., and the
rendered output contains the math cells in the right positions.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.ir.inline import MathInline


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    return Pipeline(profile="cn_current")


def _warn_codes(result) -> list[tuple[str, str]]:
    warns = list(result.warnings.warnings)
    return [(w.code, w.surface or "") for w in warns]


class TestFillinBlank:
    """`(   )` blanks: common at the end of multiple-choice items."""

    def test_no_unknown_punct_for_half_paren(self, pipe):
        r = pipe.translate_text("选项是(   )")
        assert not any(
            code == "UNKNOWN_PUNCT" and surf in ("(", ")")
            for code, surf in _warn_codes(r)
        )

    def test_both_parens_rendered(self, pipe):
        r = pipe.translate_text("选项是(   )")
        out = r.render()
        # half-width ( = math_symbol → ⠣ (1,2,6); ) → ⠜ (3,4,5)
        assert "⠣" in out
        assert "⠜" in out
        # three blank cells in the middle
        assert "⠣⠀⠀⠀⠜" in out

    def test_children_have_two_mathinline_around_space(self, pipe):
        r = pipe.translate_text("选项是(   )")
        kids = r.ir.blocks[0].children
        # `(` and `)` should each be a separate MathInline
        math_inlines = [c for c in kids if isinstance(c, MathInline)]
        assert len(math_inlines) == 2
        assert [m.surface for m in math_inlines] == ["(", ")"]
        # math is pre-filled, no dependency on latex2mathml
        for m in math_inlines:
            assert m.source == "mathml"
            assert m.math is not None


class TestSubquestionNumbering:
    """`(1)求 (2)设` sub-question numbering scenario."""

    def test_subquestion_rendered_correctly(self, pipe):
        r = pipe.translate_text("(1)求")
        out = r.render()
        # ( + number sign + 1 + ) — the math segment does not pollute the
        # state machine of the adjacent Number
        assert out.startswith("⠣⠼⠁⠜")

    def test_no_unknown_punct(self, pipe):
        r = pipe.translate_text("(1)求 (2)设")
        codes = _warn_codes(r)
        assert not any(c == "UNKNOWN_PUNCT" and s in ("(", ")") for c, s in codes)


class TestBareOperatorsBetweenLetters:
    """Bare `+` `=` outside any `$...$` wrap: Current Chinese Braille
    routes them through the math symbol cells."""

    def test_plus_between_letters(self, pipe):
        r = pipe.translate_text("a+b")
        out = r.render()
        # + → math_symbol c_235 = ⠖
        assert "⠖" in out
        assert not any(
            c == "UNKNOWN_PUNCT" and s == "+" for c, s in _warn_codes(r)
        )

    def test_equals_between_letter_and_digit(self, pipe):
        r = pipe.translate_text("x=5")
        out = r.render()
        # = → math_symbol c_2356 = ⠶
        assert "⠶" in out
        assert not any(
            c == "UNKNOWN_PUNCT" and s == "=" for c, s in _warn_codes(r)
        )

    def test_minus_after_equals_no_phantom_space(self, pipe):
        # Fix for `x=-5`: previously `-` went through punct, but the
        # Chinese punctuation table only has — (em dash), not - (hyphen),
        # so the lookup missed and emitted UNKNOWN_PUNCT + a blank cell,
        # which looked like a stray space after =. After the fix, `-` goes
        # through math_op and renders as the math minus ⠤ (c_36), flush
        # against the ⠶.
        r = pipe.translate_text("x=-5")
        out = r.render()
        # Full render: ⠰⠭ (x) + ⠶ (=) + ⠤ (-) + ⠼⠑ (5), with no blank cell
        # in between.
        assert out == "⠰⠭⠶⠤⠼⠑"
        assert not any(
            c == "UNKNOWN_PUNCT" and s == "-" for c, s in _warn_codes(r)
        )

    def test_minus_after_equals_inside_dollar_math(self, pipe):
        # `$x=-5$` goes through latex2mathml parsing, so the whole span
        # lands in one MathML tree and the math backend applies
        # space_before across tokens. Expected: one blank between x and =
        # (the rel space_before of =), and no space between = and - (here -
        # is a unary minus, which absorbs its own space_before).
        r = pipe.translate_text("$x=-5$")
        out = r.render()
        # ⠰⠭ + ⠀ + ⠶ + ⠤ + ⠼⠑
        assert out == "⠰⠭⠀⠶⠤⠼⠑"

    def test_binary_minus_inside_dollar_math_keeps_space(self, pipe):
        # `$a-b$`: - is a binary minus with operand a in front, so it
        # should keep its space_before. This case guards against the
        # unary-suppression rule mistakenly catching the minus scenario.
        r = pipe.translate_text("$a-b$")
        out = r.render()
        # ⠰⠁ + ⠀ + ⠤ + ⠰⠃
        assert out == "⠰⠁⠀⠤⠰⠃"


class TestFullWidthUnaffected:
    """Full-width `（）` still go through the Chinese punctuation table;
    their behaviour must not change."""

    def test_full_width_parens_use_punct_cells(self, pipe):
        r = pipe.translate_text("选项是（   ）")
        out = r.render()
        # full-width （ → ⠰⠄, ） → ⠠⠆
        assert "⠰⠄" in out
        assert "⠠⠆" in out
        # the half-width cells must not appear
        assert "⠣" not in out
        assert "⠜" not in out


class TestProtectedMathUnaffected:
    """Formulas wrapped in `$...$`: these already go through the math
    frontend, and their behaviour is unchanged."""

    def test_paren_inside_dollar_math_still_works(self, pipe):
        r = pipe.translate_text(
            '值$<math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></math>$是'
        )
        out = r.render()
        # the ( and ) inside f(x) still go through the math backend, with
        # cells identical to before
        assert "⠣" in out
        assert "⠜" in out
        assert not any(c.startswith("MATH_") for c, _ in _warn_codes(r))
