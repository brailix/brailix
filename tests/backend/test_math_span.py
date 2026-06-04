"""Tests for the ``data-bk-span`` source-span passthrough convention.

A MathML element that carries ``data-bk-span="start,end"`` tells the
backend to use that range as the source span for every
:class:`BrailleCell` emitted from inside that element. Without the
attribute the cells inherit the formula-level
:attr:`MathInline.span`. See ``ARCHITECTURE.md``
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import translate
from brailix.backend.math.utils import _parse_bk_span
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.core.span import Span
from brailix.frontend.math.normalizer import normalize
from brailix.ir.inline import MathInline

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


def _emit(tree: ET.Element, profile, formula_span: Span | None = None):
    """Translate a MathML tree through :func:`translate`, returning the
    cells. ``formula_span`` is the :attr:`MathInline.span` (defaults to
    a recognisable sentinel so tests can tell formula-level apart from
    sub-element spans)."""
    wc = WarningCollector(mode=RunMode.NORMAL)
    ctx = BackendContext(profile="cn_current", warnings=wc)
    node = MathInline(
        surface="", source="mathml", span=formula_span, math=tree
    )
    cells = translate(node, ctx, profile)
    return cells, wc


# ---------------------------------------------------------------------------
# _parse_bk_span helper (direct exercise)
# ---------------------------------------------------------------------------


class TestParseBkSpan:
    def test_basic_pair(self):
        assert _parse_bk_span("3,4") == Span(3, 4)

    def test_zero_start(self):
        assert _parse_bk_span("0,5") == Span(0, 5)

    def test_empty_string_returns_none(self):
        assert _parse_bk_span("") is None

    def test_none_returns_none(self):
        assert _parse_bk_span(None) is None

    def test_malformed_garbage_returns_none(self):
        assert _parse_bk_span("bad") is None

    def test_missing_comma_returns_none(self):
        assert _parse_bk_span("3") is None

    def test_extra_parts_returns_none(self):
        assert _parse_bk_span("1,2,3") is None

    def test_non_integer_returns_none(self):
        assert _parse_bk_span("a,b") is None
        assert _parse_bk_span("1.5,2") is None

    def test_negative_start_returns_none(self):
        # Span itself rejects negatives — we filter out early.
        assert _parse_bk_span("-1,2") is None

    def test_inverted_returns_none(self):
        # end < start is invalid for Span.
        assert _parse_bk_span("5,2") is None

    def test_whitespace_tolerated(self):
        # A bit of slack around the integers is fine — adapters might
        # pad for legibility.
        assert _parse_bk_span(" 3 , 4 ") == Span(3, 4)


# ---------------------------------------------------------------------------
# Backend behaviour: data-bk-span on single elements
# ---------------------------------------------------------------------------


class TestDataBkSpanOnSingleElement:
    def test_mi_with_bk_span_overrides_formula_span(self, profile):
        # <math><mi data-bk-span="3,4">x</mi></math> with formula span 0,5.
        # The x's cells should carry Span(3, 4), not Span(0, 5).
        root = ET.Element("math")
        mi = ET.SubElement(root, "mi", attrib={"data-bk-span": "3,4"})
        mi.text = "x"
        cells, _ = _emit(root, profile, formula_span=Span(0, 5))
        # x emits two cells (latin_lower_prefix + letter). Both should
        # have the per-element span.
        assert len(cells) >= 2
        for c in cells:
            assert c.source_span == Span(3, 4), (
                f"cell {c} has span {c.source_span}, expected (3, 4)"
            )

    def test_mn_with_bk_span(self, profile):
        # <math><mn data-bk-span="0,2">12</mn></math> → digits get
        # (0, 2) as their span. Number sign is emitted by the same
        # element handler, so it inherits the override too.
        root = ET.Element("math")
        mn = ET.SubElement(root, "mn", attrib={"data-bk-span": "0,2"})
        mn.text = "12"
        cells, _ = _emit(root, profile, formula_span=Span(0, 5))
        assert all(c.source_span == Span(0, 2) for c in cells)

    def test_mo_with_bk_span(self, profile):
        root = ET.Element("math")
        mo = ET.SubElement(root, "mo", attrib={"data-bk-span": "2,3"})
        mo.text = "+"
        cells, _ = _emit(root, profile, formula_span=Span(0, 5))
        # All cells emitted from this <mo> share the override span,
        # including any spacing blanks inserted by the handler.
        for c in cells:
            assert c.source_span == Span(2, 3)


# ---------------------------------------------------------------------------
# Backend behaviour: multi-element formulas
# ---------------------------------------------------------------------------


class TestMultiElementSpans:
    def test_each_segment_carries_own_span(self, profile):
        # <math><mn data-bk-span="0,2">12</mn>
        #       <mo data-bk-span="2,3">+</mo>
        #       <mn data-bk-span="3,4">3</mn></math>
        root = ET.Element("math")
        a = ET.SubElement(root, "mn", attrib={"data-bk-span": "0,2"})
        a.text = "12"
        op = ET.SubElement(root, "mo", attrib={"data-bk-span": "2,3"})
        op.text = "+"
        b = ET.SubElement(root, "mn", attrib={"data-bk-span": "3,4"})
        b.text = "3"
        cells, _ = _emit(root, profile, formula_span=Span(0, 10))

        # Bucket non-blank cells by their source_span and check each
        # element's range shows up. Blank spacing cells have no span
        # and are filtered out — they aren't a leaf token.
        content_cells = [c for c in cells if not c.is_blank]
        seen_spans = {c.source_span for c in content_cells}
        assert Span(0, 2) in seen_spans, seen_spans
        assert Span(2, 3) in seen_spans, seen_spans
        assert Span(3, 4) in seen_spans, seen_spans
        # No content cell should fall back to the formula-level span.
        assert Span(0, 10) not in seen_spans, seen_spans


# ---------------------------------------------------------------------------
# Fallback when no data-bk-span is set
# ---------------------------------------------------------------------------


class TestFallbackToFormulaSpan:
    def test_missing_attrib_falls_back(self, profile):
        # No data-bk-span anywhere → every cell uses the formula-level span.
        root = ET.Element("math")
        mi = ET.SubElement(root, "mi")
        mi.text = "x"
        cells, _ = _emit(root, profile, formula_span=Span(7, 8))
        for c in cells:
            assert c.source_span == Span(7, 8)

    def test_mixed_some_have_attrib_some_dont(self, profile):
        # Two siblings, only one carries data-bk-span. The other should
        # keep the formula-level span.
        root = ET.Element("math")
        a = ET.SubElement(root, "mi", attrib={"data-bk-span": "3,4"})
        a.text = "x"
        b = ET.SubElement(root, "mi")  # no attrib
        b.text = "y"
        cells, _ = _emit(root, profile, formula_span=Span(0, 10))
        # We can't reliably split by index because each <mi> emits 2
        # cells (prefix + letter). Bucket by span and assert both
        # spans show up exactly once-per-element-group.
        seen_spans = {c.source_span for c in cells}
        assert Span(3, 4) in seen_spans
        assert Span(0, 10) in seen_spans


# ---------------------------------------------------------------------------
# Malformed data-bk-span fallback
# ---------------------------------------------------------------------------


class TestMalformedBkSpan:
    def test_malformed_attrib_silently_falls_back(self, profile):
        # data-bk-span="bad" → not parseable; should not crash, should
        # not emit warnings, should fall back to the formula span.
        root = ET.Element("math")
        mi = ET.SubElement(root, "mi", attrib={"data-bk-span": "bad"})
        mi.text = "x"
        cells, wc = _emit(root, profile, formula_span=Span(7, 8))
        # No exception.
        assert cells
        # Cells use the formula span (no override applied).
        for c in cells:
            assert c.source_span == Span(7, 8)
        # No warning was generated for the parse-error — silent fallback
        # is intentional.
        codes = {w.code for w in wc}
        # The "bad-attrib" path should not surface any MATH_* warning.
        assert "MATH_INVALID_SPAN" not in codes

    def test_extra_parts_fall_back(self, profile):
        root = ET.Element("math")
        mi = ET.SubElement(root, "mi", attrib={"data-bk-span": "1,2,3"})
        mi.text = "x"
        cells, _ = _emit(root, profile, formula_span=Span(0, 1))
        for c in cells:
            assert c.source_span == Span(0, 1)


# ---------------------------------------------------------------------------
# Pipeline integration: LaTeX inputs (which never use data-bk-span)
# ---------------------------------------------------------------------------


class TestPipelineDefaultUnchanged:
    def test_latex2mathml_input_uses_formula_span(self, profile):
        # Mathlib doesn't fill data-bk-span; a typical LaTeX-driven input
        # should keep emitting cells with the formula-level span. This
        # is the "no behaviour change for normal input" guarantee.
        latex_mathml = '<math xmlns="http://www.w3.org/1998/Math/MathML">'
        latex_mathml += '<mi>x</mi><mo>+</mo><mn>1</mn>'
        latex_mathml += '</math>'
        tree = normalize(latex_mathml)
        # No data-bk-span anywhere in this tree.
        for elem in tree.iter():
            assert "data-bk-span" not in elem.attrib

        cells, _ = _emit(tree, profile, formula_span=Span(5, 10))
        # Every content cell should carry the formula-level span. Blank
        # cells inserted as spacing are a profile-level sentinel
        # (``BLANK_CELL``) with no span — they're orthogonal to the
        # data-bk-span feature.
        content_cells = [c for c in cells if not c.is_blank]
        assert content_cells
        for c in content_cells:
            assert c.source_span == Span(5, 10)


# ---------------------------------------------------------------------------
# Normalizer preserves data-bk-span
# ---------------------------------------------------------------------------


class TestNormalizerPreservesAttrib:
    def test_data_bk_span_survives_namespace_strip(self):
        src = (
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<mi data-bk-span="3,4">x</mi>'
            '</math>'
        )
        root = normalize(src)
        mi = root[0]
        assert mi.tag == "mi"
        assert mi.get("data-bk-span") == "3,4"

    def test_data_bk_span_survives_singleton_mrow_collapse(self):
        # <mrow><mi data-bk-span="3,4">x</mi></mrow> collapses to the
        # inner <mi>; attrib must survive.
        src = '<math><mrow><mi data-bk-span="3,4">x</mi></mrow></math>'
        root = normalize(src)
        # The mrow collapses, so root[0] is the mi directly.
        assert root[0].tag == "mi"
        assert root[0].get("data-bk-span") == "3,4"

    def test_data_bk_span_survives_whitespace_strip(self):
        # Surrounding whitespace shouldn't strip attribs.
        src = (
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            '  <mi data-bk-span="3,4">x</mi>  '
            '</math>'
        )
        root = normalize(src)
        assert root[0].get("data-bk-span") == "3,4"


# ---------------------------------------------------------------------------
# MathML pass-through adapter preserves data-bk-span
# ---------------------------------------------------------------------------


class TestMathMLAdapterPreservesAttrib:
    """The pass-through adapter just validates XML parses; it returns
    the original string. Any data-bk-span attribute round-trips
    unchanged because the adapter never re-serializes."""

    def test_adapter_passes_attrib_through_to_normalizer(self):
        from brailix.frontend.math.adapters.mathml import (
            MathMLSourceAdapter,
        )

        adapter = MathMLSourceAdapter()
        src = (
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            '<mi data-bk-span="3,4">x</mi>'
            '</math>'
        )
        result = adapter.to_mathml(src)
        # Adapter returns the original string (whitespace-stripped).
        # The attribute appears unmodified.
        assert 'data-bk-span="3,4"' in result
        # Round-trip through normalizer too: the attribute persists.
        root = normalize(result)
        assert root[0].get("data-bk-span") == "3,4"


# ---------------------------------------------------------------------------
# Nested override: child overrides parent override
# ---------------------------------------------------------------------------


class TestNestedSpanOverride:
    def test_child_overrides_parent_span(self, profile):
        # Parent mrow declares (0, 10), child mn declares (3, 5).
        # Child's cells must use (3, 5).
        root = ET.Element("math")
        mrow = ET.SubElement(
            root, "mrow", attrib={"data-bk-span": "0,10"}
        )
        mn = ET.SubElement(mrow, "mn", attrib={"data-bk-span": "3,5"})
        mn.text = "5"
        cells, _ = _emit(root, profile, formula_span=Span(50, 60))
        for c in cells:
            assert c.source_span == Span(3, 5)

    def test_parent_span_restored_after_child(self, profile):
        # Parent mrow has (0, 10); first child uses inherited parent
        # span; second child overrides to (5, 6); third child must go
        # back to (0, 10).
        root = ET.Element("math")
        mrow = ET.SubElement(
            root, "mrow", attrib={"data-bk-span": "0,10"}
        )
        a = ET.SubElement(mrow, "mi")  # inherits (0, 10)
        a.text = "x"
        b = ET.SubElement(mrow, "mi", attrib={"data-bk-span": "5,6"})
        b.text = "y"
        c = ET.SubElement(mrow, "mi")  # inherits (0, 10) again
        c.text = "z"
        cells, _ = _emit(root, profile, formula_span=Span(50, 60))
        # Find the spans of x, y, z by their source_text.
        spans_by_text: dict[str, set] = {}
        for cell in cells:
            if cell.source_text:
                spans_by_text.setdefault(cell.source_text, set()).add(
                    cell.source_span
                )
        # x and z inherit the mrow's (0, 10).
        assert spans_by_text["x"] == {Span(0, 10)}
        # y has its own override.
        assert spans_by_text["y"] == {Span(5, 6)}
        # z is back to the mrow's span (parent was restored).
        assert spans_by_text["z"] == {Span(0, 10)}
