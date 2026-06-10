r"""End-to-end chemistry braille tests: ``\ce{...}`` → braille cells.

Pins the rules the project's braille domain expert specified:

* subscripts use the lowered digit form with NO subscript indicator
  (H₂O's ``2`` = ⠆);
* an all-single-letter formula gets one leading chemical-formula
  indicator ⠸ and bare element letters (H2O = ⠸ H ⠆ O = 456 + 125 + 23 +
  135);
* a formula with a multi-letter element drops the ⠸ and prefixes each
  element's first letter with the capital sign ⠠ (H2SiO3 = ⠠H ⠆ ⠠Si ⠠O ⠒).
"""

from __future__ import annotations

import pytest

from brailix.backend.math import translate
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector, WarningLevel
from brailix.frontend.math.adapters.chem import convert_ce
from brailix.frontend.math.normalizer import normalize
from brailix.ir.inline import MathInline


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


def ce_cells(inner: str, profile):
    """``\\ce{}`` inner content → (cells, warnings), through the whole
    chem chain: convert_ce → normalize → backend translate."""
    tree = normalize(convert_ce(inner))
    wc = WarningCollector(mode=RunMode.NORMAL)
    ctx = BackendContext(profile="cn_current", warnings=wc)
    node = MathInline(surface="", source="chem", math=tree)
    return translate(node, ctx, profile), wc


def dots(cells):
    return [c.dots for c in cells]


# ---------------------------------------------------------------------------
# All-single-letter formulae: leading ⠸ + bare letters, lowered subscripts
# ---------------------------------------------------------------------------


class TestSingleLetterMode:
    def test_h2o(self, profile):
        # 456 + 125 + 23 + 135  (⠸ H ⠆ O) — the spec example.
        cells, wc = ce_cells("H2O", profile)
        assert dots(cells) == [(4, 5, 6), (1, 2, 5), (2, 3), (1, 3, 5)]
        assert not wc.warnings

    def test_co2(self, profile):
        # ⠸ C O ⠆ : C has no subscript, O carries the 2.
        cells, _ = ce_cells("CO2", profile)
        assert dots(cells) == [(4, 5, 6), (1, 4), (1, 3, 5), (2, 3)]

    def test_leading_indicator_emitted_once(self, profile):
        cells, _ = ce_cells("H2O", profile)
        indicators = [c for c in cells if c.role == "math_chem_indicator"]
        assert len(indicators) == 1

    def test_subscript_has_no_script_marker(self, profile):
        # No math_subscript marker cell anywhere — the lowered digit stands
        # in for the subscript.
        cells, _ = ce_cells("H2O", profile)
        assert all(c.role != "math_subscript" for c in cells)
        assert any(c.role == "math_digit_lower" for c in cells)

    def test_multi_digit_subscript_lowers_each_digit(self, profile):
        # C12 → ⠸ C ⠂⠆ (lower 1 then lower 2), no number sign.
        cells, _ = ce_cells("C12", profile)
        # lower-1 = c_2 = (2,), lower-2 = c_23 = (2,3)
        assert dots(cells) == [(4, 5, 6), (1, 4), (2,), (2, 3)]


# ---------------------------------------------------------------------------
# Multi-letter element: no ⠸, per-element capital sign ⠠
# ---------------------------------------------------------------------------


class TestPerElementMode:
    def test_h2sio3(self, profile):
        # ⠠H ⠆ ⠠Si ⠠O ⠒
        # 6+125 + 23 + 6+234+24 + 6+135 + 25
        cells, wc = ce_cells("H2SiO3", profile)
        assert dots(cells) == [
            (6,), (1, 2, 5), (2, 3),       # ⠠ H ₂
            (6,), (2, 3, 4), (2, 4),       # ⠠ S i
            (6,), (1, 3, 5), (2, 5),       # ⠠ O ₃
        ]
        assert not wc.warnings

    def test_no_leading_indicator_in_per_element_mode(self, profile):
        cells, _ = ce_cells("H2SiO3", profile)
        assert all(c.role != "math_chem_indicator" for c in cells)

    def test_capital_sign_per_element(self, profile):
        cells, _ = ce_cells("H2SiO3", profile)
        caps = [c for c in cells if c.role == "math_chem_capital"]
        # One per element: H, Si, O.
        assert len(caps) == 3
        assert all(c.dots == (6,) for c in caps)

    def test_nacl(self, profile):
        # ⠠Na ⠠Cl = ⠠ N a ⠠ C l
        cells, _ = ce_cells("NaCl", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4, 5), (1,),   # ⠠ N a
            (6,), (1, 4), (1, 2, 3),    # ⠠ C l
        ]

    def test_second_letter_is_bare_lowercase(self, profile):
        # The "i" in Si must be the bare lowercase letter (2,4), with no
        # latin_lower prefix (⠰) in front of it.
        cells, _ = ce_cells("SiO2", profile)
        # ⠠ S i ⠠ O ⠆
        assert dots(cells) == [(6,), (2, 3, 4), (2, 4), (6,), (1, 3, 5), (2, 3)]


# ---------------------------------------------------------------------------
# Gas ↑ / precipitate ↓ arrows (uarr = ⠰⠌ / darr = ⠘⠡), attached with no
# leading space.
# ---------------------------------------------------------------------------


class TestGasPrecipitate:
    def test_gas_arrow(self, profile):
        # ⠸ O ⠆ ↑ — uarr = c_56 + c_34.
        cells, wc = ce_cells("O2 ^", profile)
        assert dots(cells) == [
            (4, 5, 6), (1, 3, 5), (2, 3), (5, 6), (3, 4)
        ]
        assert not wc.warnings

    def test_precipitate_arrow(self, profile):
        # ⠸ H ⠆ ↓ — darr = c_45 + c_16.
        cells, _ = ce_cells("H2 v", profile)
        assert dots(cells) == [
            (4, 5, 6), (1, 2, 5), (2, 3), (4, 5), (1, 6)
        ]

    def test_literal_arrow_flagged_in_ce(self, profile):
        # \ce{} is LaTeX — a literal ↑ is non-standard (use ^) and flagged,
        # not treated as a gas arrow.
        _, wc = ce_cells("O2 ↑", profile)
        assert wc.by_code("MATH_NONSTANDARD_CHAR")

    def test_arrow_attaches_without_leading_blank(self, profile):
        # No empty () blank cell anywhere — the arrow sits flush against the
        # formula, unlike the spaced relation arrow in maths.
        cells, _ = ce_cells("O2 ^", profile)
        assert all(c.dots != () for c in cells)


# ---------------------------------------------------------------------------
# Equations: coefficients + operators reuse maths; casing is per molecule;
# reaction connectors = (yields) and ⇌ (reversible).
# ---------------------------------------------------------------------------


class TestEquations:
    def test_leading_coefficient_reuses_maths(self, profile):
        # 2H2O → ⠼⠃ (number-sign + 2) then ⠸ H ⠆ O. The coefficient is an
        # ordinary maths number (number sign), the molecule keeps its ⠸.
        cells, wc = ce_cells("2H2O", profile)
        assert dots(cells) == [
            (3, 4, 5, 6), (1, 2),            # number sign + 2
            (4, 5, 6), (1, 2, 5), (2, 3), (1, 3, 5),  # ⠸ H ₂ O
        ]
        assert cells[0].role == "number_sign"
        assert not wc.warnings

    def test_casing_decided_per_molecule(self, profile):
        # Na + H2 → ⠠Na (per-element, Na is multi-letter) + ⠸H₂ (batch, all
        # single-letter). Each molecule decides independently.
        cells, _ = ce_cells("Na + H2", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4, 5), (1,),        # ⠠ N a
            (), (2, 3, 5),                   # blank + plus sign +
            (4, 5, 6), (1, 2, 5), (2, 3),    # ⠸ H ₂
        ]

    def test_full_equation_yields_connector(self, profile):
        # 2H2 + O2 = 2H2O — mhchem -> / literal = both render as the equals sign ⠶.
        cells, wc = ce_cells("2H2 + O2 -> 2H2O", profile)
        assert dots(cells) == [
            (3, 4, 5, 6), (1, 2), (4, 5, 6), (1, 2, 5), (2, 3),  # ⠼2 ⠸ H ₂
            (), (2, 3, 5),                                       # ␣ +
            (4, 5, 6), (1, 3, 5), (2, 3),                        # ⠸ O ₂
            (), (2, 3, 5, 6),                                    # ␣ =
            (3, 4, 5, 6), (1, 2), (4, 5, 6), (1, 2, 5), (2, 3), (1, 3, 5),  # ⠼2 ⠸ H ₂ O
        ]
        assert not wc.warnings

    def test_spaceless_equation_matches_spaced(self, profile):
        # The spaceless school-equation form 2H2+O2=2H2O (how equations are
        # actually written) must render identically to the mhchem spaced form:
        # the + is the addition operator even with no surrounding space,
        # because a new species follows it.
        spaceless, wc = ce_cells("2H2+O2=2H2O", profile)
        spaced, _ = ce_cells("2H2 + O2 -> 2H2O", profile)
        assert dots(spaceless) == dots(spaced)
        assert not wc.warnings

    def test_reversible_connector(self, profile):
        # N2 <=> O2 — reversible ⇌ = ⠐⠶⠄ (5 + 2356 + 3).
        cells, _ = ce_cells("N2 <=> O2", profile)
        assert dots(cells) == [
            (4, 5, 6), (1, 3, 4, 5), (2, 3),     # ⠸ N ₂
            (), (5,), (2, 3, 5, 6), (3,),        # ␣ ⇌ (⠐⠶⠄)
            (4, 5, 6), (1, 3, 5), (2, 3),        # ⠸ O ₂
        ]


# ---------------------------------------------------------------------------
# Reaction conditions (over/under the connector): 46-prefix ⠨ + superscript
# sign ⠌ / subscript sign ⠡ + content; heat Δ is the inline ⠘⠸⠲ symbol.
# ---------------------------------------------------------------------------


class TestConditions:
    def test_heat_condition_is_inline_symbol(self, profile):
        # A ->[\Delta] B → ⠸A ␣ = ⠘⠸⠲ ⠸B. Heat is inline (no 46-prefix).
        cells, wc = ce_cells(r"A ->[\Delta] B", profile)
        assert dots(cells) == [
            (4, 5, 6), (1,),               # ⠸ A
            (), (2, 3, 5, 6),              # ␣ =
            (4, 5), (4, 5, 6), (2, 5, 6),  # ⠘⠸⠲ heat
            (4, 5, 6), (1, 2),             # ⠸ B
        ]
        assert not wc.warnings
        assert any(c.role == "math_chem_heat" for c in cells)

    def test_formula_condition_uses_46_prefix(self, profile):
        # A ->[O2] B → ⠸A ␣ = ⠨⠌ ⠸O₂ ⠱ ⠸B. 46-prefix ⠨ + superscript sign ⠌
        # + O₂ + close.
        cells, wc = ce_cells("A ->[O2] B", profile)
        assert dots(cells) == [
            (4, 5, 6), (1,),                       # ⠸ A
            (), (2, 3, 5, 6),                      # ␣ =
            (4, 6), (3, 4),                        # ⠨ 46-prefix + ⠌ superscript sign
            (4, 5, 6), (1, 3, 5), (2, 3),          # ⠸ O ₂  (condition keeps its own casing)
            (1, 5, 6),                             # ⠱ close
            (4, 5, 6), (1, 2),                     # ⠸ B
        ]
        assert not wc.warnings
        assert any(c.role == "math_big_op_script_prefix" for c in cells)

    def test_above_and_below_conditions(self, profile):
        # A ->[O2][\Delta] B → above O₂ (46-prefix), below Δ (inline heat).
        cells, _ = ce_cells(r"A ->[O2][\Delta] B", profile)
        assert dots(cells) == [
            (4, 5, 6), (1,),                       # ⠸ A
            (), (2, 3, 5, 6),                      # ␣ =
            (4, 6), (3, 4),                        # ⠨ + ⠌ (above)
            (4, 5, 6), (1, 3, 5), (2, 3),          # ⠸ O ₂
            (1, 5, 6),                             # ⠱ close
            (4, 5), (4, 5, 6), (2, 5, 6),          # ⠘⠸⠲ heat (below, inline)
            (4, 5, 6), (1, 2),                     # ⠸ B
        ]

    def test_chinese_condition_uses_injected_translator(self, profile):
        # A prose (Chinese) condition is translated through the
        # pipeline-injected ``inline_text_translator`` (the zh text path) and
        # spliced after the 46-prefix — no per-char unknown fallback.
        from brailix.ir.braille import BrailleCell

        sentinel = [BrailleCell(dots=(1, 2, 3), role="zh_test")]
        tree = normalize(convert_ce("H2 + Cl2 ->[点燃] HCl"))
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(
            profile="cn_current",
            warnings=wc,
            options={"inline_text_translator": lambda _t: list(sentinel)},
        )
        cells = translate(
            MathInline(surface="", source="chem", math=tree), ctx, profile
        )
        d = dots(cells)
        # 46-prefix ⠨ + superscript sign ⠌ then the translator's cells, then close ⠱.
        assert (4, 6) in d and (3, 4) in d
        assert (1, 2, 3) in d  # the translated condition cells
        assert not any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in wc.warnings)

    def test_chinese_condition_fallback_without_translator(self, profile):
        # With no translator injected (a bare backend-only run), a Chinese
        # condition degrades to the per-char placeholder + warning rather than
        # crashing; it still emits the 46-prefix structure.
        cells, wc = ce_cells("H2 + Cl2 ->[点燃] HCl", profile)
        assert any(c.role == "math_big_op_script_prefix" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in wc.warnings)


# ---------------------------------------------------------------------------
# Chem mode does not leak into ordinary maths
# ---------------------------------------------------------------------------


class TestChemModeIsolation:
    def test_ordinary_math_unaffected(self, profile):
        # A normal x_2 (no data-bk-chem) keeps its subscript marker.
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        tree = normalize("<math><msub><mi>x</mi><mn>2</mn></msub></math>")
        node = MathInline(surface="", source="mathml", math=tree)
        cells = translate(node, ctx, profile)
        # The math subscript marker (script.sub) must still be present.
        assert any(c.role == "math_subscript" for c in cells)


# ---------------------------------------------------------------------------
# Ionic charges: charge sign ⠨ + [number sign + digit] + plus sign ⠖ / minus
# sign ⠤. The ``+`` after a magnitude takes the ⠠ guard (⠖ = lowered digit 6);
# the ``-`` never does.
# ---------------------------------------------------------------------------


class TestCharges:
    def test_na_plus(self, profile):
        # Na⁺ = ⠠N a ⠨ ⠖ — the spec example (6+1345+1+46+235).
        cells, wc = ce_cells("Na+", profile)
        assert dots(cells) == [(6,), (1, 3, 4, 5), (1,), (4, 6), (2, 3, 5)]
        assert not wc.warnings

    def test_mg_2plus(self, profile):
        # Mg²⁺ = ⠠M g ⠨ ⠼⠃ ⠠⠖ — the ⠠ guard sits before ⠖
        # (6+134+1245+46+3456+12+6+235).
        cells, wc = ce_cells("Mg^2+", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4), (1, 2, 4, 5),   # ⠠ M g
            (4, 6),                          # ⠨ charge sign
            (3, 4, 5, 6), (1, 2),            # ⠼ 2
            (6,), (2, 3, 5),                 # ⠠ guard + ⠖
        ]
        assert not wc.warnings

    def test_f_minus(self, profile):
        # F⁻ = ⠠F ⠨ ⠤ (6+124+46+36).
        cells, wc = ce_cells("F-", profile)
        assert dots(cells) == [(6,), (1, 2, 4), (4, 6), (3, 6)]
        assert not wc.warnings

    def test_o_2minus(self, profile):
        # O²⁻ = ⠠O ⠨ ⠼⠃ ⠤ — no guard before ⠤ (6+135+46+3456+12+36).
        cells, wc = ce_cells("O^2-", profile)
        assert dots(cells) == [
            (6,), (1, 3, 5), (4, 6), (3, 4, 5, 6), (1, 2), (3, 6)
        ]
        assert not wc.warnings

    def test_single_letter_ion_uses_capital_not_indicator(self, profile):
        # F⁻ / O²⁻ take the capital sign ⠠, never the chemical-formula
        # indicator ⠸ that a neutral single-letter molecule (O₂) carries.
        cells, _ = ce_cells("O^2-", profile)
        assert all(c.role != "math_chem_indicator" for c in cells)
        assert any(c.role == "math_chem_capital" for c in cells)

    def test_plus_guard_only_with_magnitude(self, profile):
        # The ⠠ guard distinguishes ⠖ from the lowered digit 6 (same cell):
        # a unit charge (Na⁺) has no number before ⠖, so no guard; a
        # magnitude-2 charge (Mg²⁺) does.
        na, _ = ce_cells("Na+", profile)
        assert not any(c.role == "math_chem_charge_guard" for c in na)
        mg, _ = ce_cells("Mg^2+", profile)
        assert any(c.role == "math_chem_charge_guard" for c in mg)

    def test_minus_never_takes_guard(self, profile):
        # ⠤ collides with no lowered digit, so even a magnitude-2 minus has
        # no guard.
        cells, _ = ce_cells("O^2-", profile)
        assert not any(c.role == "math_chem_charge_guard" for c in cells)

    def test_coefficient_then_ion(self, profile):
        # 2Na⁺ = ⠼⠃ then ⠠Na ⠨ ⠖ — the coefficient is an ordinary number,
        # the charge still attaches (the species-atom count resets at the
        # coefficient boundary).
        cells, wc = ce_cells("2Na+", profile)
        assert dots(cells) == [
            (3, 4, 5, 6), (1, 2),                          # ⠼ 2
            (6,), (1, 3, 4, 5), (1,), (4, 6), (2, 3, 5),   # ⠠Na ⠨ ⠖
        ]
        assert not wc.warnings

    def test_ion_equation_spacing(self, profile):
        # Na+ + Cl- -> NaCl : the reaction + and the = connector keep their
        # leading space even though the preceding ion ends in a charge sign
        # (the sign is a postfix mark, not a binary operator).
        cells, wc = ce_cells("Na+ + Cl- -> NaCl", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4, 5), (1,), (4, 6), (2, 3, 5),        # ⠠Na ⠨ ⠖  (Na⁺)
            (), (2, 3, 5),                                      # ␣ + (addition)
            (6,), (1, 4), (1, 2, 3), (4, 6), (3, 6),            # ⠠Cl ⠨ ⠤  (Cl⁻)
            (), (2, 3, 5, 6),                                   # ␣ = (yields)
            (6,), (1, 3, 4, 5), (1,), (6,), (1, 4), (1, 2, 3),  # ⠠Na ⠠Cl
        ]
        assert not wc.warnings

    def test_polyatomic_ion_uses_chem_indicator(self, profile):
        # SO4²⁻ = ⠸ S O ⠴ ⠨ ⠼⠃ ⠤ — all single-letter, so the whole group
        # takes one leading chemical-formula indicator ⠸ (not a per-element
        # capital sign), with the charge appended after.
        cells, wc = ce_cells("SO4^2-", profile)
        assert dots(cells) == [
            (4, 5, 6), (2, 3, 4), (1, 3, 5), (2, 5, 6),   # ⠸ S O ₄
            (4, 6), (3, 4, 5, 6), (1, 2), (3, 6),         # ⠨ ⠼2 ⠤
        ]
        assert not wc.warnings

    def test_polyatomic_unit_charge(self, profile):
        # OH⁻ = ⠸ O H ⠨ ⠤ (all single-letter, unit charge).
        cells, wc = ce_cells("OH-", profile)
        assert dots(cells) == [(4, 5, 6), (1, 3, 5), (1, 2, 5), (4, 6), (3, 6)]
        assert not wc.warnings

    def test_polyatomic_with_multiletter_uses_capitals(self, profile):
        # MnO4⁻ — Mn is multi-letter, so the group drops ⠸ and each element
        # takes the capital sign ⠠ (⠠Mn ⠠O₄), then the charge.
        cells, _ = ce_cells("MnO4-", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4), (1, 3, 4, 5),   # ⠠ M n
            (6,), (1, 3, 5), (2, 5, 6),      # ⠠ O ₄
            (4, 6), (3, 6),                  # ⠨ ⠤
        ]
        assert all(c.role != "math_chem_indicator" for c in cells)

    def test_bracket_complex_translates(self, profile):
        # [Cu(NH3)4]^2+ — square brackets ⠷⠾ (math), per-run casing inside:
        # ⠠Cu (multi-letter run) then the (NH3) ligand ⠣⠸NH₃⠜ ×4, then the 2+
        # charge. The whole bracket carries the charge.
        cells, wc = ce_cells("[Cu(NH3)4]^2+", profile)
        assert dots(cells) == [
            (1, 2, 3, 5, 6),                          # ⠷ [
            (6,), (1, 4), (1, 3, 6),                  # ⠠ C u
            (1, 2, 6), (4, 5, 6), (1, 3, 4, 5), (1, 2, 5), (2, 5), (3, 4, 5),  # ⠣⠸N H₃⠜
            (2, 5, 6),                                # ₄ (group ×4, lowered)
            (2, 3, 4, 5, 6),                          # ⠾ ]
            (4, 6), (3, 4, 5, 6), (1, 2), (6,), (2, 3, 5),  # ⠨ ⠼2 ⠠⠖
        ]
        assert not wc.warnings


# ---------------------------------------------------------------------------
# Parenthesised groups: math parens ⠣⠜, group multiplier = lowered digit, and
# each run cased on its own — Ca(OH)₂ = ⠠Ca ⠣ ⠸OH ⠜ ⠆, (NH₄)₂SO₄ carries TWO
# chemical-formula indicators (one per all-single-letter run).
# ---------------------------------------------------------------------------


class TestParentheses:
    def test_ca_oh_2(self, profile):
        # Ca(OH)₂ = ⠠C a ⠣ ⠸ O H ⠜ ⠆ (6+14+1+126+456+135+125+345+23).
        cells, wc = ce_cells("Ca(OH)2", profile)
        assert dots(cells) == [
            (6,), (1, 4), (1,),              # ⠠ C a
            (1, 2, 6),                       # ⠣ (
            (4, 5, 6), (1, 3, 5), (1, 2, 5), # ⠸ O H
            (3, 4, 5),                       # ⠜ )
            (2, 3),                          # ⠆ ₂ (group multiplier, lowered)
        ]
        assert not wc.warnings

    def test_nh4_2_so4(self, profile):
        # (NH₄)₂SO₄ = ⠣ ⠸ N H ⠦ ⠜ ⠆ ⠸ S O ⠦ — note TWO chemical-formula
        # indicators (126+456+1345+125+256+345+23+456+234+135+256).
        cells, wc = ce_cells("(NH4)2SO4", profile)
        assert dots(cells) == [
            (1, 2, 6),                              # ⠣ (
            (4, 5, 6), (1, 3, 4, 5), (1, 2, 5), (2, 5, 6),  # ⠸ N H ₄
            (3, 4, 5),                              # ⠜ )
            (2, 3),                                 # ⠆ ₂
            (4, 5, 6), (2, 3, 4), (1, 3, 5), (2, 5, 6),     # ⠸ S O ₄
        ]
        assert not wc.warnings

    def test_two_indicators_one_per_run(self, profile):
        # The (NH₄) group and the trailing SO₄ are cased independently, so the
        # chemical-formula indicator ⠸ appears once per run.
        cells, _ = ce_cells("(NH4)2SO4", profile)
        assert sum(c.role == "math_chem_indicator" for c in cells) == 2

    def test_multiletter_outer_uses_capitals(self, profile):
        # Fe(OH)₃ — Fe is multi-letter, so the outer run takes the capital
        # sign ⠠ (⠠Fe), while the all-single-letter (OH) group still gets its ⠸.
        cells, _ = ce_cells("Fe(OH)3", profile)
        assert dots(cells) == [
            (6,), (1, 2, 4), (1, 5),         # ⠠ F e
            (1, 2, 6),                       # ⠣ (
            (4, 5, 6), (1, 3, 5), (1, 2, 5), # ⠸ O H
            (3, 4, 5),                       # ⠜ )
            (2, 5),                          # ⠒ ₃ (lowered)
        ]

    def test_atom_subscript_and_group_multiplier(self, profile):
        # Al₂(SO₄)₃ — Al's own subscript 2 and the group multiplier 3 are both
        # lowered digits; the (SO₄) group keeps its ⠸.
        cells, wc = ce_cells("Al2(SO4)3", profile)
        assert dots(cells) == [
            (6,), (1,), (1, 2, 3), (2, 3),              # ⠠ A l ₂
            (1, 2, 6),                                  # ⠣ (
            (4, 5, 6), (2, 3, 4), (1, 3, 5), (2, 5, 6), # ⠸ S O ₄
            (3, 4, 5),                                  # ⠜ )
            (2, 5),                                      # ⠒ ₃
        ]
        assert not wc.warnings

    def test_paren_group_in_equation(self, profile):
        # Ca(OH)2 -> CaO + H2O : a paren group inside an equation translates,
        # connectors keep their spacing.
        _, wc = ce_cells("Ca(OH)2 -> CaO + H2O", profile)
        assert not wc.warnings


# ---------------------------------------------------------------------------
# Physical-state labels: ⠣ + Latin-lower prefix ⠰ + bare English letters + ⠜.
# (aq) = ⠣ ⠰ a q ⠜, (l) = ⠣ ⠰ l ⠜ — English abbreviations, not elements.
# ---------------------------------------------------------------------------


class TestStates:
    def test_aqueous(self, profile):
        # NaCl(aq) → ⠠Na ⠠Cl then ⠣ ⠰ a q ⠜.
        cells, wc = ce_cells("NaCl(aq)", profile)
        assert dots(cells) == [
            (6,), (1, 3, 4, 5), (1,), (6,), (1, 4), (1, 2, 3),  # ⠠Na ⠠Cl
            (1, 2, 6),                                          # ⠣ (
            (5, 6), (1,), (1, 2, 3, 4, 5),                      # ⠰ a q
            (3, 4, 5),                                          # ⠜ )
        ]
        assert not wc.warnings

    def test_liquid(self, profile):
        # H2O(l) → ⠸H₂O then ⠣ ⠰ l ⠜.
        cells, wc = ce_cells("H2O(l)", profile)
        assert dots(cells) == [
            (4, 5, 6), (1, 2, 5), (2, 3), (1, 3, 5),  # ⠸ H ₂ O
            (1, 2, 6), (5, 6), (1, 2, 3), (3, 4, 5),  # ⠣ ⠰ l ⠜
        ]
        assert not wc.warnings

    def test_single_latin_prefix_per_state(self, profile):
        # Exactly one Latin-lowercase prefix for the whole state run (aq),
        # not one per letter.
        cells, _ = ce_cells("NaCl(aq)", profile)
        assert sum(c.role == "math_chem_state_prefix" for c in cells) == 1

    @pytest.mark.parametrize("inner,letters", [("C(s)", "s"), ("CO2(g)", "g")])
    def test_solid_and_gas(self, profile, inner, letters):
        cells, wc = ce_cells(inner, profile)
        bare = profile.bare_letter(letters)
        # ⠣ ⠰ <letter> ⠜ are the last four cells.
        assert dots(cells)[-4:] == [(1, 2, 6), (5, 6), bare, (3, 4, 5)]
        assert not wc.warnings


# ---------------------------------------------------------------------------
# Square-bracket complex ions: math brackets ⠷⠾, per-run casing inside, an
# optional whole-bracket charge — [Cu(NH3)4]²⁺, [Ag(NH3)2]⁺, [Fe(CN)6]³⁻.
# ---------------------------------------------------------------------------


class TestBrackets:
    def test_unit_charge_complex(self, profile):
        # [Ag(NH3)2]+ — unit positive charge: ⠷ ⠠Ag ⠣⠸NH₃⠜ ₂ ⠾ ⠨ ⠖
        # (no magnitude digit, so no number sign and no ⠠ guard).
        cells, wc = ce_cells("[Ag(NH3)2]+", profile)
        assert dots(cells) == [
            (1, 2, 3, 5, 6),                  # ⠷ [
            (6,), (1,), (1, 2, 4, 5),         # ⠠ A g
            (1, 2, 6), (4, 5, 6), (1, 3, 4, 5), (1, 2, 5), (2, 5), (3, 4, 5),  # ⠣⠸NH₃⠜
            (2, 3),                            # ₂ (group ×2, lowered)
            (2, 3, 4, 5, 6),                  # ⠾ ]
            (4, 6), (2, 3, 5),                # ⠨ ⠖
        ]
        assert not wc.warnings

    def test_bracket_uses_math_bracket_cells(self, profile):
        # The square brackets are the math lbrack ⠷ / rbrack ⠾ cells.
        cells, _ = ce_cells("[Fe(CN)6]^3-", profile)
        d = dots(cells)
        assert d[0] == (1, 2, 3, 5, 6)        # leading ⠷ [
        assert (2, 3, 4, 5, 6) in d           # ⠾ ]

    def test_neutral_bracket_in_formula(self, profile):
        # K3[Fe(CN)6] (neutral salt) — a bracket with no charge tail still
        # renders; the K₃ outside keeps its own ⠸.
        cells, wc = ce_cells("K3[Fe(CN)6]", profile)
        assert not wc.warnings
        assert dots(cells)[0] == (4, 5, 6)    # leading ⠸ for K₃


# ---------------------------------------------------------------------------
# Encoding tolerance + non-standard-writing diagnostics (end to end)
# ---------------------------------------------------------------------------


class TestNonStandardCharWarnings:
    def test_fullwidth_letter_warns_at_warn_level_not_translated(self, profile):
        cells, wc = ce_cells("Ｈ２Ｏ", profile)
        hits = wc.by_code("MATH_NONSTANDARD_CHAR")
        assert len(hits) == 2  # Ｈ and Ｏ (the full-width digit ２ folds)
        assert all(h.level is WarningLevel.WARN for h in hits)
        assert "half-width" in hits[0].message
        # not silently translated as if it were H2O
        b, _ = ce_cells("H2O", profile)
        assert dots(cells) != dots(b)

    def test_fullwidth_operator_warns(self, profile):
        _, wc = ce_cells("2H2 ＝ 2H2O", profile)
        hits = wc.by_code("MATH_NONSTANDARD_CHAR")
        assert len(hits) == 1
        assert "half-width" in hits[0].message

    def test_zero_width_char_warns(self, profile):
        _, wc = ce_cells("H2​O", profile)
        hits = wc.by_code("MATH_NONSTANDARD_CHAR")
        assert len(hits) == 1
        assert "zero-width" in hits[0].message

    def test_lowercase_element_is_error_level(self, profile):
        _, wc = ce_cells("h2o", profile)
        errs = wc.by_code("MATH_ERROR")
        assert len(errs) == 1
        assert errs[0].level is WarningLevel.ERROR
        assert "capitalised" in errs[0].message

    def test_unicode_arrow_flagged_in_ce(self, profile):
        # \ce{} is LaTeX — a literal → is non-standard input, flagged (not
        # silently translated like the ASCII -> connector).
        _, wc = ce_cells("Na + Cl2 → 2NaCl", profile)
        assert wc.by_code("MATH_NONSTANDARD_CHAR")


class TestRepeatedConnectorWarning:
    def test_double_equals_warns_at_warn_level(self, profile):
        cells, wc = ce_cells("H2 + O2 == H2O", profile)
        hits = wc.by_code("MATH_REPEATED_OPERATOR")
        assert len(hits) == 1
        assert hits[0].level is WarningLevel.WARN
        # faithful output: both '=' cells (⠶ = 2356) still present
        assert dots(cells).count((2, 3, 5, 6)) == 2

    def test_single_equals_no_warning(self, profile):
        _, wc = ce_cells("H2 + O2 = H2O", profile)
        assert wc.by_code("MATH_REPEATED_OPERATOR") == []

    def test_double_bond_no_warning(self, profile):
        # O=C=O: two double bonds, not consecutive — must not warn.
        _, wc = ce_cells("O=C=O", profile)
        assert wc.by_code("MATH_REPEATED_OPERATOR") == []


# ---------------------------------------------------------------------------
# Triple bond  #  →  ⠿ (c_123456), tight (no surrounding space)
# ---------------------------------------------------------------------------


class TestTripleBond:
    def test_n_triple_n_is_one_molecule(self, profile):
        # ⠸ N ⠿ N — one leading indicator (the bond stays inside the molecule
        # run), ⠿ tight with no blank cell on either side.
        cells, wc = ce_cells("N#N", profile)
        assert dots(cells) == [
            (4, 5, 6),         # ⠸ one chemical-formula indicator
            (1, 3, 4, 5),      # N
            (1, 2, 3, 4, 5, 6),  # ⠿ triple bond
            (1, 3, 4, 5),      # N
        ]
        assert cells[2].role == "math_chem_bond"
        assert not wc.warnings

    def test_not_the_math_equiv_cell(self, profile):
        # The chem triple bond is ⠿, NOT the math ≡ / equiv ⠘⠶ (⠘ = 56).
        assert (5, 6) not in dots(ce_cells("N#N", profile)[0])

    def test_hash_is_the_only_triple_bond_input(self, profile):
        # mhchem ``#`` works; a literal ≡ is non-standard in \ce{} and flagged.
        _, wc = ce_cells("C≡C", profile)
        assert wc.by_code("MATH_NONSTANDARD_CHAR")


# ---------------------------------------------------------------------------
# Double bond  =  (tight, in a lone molecule)  →  ⠶, no space, one indicator;
# the spaced / spaceless reaction yields keeps its spacing.
# ---------------------------------------------------------------------------


class TestDoubleBond:
    def test_oco_is_one_molecule_tight(self, profile):
        # ⠸ O ⠶ C ⠶ O — one indicator, both double bonds ⠶ tight (no 空方).
        cells, wc = ce_cells("O=C=O", profile)
        assert dots(cells) == [
            (4, 5, 6),     # ⠸
            (1, 3, 5),     # O
            (2, 3, 5, 6),  # ⠶ double bond
            (1, 4),        # C
            (2, 3, 5, 6),  # ⠶ double bond
            (1, 3, 5),     # O
        ]
        assert cells[2].role == "math_chem_bond"
        assert not wc.warnings

    def test_double_bond_indicator_emitted_once(self, profile):
        cells, _ = ce_cells("CH2=CH2", profile)
        inds = [c for c in cells if c.role == "math_chem_indicator"]
        assert len(inds) == 1  # one molecule, one ⠸
        assert () not in dots(cells)  # tight: no blank around the bond

    def test_reaction_yields_keeps_spacing(self, profile):
        # In a reaction the '=' is the yields connector — spaced (blank before)
        # and re-indicating each species — NOT a tight double bond.
        cells, _ = ce_cells("H2 + O2 = H2O", profile)
        d = dots(cells)
        assert () in d  # the yields keeps its leading blank (space)
        inds = [c for c in cells if c.role == "math_chem_indicator"]
        assert len(inds) == 3  # one per species

    def test_spaceless_equation_still_yields(self, profile):
        # 2H2+O2=2H2O (school spaceless form) — the tight '=' is still the
        # yields, identical to the spaced form, not a double bond.
        a, _ = ce_cells("2H2+O2=2H2O", profile)
        b, _ = ce_cells("2H2 + O2 = 2H2O", profile)
        assert dots(a) == dots(b)
        assert not any(c.role == "math_chem_bond" for c in a)


# ---------------------------------------------------------------------------
# Reverse reaction arrow  <-  →  ⠠⠶⠂ (chem.arrow_reverse), spaced
# ---------------------------------------------------------------------------


class TestReverseArrow:
    _REV = [(6,), (2, 3, 5, 6), (2,)]  # ⠠⠶⠂

    def test_reverse_arrow_cells_and_leading_space(self, profile):
        cells, wc = ce_cells("2NH3 <- N2 + 3H2", profile)
        d = dots(cells)
        k = next(
            (k for k in range(len(d) - 2) if d[k : k + 3] == self._REV), None
        )
        assert k is not None  # ⠠⠶⠂ present
        assert d[k - 1] == ()  # spaced: a blank cell sits before it
        assert cells[k].role == "math_rel"
        assert not wc.warnings

    def test_reverse_arrow_is_not_the_math_left_arrow(self, profile):
        # ⠫ (1-2-4-6, the math larr's lead cell) must not appear.
        assert (1, 2, 4, 6) not in dots(ce_cells("A <- B", profile)[0])
