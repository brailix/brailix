"""Tests for :mod:`brailix.frontend.math.adapters.eq_field`.

The EQ field adapter parses Word's legacy ``eq`` field codes —
``\\f(1,2)``, ``\\b\\lc\\{(...)``, ``\\a\\co2(a,b,c,d)`` and the rest of
the 10-switch family — into MathML. These tests pin each switch's
mapping plus the parser's escape / nesting behaviour, since downstream
braille rendering is byte-for-byte sensitive to the MathML shape.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend.math.adapters.eq_field import EqFieldMathSourceAdapter


def _mathml(formula: str) -> str:
    return EqFieldMathSourceAdapter().to_mathml(formula)


def _root(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def _local(tag: str) -> str:
    """Strip the namespace prefix ET prepends when xmlns is set."""
    if tag.startswith("{"):
        return tag.partition("}")[2]
    return tag


class TestEntryPoint:
    def test_eq_prefix_optional(self):
        with_prefix = _mathml("eq \\f(1,2)")
        without = _mathml("\\f(1,2)")
        assert "<mfrac>" in with_prefix
        assert "<mfrac>" in without

    def test_eq_prefix_case_insensitive(self):
        for prefix in ("eq ", "EQ ", "Eq "):
            out = _mathml(f"{prefix}\\f(1,2)")
            assert "<mfrac>" in out

    def test_empty_input_yields_merror(self):
        out = _mathml("")
        assert "<merror" in out

    def test_unknown_switch_does_not_crash(self):
        # \z is not a real switch — should still produce valid MathML
        # with a placeholder rather than blowing up.
        out = _mathml("\\z(x)")
        assert "<math" in out
        root = _root(out)
        assert _local(root.tag) == "math"

    def test_root_is_math_with_namespace(self):
        out = _mathml("\\f(1,2)")
        # The xmlns attribute is set on the serialised root; ET hoists
        # it into the tag namespace when parsed back.
        assert 'xmlns="http://www.w3.org/1998/Math/MathML"' in out


class TestFraction:
    def test_simple_fraction(self):
        out = _mathml("\\f(1,2)")
        assert "<mfrac>" in out
        assert "<mn>1</mn>" in out
        assert "<mn>2</mn>" in out

    def test_fraction_with_pi(self):
        # Sample from real document.
        out = _mathml("\\f(π,4)")
        assert "<mfrac>" in out
        assert "<mi>π</mi>" in out
        assert "<mn>4</mn>" in out

    def test_fraction_with_compound_numerator(self):
        out = _mathml("\\f(3π,8)")
        # Both 3 and π should appear inside the numerator mrow.
        assert "<mn>3</mn>" in out
        assert "<mi>π</mi>" in out

    def test_nested_fraction(self):
        out = _mathml("\\f(\\f(1,2),3)")
        # Outer fraction has a nested fraction in its numerator.
        assert out.count("<mfrac>") == 2


class TestRadical:
    def test_square_root(self):
        out = _mathml("\\r(x)")
        assert "<msqrt>" in out
        assert "<mi>x</mi>" in out
        assert "<mroot>" not in out

    def test_nth_root(self):
        out = _mathml("\\r(3,x)")
        assert "<mroot>" in out


class TestBrackets:
    def test_default_parens(self):
        out = _mathml("\\b(x)")
        assert '<mo fence="true">(</mo>' in out
        assert '<mo fence="true">)</mo>' in out

    def test_square_brackets(self):
        out = _mathml("\\b\\lc\\[\\rc\\](x)")
        assert '<mo fence="true">[</mo>' in out
        assert '<mo fence="true">]</mo>' in out

    def test_lc_only_yields_no_right_bracket(self):
        # The cases-style ``\b\lc\{(...)`` must give "{ ... " with no
        # right brace. Auto-mirroring would break piecewise notation.
        out = _mathml("\\b\\lc\\{(x)")
        assert '<mo fence="true">{</mo>' in out
        assert '<mo fence="true">}</mo>' not in out

    def test_rc_only_yields_no_left_bracket(self):
        out = _mathml("\\b\\rc\\}(x)")
        assert '<mo fence="true">}</mo>' in out
        assert '<mo fence="true">{</mo>' not in out

    def test_bc_uses_same_char_both_sides(self):
        out = _mathml("\\b\\bc\\|(x)")
        # Two pipe fences, one on each side.
        assert out.count('<mo fence="true">|</mo>') == 2


class TestArray:
    def test_single_column_array(self):
        # ``\co1`` with two cells = 2 rows.
        out = _mathml("\\a\\co1(a,b)")
        assert "<mtable" in out
        assert out.count("<mtr>") == 2
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out

    def test_two_column_array_row_major(self):
        # ``\co2`` with four cells = 2x2; row-major fill.
        out = _mathml("\\a\\co2(a,b,c,d)")
        assert out.count("<mtr>") == 2
        # Each row has 2 mtds.
        assert out.count("<mtd>") == 4

    def test_array_default_column_count_is_one(self):
        out = _mathml("\\a(a,b,c)")
        assert out.count("<mtr>") == 3

    def test_alignment_attribute(self):
        out_l = _mathml("\\a\\al\\co2(a,b)")
        assert 'columnalign="left"' in out_l
        out_c = _mathml("\\a\\ac\\co2(a,b)")
        assert 'columnalign="center"' in out_c
        out_r = _mathml("\\a\\ar\\co2(a,b)")
        assert 'columnalign="right"' in out_r

    def test_vs_hs_options_are_consumed_without_crashing(self):
        # The vertical/horizontal-space hints are visual-only; we
        # accept and discard them so the rest of the field parses.
        out = _mathml("\\a\\vs4\\hs6\\co1(a,b)")
        assert "<mtable" in out


class TestPiecewise:
    """The motivating example: cases-style piecewise function from the
    real ``周练习6-5.4学生版.docx`` problem 15."""

    def test_problem_15_piecewise_function(self):
        out = _mathml(
            "eq \\b\\lc\\{(\\a\\vs4\\al\\co1("
            "sin x，x≥0，,x＋2，x<0，))"
        )
        # Left brace, no right.
        assert '<mo fence="true">{</mo>' in out
        assert '<mo fence="true">}</mo>' not in out
        # Two rows of one column each.
        assert "<mtable" in out
        assert out.count("<mtr>") == 2
        # Branch contents survived.
        assert "<mi>sin</mi>" in out or "<mi>s</mi>" in out  # tokenization
        assert "<mi>x</mi>" in out


class TestIntegral:
    def test_default_is_integral(self):
        out = _mathml("\\i(0,1,x)")
        # Integral sign present, sub/superscript layout (not under/over).
        assert "∫" in out
        assert "<msubsup>" in out

    def test_summation_switch(self):
        out = _mathml("\\i\\su(i=1,n,i)")
        assert "∑" in out
        # Summation uses under/over for limits.
        assert "<munderover>" in out

    def test_product_switch(self):
        out = _mathml("\\i\\pr(i=1,n,i)")
        assert "∏" in out
        assert "<munderover>" in out

    def test_custom_operator_via_fc(self):
        out = _mathml("\\i\\fc\\∮(0,1,x)")
        assert "∮" in out

    def test_only_lower_limit(self):
        out = _mathml("\\i(0,,x)")
        assert "<msub>" in out
        assert "<msubsup>" not in out


class TestScript:
    def test_up_becomes_superscript(self):
        out = _mathml("\\s\\up6(2)")
        assert "<msup>" in out
        assert "<mn>2</mn>" in out

    def test_do_becomes_subscript(self):
        out = _mathml("\\s\\do6(n)")
        assert "<msub>" in out
        assert "<mi>n</mi>" in out


class TestBox:
    def test_default_draws_all_four_sides(self):
        out = _mathml("\\x(x)")
        assert 'notation="box"' in out

    def test_top_only(self):
        out = _mathml("\\x\\to(x)")
        assert 'notation="top"' in out

    def test_top_and_bottom(self):
        out = _mathml("\\x\\to\\bo(x)")
        # Sides are sorted alphabetically for deterministic output.
        assert 'notation="bottom top"' in out


class TestOverstrike:
    def test_single_item_emits_just_content(self):
        out = _mathml("\\o(x)")
        # No mover needed for a single item.
        assert "<mover" not in out
        assert "<mi>x</mi>" in out

    def test_two_items_become_mover(self):
        out = _mathml("\\o(x,y)")
        assert "<mover" in out


class TestDisplace:
    def test_zero_displacement_emits_nothing(self):
        # ``\d`` with no sub-options is a no-op — no mspace needed.
        # Whitespace separates ``\d`` from the next text so the
        # tokenizer doesn't greedy-merge them into one switch.
        out = _mathml("a \\d b")
        assert "<mspace" not in out
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out

    def test_forward_displacement_emits_mspace(self):
        out = _mathml("a\\d\\fo10 b")
        assert 'mspace width="10pt"' in out

    def test_backward_displacement_negative_width(self):
        out = _mathml("a\\d\\ba5 b")
        assert 'mspace width="-5pt"' in out


class TestList:
    def test_list_flattens_items(self):
        out = _mathml("\\l(a,b,c)")
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out
        assert "<mi>c</mi>" in out


class TestEscaping:
    def test_escaped_comma_stays_literal(self):
        # \, inside an arg should NOT split the arg.
        out = _mathml("\\f(a\\,b,c)")
        # The numerator should contain both a and b as text characters.
        # Tokenizer turns them into mi atoms; both must be present.
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out
        assert "<mi>c</mi>" in out

    def test_escaped_paren_stays_literal(self):
        out = _mathml("\\f(\\(x,1)")
        # The numerator contains a literal '(' character — emitted as mo.
        assert "<mo>(</mo>" in out

    def test_escaped_backslash_stays_literal(self):
        out = _mathml("\\f(\\\\,1)")
        # Literal backslash survives — split as mo.
        assert "<mo>\\</mo>" in out


class TestTokenClassification:
    def test_digits_become_mn(self):
        out = _mathml("\\f(42,1)")
        assert "<mn>42</mn>" in out

    def test_letters_become_mi(self):
        out = _mathml("\\f(x,y)")
        assert "<mi>x</mi>" in out
        assert "<mi>y</mi>" in out

    def test_operator_becomes_mo(self):
        out = _mathml("\\f(2x+1,3)")
        assert "<mn>2</mn>" in out
        assert "<mi>x</mi>" in out
        assert "<mo>+</mo>" in out
        assert "<mn>1</mn>" in out

    def test_greek_pi_classified_as_identifier(self):
        out = _mathml("\\f(π,2)")
        assert "<mi>π</mi>" in out
