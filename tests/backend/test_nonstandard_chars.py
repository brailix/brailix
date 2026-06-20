"""Non-standard characters (full-width variants, invisible zero-width chars)
are flagged with one actionable hint everywhere — prose, math, chemistry —
and never silently folded: ＝ (U+FF1D) and = (U+003D) are different code
points, so the translator names the problem instead of papering over it."""

from __future__ import annotations

from brailix import Pipeline
from brailix.core.chars import (
    INVISIBLE_CPS,
    fold_fullwidth,
    nonstandard_char_hint,
)


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

    def test_word_joiner_and_soft_hyphen_are_invisible(self):
        # U+2060 / U+00AD now come from the shared INVISIBLE_CPS set, so the
        # hint fires for both (built via chr() — the chars are invisible).
        assert "invisible" in (nonstandard_char_hint(chr(0x2060)) or "")
        assert "invisible" in (nonstandard_char_hint(chr(0x00AD)) or "")


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


class TestFoldFullwidth:
    """fold_fullwidth is the single authority for the half-width form — the
    knowledge an editor consumes so it never re-derives the FF01..FF5E offset
    or the ideographic-space mapping."""

    def test_fullwidth_digit(self):
        assert fold_fullwidth("０") == "0"
        assert fold_fullwidth("９") == "9"

    def test_fullwidth_letter(self):
        assert fold_fullwidth("Ｘ") == "X"
        assert fold_fullwidth("ｚ") == "z"

    def test_fullwidth_operator(self):
        assert fold_fullwidth("＝") == "="
        assert fold_fullwidth("＋") == "+"

    def test_fullwidth_punctuation_also_folds(self):
        # The raw Unicode fact: the full-width comma *does* have a half-width
        # form.  Whether to apply it is the caller's policy (prose keeps
        # full-width Chinese punctuation; callers simply never feed it here).
        assert fold_fullwidth("，") == ","

    def test_ideographic_space(self):
        assert fold_fullwidth("　") == " "

    def test_halfwidth_returns_none(self):
        assert fold_fullwidth("a") is None
        assert fold_fullwidth("=") is None
        assert fold_fullwidth(" ") is None

    def test_non_single_char_returns_none(self):
        assert fold_fullwidth("") is None
        assert fold_fullwidth("ＡＢ") is None


class TestInvisibleCps:
    def test_contains_word_joiner_and_soft_hyphen(self):
        # The two that previously drifted between this set and a downstream
        # source normalizer.
        assert 0x2060 in INVISIBLE_CPS  # word joiner
        assert 0x00AD in INVISIBLE_CPS  # soft hyphen

    def test_contains_the_usual_zero_width_set(self):
        assert {0x200B, 0x200C, 0x200D, 0xFEFF} <= INVISIBLE_CPS
