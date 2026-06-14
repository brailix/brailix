"""Direct tests for the shared math-text lexer (``_atoms``) — the one
implementation the OMML / MTEF / EQ-field adapters share. It previously had
no direct coverage, only indirect exercise through those adapters, despite
being the widest-blast-radius helper in the math frontend."""

from __future__ import annotations

from brailix.frontend.math.adapters._atoms import (
    classify_math_token,
    is_identifier_char,
    tokenize_math_text,
)


def _atoms(text, **kw):
    return [(e.tag, e.text) for e in tokenize_math_text(text, **kw)]


class TestIsIdentifierChar:
    def test_ascii_and_greek_letters_are_identifiers(self):
        assert is_identifier_char("x")
        assert is_identifier_char("Z")
        assert is_identifier_char("α")
        assert is_identifier_char("Ω")

    def test_operators_digits_cjk_are_not(self):
        assert not is_identifier_char("+")
        assert not is_identifier_char("∑")
        assert not is_identifier_char("3")
        assert not is_identifier_char("当")


class TestTokenizeMathText:
    def test_digit_run_and_decimal_coalesce(self):
        assert _atoms("123") == [("mn", "123")]
        assert _atoms("3.5") == [("mn", "3.5")]

    def test_comma_grouping_default_on(self):
        assert _atoms("1,234") == [("mn", "1,234")]

    def test_comma_grouping_off_splits(self):
        # EQ-field passes comma_in_number=False: "," is an arg separator.
        assert _atoms("1,2", comma_in_number=False) == [
            ("mn", "1"),
            ("mo", ","),
            ("mn", "2"),
        ]

    def test_identifier_run_coalesces(self):
        assert _atoms("sin") == [("mi", "sin")]

    def test_mixed_run_splits_by_class(self):
        assert _atoms("2x+1") == [
            ("mn", "2"),
            ("mi", "x"),
            ("mo", "+"),
            ("mn", "1"),
        ]

    def test_cjk_run_becomes_mtext_not_per_char_mo(self):
        # Natural-language letters coalesce into <mtext> so the backend
        # routes them through the inline-text translator (the 当…时 case).
        assert _atoms("当x>0时") == [
            ("mtext", "当"),
            ("mi", "x"),
            ("mo", ">"),
            ("mn", "0"),
            ("mtext", "时"),
        ]

    def test_whitespace_dropped(self):
        assert _atoms("a b") == [("mi", "a"), ("mi", "b")]


class TestClassifyMathToken:
    def test_number_and_identifier(self):
        assert classify_math_token("123") == "mn"
        assert classify_math_token("3.5") == "mn"
        assert classify_math_token("sin") == "mi"
        assert classify_math_token("x") == "mi"

    def test_lone_operator_is_mo(self):
        assert classify_math_token("+") == "mo"

    def test_lone_cjk_letter_is_mtext_not_mo(self):
        # A single CJK letter must not become <mo> (→ MATH_UNKNOWN_SYMBOL);
        # it is natural-language text.
        assert classify_math_token("当") == "mtext"

    def test_mixed_and_empty_are_mtext(self):
        assert classify_math_token("12a") == "mtext"
        assert classify_math_token("") == "mtext"
