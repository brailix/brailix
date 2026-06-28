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

    def test_mi_multi_char_unregistered_is_letter_word_not_function(
        self, profile
    ):
        # Three-letter name not in the functions table — it's a letter
        # word, not a function: no ⠫, and the same-class run shares ONE
        # lowercase sign: ⠰ + h + y + p. The sign attributes to the first
        # letter it governs — every letter now flows through one per-char
        # emit path, so the prefix's source_text is that char, not the word.
        cells, _ = emit(mml("<math><mi>hyp</mi></math>"), profile)
        assert all(c.role == "math_identifier" for c in cells)
        assert [c.dots for c in cells] == [
            (5, 6), (1, 2, 5), (1, 3, 4, 5, 6), (1, 2, 3, 4),
        ]
        assert [c.source_text for c in cells] == ["h", "h", "y", "p"]

    def test_mi_all_caps_word_keeps_single_capital_sign(self, profile):
        # Math is not embedded English: an all-capital run keeps ONE
        # capital sign (ABC → ⠠⠁⠃⠉), the per-class run carrying the case.
        cells, _ = emit(mml("<math><mi>ABC</mi></math>"), profile)
        assert [c.dots for c in cells] == [
            (6,), (1,), (1, 2), (1, 4),
        ]

    def test_mi_capital_then_lower_runs_split_per_docx(self, profile):
        # Abc → ⠠⠁⠰⠃⠉ — the capital
        # and the lowercase stretch each take their own sign; the
        # lowercase sign covers its whole run.
        cells, _ = emit(mml("<math><mi>Abc</mi></math>"), profile)
        assert [c.dots for c in cells] == [
            (6,), (1,), (5, 6), (1, 2), (1, 4),
        ]

    def test_mi_greek_then_latin_runs_split_per_docx(self, profile):
        # πr → ⠨⠏⠰⠗.
        cells, _ = emit(mml("<math><mi>πr</mi></math>"), profile)
        assert [c.dots for c in cells] == [
            (4, 6), (1, 2, 3, 4), (5, 6), (1, 2, 3, 5),
        ]

    def test_mi_backslash_unknown_keeps_function_prefix_run_spelled(
        self, profile
    ):
        # A literal "\foo" (unrecognised LaTeX command) still routes to
        # the function path (⠫), but the spelled name follows the
        # letter-sign run rule: one ⠰ for the whole lowercase run.
        cells, _ = emit(mml("<math><mi>\\foo</mi></math>"), profile)
        assert cells[0].role == "math_function_prefix"
        rest = cells[1:]
        assert [c.dots for c in rest] == [
            (5, 6), (1, 2, 4), (1, 3, 5), (1, 3, 5),
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

    def test_mn_starting_with_non_digit_suppresses_dangling_number_sign(
        self, profile
    ):
        # A malformed <mn> mis-tagging a non-digit (here a leading thousands
        # separator) must not emit a dangling number sign with no digit
        # behind it; the run is flagged via MATH_MISSING_NUMBER_PART instead.
        cells, wc = emit(mml("<math><mn>,5</mn></math>"), profile)
        assert "number_sign" not in roles(cells)
        assert "MATH_MISSING_NUMBER_PART" in [w.code for w in wc]


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


class TestMoIndicator:
    """Class-marker indicators (⠰ operation / ⠈ negation) are emitted by the
    backend from ``structures.indicator.<name>`` BEFORE the symbol's own
    cells — the symbol table stays bare (just the distinguishing cells, or a
    reference to the base symbol). This is the same backend pathway the ⠫
    symbol marker uses; before this, only the ⠫/symbol path had backend
    coverage (operation and negation were tested at the lookup layer only).

    All assertions compare against profile-derived values
    (``math_structure`` / ``math_symbol``) rather than hard-coded dots, so
    they track the resource tables and catch a missing, wrong, doubled, or
    mis-ordered indicator.
    """

    def test_operation_indicator_prefixes_symbol(self, profile):
        # ∪ (cup): the operation indicator (⠰) precedes the union cell, both
        # tagged math_op. space_before can't fire on a leading symbol (no
        # preceding cell), so a lone ∪ is exactly indicator + symbol.
        cells, wc = emit(mml("<math><mo>∪</mo></math>"), profile)
        indicator = profile.math_structure("indicator.operation")
        symbol = profile.math_symbol("∪")
        assert [c.dots for c in cells] == list(indicator) + list(symbol)
        assert all(c.role == "math_op" for c in cells)
        assert not any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)

    def test_negation_indicator_prefixes_base_symbol(self, profile):
        # ≠ (ne): the negation indicator (⠈) precedes the equals cell. The
        # symbol table references "equals" instead of baking the marker in,
        # so the backend composes ⠈ + the equals cell here.
        cells, wc = emit(mml("<math><mo>≠</mo></math>"), profile)
        indicator = profile.math_structure("indicator.negation")
        symbol = profile.math_symbol("≠")
        non_blank = [c.dots for c in cells if not c.is_blank]
        assert non_blank == list(indicator) + list(symbol)
        assert not any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)

    def test_operation_marker_is_not_the_symbol_marker(self, profile):
        # ∪ leads with the operation indicator (⠰), which is a different cell
        # from the ⠫ symbol marker used by shapes / functions — the
        # indicator name drives which marker the backend emits, it is not
        # cosmetic. A regression that emitted ⠫ for an operation would fail.
        op_cells, _ = emit(mml("<math><mo>∪</mo></math>"), profile)
        op_marker = profile.math_structure("indicator.operation")[0]
        symbol_marker = profile.math_structure("indicator.symbol")[0]
        assert op_cells[0].dots == op_marker
        assert op_marker != symbol_marker


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
        # Multi-char mi that isn't a registered function is a letter run,
        # not a function — no ⠫. A case change starts a new letter sign,
        # which keeps mixed case lossless:
        # Ax → (6 + A) + (56 + x).
        cells, _ = emit(mml("<math><mi>Ax</mi></math>"), profile)
        assert all(c.role == "math_identifier" for c in cells)
        assert [c.dots for c in cells] == [(6,), (1,), (5, 6), (1, 3, 4, 6)]

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

    def test_mn_fullwidth_digit_is_writing_error(self, profile):
        # A full-width digit inside a formula is a source writing error:
        # warn and blank, never silently fold to the half-width digit.
        # (Prose numbers keep folding — full-width digits are routine
        # typography in CJK running text; the divergence is deliberate.)
        cells, wc = emit(mml("<math><mn>２</mn></math>"), profile)
        assert not any(c.role == "math_digit" for c in cells)
        assert any(c.role == "unknown" and c.dots == () for c in cells)
        assert any(w.code == "MATH_UNKNOWN_DIGIT" for w in wc)

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
        # ∑ now takes the 46-dot prefix too.
        assert profile.math_symbol_script_prefix("∑") is True

    def test_math_function_big_op_lim(self, profile):
        assert profile.math_function_big_op("lim") is True

    def test_math_function_script_prefix_lim(self, profile):
        assert profile.math_function_script_prefix("lim") is True

    def test_math_function_big_op_sin(self, profile):
        # sin isn't a big-op.
        assert profile.math_function_big_op("sin") is False
