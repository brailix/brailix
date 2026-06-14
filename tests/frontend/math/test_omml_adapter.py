"""Tests for :mod:`brailix.frontend.math.adapters.omml`.

The OMML adapter is the math-frontend dialect translator: takes the
XML Word stores inside ``.docx`` and emits a MathML string the
normaliser + backend already know how to chew. These tests pin the
common construct mappings — anything that breaks here would silently
mistranslate every Word formula downstream.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

import pytest

from brailix.core.context import MathContext
from brailix.core.errors import WarningCollector
from brailix.frontend import parse_math_tree
from brailix.frontend.math.adapters.omml import OmmlMathSourceAdapter

_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _omml(body: str) -> str:
    """Wrap ``body`` in ``<m:oMath xmlns:m="...">`` so it parses."""
    return f'<m:oMath xmlns:m="{_M_NS}">{body}</m:oMath>'


def _mathml_dump(xml: str) -> str:
    """Parse and reserialise so cross-test comparisons are
    canonical-form independent of attribute order quirks."""
    return ET.tostring(ET.fromstring(xml), encoding="unicode")


def _shape_tree(formula: str) -> str:
    """Full-tree shape of the OMML adapter output after normalization,
    e.g. ``mfrac(mi:x,mi:y)`` / ``msup(mi:x,mn:2)``. Unlike substring
    asserts, this catches structural / ordering mistranslations that
    still emit the right tags — the OMML path has no real-docx fixture."""
    root = parse_math_tree(formula, MathContext(source="omml", warnings=WarningCollector()))

    def fmt(el: ET.Element) -> str:
        kids = list(el)
        tag = el.tag.split("}")[-1]
        if not kids:
            text = (el.text or "").strip()
            return f"{tag}:{text}" if text else tag
        return f"{tag}({','.join(fmt(k) for k in kids)})"

    return ",".join(fmt(c) for c in root) if root is not None else "<none>"


class TestTextRuns:
    def test_letters_become_mi(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>x</m:t></m:r>"))
        assert "<mi>x</mi>" in out

    def test_digits_become_mn(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>42</m:t></m:r>"))
        assert "<mn>42</mn>" in out

    def test_operator_char_becomes_mo(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>+</m:t></m:r>"))
        assert "<mo>+</mo>" in out

    def test_mixed_text_splits_by_class(self):
        # ``2x+1`` should split into <mn>2</mn><mi>x</mi><mo>+</mo><mn>1</mn>.
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>2x+1</m:t></m:r>"))
        assert "<mn>2</mn>" in out
        assert "<mi>x</mi>" in out
        assert "<mo>+</mo>" in out
        assert "<mn>1</mn>" in out

    def test_cjk_text_run_becomes_mtext_not_per_char_mo(self):
        # Chinese inside a Word formula (the condition 「当x>0时」) must
        # coalesce into <mtext> runs so the backend routes them through the
        # injected inline-text translator. Per-char <mo>当</mo> hit the
        # operator path and surfaced one MATH_UNKNOWN_SYMBOL per character.
        # Interleaved math content (x / > / 0) keeps its own classes.
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>当x&gt;0时</m:t></m:r>"))
        assert "<mtext>当</mtext>" in out
        assert "<mtext>时</mtext>" in out
        assert "<mo>当</mo>" not in out and "<mo>时</mo>" not in out
        assert "<mi>x</mi>" in out and "<mn>0</mn>" in out

    def test_consecutive_cjk_coalesce_into_one_mtext(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml("<m:r><m:t>时刻</m:t></m:r>"))
        assert "<mtext>时刻</mtext>" in out


class TestFraction:
    def test_basic_fraction(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:f>"
            "<m:num><m:r><m:t>x</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>y</m:t></m:r></m:den>"
            "</m:f>"
        ))
        assert "<mfrac>" in out
        assert "<mi>x</mi>" in out and "<mi>y</mi>" in out

    def test_no_bar_fraction_sets_linethickness_zero(self):
        adapter = OmmlMathSourceAdapter()
        body = (
            "<m:f>"
            f'<m:fPr><m:type m:val="noBar" xmlns:m="{_M_NS}"/></m:fPr>'
            "<m:num><m:r><m:t>a</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>b</m:t></m:r></m:den>"
            "</m:f>"
        )
        out = adapter.to_mathml(_omml(body))
        # The attribute should survive on the <mfrac> element.
        assert 'linethickness="0"' in out


class TestSubSup:
    def test_superscript(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:sSup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
            "</m:sSup>"
        ))
        assert "<msup>" in out
        assert "<mi>x</mi>" in out and "<mn>2</mn>" in out

    def test_subscript(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:sSub>"
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:sub><m:r><m:t>i</m:t></m:r></m:sub>"
            "</m:sSub>"
        ))
        assert "<msub>" in out

    def test_subscript_and_superscript(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:sSubSup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "<m:sub><m:r><m:t>i</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
            "</m:sSubSup>"
        ))
        assert "<msubsup>" in out


class TestRadical:
    def test_square_root_no_degree(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:rad>"
            "<m:deg/>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:rad>"
        ))
        assert "<msqrt>" in out

    def test_root_with_degree(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:rad>"
            "<m:deg><m:r><m:t>3</m:t></m:r></m:deg>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:rad>"
        ))
        assert "<mroot>" in out


class TestNary:
    def test_default_summation(self):
        adapter = OmmlMathSourceAdapter()
        body = (
            "<m:nary>"
            f'<m:naryPr><m:chr m:val="∑" xmlns:m="{_M_NS}"/></m:naryPr>'
            "<m:sub><m:r><m:t>i</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>n</m:t></m:r></m:sup>"
            "<m:e><m:r><m:t>i</m:t></m:r></m:e>"
            "</m:nary>"
        )
        out = adapter.to_mathml(_omml(body))
        # Default limit location is "undOvr" → munderover.
        assert "<munderover>" in out
        assert "∑" in out


class TestDelimiter:
    def test_parentheses_default(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:d>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:d>"
        ))
        # Default delimiter pair is ( ).
        assert ">(</mo>" in out and ">)</mo>" in out


class TestMatrix:
    def test_two_by_two_matrix(self):
        adapter = OmmlMathSourceAdapter()
        body = (
            "<m:m>"
            "<m:mr>"
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e>"
            "</m:mr>"
            "<m:mr>"
            "<m:e><m:r><m:t>c</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>d</m:t></m:r></m:e>"
            "</m:mr>"
            "</m:m>"
        )
        out = adapter.to_mathml(_omml(body))
        assert "<mtable>" in out
        # Two rows, two columns each.
        assert out.count("<mtr>") == 2
        assert out.count("<mtd>") == 4


class TestErrorRecovery:
    def test_malformed_xml_wraps_in_merror(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml("<m:not-real>")
        assert "<merror" in out

    def test_empty_input_wraps_in_merror(self):
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml("")
        assert "<merror" in out

    def test_unknown_construct_falls_back_to_mtext(self):
        # A made-up tag inside otherwise valid OMML degrades gracefully:
        # contents survive as <mtext> rather than crashing the adapter.
        adapter = OmmlMathSourceAdapter()
        out = adapter.to_mathml(_omml(
            "<m:mysteryConstruct>"
            "<m:r><m:t>blob</m:t></m:r>"
            "</m:mysteryConstruct>"
        ))
        # No exceptions — and the inner text survives as <mtext>, not just
        # "the root is <math>" (which holds for any parseable input).
        assert "<mtext>blob</mtext>" in out


class TestEndToEndThroughParseMathTree:
    def test_omml_routes_through_parse_math_tree(self):
        # The integration check: ``parse_math_tree`` with
        # ``source="omml"`` runs the adapter we registered and returns
        # a normalised :class:`ET.Element` tree.
        ctx = MathContext(
            source="omml",
            mode="display",
            profile="cn_current",
            warnings=WarningCollector(),
        )
        omml = _omml(
            "<m:f>"
            "<m:num><m:r><m:t>x</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>2</m:t></m:r></m:den>"
            "</m:f>"
        )
        tree = parse_math_tree(omml, ctx)
        assert tree is not None
        # Namespace stripped by normaliser; tag is bare local name.
        assert tree.tag == "math"
        # The fraction survived through to the normalised tree.
        mfrac = tree.find("mfrac")
        assert mfrac is not None


class TestStructuralShape:
    """Full-tree-shape assertions (not substring) so a structural /
    ordering mistranslation that still contains the right tags can't pass
    silently. The OMML path is every native-Word equation and has no
    real-docx fixture, so these pin the tree shape exactly."""

    def test_fraction_shape(self):
        out = _shape_tree(_omml(
            "<m:f>"
            "<m:num><m:r><m:t>x</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>y</m:t></m:r></m:den>"
            "</m:f>"
        ))
        assert out == "mfrac(mi:x,mi:y)"

    def test_superscript_shape(self):
        out = _shape_tree(_omml(
            "<m:sSup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "<m:sup><m:r><m:t>2</m:t></m:r></m:sup>"
            "</m:sSup>"
        ))
        assert out == "msup(mi:x,mn:2)"

    def test_matrix_2x2_shape(self):
        # [[a,b],[c,d]] — row/cell nesting + order must be exact.
        out = _shape_tree(_omml(
            "<m:m>"
            "<m:mr><m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e></m:mr>"
            "<m:mr><m:e><m:r><m:t>c</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>d</m:t></m:r></m:e></m:mr>"
            "</m:m>"
        ))
        assert out == "mtable(mtr(mtd(mi:a),mtd(mi:b)),mtr(mtd(mi:c),mtd(mi:d)))"


# ---------------------------------------------------------------------------
# Constructs that were implemented but previously untested — synthetic OMML
# exercising each handler's tag + structure mapping.
# ---------------------------------------------------------------------------


class TestPreScript:
    def test_spre_becomes_mmultiscripts(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:sPre>"
            "<m:e><m:r><m:t>X</m:t></m:r></m:e>"
            "<m:sub><m:r><m:t>a</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>b</m:t></m:r></m:sup>"
            "</m:sPre>"
        ))
        assert "<mmultiscripts>" in out
        assert "<mprescripts" in out
        assert "<mi>X</mi>" in out
        assert "<mi>a</mi>" in out and "<mi>b</mi>" in out


class TestFunc:
    def test_func_emits_name_apply_arg(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:func>"
            "<m:fName><m:r><m:t>sin</m:t></m:r></m:fName>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:func>"
        ))
        assert "<mrow>" in out
        assert "⁡" in out  # U+2061 apply-function operator
        assert "<mi>x</mi>" in out


class TestEqArray:
    def test_eqarr_becomes_mtable_rows(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:eqArr>"
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e>"
            "</m:eqArr>"
        ))
        assert "<mtable>" in out
        assert out.count("<mtr>") == 2


class TestLimits:
    def test_lim_low_becomes_munder(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:limLow><m:e><m:r><m:t>L</m:t></m:r></m:e>"
            "<m:lim><m:r><m:t>x</m:t></m:r></m:lim></m:limLow>"
        ))
        assert "<munder>" in out

    def test_lim_upp_becomes_mover(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:limUpp><m:e><m:r><m:t>L</m:t></m:r></m:e>"
            "<m:lim><m:r><m:t>x</m:t></m:r></m:lim></m:limUpp>"
        ))
        assert "<mover>" in out


class TestAccentsAndGroups:
    def test_group_chr_default_underbrace(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:groupChr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:groupChr>"
        ))
        assert "<munder>" in out
        assert "⏟" in out  # default underbrace

    def test_bar_default_overbar(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:bar><m:e><m:r><m:t>x</m:t></m:r></m:e></m:bar>"
        ))
        assert "<mover>" in out
        assert "¯" in out  # macron / overbar

    def test_acc_default_is_mover(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:acc><m:e><m:r><m:t>x</m:t></m:r></m:e></m:acc>"
        ))
        assert "<mover>" in out
        assert "<mi>x</mi>" in out


class TestBoxAndPhantom:
    def test_box_passes_contents_through(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:box><m:e><m:r><m:t>x</m:t></m:r></m:e></m:box>"
        ))
        assert "<mi>x</mi>" in out
        assert "box" not in out.lower()  # no box wrapper survives

    def test_border_box_passes_contents_through(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:borderBox><m:e><m:r><m:t>y</m:t></m:r></m:e></m:borderBox>"
        ))
        assert "<mi>y</mi>" in out

    def test_phant_becomes_mphantom(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:phant><m:e><m:r><m:t>x</m:t></m:r></m:e></m:phant>"
        ))
        assert "<mphantom>" in out
        assert "<mi>x</mi>" in out


class TestNaryOptions:
    def test_nary_sub_hidden_drops_lower_limit(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            "<m:naryPr><m:chr m:val=\"∑\"/><m:subHide m:val=\"on\"/></m:naryPr>"
            "<m:sub><m:r><m:t>a</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>b</m:t></m:r></m:sup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<mi>a</mi>" not in out  # lower limit suppressed
        assert "<mi>b</mi>" in out  # upper limit kept

    def test_nary_limloc_subsup_uses_msubsup(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            "<m:naryPr><m:chr m:val=\"∑\"/>"
            "<m:limLoc m:val=\"subSup\"/></m:naryPr>"
            "<m:sub><m:r><m:t>a</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>b</m:t></m:r></m:sup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<msubsup>" in out
        assert "<munderover>" not in out

    def test_nary_empty_chr_defaults_to_integral(self):
        # An explicit empty <m:chr m:val=""/> must fall back to the default
        # integral (∫), not emit an empty <mo/>.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            "<m:naryPr><m:chr m:val=\"\"/></m:naryPr>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "∫" in out
        assert "<mi>x</mi>" in out


# ---------------------------------------------------------------------------
# Robustness and option branches: byte input, paragraph wrappers, bare
# fragments, omitted optional children, and per-construct property variants.
# ---------------------------------------------------------------------------


class TestInputForms:
    def test_utf8_bytes_input_is_decoded(self):
        out = OmmlMathSourceAdapter().to_mathml(
            _omml("<m:r><m:t>x</m:t></m:r>").encode("utf-8")
        )
        assert "<mi>x</mi>" in out

    def test_non_utf8_bytes_wrap_in_merror(self):
        out = OmmlMathSourceAdapter().to_mathml(b"\xff\xfe\xfd")
        assert "<merror" in out
        assert 'data-reason="non-utf8 bytes"' in out

    def test_omath_para_wrapper_is_flattened(self):
        # Word wraps display equations in <m:oMathPara> with paragraph
        # properties; all contained <m:oMath> runs flatten into one tree.
        out = OmmlMathSourceAdapter().to_mathml(
            f'<m:oMathPara xmlns:m="{_M_NS}">'
            '<m:oMathParaPr><m:jc m:val="centerGroup"/></m:oMathParaPr>'
            "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>"
            "<m:oMath><m:r><m:t>y</m:t></m:r></m:oMath>"
            "</m:oMathPara>"
        )
        assert "<mi>x</mi>" in out and "<mi>y</mi>" in out
        assert out.index("<mi>x</mi>") < out.index("<mi>y</mi>")

    def test_bare_construct_root_is_wrapped(self):
        # A single construct without the <m:oMath> envelope is accepted
        # and wrapped in <math> by the adapter itself.
        out = OmmlMathSourceAdapter().to_mathml(
            f'<m:f xmlns:m="{_M_NS}">'
            "<m:num><m:r><m:t>x</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>y</m:t></m:r></m:den>"
            "</m:f>"
        )
        assert "<mfrac>" in out

    def test_bare_wrapper_fragment_passes_through(self):
        # Wrapper elements (m:e, m:num, ...) handed in as direct
        # conversion targets just forward their children.
        out = OmmlMathSourceAdapter().to_mathml(
            f'<m:e xmlns:m="{_M_NS}"><m:r><m:t>x</m:t></m:r></m:e>'
        )
        assert "<mi>x</mi>" in out

    def test_pathological_nesting_degrades_to_merror(self):
        # Nesting deep enough to blow the interpreter recursion limit
        # must come back as a soft <merror>, not an escaping exception.
        depth = sys.getrecursionlimit() + 100
        body = "<m:e>" * depth + "<m:r><m:t>x</m:t></m:r>" + "</m:e>" * depth
        out = OmmlMathSourceAdapter().to_mathml(_omml(body))
        assert "<merror" in out
        assert "omml convert error" in out


class TestRunAndFallbackEdges:
    def test_unknown_empty_element_yields_nothing(self):
        # Unknown tag with no text content: dropped silently, no <mtext>
        # placeholder and no error wrapper.
        out = OmmlMathSourceAdapter().to_mathml(_omml("<m:mysteryEmpty/>"))
        assert "<mtext" not in out
        assert "<merror" not in out

    def test_whitespace_only_text_run_is_skipped(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            '<m:r><m:t xml:space="preserve"> </m:t></m:r>'
            "<m:r><m:t>x</m:t></m:r>"
        ))
        assert "<mi>x</mi>" in out
        assert out.count("<mi>") == 1

    def test_multi_token_argument_gets_mrow(self):
        # A numerator holding several atoms must be grouped in <mrow> so
        # <mfrac> keeps exactly two children.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:f>"
            "<m:num><m:r><m:t>x+1</m:t></m:r></m:num>"
            "<m:den><m:r><m:t>y</m:t></m:r></m:den>"
            "</m:f>"
        ))
        assert "<mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow>" in out


class TestInvalidConstructFallbacks:
    """Constructs missing a required child degrade to a labelled
    ``<mtext>`` instead of raising — one case per handler guard."""

    @pytest.mark.parametrize(
        ("body", "label"),
        [
            (
                "<m:f><m:num><m:r><m:t>x</m:t></m:r></m:num></m:f>",
                "(invalid fraction)",
            ),
            (
                "<m:sSub><m:e><m:r><m:t>a</m:t></m:r></m:e></m:sSub>",
                "(invalid sub)",
            ),
            (
                "<m:sSup><m:e><m:r><m:t>a</m:t></m:r></m:e></m:sSup>",
                "(invalid sup)",
            ),
            (
                "<m:sSubSup><m:e><m:r><m:t>a</m:t></m:r></m:e>"
                "<m:sub><m:r><m:t>i</m:t></m:r></m:sub></m:sSubSup>",
                "(invalid subsup)",
            ),
            (
                "<m:sPre><m:e><m:r><m:t>X</m:t></m:r></m:e></m:sPre>",
                "(invalid sPre)",
            ),
            ("<m:rad><m:deg/></m:rad>", "(invalid radical)"),
            (
                "<m:func><m:e><m:r><m:t>x</m:t></m:r></m:e></m:func>",
                "(invalid func)",
            ),
            (
                "<m:limLow><m:e><m:r><m:t>L</m:t></m:r></m:e></m:limLow>",
                "(invalid limit)",
            ),
            (
                "<m:limUpp><m:lim><m:r><m:t>x</m:t></m:r></m:lim></m:limUpp>",
                "(invalid limit)",
            ),
            ("<m:groupChr/>", "(invalid groupChr)"),
            ("<m:bar/>", "(invalid bar)"),
            ("<m:acc/>", "(invalid acc)"),
        ],
    )
    def test_missing_required_child_degrades_to_mtext(self, body: str, label: str):
        out = OmmlMathSourceAdapter().to_mathml(_omml(body))
        assert f"<mtext>{label}</mtext>" in out


class TestNaryFallbacks:
    def test_nary_without_properties_defaults_to_integral(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary><m:e><m:r><m:t>x</m:t></m:r></m:e></m:nary>"
        ))
        assert "<mo>∫</mo>" in out
        assert "<munderover>" not in out and "<msubsup>" not in out

    def test_nary_hidden_limits_emit_bare_operator(self):
        # Word's plain integral: naryPr without <m:chr> (∫ is the
        # default) and both limit slots present but hidden.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            '<m:naryPr><m:subHide m:val="on"/><m:supHide m:val="on"/></m:naryPr>'
            "<m:sub/><m:sup/>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<mo>∫</mo>" in out
        assert "<munder" not in out and "<mover" not in out

    def test_nary_without_operand_emits_operator_only(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            '<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr></m:nary>'
        ))
        assert "<mo>∑</mo>" in out
        assert "<merror" not in out

    def test_nary_lower_limit_only_stacks_under(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            '<m:naryPr><m:chr m:val="∑"/></m:naryPr>'
            "<m:sub><m:r><m:t>i</m:t></m:r></m:sub>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<munder>" in out
        assert "<munderover>" not in out

    def test_nary_lower_limit_only_subsup_location_uses_msub(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            '<m:naryPr><m:chr m:val="∑"/><m:limLoc m:val="subSup"/></m:naryPr>'
            "<m:sub><m:r><m:t>i</m:t></m:r></m:sub>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<msub>" in out
        assert "<msubsup>" not in out

    def test_nary_upper_limit_only_subsup_location_uses_msup(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:nary>"
            '<m:naryPr><m:chr m:val="∑"/><m:limLoc m:val="subSup"/>'
            '<m:subHide m:val="on"/></m:naryPr>'
            "<m:sub><m:r><m:t>a</m:t></m:r></m:sub>"
            "<m:sup><m:r><m:t>n</m:t></m:r></m:sup>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:nary>"
        ))
        assert "<msup>" in out
        assert "<msubsup>" not in out


class TestDelimiterOptions:
    def test_multiple_entries_get_default_separator(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:d>"
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e>"
            "</m:d>"
        ))
        assert '<mo separator="true">|</mo>' in out

    def test_custom_delimiter_and_separator_chars(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:d>"
            '<m:dPr><m:begChr m:val="["/><m:sepChr m:val=";"/>'
            '<m:endChr m:val="]"/></m:dPr>'
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e>"
            "</m:d>"
        ))
        assert ">[</mo>" in out and ">]</mo>" in out
        assert ">;</mo>" in out

    def test_empty_delimiter_chars_emit_no_fences(self):
        # Empty char values mean "render nothing" — exercised through the
        # plain ``val`` attribute alias the adapter also accepts.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:d>"
            '<m:dPr><m:begChr val=""/><m:sepChr val=""/><m:endChr val=""/></m:dPr>'
            "<m:e><m:r><m:t>a</m:t></m:r></m:e>"
            "<m:e><m:r><m:t>b</m:t></m:r></m:e>"
            "</m:d>"
        ))
        assert "<mo" not in out
        assert "<mi>a</mi>" in out and "<mi>b</mi>" in out


class TestRadicalOptions:
    def test_missing_deg_element_is_square_root(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:rad><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>"
        ))
        assert "<msqrt>" in out

    def test_deg_hide_suppresses_written_degree(self):
        # degHide=on means the degree is present in the file but not
        # displayed; the output is a plain square root.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:rad>"
            '<m:radPr><m:degHide m:val="on"/></m:radPr>'
            "<m:deg><m:r><m:t>3</m:t></m:r></m:deg>"
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:rad>"
        ))
        assert "<msqrt>" in out
        assert "<mroot>" not in out
        assert "<mn>3</mn>" not in out


class TestDecorationPositions:
    def test_group_chr_top_becomes_mover(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:groupChr>"
            '<m:groupChrPr><m:chr m:val="⏞"/><m:pos m:val="top"/></m:groupChrPr>'
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:groupChr>"
        ))
        assert "<mover>" in out
        assert "⏞" in out

    def test_bar_bottom_becomes_munder(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:bar>"
            '<m:barPr><m:pos m:val="bot"/></m:barPr>'
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:bar>"
        ))
        assert "<munder>" in out
        assert "¯" in out

    def test_acc_custom_accent_char(self):
        # Dot accent: <m:chr m:val="̇"/> with combining dot above U+0307.
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:acc>"
            '<m:accPr><m:chr m:val="̇"/></m:accPr>'
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:acc>"
        ))
        assert "<mover>" in out
        assert "̇" in out


class TestEmptyWrappers:
    def test_box_without_content_yields_nothing(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml("<m:box/>"))
        assert "<mtext" not in out and "<merror" not in out

    def test_border_box_without_content_yields_nothing(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml("<m:borderBox/>"))
        assert "<mtext" not in out and "<merror" not in out

    def test_phant_without_content_yields_nothing(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml("<m:phant/>"))
        assert "<mphantom" not in out and "<merror" not in out


class TestNamespacedEmptyDelimiterValue:
    """Word writes ``<m:endChr m:val=""/>`` (namespaced, explicitly
    empty) for "no right delimiter" — the piecewise-function brace.
    The attribute reader used to collapse that empty value to ``None``
    via an ``or`` fallback chain, and the delimiter handler then
    invented a phantom ``)``."""

    def test_piecewise_brace_has_no_phantom_close(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:d>"
            '<m:dPr><m:begChr m:val="{"/><m:endChr m:val=""/></m:dPr>'
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:d>"
        ))
        assert '<mo fence="true">{</mo>' in out
        assert ")" not in out

    def test_namespaced_empty_open_suppresses_only_that_side(self):
        out = OmmlMathSourceAdapter().to_mathml(_omml(
            "<m:d>"
            '<m:dPr><m:begChr m:val=""/></m:dPr>'
            "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
            "</m:d>"
        ))
        assert "(" not in out
        assert '<mo fence="true">)</mo>' in out
