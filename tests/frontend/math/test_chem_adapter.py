r"""Tests for the mhchem ``\ce{...}`` → MathML chemistry adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.frontend.math.adapters.chem import (
    ChemMathSourceAdapter,
    convert_ce,
    extract_ce_inner,
)
from brailix.frontend.math.adapters.latex import LatexMathSourceAdapter

_NS = "{http://www.w3.org/1998/Math/MathML}"


def _merror(out: str) -> ET.Element | None:
    return ET.fromstring(out).find(f".//{_NS}merror")


# ---------------------------------------------------------------------------
# \ce{...} detection / extraction
# ---------------------------------------------------------------------------


class TestExtractCeInner:
    @pytest.mark.parametrize(
        "text,inner",
        [
            (r"\ce{H2O}", "H2O"),
            (r"\ce {H2O}", "H2O"),          # optional space before {
            (r"  \ce{H2SiO3}  ", "H2SiO3"),  # surrounding whitespace
            (r"\ce{->[{cat}]{heat}}", "->[{cat}]{heat}"),  # nested braces
        ],
    )
    def test_extracts_top_level_ce(self, text, inner):
        assert extract_ce_inner(text) == inner

    @pytest.mark.parametrize(
        "text",
        [
            "x^2",               # ordinary LaTeX, no \ce
            r"\cefoo{H2O}",      # not the \ce macro
            r"\ce{H2O} + 1",     # trailing content → not a pure \ce
            r"\ce{H2O",          # unbalanced
            r"\ce",              # no brace group
        ],
    )
    def test_rejects_non_ce(self, text):
        assert extract_ce_inner(text) is None


# ---------------------------------------------------------------------------
# convert_ce → MathML shape
# ---------------------------------------------------------------------------


class TestConvertCe:
    def test_tags_root_as_chem(self):
        root = ET.fromstring(convert_ce("H2O"))
        assert root.get("data-bk-chem") == "1"

    def test_subscript_becomes_msub(self):
        root = ET.fromstring(convert_ce("H2O"))
        # H2O → <msub><mi>H</mi><mn>2</mn></msub><mi>O</mi>
        kids = list(root)
        assert kids[0].tag == f"{_NS}msub"
        assert [c.tag for c in kids[0]] == [f"{_NS}mi", f"{_NS}mn"]
        assert kids[0][0].text == "H" and kids[0][1].text == "2"
        assert kids[1].tag == f"{_NS}mi" and kids[1].text == "O"

    def test_multi_letter_element_is_single_mi(self):
        root = ET.fromstring(convert_ce("H2SiO3"))
        mis = [e.text for e in root.iter(f"{_NS}mi")]
        # Si is one <mi>, not S + i.
        assert mis == ["H", "Si", "O"]

    def test_bare_element_no_subscript(self):
        root = ET.fromstring(convert_ce("NaCl"))
        assert [e.tag for e in root] == [f"{_NS}mi", f"{_NS}mi"]
        assert [e.text for e in root] == ["Na", "Cl"]

    @pytest.mark.parametrize(
        "inner,arrow",
        [
            ("O2 ^", "↑"),       # mhchem gas
            ("BaSO4 v", "↓"),    # mhchem precipitate
        ],
    )
    def test_gas_precipitate_arrow_becomes_mo(self, inner, arrow):
        # Only the mhchem ASCII forms ^ / v are recognised; the emitted <mo>
        # still carries the canonical ↑ / ↓ glyph for the backend.
        root = ET.fromstring(convert_ce(inner))
        mos = [e.text for e in root.iter(f"{_NS}mo")]
        assert mos == [arrow]

    @pytest.mark.parametrize("inner", ["", "   "])
    def test_empty_yields_merror(self, inner):
        assert _merror(convert_ce(inner)) is not None

    @pytest.mark.parametrize("src", ["C#C", "N#N", "HC#CH"])
    def test_triple_bond_emits_bond_marker(self, src):
        # mhchem ``#`` becomes a triple-bond <mo> the backend renders ⠿ — no
        # longer a degraded soft <merror>. (A literal ≡ is non-standard in
        # \ce{} and is flagged instead — see TestUnicodeArrowsRejectedInCe.)
        out = convert_ce(src)
        assert _merror(out) is None
        bonds = [
            e
            for e in ET.fromstring(out).iter(f"{_NS}mo")
            if e.get("data-bk-chem-bond") == "triple"
        ]
        assert len(bonds) == 1 and bonds[0].text == "≡"

    def test_leading_coefficient_is_mn(self):
        root = ET.fromstring(convert_ce("2H2O"))
        # 2 H2O → <mn>2</mn> then the molecule.
        assert root[0].tag == f"{_NS}mn" and root[0].text == "2"
        assert root[1].tag == f"{_NS}msub"

    @pytest.mark.parametrize(
        "inner,texts",
        [
            ("H2 + O2", ["+"]),
            ("H2 -> O2", ["="]),       # mhchem yields → equals connector
            ("H2 = O2", ["="]),        # literal = also a connector
            ("N2 <=> O2", ["⇌"]),      # reversible
            ("H2+O2", ["+"]),          # spaceless + (followed by an element)
            ("2H2+O2=2H2O", ["+", "="]),  # spaceless school-equation form
            ("NaCl+AgNO3", ["+"]),     # + before a multi-letter species
        ],
    )
    def test_operators_and_connectors(self, inner, texts):
        root = ET.fromstring(convert_ce(inner))
        assert [e.text for e in root.iter(f"{_NS}mo")] == texts

    def test_spaceless_equation_matches_spaced(self):
        # School equations are written without spaces around + / = (e.g.
        # 2H2+O2=2H2O). They must parse identically to the mhchem spaced form
        # — same MathML, so the same braille downstream.
        assert convert_ce("2H2+O2=2H2O") == convert_ce("2H2 + O2 = 2H2O")

    @pytest.mark.parametrize("inner", ["Na+", "H+", "Cl-"])
    def test_trailing_charge_becomes_msup(self, inner):
        # A +/- that ENDS a species — nothing (or a space then a connector)
        # follows it — is a charge: it wraps the species in an <msup>, never
        # the addition operator, never <merror>. (An addition ``+`` always has
        # a new species after it, which is how the two are told apart.)
        out = convert_ce(inner)
        assert _merror(out) is None
        root = ET.fromstring(out)
        msup = root.find(f"{_NS}msup")
        assert msup is not None
        assert msup[1].tag == f"{_NS}mo" and msup[1].text in ("+", "-")


class TestConditions:
    def test_single_condition_is_mover(self):
        # A ->[O2] B → <mover><mo>=</mo>{O2}</mover>
        root = ET.fromstring(convert_ce("A ->[O2] B"))
        mover = root.find(f"{_NS}mover")
        assert mover is not None
        assert mover[0].tag == f"{_NS}mo" and mover[0].text == "="

    def test_two_conditions_are_munderover(self):
        # A ->[above][below] B → <munderover><mo>=</mo>{below}{above}</munderover>
        root = ET.fromstring(convert_ce("A ->[O2][N2] B"))
        muo = root.find(f"{_NS}munderover")
        assert muo is not None
        assert muo[0].text == "="
        # munderover order is base, under(below=N2), over(above=O2).
        assert muo[1].find(f".//{_NS}mi").text == "N"   # below
        assert muo[2].find(f".//{_NS}mi").text == "O"   # above

    def test_heat_condition_is_delta_marker(self):
        root = ET.fromstring(convert_ce(r"A ->[\Delta] B"))
        mover = root.find(f"{_NS}mover")
        assert mover[1].tag == f"{_NS}mi" and mover[1].text == "Δ"

    def test_chinese_condition_is_mtext_placeholder(self):
        # Chinese condition is carried as <mtext> for now (zh path pending).
        root = ET.fromstring(convert_ce("A ->[点燃] B"))
        mover = root.find(f"{_NS}mover")
        assert mover[1].tag == f"{_NS}mtext" and mover[1].text == "点燃"


# ---------------------------------------------------------------------------
# Ionic charges on a single-atom species → <msup> (Na+, Mg^2+, F-, O^2-)
# ---------------------------------------------------------------------------


class TestCharges:
    def test_unit_positive_charge_is_msup_with_mo(self):
        # Na+ → <msup><mi>Na</mi><mo>+</mo></msup>
        root = ET.fromstring(convert_ce("Na+"))
        msup = root.find(f"{_NS}msup")
        assert msup is not None
        assert msup[0].tag == f"{_NS}mi" and msup[0].text == "Na"
        assert msup[1].tag == f"{_NS}mo" and msup[1].text == "+"

    def test_negative_unit_charge(self):
        # F- → <msup><mi>F</mi><mo>-</mo></msup>
        root = ET.fromstring(convert_ce("F-"))
        msup = root.find(f"{_NS}msup")
        assert msup[0].text == "F"
        assert msup[1].tag == f"{_NS}mo" and msup[1].text == "-"

    def test_multi_unit_charge_is_msup_with_mrow(self):
        # Mg^2+ → <msup><mi>Mg</mi><mrow><mn>2</mn><mo>+</mo></mrow></msup>
        root = ET.fromstring(convert_ce("Mg^2+"))
        msup = root.find(f"{_NS}msup")
        assert msup[0].text == "Mg"
        mrow = msup[1]
        assert mrow.tag == f"{_NS}mrow"
        assert mrow[0].tag == f"{_NS}mn" and mrow[0].text == "2"
        assert mrow[1].tag == f"{_NS}mo" and mrow[1].text == "+"

    def test_braced_charge_is_accepted(self):
        # mhchem also writes the charge braced: O^{2-}.
        root = ET.fromstring(convert_ce("O^{2-}"))
        mrow = root.find(f"{_NS}msup")[1]
        assert mrow[0].text == "2" and mrow[1].text == "-"

    def test_subscripted_ion_is_msubsup(self):
        # Hg2^2+ → <msubsup><mi>Hg</mi><mn>2</mn><mrow>2 +</mrow></msubsup>
        root = ET.fromstring(convert_ce("Hg2^2+"))
        msubsup = root.find(f"{_NS}msubsup")
        assert msubsup is not None
        assert msubsup[0].text == "Hg"
        assert msubsup[1].tag == f"{_NS}mn" and msubsup[1].text == "2"
        assert msubsup[2].tag == f"{_NS}mrow"

    def test_charge_in_equation_keeps_addition_operator(self):
        # Na+ + Cl- : the spaced + between the two ions is the addition
        # operator (one top-level <mo>+</mo>); each ion is its own <msup>.
        root = ET.fromstring(convert_ce("Na+ + Cl-"))
        top_mo = [e.text for e in root if e.tag == f"{_NS}mo"]
        assert top_mo == ["+"]
        assert len(root.findall(f"{_NS}msup")) == 2

    def test_coefficient_then_ion(self):
        # 2Na+ → <mn>2</mn> then the Na⁺ <msup>; the charge still attaches.
        root = ET.fromstring(convert_ce("2Na+"))
        assert root[0].tag == f"{_NS}mn" and root[0].text == "2"
        assert root[1].tag == f"{_NS}msup"

    @pytest.mark.parametrize("inner", ["SO4^2-", "OH-", "NH4+", "CO3^2-"])
    def test_polyatomic_charge_wraps_whole_group(self, inner):
        # A charge on a (paren-free) multi-atom species wraps the WHOLE atom
        # group in one <msup><mrow>…, not just its last atom.
        out = convert_ce(inner)
        assert _merror(out) is None
        msup = ET.fromstring(out).find(f"{_NS}msup")
        assert msup is not None
        assert msup[0].tag == f"{_NS}mrow"   # the group is the charge base
        assert len(msup[0]) >= 2             # ≥2 atoms inside the group

    def test_polyatomic_charge_keeps_all_atoms(self):
        # SO4^2- → the group base holds S and O₄ (an <msub>), then the 2- sup.
        root = ET.fromstring(convert_ce("SO4^2-"))
        group = root.find(f"{_NS}msup")[0]
        assert group[0].tag == f"{_NS}mi" and group[0].text == "S"
        assert group[1].tag == f"{_NS}msub"
        assert group[1][0].text == "O" and group[1][1].text == "4"


# ---------------------------------------------------------------------------
# Parenthesised groups: (OH)2, (NH4)2SO4 → <mrow>(…)</mrow> with the group
# multiplier as a subscript; group content is parsed (and cased) on its own.
# ---------------------------------------------------------------------------


class TestParentheses:
    def test_group_with_multiplier_is_msub_over_mrow(self):
        # Ca(OH)2 → <mi>Ca</mi><msub><mrow>(<mi>O</mi><mi>H</mi>)</mrow><mn>2</mn></msub>
        root = ET.fromstring(convert_ce("Ca(OH)2"))
        assert root[0].tag == f"{_NS}mi" and root[0].text == "Ca"
        msub = root[1]
        assert msub.tag == f"{_NS}msub"
        group, mult = msub[0], msub[1]
        assert group.tag == f"{_NS}mrow"
        assert group[0].tag == f"{_NS}mo" and group[0].text == "("
        assert group[-1].tag == f"{_NS}mo" and group[-1].text == ")"
        assert [e.text for e in group.iter(f"{_NS}mi")] == ["O", "H"]
        assert mult.tag == f"{_NS}mn" and mult.text == "2"

    def test_group_without_multiplier_is_bare_mrow(self):
        # (OH) with no following digit → a plain parenthesised mrow, no msub.
        root = ET.fromstring(convert_ce("Ca(OH)"))
        assert root[1].tag == f"{_NS}mrow"
        assert root[1][0].text == "(" and root[1][-1].text == ")"

    def test_group_at_start_then_atoms(self):
        # (NH4)2SO4 → group (NH4) with ×2, then the SO4 atoms as siblings.
        root = ET.fromstring(convert_ce("(NH4)2SO4"))
        assert root[0].tag == f"{_NS}msub"          # (NH4)2
        assert root[0][0].tag == f"{_NS}mrow"
        # The trailing SO4 sits outside the group as ordinary atoms.
        tail = [e.text for e in root[1:] if e.tag == f"{_NS}mi"]
        assert tail == ["S"]                        # S, then <msub>O4</msub>

    def test_inner_subscript_preserved(self):
        # (NH4)2 keeps H's own subscript 4 inside the group.
        group = ET.fromstring(convert_ce("(NH4)2"))[0][0]   # msub → mrow
        h = group.find(f"{_NS}msub")
        assert h[0].text == "H" and h[1].text == "4"

    @pytest.mark.parametrize("inner", ["Ca(OH)2", "Fe(OH)3", "Al2(SO4)3"])
    def test_no_merror_for_supported_groups(self, inner):
        assert _merror(convert_ce(inner)) is None

    def test_unbalanced_paren_degrades(self):
        assert _merror(convert_ce("Ca(OH2")) is not None


# ---------------------------------------------------------------------------
# Physical-state labels: (s)/(l)/(g)/(aq) → parens around an
# <mtext data-bk-chem-state> (English letters, not chemical elements).
# ---------------------------------------------------------------------------


class TestStates:
    @pytest.mark.parametrize("state", ["s", "l", "g", "aq"])
    def test_state_is_marked_mtext_in_parens(self, state):
        root = ET.fromstring(convert_ce(f"H2O({state})"))
        group = root[-1]                       # the (state) group sits last
        assert group.tag == f"{_NS}mrow"
        assert group[0].text == "(" and group[-1].text == ")"
        mtext = group.find(f"{_NS}mtext")
        assert mtext is not None
        assert mtext.get("data-bk-chem-state") == "1"
        assert mtext.text == state

    def test_state_letters_not_elements(self):
        # (aq) must NOT become <mi> element symbols — it's an <mtext> label.
        root = ET.fromstring(convert_ce("NaCl(aq)"))
        group = root[-1]
        assert group.find(f"{_NS}mi") is None
        assert group.find(f"{_NS}mtext").text == "aq"

    def test_uppercase_S_is_sulfur_not_state(self):
        # (S) (capital) is sulfur in a group, not the solid-state label.
        root = ET.fromstring(convert_ce("(S)"))
        assert root[0].find(f".//{_NS}mi").text == "S"
        assert root.find(f".//{_NS}mtext") is None


# ---------------------------------------------------------------------------
# Square brackets: [...] like (...) but with bracket <mo>s, plus an optional
# trailing charge for complex ions ([Cu(NH3)4]^2+).
# ---------------------------------------------------------------------------


class TestBrackets:
    def test_bracket_group_uses_bracket_mo(self):
        # [Fe(CN)6] → <mrow><mo>[</mo>…<mo>]</mo></mrow>
        root = ET.fromstring(convert_ce("K3[Fe(CN)6]"))
        mrow = root.find(f".//{_NS}mrow")
        assert mrow[0].tag == f"{_NS}mo" and mrow[0].text == "["
        assert mrow[-1].tag == f"{_NS}mo" and mrow[-1].text == "]"

    def test_charged_complex_is_msup_over_bracket(self):
        # [Cu(NH3)4]^2+ → <msup><mrow>[…]</mrow><mrow>2 +</mrow></msup>
        root = ET.fromstring(convert_ce("[Cu(NH3)4]^2+"))
        msup = root.find(f"{_NS}msup")
        assert msup is not None
        base = msup[0]
        assert base.tag == f"{_NS}mrow"
        assert base[0].text == "[" and base[-1].text == "]"
        sup = msup[1]
        assert sup.find(f"{_NS}mn").text == "2"
        assert sup.find(f"{_NS}mo").text == "+"

    def test_unit_charge_complex_is_msup_with_mo(self):
        # [Ag(NH3)2]+ → unit charge is a bare <mo>+</mo> superscript.
        root = ET.fromstring(convert_ce("[Ag(NH3)2]+"))
        sup = root.find(f"{_NS}msup")[1]
        assert sup.tag == f"{_NS}mo" and sup.text == "+"

    def test_nested_paren_inside_bracket(self):
        # The (NH3) group survives inside the bracket as its own <mrow>.
        root = ET.fromstring(convert_ce("[Cu(NH3)4]^2+"))
        # bracket mrow → contains an msub whose base is the (NH3) mrow.
        inner = root.find(f".//{_NS}msub/{_NS}mrow")
        assert inner is not None
        assert inner[0].text == "(" and inner[-1].text == ")"

    def test_unbalanced_bracket_degrades(self):
        assert _merror(convert_ce("[Fe(CN)6")) is not None


# ---------------------------------------------------------------------------
# Adapter wrapper + latex delegation
# ---------------------------------------------------------------------------


class TestChemAdapter:
    def test_wrapped_ce(self):
        out = ChemMathSourceAdapter().to_mathml(r"\ce{H2O}")
        assert ET.fromstring(out).get("data-bk-chem") == "1"

    def test_bare_formula_text(self):
        # source="chem" without the \ce{} wrapper still parses.
        out = ChemMathSourceAdapter().to_mathml("H2O")
        assert ET.fromstring(out).get("data-bk-chem") == "1"

    def test_dollar_delimiters_stripped(self):
        out = ChemMathSourceAdapter().to_mathml(r"$\ce{H2O}$")
        assert ET.fromstring(out).get("data-bk-chem") == "1"

    def test_protocol_conformance(self):
        from brailix.core.protocols import MathSourceAdapter

        assert isinstance(ChemMathSourceAdapter(), MathSourceAdapter)


class TestLatexDelegation:
    def test_latex_adapter_delegates_ce_to_chem(self):
        # The injected converter must NOT be called for \ce — the chem
        # path takes over and emits data-bk-chem MathML.
        def boom(_: str) -> str:
            raise AssertionError("latex2mathml should not see \\ce content")

        adapter = LatexMathSourceAdapter(converter=boom)
        out = adapter.to_mathml(r"\ce{H2O}")
        assert ET.fromstring(out).get("data-bk-chem") == "1"

    def test_non_ce_still_uses_converter(self):
        adapter = LatexMathSourceAdapter(
            converter=lambda f: f"<math><mtext>{f}</mtext></math>"
        )
        out = adapter.to_mathml("x^2")
        assert "<mtext>x^2</mtext>" in out
        assert "data-bk-chem" not in out

    def test_registered_in_source_registry(self):
        from brailix.frontend.math.registry import math_source_registry

        adapter = math_source_registry.get("chem")
        assert isinstance(adapter, ChemMathSourceAdapter)


# ---------------------------------------------------------------------------
# Source-text normalization: full-width / zero-width folding
# ---------------------------------------------------------------------------


class TestNonStandardCharsAreFlaggedNotFolded:
    """Full-width symbols and invisible zero-width chars are writing errors:
    flagged in place (soft <merror>), never silently folded to half-width —
    ＝ (U+FF1D) and = (U+003D) are different code points. The rest of the
    equation still translates; only the offending character is blanked."""

    @staticmethod
    def _soft(out):
        return [
            e
            for e in ET.fromstring(out).iter(f"{_NS}merror")
            if e.get("data-bk-soft") == "1"
        ]

    def test_fullwidth_letters_flagged_in_place(self):
        out = convert_ce("Ｈ２Ｏ")
        assert ET.fromstring(out).get("data-bk-chem") == "1"  # not whole-merror
        # the full-width letters are flagged; the full-width digit ２ folds
        # downstream in the backend digit path, so it isn't flagged here.
        assert [e.text for e in self._soft(out)] == ["Ｈ", "Ｏ"]

    def test_fullwidth_operator_flagged_not_folded(self):
        out = convert_ce("2H2 ＝ 2H2O")
        assert ET.fromstring(out).get("data-bk-chem") == "1"
        assert [e.text for e in self._soft(out)] == ["＝"]
        # the ＝ is NOT turned into an '=' connector
        assert "=" not in [e.text for e in ET.fromstring(out).iter(f"{_NS}mo")]

    def test_zero_width_space_flagged_not_silently_dropped(self):
        out = convert_ce("H2​O")  # ZWSP between the 2 and the O
        assert ET.fromstring(out).get("data-bk-chem") == "1"
        soft = self._soft(out)
        assert len(soft) == 1 and soft[0].text == "​"

    def test_lowercase_element_is_whole_formula_error(self):
        # A casing mistake degrades the whole formula with an actionable reason
        # (capitalise it) rather than blanking each letter.
        err = _merror(convert_ce("h2o"))
        assert err is not None
        assert "capitalised" in err.get("data-reason", "")
        assert ET.fromstring(convert_ce("h2o")).get("data-bk-chem") is None

    def test_supported_halfwidth_stays_clean(self):
        out = convert_ce("2H2 + O2 = 2H2O")
        assert self._soft(out) == []
        assert _merror(out) is None


class TestSoftFailureBackstop:
    def test_pathological_nesting_degrades_to_merror(self):
        # Hundreds of nested groups exhaust the recursive-descent
        # parser (RecursionError).  chem was the only math adapter
        # without an except-Exception backstop, so this escaped the
        # "adapters never raise" contract and crashed the pipeline.
        out = convert_ce("(" * 600 + "H" + ")" * 600)
        assert _merror(out) is not None


class TestUnicodeArrowsRejectedInCe:
    """``\\ce{}`` is LaTeX — only the mhchem ASCII forms are recognised. A
    literal Unicode arrow / ≡ / ↑ / ↓ is non-standard input: flagged in place
    (soft <merror>), not silently translated. Unicode glyphs belong to
    plain-text sources, not LaTeX."""

    @staticmethod
    def _soft(out):
        return [
            e.text
            for e in ET.fromstring(out).iter(f"{_NS}merror")
            if e.get("data-bk-soft") == "1"
        ]

    @pytest.mark.parametrize("ch", ["→", "⟶", "⇌", "↑", "↓", "≡"])
    def test_unicode_symbol_flagged_not_translated(self, ch):
        assert self._soft(convert_ce(f"A {ch} B")) == [ch]

    def test_ascii_mhchem_arrows_still_work(self):
        assert _merror(convert_ce("Na + Cl2 -> 2NaCl")) is None
        mos = [
            e.text for e in ET.fromstring(convert_ce("N2 <=> O2")).iter(f"{_NS}mo")
        ]
        assert "⇌" in mos  # <=> still renders the reversible symbol


class TestReverseArrowConnector:
    def test_reverse_arrow_recognised(self):
        out = convert_ce("2NH3 <- N2 + 3H2")
        assert _merror(out) is None
        mos = [e.text for e in ET.fromstring(out).iter(f"{_NS}mo")]
        assert "←" in mos  # the mhchem <- reverse arrow

    @pytest.mark.parametrize("src", ["A <-> B", "A <--> B"])
    def test_resonance_arrows_not_supported(self, src):
        # <-> / <--> aren't supported yet — the leading '<' is flagged, not
        # silently rendered.
        out = convert_ce(src)
        soft = [
            e.text
            for e in ET.fromstring(out).iter(f"{_NS}merror")
            if e.get("data-bk-soft") == "1"
        ]
        assert "<" in soft


class TestRepeatedConnectorTagging:
    def test_double_equals_tags_only_second(self):
        out = convert_ce("H2 + O2 == H2O")
        eqs = [e for e in ET.fromstring(out).iter(f"{_NS}mo") if e.text == "="]
        warned = [e for e in eqs if e.get("data-bk-warn") == "repeated-operator"]
        assert len(eqs) == 2
        assert len(warned) == 1  # faithful: both kept; only the 2nd flagged

    def test_spaced_double_equals_still_tagged(self):
        out = convert_ce("H2 = = O2")
        warned = [
            e
            for e in ET.fromstring(out).iter(f"{_NS}mo")
            if e.get("data-bk-warn") == "repeated-operator"
        ]
        assert len(warned) == 1

    def test_double_bond_not_tagged(self):
        # O=C=O has two '=' separated by C — legitimate double bonds, no flag.
        out = convert_ce("O=C=O")
        warned = [
            e for e in ET.fromstring(out).iter(f"{_NS}mo") if e.get("data-bk-warn")
        ]
        assert warned == []


class TestStructuralBondMarkers:
    @staticmethod
    def _bonds(out):
        return [
            e.get("data-bk-chem-bond")
            for e in ET.fromstring(out).iter(f"{_NS}mo")
            if e.get("data-bk-chem-bond")
        ]

    def test_tight_equals_in_molecule_is_double_bond(self):
        # O=C=O (no '+', no arrow) — both tight '=' are structural double bonds.
        assert self._bonds(convert_ce("O=C=O")) == ["double", "double"]

    def test_reaction_equals_is_not_a_bond(self):
        # The yields '=' (spaced or spaceless, formula has '+' and no arrow) is
        # a connector, not a structural bond — no data-bk-chem-bond marker.
        assert self._bonds(convert_ce("H2 + O2 = H2O")) == []
        assert self._bonds(convert_ce("2H2+O2=2H2O")) == []

    def test_double_bond_inside_reactant_molecule(self):
        # A lone double-bonded molecule keeps its bond marker.
        assert self._bonds(convert_ce("CH2=CH2")) == ["double"]

    def test_double_bond_in_arrow_reaction_reactant(self):
        # Organic addition CH2=CH2 + H2 -> CH3CH3: the '->' is the yields, so
        # the '=' inside the ethylene reactant is a double bond — not the
        # yields, even though the whole expression is a reaction.
        assert self._bonds(convert_ce("CH2=CH2 + H2 -> CH3CH3")) == ["double"]
