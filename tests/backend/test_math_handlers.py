"""Per-handler granular tests for the math backend.

Each handler in ``backend/math.py`` has its own behavioural quirks
(spacing, number-sign reset, lookup chain ordering, structural markers).
These tests pin those behaviours one assertion at a time so a refactor
that drops a side effect is caught immediately.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import (
    translate,
)
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.core.span import Span
from brailix.ir.inline import MathInline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def mml(xml: str) -> ET.Element:
    from brailix.frontend.math.normalizer import normalize

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


# ---------------------------------------------------------------------------
# Math root handler
# ---------------------------------------------------------------------------


class TestMathRoot:
    def test_empty_math_no_cells(self, profile):
        cells, _ = emit(mml("<math></math>"), profile)
        assert cells == []

    def test_math_with_multiple_children(self, profile):
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"), profile
        )
        # 56 + a (1) + blank + 235 + 56 + b (12) = 6 cells.
        # The math root just iterates children — no extra wrapping.
        assert any(c.role == "math_identifier" for c in cells)
        assert any(c.role == "math_op" for c in cells)

    def test_math_root_doesnt_emit_extra_markers(self, profile):
        cells, _ = emit(mml("<math><mi>x</mi></math>"), profile)
        # Should be exactly 2 cells: 56 + 1346.
        assert len(cells) == 2


# ---------------------------------------------------------------------------
# mi handler — letter prefix table
# ---------------------------------------------------------------------------


class TestMiLetterPrefixes:
    @pytest.mark.parametrize(
        "letter,expected_dots",
        [
            ("a", [(5, 6), (1,)]),
            ("b", [(5, 6), (1, 2)]),
            ("z", [(5, 6), (1, 3, 5, 6)]),
        ],
    )
    def test_latin_lowercase(self, profile, letter, expected_dots):
        cells, _ = emit(mml(f"<math><mi>{letter}</mi></math>"), profile)
        assert [c.dots for c in cells] == expected_dots

    @pytest.mark.parametrize(
        "letter,expected_dots",
        [
            ("A", [(6,), (1,)]),
            ("Z", [(6,), (1, 3, 5, 6)]),
        ],
    )
    def test_latin_uppercase(self, profile, letter, expected_dots):
        cells, _ = emit(mml(f"<math><mi>{letter}</mi></math>"), profile)
        assert [c.dots for c in cells] == expected_dots

    @pytest.mark.parametrize(
        "letter,expected_first",
        [
            ("α", (4, 6)),
            ("β", (4, 6)),
            ("π", (4, 6)),
        ],
    )
    def test_greek_lowercase_uses_46(self, profile, letter, expected_first):
        cells, _ = emit(mml(f"<math><mi>{letter}</mi></math>"), profile)
        assert cells[0].dots == expected_first

    @pytest.mark.parametrize(
        "letter,expected_first",
        [
            ("Α", (4, 5, 6)),
            ("Π", (4, 5, 6)),
            ("Δ", (4, 5, 6)),
        ],
    )
    def test_greek_uppercase_uses_456(self, profile, letter, expected_first):
        cells, _ = emit(mml(f"<math><mi>{letter}</mi></math>"), profile)
        assert cells[0].dots == expected_first


# ---------------------------------------------------------------------------
# mn handler — digit / decimal / thousands
# ---------------------------------------------------------------------------


class TestMnDigits:
    @pytest.mark.parametrize(
        "digit,expected_dots",
        [
            ("0", (2, 4, 5)),
            ("1", (1,)),
            ("2", (1, 2)),
            ("3", (1, 4)),
            ("4", (1, 4, 5)),
            ("5", (1, 5)),
            ("6", (1, 2, 4)),
            ("7", (1, 2, 4, 5)),
            ("8", (1, 2, 5)),
            ("9", (2, 4)),
        ],
    )
    def test_single_digit(self, profile, digit, expected_dots):
        cells, _ = emit(mml(f"<math><mn>{digit}</mn></math>"), profile)
        # number_sign first, then the digit cell.
        assert cells[0].role == "number_sign"
        assert cells[1].dots == expected_dots

    def test_decimal_point_cell(self, profile):
        cells, _ = emit(mml("<math><mn>0.5</mn></math>"), profile)
        dp = next(c for c in cells if c.role == "decimal_point")
        # cn_current uses dot 2 for decimal point.
        assert dp.dots == (2,)

    def test_thousands_sep_cell(self, profile):
        cells, _ = emit(mml("<math><mn>1,234</mn></math>"), profile)
        ts = next(c for c in cells if c.role == "thousands_sep")
        assert ts.dots == (3,)

    def test_long_number_one_sign(self, profile):
        cells, _ = emit(mml("<math><mn>1234567890</mn></math>"), profile)
        ns_count = sum(1 for c in cells if c.role == "number_sign")
        assert ns_count == 1
        digits = [c for c in cells if c.role == "math_digit"]
        assert len(digits) == 10


# ---------------------------------------------------------------------------
# mo handler — per-role spacing
# ---------------------------------------------------------------------------


class TestMoSpacing:
    def test_plus_after_letter_inserts_blank(self, profile):
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"), profile
        )
        op_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        assert cells[op_idx - 1].is_blank

    def test_minus_after_letter_inserts_blank(self, profile):
        # − has space_before=true
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>−</mo><mi>b</mi></math>"), profile
        )
        op_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        assert cells[op_idx - 1].is_blank

    def test_equals_has_space_before(self, profile):
        cells, _ = emit(
            mml("<math><mi>x</mi><mo>=</mo><mi>y</mi></math>"), profile
        )
        rel_idx = next(i for i, c in enumerate(cells) if c.role == "math_rel")
        assert cells[rel_idx - 1].is_blank

    def test_in_has_both_spaces(self, profile):
        cells, _ = emit(
            mml("<math><mi>x</mi><mo>∈</mo><mi>S</mi></math>"), profile
        )
        rel_starts = [i for i, c in enumerate(cells) if c.role == "math_rel"]
        # The relation ∈ takes 2 cells.
        assert len(rel_starts) == 2
        first_rel = rel_starts[0]
        last_rel = rel_starts[-1]
        # Blank before, blank after.
        assert cells[first_rel - 1].is_blank
        assert cells[last_rel + 1].is_blank

    def test_paren_has_no_spacing(self, profile):
        cells, _ = emit(
            mml("<math><mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo></math>"),
            profile,
        )
        # No blanks around the parens.
        delim_idx = [i for i, c in enumerate(cells) if c.role == "math_delim"]
        for di in delim_idx:
            # The cells around the delim must not all be blanks.
            assert not (
                (di > 0 and cells[di - 1].is_blank)
                or (di + 1 < len(cells) and cells[di + 1].is_blank)
            )

    def test_minus_after_relation_drops_space_before(self, profile):
        # `x = -5`: a `-` following `=` is a negative sign (unary), not a
        # minus sign (binary). The `=` already took its space_before as a
        # rel; the `-` must not eat another blank, or it would read like
        # "equals BLANK minus 5" instead of "equals negative 5".
        cells, _ = emit(
            mml("<math><mi>x</mi><mo>=</mo><mo>−</mo><mn>5</mn></math>"),
            profile,
        )
        # Find the `-` cell; the `=` cell sits right before it, with no blank.
        minus_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        rel_idx = next(i for i, c in enumerate(cells) if c.role == "math_rel")
        # No blank cell between `=` and `-`.
        assert minus_idx == rel_idx + 1
        assert not cells[minus_idx - 1].is_blank
        # The `=`'s own space_before still applies (a blank sits between x and =).
        assert cells[rel_idx - 1].is_blank

    def test_minus_after_operator_drops_space_before(self, profile):
        # `x + -5`: a `-` following `+` is likewise unary (irregular but
        # legal); no blank may be inserted between `+` and `-`.
        cells, _ = emit(
            mml("<math><mi>x</mi><mo>+</mo><mo>−</mo><mn>5</mn></math>"),
            profile,
        )
        ops = [i for i, c in enumerate(cells) if c.role == "math_op"]
        assert len(ops) == 2
        plus_idx, minus_idx = ops
        # `+` and `-` are adjacent.
        assert minus_idx == plus_idx + 1

    def test_minus_after_open_paren_drops_space_before(self, profile):
        # `(-5)`: a `-` following an open parenthesis is unary. `(` has no
        # space_before to begin with; the `-` can't take space_before either
        # (there is no left operand after an open parenthesis).
        cells, _ = emit(
            mml("<math><mo>(</mo><mo>−</mo><mn>5</mn><mo>)</mo></math>"),
            profile,
        )
        minus_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        # `(` is adjacent to `-`, with no blank between them.
        assert cells[minus_idx - 1].source_text == "("
        assert not cells[minus_idx - 1].is_blank

    def test_minus_after_close_paren_keeps_space_before(self, profile):
        # `(a) - b`: a `-` after `)` is a binary minus sign (the `)` ends an
        # operand), so a blank must still be kept before the `-`.
        cells, _ = emit(
            mml(
                "<math><mo>(</mo><mi>a</mi><mo>)</mo>"
                "<mo>−</mo><mi>b</mi></math>"
            ),
            profile,
        )
        minus_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        assert cells[minus_idx - 1].is_blank
        assert cells[minus_idx - 2].source_text == ")"


# ---------------------------------------------------------------------------
# mo role assignment
# ---------------------------------------------------------------------------


class TestMoRoleAssignment:
    @pytest.mark.parametrize(
        "char,expected_role",
        [
            ("+", "math_op"),
            ("−", "math_op"),
            ("=", "math_rel"),
            ("∈", "math_rel"),
            ("(", "math_delim"),
            (")", "math_delim"),
            (",", "math_punct"),
            ("∑", "math_big_op"),
            ("∫", "math_big_op"),
            # role=shape is a valid role in the schema but cn_current
            # has no shape entries (Chinese math text writes 三角形ABC
            # instead of △ABC). Future profiles can opt in.
        ],
    )
    def test_role_per_symbol(self, profile, char, expected_role):
        cells, _ = emit(mml(f"<math><mo>{char}</mo></math>"), profile)
        roles_seen = {c.role for c in cells if c.role != "space"}
        assert expected_role in roles_seen


# ---------------------------------------------------------------------------
# Number-sign reset semantics
# ---------------------------------------------------------------------------


class TestNumberSignReset:
    def test_op_resets(self, profile):
        cells, _ = emit(
            mml("<math><mn>1</mn><mo>+</mo><mn>2</mn></math>"), profile
        )
        ns = sum(1 for c in cells if c.role == "number_sign")
        assert ns == 2

    def test_rel_resets(self, profile):
        cells, _ = emit(
            mml("<math><mn>1</mn><mo>=</mo><mn>2</mn></math>"), profile
        )
        ns = sum(1 for c in cells if c.role == "number_sign")
        assert ns == 2

    # role=shape number-sign reset behavior is in the contract but
    # cn_current has no shape entries (Chinese math writes 三角形ABC
    # instead of △ABC). Test resurfaces when a profile registers
    # shape symbols.

    def test_delim_does_not_reset(self, profile):
        # Delim doesn't reset, but there's no easy way to keep the
        # number-sign state across a delim because there's no mn
        # adjacent. Let's check the inverse: a digit followed by a
        # delim followed by another digit — both digit runs need
        # their own number sign because... actually delim DOESN'T
        # reset, so the second mn after `)` should not get a sign
        # IF the state survived. But our state machine resets per
        # mn end, so this might not be visible. Let me write the
        # test for delim NOT resetting via mrow boundary.
        cells, _ = emit(
            mml("<math><mn>1</mn><mo>(</mo><mn>2</mn></math>"), profile
        )
        # The 2 gets its own number-sign — but that's because <mn> ends
        # by setting need_number_sign=False, and the delim doesn't
        # reset it back to True. So in fact the 2 should NOT get a sign.
        # Wait — but mn sets need_number_sign=False at the END, after
        # the first mn finishes. So entering the second mn, the state
        # is False — no sign. Let's see.
        ns = sum(1 for c in cells if c.role == "number_sign")
        # Delim doesn't reset state — only ONE number_sign.
        assert ns == 1

    def test_punct_does_not_reset(self, profile):
        # Same logic for punct.
        cells, _ = emit(
            mml("<math><mn>1</mn><mo>,</mo><mn>2</mn></math>"), profile
        )
        ns = sum(1 for c in cells if c.role == "number_sign")
        assert ns == 1


# ---------------------------------------------------------------------------
# Structure markers — exact dot patterns
# ---------------------------------------------------------------------------


class TestStructureMarkers:
    def test_fraction_open_dots(self, profile):
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
                "<mi>c</mi></mfrac></math>"
            ),
            profile,
        )
        op = next(c for c in cells if c.role == "math_fraction_open")
        assert op.dots == (2, 3)

    def test_fraction_bar_dots(self, profile):
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mi>y</mi></mfrac></math>"), profile
        )
        bar = next(c for c in cells if c.role == "math_fraction_bar")
        assert bar.dots == (1, 2, 5, 6)

    def test_fraction_close_dots(self, profile):
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
                "<mi>c</mi></mfrac></math>"
            ),
            profile,
        )
        close = next(c for c in cells if c.role == "math_fraction_close")
        assert close.dots == (5, 6)

    def test_sqrt_open_dots(self, profile):
        cells, _ = emit(mml("<math><msqrt><mi>x</mi></msqrt></math>"), profile)
        op = next(c for c in cells if c.role == "math_sqrt_open")
        assert op.dots == (1, 4, 6)

    def test_sqrt_indicator_dots(self, profile):
        cells, _ = emit(mml("<math><msqrt><mi>x</mi></msqrt></math>"), profile)
        ind = next(c for c in cells if c.role == "math_sqrt_indicator")
        assert ind.dots == (1, 5, 6)

    def test_sqrt_close_dots(self, profile):
        cells, _ = emit(mml("<math><msqrt><mi>x</mi></msqrt></math>"), profile)
        close = next(c for c in cells if c.role == "math_sqrt_close")
        assert close.dots == (1, 4, 5, 6)

    def test_script_sub_dots(self, profile):
        cells, _ = emit(
            mml("<math><msub><mi>x</mi><mi>n</mi></msub></math>"), profile
        )
        sub = next(c for c in cells if c.role == "math_subscript")
        assert sub.dots == (1, 6)

    def test_script_sup_dots(self, profile):
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mi>n</mi></msup></math>"), profile
        )
        sup = next(c for c in cells if c.role == "math_superscript")
        assert sup.dots == (3, 4)

    def test_script_close_dots(self, profile):
        cells, _ = emit(
            mml(
                "<math><msup><mi>x</mi>"
                "<mrow><mi>n</mi><mo>+</mo><mn>1</mn></mrow>"
                "</msup></math>"
            ),
            profile,
        )
        close = next(c for c in cells if c.role == "math_script_close")
        assert close.dots == (1, 5, 6)

    def test_big_op_script_prefix_dots(self, profile):
        cells, _ = emit(
            mml("<math><msub><mo>∫</mo><mn>0</mn></msub></math>"), profile
        )
        prefix = next(c for c in cells if c.role == "math_big_op_script_prefix")
        assert prefix.dots == (4, 6)

    def test_function_prefix_dots(self, profile):
        cells, _ = emit(mml("<math><mi>sin</mi></math>"), profile)
        prefix = next(c for c in cells if c.role == "math_function_prefix")
        assert prefix.dots == (1, 2, 4, 6)


# ---------------------------------------------------------------------------
# Letter prefixes — exact dot patterns
# ---------------------------------------------------------------------------


class TestLetterPrefixes:
    def test_latin_lower_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>x</mi></math>"), profile)
        assert cells[0].dots == (5, 6)

    def test_latin_upper_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>X</mi></math>"), profile)
        assert cells[0].dots == (6,)

    def test_greek_lower_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>α</mi></math>"), profile)
        assert cells[0].dots == (4, 6)

    def test_greek_upper_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>Α</mi></math>"), profile)
        assert cells[0].dots == (4, 5, 6)


# ---------------------------------------------------------------------------
# Antoine fraction edge cases
# ---------------------------------------------------------------------------


class TestAntoineEdges:
    @pytest.mark.parametrize(
        "den,expected",
        [
            ("1", (2,)),
            ("2", (2, 3)),
            ("3", (2, 5)),
            ("4", (2, 5, 6)),
            ("5", (2, 6)),
            ("6", (2, 3, 5)),
            ("7", (2, 3, 5, 6)),
            ("8", (2, 3, 6)),
            ("9", (3, 5)),
            ("0", (3, 5, 6)),
        ],
    )
    def test_antoine_lower_form(self, profile, den, expected):
        cells, _ = emit(
            mml(f"<math><mfrac><mn>1</mn><mn>{den}</mn></mfrac></math>"),
            profile,
        )
        lower = next(c for c in cells if c.role == "math_digit_lower")
        assert lower.dots == expected

    def test_multi_digit_numerator_breaks_antoine(self, profile):
        cells, _ = emit(
            mml("<math><mfrac><mn>11</mn><mn>2</mn></mfrac></math>"), profile
        )
        # No Antoine — falls through to simplified slash.
        assert all(c.role != "math_digit_lower" for c in cells)
        assert any(c.role == "math_fraction_bar" for c in cells)


# ---------------------------------------------------------------------------
# Provenance — source_text + source_span propagation
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_mi_source_text_on_each_cell(self, profile):
        cells, _ = emit(mml("<math><mi>x</mi></math>"), profile)
        # Both cells (prefix + body) carry source_text = "x".
        for c in cells:
            assert c.source_text == "x"

    def test_mn_source_text_per_digit(self, profile):
        cells, _ = emit(mml("<math><mn>42</mn></math>"), profile)
        # The number_sign has no source_text (backend-inserted).
        # Each digit cell has its own char as source_text.
        digit_cells = [c for c in cells if c.role == "math_digit"]
        assert [c.source_text for c in digit_cells] == ["4", "2"]

    def test_mo_source_text_carries_symbol(self, profile):
        cells, _ = emit(
            mml("<math><mi>x</mi><mo>+</mo><mi>y</mi></math>"), profile
        )
        op = next(c for c in cells if c.role == "math_op")
        assert op.source_text == "+"

    def test_function_name_source_text_carries_full_name(self, profile):
        cells, _ = emit(mml("<math><mi>sin</mi></math>"), profile)
        name = next(c for c in cells if c.role == "math_function_name")
        assert name.source_text == "sin"

    def test_inline_span_propagates_to_emitted_cells(self, profile):
        tree = mml("<math><mi>x</mi></math>")
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        node = MathInline(surface="x", span=Span(5, 6), math=tree)
        cells = translate(node, ctx, profile)
        # Every cell should report the formula-level span.
        for c in cells:
            assert c.source_span == Span(5, 6)


# ---------------------------------------------------------------------------
# Warning collector integration
# ---------------------------------------------------------------------------


class TestWarningIntegration:
    def test_no_warning_for_clean_input(self, profile):
        _, wc = emit(mml("<math><mi>x</mi></math>"), profile)
        assert not list(wc)

    def test_no_warning_for_complex_clean_input(self, profile):
        _, wc = emit(
            mml(
                "<math><msup>"
                "<mi>x</mi><mn>2</mn>"
                "</msup><mo>+</mo>"
                "<mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
            ),
            profile,
        )
        assert not [w for w in wc if w.code.startswith("MATH_")]

    def test_warning_carries_source_info(self, profile):
        _, wc = emit(mml("<math><mi>好</mi></math>"), profile)
        warnings = wc.by_code("MATH_UNKNOWN_IDENTIFIER")
        assert len(warnings) == 1
        assert warnings[0].surface == "好"

    def test_warning_source_module(self, profile):
        _, wc = emit(mml("<math><mi>好</mi></math>"), profile)
        warnings = wc.by_code("MATH_UNKNOWN_IDENTIFIER")
        assert warnings[0].source == "backend.math"


# ---------------------------------------------------------------------------
# Fallback-chain tail branches that aren't reached by typical formulas
# ---------------------------------------------------------------------------


class TestFallbackChainTails:
    """Each handler walks: profile-specific tables → letter table →
    punctuation table → unknown. The "punctuation" arm rarely fires for
    real input because most punctuation chars are also in the
    math-symbol table. These tests pick chars that only live in the
    punctuation table to exercise that arm explicitly."""

    def test_mi_single_char_punct_fallback(self, profile):
        # ``?`` (U+003F) is in cn_current's punctuation table but not
        # in any letter / math-symbol table. A bare <mi>?</mi> should
        # round-trip through the identifier punctuation-fallback arm
        # without emitting an unknown cell.
        cells, wc = emit(mml("<math><mi>?</mi></math>"), profile)
        assert all(c.role != "unknown" for c in cells)
        # Role is math_identifier (from the mi handler, not the punct
        # fallback inside _emit_mo).
        ident_cells = [c for c in cells if c.role == "math_identifier"]
        assert ident_cells, "expected at least one math_identifier cell"
        assert ident_cells[0].source_text == "?"
        # No warning because the punctuation fallback fired cleanly.
        assert not any(w.code == "MATH_UNKNOWN_IDENTIFIER" for w in wc)

    def test_mn_empty_text_short_circuits(self, profile):
        # <mn></mn> (empty body) hits ``return`` early in _emit_mn and
        # emits nothing.
        cells, wc = emit(mml("<math><mn></mn></math>"), profile)
        # Only the math-root iteration; no digit cells, no number sign.
        digit_cells = [c for c in cells if c.role.startswith("math_digit")]
        ns_cells = [c for c in cells if c.role == "number_sign"]
        assert digit_cells == []
        assert ns_cells == []

    def test_mo_multi_char_function_name_routes_to_function_path(self, profile):
        # latex2mathml sometimes emits ``<mo>sin</mo>`` for ``\sin``
        # without a script. The fallback chain in _emit_mo recognises
        # multi-char function names and routes through the function
        # path (function_prefix + functions cells).
        root = ET.Element("math")
        mo = ET.SubElement(root, "mo")
        mo.text = "sin"
        cells, _ = emit(root, profile)
        assert any(c.role == "math_function_prefix" for c in cells)
        name_cells = [c for c in cells if c.role == "math_function_name"]
        assert len(name_cells) == 1
        assert name_cells[0].source_text == "sin"

    def test_mo_single_char_letter_fallback(self, profile):
        # A bare ASCII letter inside an ``<mo>`` falls back to the
        # letter table when no math-symbol entry exists for that
        # character. Result: latin_lower_prefix + letter cells with
        # role=math_identifier.
        root = ET.Element("math")
        mo = ET.SubElement(root, "mo")
        mo.text = "a"  # not in symbols.json, but is in latin lower letters
        cells, _ = emit(root, profile)
        # Two cells (prefix + letter) — both role=math_identifier.
        ident_cells = [c for c in cells if c.role == "math_identifier"]
        assert len(ident_cells) == 2
        assert all(c.source_text == "a" for c in ident_cells)

    def test_mtext_punctuation_fallback(self, profile):
        # mtext walks: math_symbol → letter → punctuation → unknown.
        # ``?`` is only in punctuation — hits the punctuation arm.
        cells, wc = emit(mml("<math><mtext>?</mtext></math>"), profile)
        text_cells = [c for c in cells if c.role == "math_text"]
        assert text_cells, "expected math_text cells from punctuation fallback"
        # No unknown cells / warnings — punctuation lookup succeeded.
        assert all(c.role != "unknown" for c in cells)
        assert not any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in wc)


# ---------------------------------------------------------------------------
# Antoine / atomic-lower-digit defensive branches
# ---------------------------------------------------------------------------


class TestAtomicLowerDigitDefensive:
    """``_try_emit_atomic_lower_digit`` and the Antoine helper both
    check that the digit text actually resolves in
    ``profile.math_digits_lower`` before committing to the lower-form
    path. A char that's an ``isdigit()`` (so ``_is_single_digit_mn``
    returns True) but absent from those profile tables falls back to
    the normal script path."""

    def test_superscript_digit_in_fraction_falls_back_from_antoine(
        self, profile
    ):
        # ``²`` (U+00B2 SUPERSCRIPT TWO) has ``isdigit() == True`` but
        # isn't in profile.digits or profile.math_digits_lower. The
        # Antoine helper returns False at the digit-lookup arm, and the
        # fraction renders through the simplified or compound path
        # (whichever the simplify_fraction feature picks).
        cells, _ = emit(
            mml("<math><mfrac><mn>²</mn><mn>²</mn></mfrac></math>"),
            profile,
        )
        # No Antoine lower-form digit cell was emitted.
        assert all(c.role != "math_digit_lower" for c in cells)

    def test_superscript_digit_in_script_falls_back_from_lower_form(
        self, profile
    ):
        # Same as above for the script path: ²  passes
        # ``_is_single_digit_mn`` (isdigit) but math_digits_lower miss
        # forces the regular script path.
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mn>²</mn></msup></math>"),
            profile,
        )
        # No lower-form digit; the script either closes (compound) or
        # simplifies — but ``math_digit_lower`` is NOT in the result.
        assert all(c.role != "math_digit_lower" for c in cells)


# ---------------------------------------------------------------------------
# Degenerate script shapes — _route_script with base=None
# ---------------------------------------------------------------------------


class TestDegenerateScripts:
    def test_msub_with_no_children_treated_as_regular(self, profile):
        # <msub></msub> has no base nor sub. _route_script must not
        # crash — it falls through to the regular handler which simply
        # emits nothing (no base, no sub, no sup).
        root = ET.Element("math")
        ET.SubElement(root, "msub")  # truly empty
        cells, wc = emit(root, profile)
        # No exception, no crash; output may legally be empty or carry
        # nothing controversial. We only assert no unknown cells were
        # synthesised.
        assert all(c.role != "unknown" for c in cells)

    def test_munder_with_no_children_treated_as_regular(self, profile):
        # Same as above for the under/over dispatcher path. Without
        # accent="true" it routes through _route_script.
        root = ET.Element("math")
        ET.SubElement(root, "munder")
        cells, _ = emit(root, profile)
        assert all(c.role != "unknown" for c in cells)


# ---------------------------------------------------------------------------
# Big-op function script — sup branch
# ---------------------------------------------------------------------------


class TestBigOpFunctionScriptSup:
    def test_lim_with_sup_emits_sup_side(self, profile):
        # ``lim^{n}`` — unusual but legal MathML. The big-op-function
        # handler must emit the sup side via _emit_big_op_side.
        # ``msup`` shape: base=<mi>lim</mi>, sup=<mi>n</mi>.
        cells, _ = emit(
            mml(
                "<math><msup>"
                "<mi>lim</mi>"
                "<mi>n</mi>"
                "</msup></math>"
            ),
            profile,
        )
        r = [c.role for c in cells]
        assert "math_function_prefix" in r
        assert "math_superscript" in r
        # The sup-side prefix is the big_op_script_prefix (when the
        # function opts in via script_prefix=true in functions.json).
        # cn_current's ``lim`` does opt in.
        assert "math_big_op_script_prefix" in r


# ---------------------------------------------------------------------------
# Accent handler — empty / letter / punctuation arms
# ---------------------------------------------------------------------------


class TestAccentEdges:
    def test_accent_with_no_children_returns_silently(self, profile):
        # <mover accent="true"/> with no kids → handler returns
        # without crashing, emits nothing.
        root = ET.Element("math")
        ET.SubElement(root, "mover", attrib={"accent": "true"})
        # No children at all.
        cells, wc = emit(root, profile)
        # No cells, no warning.
        assert cells == []
        assert all(not w.code.startswith("MATH_") for w in wc)

    def test_accent_with_empty_accent_node_skipped(self, profile):
        # base <mi>x</mi>, accent node with empty text — the loop
        # ``continue``s over the empty accent. The base still emits.
        root = ET.Element("math")
        mover = ET.SubElement(root, "mover", attrib={"accent": "true"})
        base = ET.SubElement(mover, "mi")
        base.text = "x"
        empty_acc = ET.SubElement(mover, "mo")
        empty_acc.text = ""  # explicit empty
        cells, wc = emit(root, profile)
        # x's two cells (latin lower prefix + letter) are emitted.
        ident_cells = [c for c in cells if c.role == "math_identifier"]
        assert len(ident_cells) >= 2

    def test_accent_char_resolves_via_letter_table(self, profile):
        # base <mi>x</mi>, accent node = "a". ``a`` is in the letter
        # table (latin lower), so the letter arm of _emit_accent_char
        # fires. Cells get role=math_accent.
        root = ET.Element("math")
        mover = ET.SubElement(root, "mover", attrib={"accent": "true"})
        base = ET.SubElement(mover, "mi")
        base.text = "x"
        accent_node = ET.SubElement(mover, "mo")
        accent_node.text = "a"
        cells, wc = emit(root, profile)
        accent_cells = [c for c in cells if c.role == "math_accent"]
        assert accent_cells, "expected math_accent cells from letter arm"
        assert all(c.source_text == "a" for c in accent_cells)

    def test_accent_char_resolves_via_punctuation_table(self, profile):
        # base <mi>x</mi>, accent node = "?". ``?`` is only in the
        # punctuation table (not in symbols, not in letter). Hits the
        # punctuation arm of _emit_accent_char.
        root = ET.Element("math")
        mover = ET.SubElement(root, "mover", attrib={"accent": "true"})
        base = ET.SubElement(mover, "mi")
        base.text = "x"
        accent_node = ET.SubElement(mover, "mo")
        accent_node.text = "?"
        cells, wc = emit(root, profile)
        accent_cells = [c for c in cells if c.role == "math_accent"]
        assert accent_cells
        assert accent_cells[0].source_text == "?"
        # No unknown — punctuation fallback succeeded.
        assert all(c.role != "unknown" for c in cells)
        assert not any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)


# ---------------------------------------------------------------------------
# merror: text-only payload (no child elements)
# ---------------------------------------------------------------------------


class TestMerrorTextOnly:
    def test_merror_with_only_text_uses_elem_text(self, profile):
        # <merror>some text</merror> with no child elements — the
        # handler reads elem.text directly (the ``else`` branch of
        # _emit_merror).
        root = ET.Element("math")
        err = ET.SubElement(root, "merror", attrib={"data-reason": "parse"})
        err.text = "boom"
        cells, wc = emit(root, profile)
        # Exactly one unknown cell with surface from elem.text.
        unknown_cells = [c for c in cells if c.role == "unknown"]
        assert len(unknown_cells) == 1
        # Warning fired with the elem.text payload.
        math_errs = list(wc.by_code("MATH_ERROR"))
        assert len(math_errs) == 1
        assert math_errs[0].surface == "boom"


# ---------------------------------------------------------------------------
# _is_leaf_like / _is_single_digit_mn — defensive False branches
# ---------------------------------------------------------------------------


class TestLeafLikeDefensive:
    """Direct unit tests against the predicate helpers. These cover
    branches the dispatcher rarely reaches because real MathML never
    contains the degenerate shapes."""

    def test_is_leaf_like_rejects_element_with_children(self):
        from brailix.backend.math.utils import _is_leaf_like

        elem = ET.fromstring("<mi><mi>x</mi></mi>")
        assert _is_leaf_like(elem) is False

    def test_is_leaf_like_rejects_empty_text(self):
        from brailix.backend.math.utils import _is_leaf_like

        elem = ET.Element("mi")
        # No text at all.
        assert _is_leaf_like(elem) is False
        # Whitespace-only text strips to "".
        elem.text = "   "
        assert _is_leaf_like(elem) is False

    def test_is_leaf_like_rejects_multi_char_mi(self):
        from brailix.backend.math.utils import _is_leaf_like

        elem = ET.Element("mi")
        elem.text = "sin"
        assert _is_leaf_like(elem) is False

    def test_is_leaf_like_accepts_single_char_mi(self):
        from brailix.backend.math.utils import _is_leaf_like

        elem = ET.Element("mi")
        elem.text = "x"
        assert _is_leaf_like(elem) is True

    def test_is_leaf_like_accepts_multi_digit_mn(self):
        # mn doesn't have the single-character rule that mi does — a
        # multi-digit number is still leaf-like.
        from brailix.backend.math.utils import _is_leaf_like

        elem = ET.Element("mn")
        elem.text = "42"
        assert _is_leaf_like(elem) is True

    def test_is_single_digit_mn_rejects_element_with_children(self):
        from brailix.backend.math.utils import _is_single_digit_mn

        elem = ET.fromstring("<mn><mn>1</mn></mn>")
        assert _is_single_digit_mn(elem) is False


# ---------------------------------------------------------------------------
# Function-name coalescing — MTEF / OMML emit each letter as its own <mi>;
# the backend's pre-pass merges runs back into multi-char <mi> so the
# function-name path fires instead of letter-by-letter spelling.
# ---------------------------------------------------------------------------


class TestFunctionNameCoalesce:
    def test_split_cos_emits_function_prefix_plus_c(self, profile):
        cells, _ = emit(
            mml("<math><mi>c</mi><mi>o</mi><mi>s</mi></math>"), profile
        )
        roles = [c.role for c in cells]
        assert roles == ["math_function_prefix", "math_function_name"]
        assert cells[0].dots == (1, 2, 4, 6)
        assert cells[1].dots == (1, 4)

    def test_split_sin_emits_function_prefix_plus_s(self, profile):
        cells, _ = emit(
            mml("<math><mi>s</mi><mi>i</mi><mi>n</mi></math>"), profile
        )
        roles = [c.role for c in cells]
        assert roles == ["math_function_prefix", "math_function_name"]
        assert cells[1].dots == (2, 3, 4)

    def test_split_function_followed_by_variable(self, profile):
        # cos α  →  function_prefix + c + greek_lower_prefix + α
        cells, _ = emit(
            mml("<math><mi>c</mi><mi>o</mi><mi>s</mi><mi>α</mi></math>"),
            profile,
        )
        roles = [c.role for c in cells]
        assert roles[0] == "math_function_prefix"
        assert roles[1] == "math_function_name"
        assert "math_identifier" in roles[2:]

    def test_split_arcsin_uses_longest_match(self, profile):
        # a + r + c + s + i + n   →   arcsin (not arc + sin or a + rc + sin)
        cells, _ = emit(
            mml(
                "<math><mi>a</mi><mi>r</mi><mi>c</mi>"
                "<mi>s</mi><mi>i</mi><mi>n</mi></math>"
            ),
            profile,
        )
        # arcsin = function_prefix + a (c_1) + sin (c_234) → 3 cells.
        assert [c.role for c in cells] == [
            "math_function_prefix",
            "math_function_name",
            "math_function_name",
        ]

    def test_split_sinh_uses_longest_match(self, profile):
        # s + i + n + h  →  sinh (not sin + h, since longest is preferred)
        cells, _ = emit(
            mml("<math><mi>s</mi><mi>i</mi><mi>n</mi><mi>h</mi></math>"),
            profile,
        )
        # sinh = function_prefix + sin + h(c_125)
        assert cells[0].role == "math_function_prefix"
        name_cells = [c for c in cells if c.role == "math_function_name"]
        assert [c.dots for c in name_cells] == [(2, 3, 4), (1, 2, 5)]

    def test_unknown_letters_merge_into_one_letter_run(self, profile):
        # x + y + z  →  no function match; the adjacent letters merge
        # into one run sharing a single latin-lower sign
        # (《盲文常用数学符号》二.规则1): ⠰ + x + y + z.
        cells, _ = emit(
            mml("<math><mi>x</mi><mi>y</mi><mi>z</mi></math>"), profile
        )
        roles = [c.role for c in cells]
        assert "math_function_prefix" not in roles
        assert [c.dots for c in cells] == [
            (5, 6), (1, 3, 4, 6), (1, 3, 4, 5, 6), (1, 3, 5, 6),
        ]

    def test_partial_match_consumes_only_function_letters(self, profile):
        # s + i + n + x  →  sin (function) + x (variable)
        cells, _ = emit(
            mml("<math><mi>s</mi><mi>i</mi><mi>n</mi><mi>x</mi></math>"),
            profile,
        )
        assert cells[0].role == "math_function_prefix"
        # Last two cells should be x with its latin lower prefix.
        assert cells[-2].dots == (5, 6)
        assert cells[-1].source_text == "x"

    def test_inside_mfrac_numerator(self, profile):
        # cos / 2 — letters split inside the numerator. The coalescer
        # has to recurse into <mfrac>'s children.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>c</mi><mi>o</mi><mi>s</mi></mrow>"
                "<mn>2</mn>"
                "</mfrac></math>"
            ),
            profile,
        )
        # function_prefix + c on the numerator side.
        roles = [c.role for c in cells]
        assert "math_function_prefix" in roles
        assert "math_function_name" in roles

    def test_function_name_source_text_records_full_name(self, profile):
        cells, _ = emit(
            mml("<math><mi>c</mi><mi>o</mi><mi>s</mi></math>"), profile
        )
        name = next(c for c in cells if c.role == "math_function_name")
        assert name.source_text == "cos"

    def test_coalesce_does_not_mutate_input_tree(self, profile):
        # MathInline.math is cached by the pipeline and serialized into the
        # proofread JSON; the backend must consume it read-only. The
        # function-name pre-pass must coalesce on a copy, never edit the
        # caller's tree (ARCHITECTURE.md).
        tree = mml("<math><mi>c</mi><mi>o</mi><mi>s</mi></math>")
        before = ET.tostring(tree, encoding="unicode")
        cells, _ = emit(tree, profile)
        after = ET.tostring(tree, encoding="unicode")
        assert after == before, "translate() mutated the shared MathML IR tree"
        assert [child.text for child in tree] == ["c", "o", "s"]
        # …and the coalescing still happened on the working copy.
        assert [c.role for c in cells] == [
            "math_function_prefix",
            "math_function_name",
        ]

    def test_coalesce_does_not_mutate_nested_tree(self, profile):
        # Copy-on-write has to rebuild the parent chain (math→mfrac→mrow)
        # when a deep run coalesces — verify the original stays intact.
        tree = mml(
            "<math><mfrac>"
            "<mrow><mi>c</mi><mi>o</mi><mi>s</mi></mrow>"
            "<mn>2</mn>"
            "</mfrac></math>"
        )
        before = ET.tostring(tree, encoding="unicode")
        emit(tree, profile)
        assert ET.tostring(tree, encoding="unicode") == before


# ---------------------------------------------------------------------------
# Letter-word coalescing + positional-slot gating — adjacent letters that
# don't spell a function merge into one letter run (one sign per same-class
# stretch, 《盲文常用数学符号》二.规则1); the children of positional
# containers (msub / mfrac / ...) are distinct slots and never merge.
# ---------------------------------------------------------------------------


class TestLetterRunCoalesce:
    def test_all_caps_run_doubles_capital_sign(self, profile):
        # A + B → whole-word capitals ⠠⠠ + bare letters.
        cells, _ = emit(mml("<math><mi>A</mi><mi>B</mi></math>"), profile)
        assert [c.dots for c in cells] == [(6,), (6,), (1,), (1, 2)]

    def test_mathvariant_normal_letters_merge(self, profile):
        # \mathrm{AB} arrives as mathvariant="normal" letters — upright
        # words are exactly what the letter-run rule is for.
        cells, _ = emit(
            mml(
                '<math><mi mathvariant="normal">A</mi>'
                '<mi mathvariant="normal">B</mi></math>'
            ),
            profile,
        )
        assert [c.dots for c in cells] == [(6,), (6,), (1,), (1, 2)]

    def test_msub_slots_never_merge_to_function(self, profile):
        # l_n: base and subscript are SLOTS, not a sequence — they must
        # not coalesce into the function "ln". The subscript structure
        # (⠡) survives.
        cells, _ = emit(
            mml("<math><msub><mi>l</mi><mi>n</mi></msub></math>"), profile
        )
        roles = [c.role for c in cells]
        assert "math_function_prefix" not in roles
        assert "math_subscript" in roles

    def test_msub_slots_never_merge_to_letter_word(self, profile):
        # T_r: same gating for the letter-word merge — T stays the base
        # (single ⠠, not ⠠⠠) and r stays a subscript.
        cells, _ = emit(
            mml("<math><msub><mi>T</mi><mi>r</mi></msub></math>"), profile
        )
        roles = [c.role for c in cells]
        assert "math_function_prefix" not in roles
        assert "math_subscript" in roles
        assert [c.dots for c in cells][:2] == [(6,), (2, 3, 4, 5)]

    def test_script_content_mrow_still_merges(self, profile):
        # x_{ab}: the mrow INSIDE the slot is a sequence — a and b share
        # one lowercase sign, and the script close marker stays (a
        # two-letter word is not atomic).
        cells, _ = emit(
            mml(
                "<math><msub><mi>x</mi>"
                "<mrow><mi>a</mi><mi>b</mi></mrow></msub></math>"
            ),
            profile,
        )
        dots = [c.dots for c in cells]
        # x(56+1346) + sub(16) + ⠰ab(56, 1, 12) + close(156)
        assert dots == [
            (5, 6), (1, 3, 4, 6), (1, 6), (5, 6), (1,), (1, 2), (1, 5, 6),
        ]

    def test_function_match_wins_over_letter_word(self, profile):
        # m + a + x in a sequence container still coalesces to the
        # registered function (⠫ + abbreviation), not a letter word.
        cells, _ = emit(
            mml(
                "<math><msub><mi>x</mi>"
                "<mrow><mi>m</mi><mi>a</mi><mi>x</mi></mrow></msub></math>"
            ),
            profile,
        )
        assert "math_function_prefix" in [c.role for c in cells]

    def test_merged_run_unions_data_bk_spans(self, profile):
        # When every member of a run carries a data-bk-span the merged
        # word's cells land on the union span (proofread jumps select
        # the whole word).
        cells, _ = emit(
            mml(
                '<math><mi data-bk-span="3,4">a</mi>'
                '<mi data-bk-span="4,5">b</mi></math>'
            ),
            profile,
        )
        spans = {(c.source_span.start, c.source_span.end) for c in cells if c.source_span}
        assert spans == {(3, 5)}
        assert [c.dots for c in cells] == [(5, 6), (1,), (1, 2)]

    def test_mixed_span_presence_blocks_merge(self, profile):
        # One spanned + one unspanned letter: merging would mis-attribute
        # cells, so the run stays per-letter (two ⠰ signs).
        cells, _ = emit(
            mml(
                '<math><mi data-bk-span="3,4">a</mi><mi>b</mi></math>'
            ),
            profile,
        )
        assert [c.dots for c in cells] == [(5, 6), (1,), (5, 6), (1, 2)]
