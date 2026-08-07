"""Non-standard characters (full-width variants, invisible zero-width chars)
are flagged with one actionable hint everywhere — prose, math, chemistry —
and never silently folded: ＝ (U+FF1D) and = (U+003D) are different code
points, so the translator names the problem instead of papering over it."""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.core.chars import INVISIBLE_CPS, nonstandard_char_hint


class TestHint:
    """Exact hint wording — the actionable format an editor shows verbatim.
    WHEN a hint fires (exactly the foldable ∪ invisible classes, single
    chars only) is contract-tested over all code points in
    tests/crosshair/contracts.py::check_nonstandard_hint_consistency."""

    def test_fullwidth_names_its_halfwidth(self):
        assert nonstandard_char_hint("＝") == (
            "full-width '＝' (U+FF1D); use the half-width '='"
        )

    def test_fullwidth_space(self):
        assert "normal space" in (nonstandard_char_hint("　") or "")


class TestProseAndMathSurfaceTheHint:
    @staticmethod
    def _hits(text, code):
        res = Pipeline(profile="cn_current").translate_text(text)
        return res.warnings.by_code(code)

    def test_prose_fullwidth_symbol_hints_halfwidth(self):
        hits = self._hits("得分＝95", "UNKNOWN_PUNCT")
        assert hits and "half-width" in hits[0].message

    def test_prose_zero_width_flagged(self):
        hits = self._hits("a​b", "UNKNOWN_NODE")
        assert hits and "zero-width" in hits[0].message

    @pytest.mark.requires("latex2mathml")
    def test_math_fullwidth_identifier_hints_halfwidth(self):
        hits = self._hits("$Ｘ + 1$", "MATH_UNKNOWN_IDENTIFIER")
        assert hits and "half-width" in hits[0].message


class TestProseMathSymbolWarning:
    """A math symbol with no prose rule that the segmenter does NOT auto-route
    to the math path (the ASCII tilde ~ is the standing example) gets the
    actionable PROSE_MATH_SYMBOL code, not a bare UNKNOWN_PUNCT. Real math
    symbols (∈) route to the math backend instead, so they never reach here;
    full-width forms (＝) keep their "use the half-width form" hint."""

    @staticmethod
    def _translate(text):
        return Pipeline(profile="cn_current").translate_text(text)

    def test_tilde_warns_as_math_symbol(self):
        res = self._translate("1~10")
        hits = res.warnings.by_code("PROSE_MATH_SYMBOL")
        assert hits and hits[0].surface == "~"
        assert not res.warnings.by_code("UNKNOWN_PUNCT")

    def test_element_of_routes_to_math_no_prose_warning(self):
        # ∈ is auto-routed and translated (⠐⠪), so it never hits the prose
        # unknown path — neither PROSE_MATH_SYMBOL nor UNKNOWN_*.
        res = self._translate("x∈A")
        assert not res.warnings.by_code("PROSE_MATH_SYMBOL")
        assert not res.warnings.by_code("UNKNOWN_PUNCT")
        assert not res.warnings.by_code("UNKNOWN_NODE")

    def test_fullwidth_operator_keeps_halfwidth_hint(self):
        # ＝ is category Sm but full-width: it keeps the UNKNOWN_PUNCT
        # "use the half-width form" hint, never reclassified as a bare math
        # symbol.
        res = self._translate("得分＝95")
        assert not res.warnings.by_code("PROSE_MATH_SYMBOL")
        hits = res.warnings.by_code("UNKNOWN_PUNCT")
        assert hits and "half-width" in hits[0].message


@pytest.mark.requires("latex2mathml")
class TestMathFullwidthPunctuation:
    """A full-width comma / paren / semicolon (``，（）；``) typed via a Chinese
    IME inside a formula is wrong input. The math backend must NOT borrow the
    prose punctuation table to render it as the Chinese mark — it warns (use
    the half-width form) and marks the spot like any other unknown symbol,
    exactly as a full-width operator (``＝`` ``＋``) already does. Half-width
    punctuation keeps its ordinary rendering — the gate is full-width-only."""

    @staticmethod
    def _translate(text):
        return Pipeline(profile="cn_current").translate_text(text)

    def test_fullwidth_comma_warns_and_is_not_translated(self):
        res = self._translate("$(x，y)$")
        hits = res.warnings.by_code("MATH_UNKNOWN_IDENTIFIER")
        assert hits and "half-width" in hits[0].message
        # Refused, not rendered: it must NOT produce the comma cell ⠐ (c_5)
        # that the correctly-typed half-width comma yields.
        assert "⠐" not in res.render()

    def test_fullwidth_semicolon_and_paren_warn(self):
        for text in ("$x；y$", "$（x）$"):
            assert self._translate(text).warnings.by_code(
                "MATH_UNKNOWN_IDENTIFIER"
            ), text

    def test_halfwidth_comma_renders_dot5_chinese_comma(self):
        # The correctly-typed half-width comma resolves via the symbol table to
        # the dot-5 Chinese comma ⠐ (c_5) — the Chinese math convention — and
        # raises no warning. (Full-width input is what gets refused.)
        res = self._translate("$(x,y)$")
        assert not res.warnings.by_code("MATH_UNKNOWN_IDENTIFIER")
        assert not res.warnings.by_code("MATH_UNKNOWN_SYMBOL")
        assert "⠐" in res.render()  # c_5 (dot-5), not c_2

    def test_halfwidth_semicolon_still_renders_via_prose_table(self):
        # A half-width mark only the prose table defines keeps working — only
        # full-width input is refused.
        res = self._translate("$(x;y)$")
        assert not res.warnings.by_code("MATH_UNKNOWN_SYMBOL")
        assert "⠆" in res.render()  # c_23 semicolon


# fold_fullwidth's exact mapping (the 0xFEE0 relation, the ideographic
# space, None everywhere else) and is_math_symbol's "category Sm, exactly"
# line are contract-tested over all code points in
# tests/crosshair/contracts.py; INVISIBLE_CPS below is the design decision
# those contracts consume, so its contents stay pinned here.


class TestInvisibleCps:
    def test_contains_word_joiner_and_soft_hyphen(self):
        # The two that previously drifted between this set and a downstream
        # source normalizer.
        assert 0x2060 in INVISIBLE_CPS  # word joiner
        assert 0x00AD in INVISIBLE_CPS  # soft hyphen

    def test_contains_the_usual_zero_width_set(self):
        assert {0x200B, 0x200C, 0x200D, 0xFEFF} <= INVISIBLE_CPS
