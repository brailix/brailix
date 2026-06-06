"""Math backend tests for leaf elements: <mi>, <mn>, <mo>, <mtext>,
plus function-name lookups and profile-level symbol queries.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.backend._math_common import emit, mml, roles

# ---------------------------------------------------------------------------
# 1-5: <mi> single-char + multi-char (function) + fallback chains
# ---------------------------------------------------------------------------


class TestMi:
    def test_mi_latin_lower_uses_letter_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>x</mi></math>"), profile)
        # 56 (latin lower prefix) + 1346 (x cell)
        assert [c.dots for c in cells] == [(5, 6), (1, 3, 4, 6)]
        assert all(c.role == "math_identifier" for c in cells)
        assert cells[-1].source_text == "x"

    def test_mi_latin_upper_uses_capital_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>A</mi></math>"), profile)
        # 6 (latin upper prefix) + 1 (a cell)
        assert [c.dots for c in cells] == [(6,), (1,)]

    def test_mi_greek_lower_uses_46_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>π</mi></math>"), profile)
        # 46 (greek lower prefix) + 1234 (π cell)
        assert [c.dots for c in cells] == [(4, 6), (1, 2, 3, 4)]

    def test_mi_greek_upper_uses_456_prefix(self, profile):
        cells, _ = emit(mml("<math><mi>Π</mi></math>"), profile)
        # 456 (greek upper prefix) + 1234 (Π cell)
        assert [c.dots for c in cells] == [(4, 5, 6), (1, 2, 3, 4)]

    def test_mi_unknown_shape_char_falls_to_unknown(self, profile):
        # ▵ (U+25B5) is not in cn_current's tables — Chinese math doesn't
        # write a symbol like △ABC; it spells out "三角形ABC" (triangle ABC).
        # So ▵ hits the unknown at the end of the fallback chain +
        # MATH_UNKNOWN_IDENTIFIER warning. Other profiles may still register
        # role=shape entries; the role is a valid one.
        cells, wc = emit(mml("<math><mi>▵</mi></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_IDENTIFIER" for w in wc)

    def test_mi_unknown_char_warns(self, profile):
        cells, wc = emit(mml("<math><mi>好</mi></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_IDENTIFIER" for w in wc)

    def test_mi_multi_char_emits_function_prefix_and_table_cells(self, profile):
        # sin is in functions.json — single-cell abbreviation 's' = c_234.
        cells, _ = emit(mml("<math><mi>sin</mi></math>"), profile)
        assert cells[0].role == "math_function_prefix"
        assert cells[0].dots == (1, 2, 4, 6)
        name_cells = [c for c in cells if c.role == "math_function_name"]
        assert len(name_cells) == 1
        assert name_cells[0].dots == (2, 3, 4)
        assert name_cells[0].source_text == "sin"

    def test_mi_multi_char_unknown_function_falls_back_to_letter_by_letter(
        self, profile
    ):
        # Three-letter name not in the table — each letter goes through
        # the identifier path (56 + letter), totalling 6 inner cells +
        # 1 function_prefix.
        cells, _ = emit(mml("<math><mi>hyp</mi></math>"), profile)
        assert cells[0].role == "math_function_prefix"
        ident_cells = [c for c in cells if c.role == "math_identifier"]
        assert [c.source_text for c in ident_cells] == [
            "h", "h", "y", "y", "p", "p",
        ]


# ---------------------------------------------------------------------------
# 6-11: <mn>
# ---------------------------------------------------------------------------


class TestMn:
    def test_mn_simple_emits_number_sign_and_digits(self, profile):
        cells, _ = emit(mml("<math><mn>42</mn></math>"), profile)
        assert cells[0].role == "number_sign"
        assert cells[0].dots == (3, 4, 5, 6)
        assert [c.role for c in cells[1:]] == ["math_digit", "math_digit"]

    def test_mn_with_decimal(self, profile):
        cells, _ = emit(mml("<math><mn>3.14</mn></math>"), profile)
        r = roles(cells)
        assert r[0] == "number_sign"
        assert "decimal_point" in r
        # Order: number_sign, 3, decimal_point, 1, 4
        assert r == ["number_sign", "math_digit", "decimal_point", "math_digit", "math_digit"]

    def test_mn_with_thousands_sep(self, profile):
        cells, _ = emit(mml("<math><mn>1,000</mn></math>"), profile)
        r = roles(cells)
        assert r[0] == "number_sign"
        assert "thousands_sep" in r
        assert r == [
            "number_sign", "math_digit", "thousands_sep",
            "math_digit", "math_digit", "math_digit",
        ]

    def test_mn_after_op_gets_number_sign(self, profile):
        cells, _ = emit(mml("<math><mrow><mo>+</mo><mn>5</mn></mrow></math>"), profile)
        # The + emits, then number_sign must appear before the 5.
        r = roles(cells)
        assert "number_sign" in r
        plus_idx = r.index("math_op")
        ns_idx = r.index("number_sign")
        assert plus_idx < ns_idx

    def test_consecutive_mn_in_mrow_emits_single_number_sign(self, profile):
        cells, _ = emit(mml("<math><mrow><mn>1</mn><mn>2</mn></mrow></math>"), profile)
        # In MathML, consecutive mn are unusual but allowed. State stays
        # inside a number run, so the second mn doesn't re-emit the sign.
        ns = [c for c in cells if c.role == "number_sign"]
        assert len(ns) == 1

    def test_structure_boundary_resets_number_sign(self, profile):
        # \frac{12}{34} — multi-digit numerator/denominator means
        # Antoine doesn't apply; we go through the slash/simplified
        # path. But each side should get its own number_sign because
        # structure boundaries reset state.
        cells, _ = emit(
            mml("<math><mfrac><mn>12</mn><mn>34</mn></mfrac></math>"), profile
        )
        ns = [c for c in cells if c.role == "number_sign"]
        assert len(ns) == 2


# ---------------------------------------------------------------------------
# 12-17: <mo>
# ---------------------------------------------------------------------------


class TestMo:
    def test_op_spacing_inserts_blank_before(self, profile):
        # 1 + 2: + has space_before=true, so a blank cell appears just
        # before the + in the cell stream.
        cells, _ = emit(
            mml("<math><mrow><mn>1</mn><mo>+</mo><mn>2</mn></mrow></math>"),
            profile,
        )
        op_idx = next(i for i, c in enumerate(cells) if c.role == "math_op")
        assert cells[op_idx - 1].is_blank

    def test_rel_with_double_spacing(self, profile):
        # ∈ has both space_before=true AND space_after=true.
        cells, _ = emit(
            mml("<math><mrow><mi>x</mi><mo>∈</mo><mi>S</mi></mrow></math>"),
            profile,
        )
        rel_idx = next(i for i, c in enumerate(cells) if c.role == "math_rel")
        assert cells[rel_idx - 1].is_blank   # space_before
        assert cells[rel_idx + len(profile.math_symbol("∈"))].is_blank  # space_after

    def test_delim_no_spacing(self, profile):
        # ( has role=delim, no spacing flags.
        cells, _ = emit(mml("<math><mo>(</mo></math>"), profile)
        # Only the bare delim cell — no blanks.
        assert [c.role for c in cells] == ["math_delim"]
        assert cells[0].dots == (1, 2, 6)

    def test_punct_space_after(self, profile):
        # comma has space_after=true.
        cells, _ = emit(mml("<math><mo>,</mo></math>"), profile)
        assert any(c.role == "math_punct" for c in cells)
        # After the punct cell, a blank is appended.
        assert cells[-1].is_blank

    def test_degree_renders_as_two_cell_punct(self, profile):
        # ° U+00B0 — postfix unit marker, role=punct, cells=[c_5, c_356],
        # no spacing flags.
        cells, wc = emit(mml("<math><mo>°</mo></math>"), profile)
        assert [c.role for c in cells] == ["math_punct", "math_punct"]
        assert [c.dots for c in cells] == [(5,), (3, 5, 6)]
        assert not any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)

    def test_degree_after_number_no_separator(self, profile):
        # 10° — no blank between digit cells and ° cells; the number
        # context closes naturally, then ° emits as math_punct without
        # space_before (matches period's pattern).
        cells, _ = emit(
            mml("<math><mn>10</mn><mo>°</mo></math>"), profile
        )
        roles_seq = [c.role for c in cells]
        # number_sign + 1 + 0 + ° (5) + ° (356)
        assert roles_seq == [
            "number_sign", "math_digit", "math_digit",
            "math_punct", "math_punct",
        ]
        dots_seq = [c.dots for c in cells]
        # Last two cells are the ° encoding
        assert dots_seq[-2:] == [(5,), (3, 5, 6)]

    def test_degree_with_function_name(self, profile):
        # cos 10° — function prefix + cos + numsign + 1 + 0 + ° cells.
        cells, _ = emit(
            mml("<math><mi>cos</mi><mn>10</mn><mo>°</mo></math>"), profile
        )
        last_two = [(c.role, c.dots) for c in cells[-2:]]
        assert last_two == [("math_punct", (5,)), ("math_punct", (3, 5, 6))]

    def test_mo_unknown_symbol_warns(self, profile):
        # ⊗ — not in symbols/punctuation/identifiers, should trigger
        # MATH_UNKNOWN_SYMBOL after exhausting the fallback chain.
        cells, wc = emit(mml("<math><mo>⊗</mo></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)

    def test_mo_big_op_standalone(self, profile):
        # ∑ standalone: role=big_op, 2-cell encoding 456 + 234.
        cells, _ = emit(mml("<math><mo>∑</mo></math>"), profile)
        big = [c for c in cells if c.role == "math_big_op"]
        assert len(big) == 2
        assert [c.dots for c in big] == [(4, 5, 6), (2, 3, 4)]


# ---------------------------------------------------------------------------
# 34-36: <mtext>
# ---------------------------------------------------------------------------


class TestMtext:
    """``<mtext>`` per-char fallback path.

    ``emit`` injects no ``inline_text_translator``, so these exercise the
    backend-only fallback (symbols → letters → punctuation → unknown).
    The *primary* path — routing the whole run through the injected text
    translator — is covered by ``test_mtext_routes_through_translator``
    below and end-to-end in ``test_latex_braille_golden.TestText``.
    """

    def test_mtext_finds_symbol(self, profile):
        # ≤ — entity "le" in symbols.json.
        cells, _ = emit(mml("<math><mtext>≤</mtext></math>"), profile)
        # We don't really care about the exact cells; the role should
        # all be math_text and no warning should fire.
        assert all(c.role == "math_text" for c in cells if c.role != "space")
        assert cells  # non-empty

    def test_mtext_spaces_become_blank(self, profile):
        cells, _ = emit(mml("<math><mtext>a b</mtext></math>"), profile)
        # a (2 cells via identifier path? No — mtext uses math_text role)
        # there should be at least one blank in the output.
        assert any(c.is_blank for c in cells)

    def test_mtext_unknown_char_warns(self, profile):
        cells, wc = emit(mml("<math><mtext>☃</mtext></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in wc)

    def test_mtext_routes_through_translator(self, profile):
        # When a pipeline injects an inline_text_translator, <mtext> is
        # natural-language text: hand the whole run to it (not the per-char
        # math-table path), with latex2mathml's U+00A0 space normalised to
        # a real space so the text path sees a word break.
        from brailix.backend.math import translate
        from brailix.core.context import (
            INLINE_TEXT_TRANSLATOR_KEY,
            BackendContext,
        )
        from brailix.core.errors import RunMode, WarningCollector
        from brailix.ir.braille import BrailleCell
        from brailix.ir.inline import MathInline

        seen: list[str] = []
        marker = BrailleCell(dots=(1, 2, 3), role="math_text")

        def fake_translator(text: str) -> list[BrailleCell]:
            seen.append(text)
            return [marker]

        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(
            profile="cn_current",
            warnings=wc,
            options={INLINE_TEXT_TRANSLATOR_KEY: fake_translator},
        )
        tree = ET.fromstring("<math><mtext>a\u00a0b</mtext></math>")
        node = MathInline(surface="", source="mathml", span=None, math=tree)
        cells = translate(node, ctx, profile)
        assert seen == ["a b"]  # NBSP normalised; whole run sent once
        assert marker in cells
        assert not any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in wc)


# ---------------------------------------------------------------------------
# Additional <mi> / <mn> coverage
# ---------------------------------------------------------------------------


class TestMiExtras:
    def test_empty_mi_emits_nothing(self, profile):
        cells, wc = emit(mml("<math><mi></mi></math>"), profile)
        assert cells == []
        assert not list(wc)

    def test_whitespace_only_mi_emits_nothing(self, profile):
        cells, wc = emit(mml("<math><mi>   </mi></math>"), profile)
        assert cells == []

    def test_mi_letter_case_mixed_sequence(self, profile):
        # Multi-char mi with mixed-case is a function fallback — each
        # letter goes through identifier path with its own case prefix.
        cells, _ = emit(mml("<math><mi>Ax</mi></math>"), profile)
        # function_prefix + (6 + A) + (56 + x)
        assert cells[0].role == "math_function_prefix"
        ident_cells = [c for c in cells if c.role == "math_identifier"]
        assert [c.dots for c in ident_cells] == [(6,), (1,), (5, 6), (1, 3, 4, 6)]

    def test_mi_resets_number_sign_state(self, profile):
        # Whitespace handling: an mi after an mn run should reset the
        # state so a following mn re-emits the number sign.
        cells, _ = emit(
            mml("<math><mrow><mn>1</mn><mi>x</mi><mn>2</mn></mrow></math>"),
            profile,
        )
        ns = [c for c in cells if c.role == "number_sign"]
        assert len(ns) == 2


class TestMnExtras:
    def test_mn_zero(self, profile):
        cells, _ = emit(mml("<math><mn>0</mn></math>"), profile)
        assert cells[0].role == "number_sign"
        assert cells[1].role == "math_digit"
        assert cells[1].dots == (2, 4, 5)

    def test_mn_multidigit_no_sep(self, profile):
        cells, _ = emit(mml("<math><mn>9876</mn></math>"), profile)
        # One number_sign + 4 digit cells.
        assert len(cells) == 5
        assert cells[0].role == "number_sign"

    def test_mn_decimal_only(self, profile):
        # Pure ".5" — leading decimal point.
        cells, _ = emit(mml("<math><mn>.5</mn></math>"), profile)
        r = roles(cells)
        # number_sign + decimal_point + math_digit
        assert r == ["number_sign", "decimal_point", "math_digit"]

    def test_mn_unknown_digit_warns(self, profile):
        # A char inside mn that's neither digit/./, gets a warning.
        cells, wc = emit(mml("<math><mn>3z</mn></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_DIGIT" for w in wc)

    def test_mn_fullwidth_digit_renders(self, profile):
        # Full-width digits flow through the shared emitter's ASCII
        # fallback now — previously math dropped them with a warning
        # while prose numbers rendered them (the fixed divergence).
        cells, wc = emit(mml("<math><mn>２</mn></math>"), profile)
        digit_cells = [c for c in cells if c.role == "math_digit"]
        assert [c.dots for c in digit_cells] == [profile.digits["2"]]
        assert not any(w.code == "MATH_UNKNOWN_DIGIT" for w in wc)

    def test_mn_with_features_number_sign_off(self, profile, monkeypatch):
        # math.number_sign gates the math backend's number sign behaviour
        # independently of zh.number_sign — turning off the math feature
        # alone should suppress the leading sign in math.
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "number_sign", False
        )
        cells, _ = emit(mml("<math><mn>5</mn></math>"), profile)
        assert all(c.role != "number_sign" for c in cells)

    def test_mn_math_number_sign_is_independent_of_zh(self, profile, monkeypatch):
        # Setting zh.number_sign=False must NOT affect math —
        # math has its own math.number_sign feature.
        monkeypatch.setitem(
            profile.features.setdefault("zh", {}), "number_sign", False
        )
        cells, _ = emit(mml("<math><mn>5</mn></math>"), profile)
        # math.number_sign is still on (default) → leading sign present.
        assert any(c.role == "number_sign" for c in cells)


# ---------------------------------------------------------------------------
# Additional <mo> coverage
# ---------------------------------------------------------------------------


class TestMoExtras:
    def test_op_no_leading_blank_at_start(self, profile):
        # The very first cell of a translation shouldn't be a blank
        # even if the symbol has space_before.
        cells, _ = emit(mml("<math><mo>+</mo></math>"), profile)
        if cells:
            assert not cells[0].is_blank

    def test_minus_op_has_role_math_op(self, profile):
        cells, _ = emit(mml("<math><mo>−</mo></math>"), profile)
        ops = [c for c in cells if c.role == "math_op"]
        assert ops
        assert ops[0].dots == (3, 6)

    def test_equals_role_math_rel(self, profile):
        cells, _ = emit(mml("<math><mo>=</mo></math>"), profile)
        rels = [c for c in cells if c.role == "math_rel"]
        assert rels
        assert rels[0].dots == (2, 3, 5, 6)

    def test_lpar_role_math_delim(self, profile):
        cells, _ = emit(mml("<math><mo>(</mo></math>"), profile)
        assert cells[0].role == "math_delim"

    def test_period_role_math_punct(self, profile):
        # Period (math punct) — no trailing blank.
        cells, _ = emit(mml("<math><mo>.</mo></math>"), profile)
        # math_punct cells, no blank at end (period has space_after default false)
        punct_cells = [c for c in cells if c.role == "math_punct"]
        assert punct_cells

    def test_arrow_rel(self, profile):
        cells, _ = emit(mml("<math><mo>→</mo></math>"), profile)
        rels = [c for c in cells if c.role == "math_rel"]
        # → is a 2-cell rel.
        assert len(rels) == 2

    def test_empty_mo_emits_nothing(self, profile):
        cells, wc = emit(mml("<math><mo></mo></math>"), profile)
        assert cells == []

    def test_big_op_role_math_big_op(self, profile):
        cells, _ = emit(mml("<math><mo>∑</mo></math>"), profile)
        assert all(
            c.role == "math_big_op" for c in cells if c.role != "space"
        )


class TestMtextExtras:
    def test_mtext_empty(self, profile):
        cells, _ = emit(mml("<math><mtext></mtext></math>"), profile)
        assert cells == []

    def test_mtext_only_space(self, profile):
        # Construct the tree directly (bypass the normalizer which would
        # strip pure-whitespace text); a single space char inside mtext
        # should emit one blank cell.
        root = ET.Element("math")
        text = ET.SubElement(root, "mtext")
        text.text = " "
        cells, _ = emit(root, profile)
        assert len(cells) == 1
        assert cells[0].is_blank

    def test_mtext_with_punctuation(self, profile):
        # comma is in symbols + punctuation; mtext picks symbol first.
        cells, _ = emit(mml("<math><mtext>,</mtext></math>"), profile)
        assert any(c.role == "math_text" for c in cells)


# ---------------------------------------------------------------------------
# Function name lookups
# ---------------------------------------------------------------------------


class TestFunctionExtras:
    def test_cos_in_table(self, profile):
        cells, _ = emit(mml("<math><mi>cos</mi></math>"), profile)
        prefix = next(c for c in cells if c.role == "math_function_prefix")
        assert prefix.dots == (1, 2, 4, 6)
        name = [c for c in cells if c.role == "math_function_name"]
        assert [c.dots for c in name] == [(1, 4)]

    def test_ln_letter_pair(self, profile):
        cells, _ = emit(mml("<math><mi>ln</mi></math>"), profile)
        name = [c for c in cells if c.role == "math_function_name"]
        # ln = l (123) + n (1345)
        assert [c.dots for c in name] == [(1, 2, 3), (1, 3, 4, 5)]

    def test_arcsin_uses_morphology_ref(self, profile):
        # arcsin = c_1 + sin's cells.
        cells, _ = emit(mml("<math><mi>arcsin</mi></math>"), profile)
        name = [c for c in cells if c.role == "math_function_name"]
        assert [c.dots for c in name] == [(1,), (2, 3, 4)]

    def test_lim_in_table_with_mi(self, profile):
        # lim alone as mi — emits as function with no scripts.
        cells, _ = emit(mml("<math><mi>lim</mi></math>"), profile)
        prefix = next(c for c in cells if c.role == "math_function_prefix")
        assert prefix.dots == (1, 2, 4, 6)
        name = [c for c in cells if c.role == "math_function_name"]
        # lim = l + m
        assert [c.dots for c in name] == [(1, 2, 3), (1, 3, 4)]


# ---------------------------------------------------------------------------
# Lookups + caching
# ---------------------------------------------------------------------------


class TestProfileLookups:
    def test_math_symbol_role_for_plus(self, profile):
        assert profile.math_symbol_role("+") == "op"

    def test_math_symbol_role_for_int(self, profile):
        assert profile.math_symbol_role("∫") == "big_op"

    def test_math_symbol_script_prefix_int(self, profile):
        assert profile.math_symbol_script_prefix("∫") is True

    def test_math_symbol_script_prefix_sum(self, profile):
        # ∑ now takes the 46-dot prefix too (《盲文常用数学符号》).
        assert profile.math_symbol_script_prefix("∑") is True

    def test_math_function_big_op_lim(self, profile):
        assert profile.math_function_big_op("lim") is True

    def test_math_function_script_prefix_lim(self, profile):
        assert profile.math_function_script_prefix("lim") is True

    def test_math_function_big_op_sin(self, profile):
        # sin isn't a big-op.
        assert profile.math_function_big_op("sin") is False
