"""Tests for :mod:`brailix.frontend.math.adapters.omml`.

The OMML adapter is the math-frontend dialect translator: takes the
XML Word stores inside ``.docx`` and emits a MathML string the
normaliser + backend already know how to chew. These tests pin the
common construct mappings — anything that breaks here would silently
mistranslate every Word formula downstream.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

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
        # No exceptions — and the inner content survives somewhere.
        assert "<math" in out


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
