"""Non-standard characters (full-width variants, invisible zero-width chars)
are flagged with one actionable hint everywhere — prose, math, chemistry —
and never silently folded: ＝ (U+FF1D) and = (U+003D) are different code
points, so the translator names the problem instead of papering over it."""

from __future__ import annotations

from brailix import Pipeline
from brailix.backend._chars import nonstandard_char_hint


class TestHint:
    def test_fullwidth_names_its_halfwidth(self):
        assert nonstandard_char_hint("＝") == (
            "full-width '＝' (U+FF1D); use the half-width '='"
        )

    def test_fullwidth_space(self):
        assert "normal space" in (nonstandard_char_hint("　") or "")

    def test_zero_width(self):
        assert "zero-width" in (nonstandard_char_hint("​") or "")

    def test_ordinary_char_has_no_hint(self):
        assert nonstandard_char_hint("=") is None
        assert nonstandard_char_hint("x") is None
        assert nonstandard_char_hint("ab") is None  # only single chars classify


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

    def test_math_fullwidth_identifier_hints_halfwidth(self):
        hits = self._hits("$Ｘ + 1$", "MATH_UNKNOWN_IDENTIFIER")
        assert hits and "half-width" in hits[0].message
