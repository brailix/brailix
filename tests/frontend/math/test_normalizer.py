"""Tests for the MathML normalizer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend.math.normalizer import normalize


def _tags(elem: ET.Element) -> list[str]:
    return [elem.tag] + [t for child in elem for t in _tags(child)]


class TestNamespaceStripping:
    def test_strips_default_mathml_namespace(self):
        src = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
        root = normalize(src)
        assert root.tag == "math"
        assert root[0].tag == "mi"

    def test_passes_through_unnamespaced(self):
        root = normalize("<math><mi>x</mi></math>")
        assert root.tag == "math"
        assert root[0].tag == "mi"


class TestSingletonMrowCollapse:
    def test_collapses_single_child_mrow(self):
        root = normalize("<math><mrow><mi>x</mi></mrow></math>")
        # <mrow><mi>x</mi></mrow> → <mi>x</mi>
        assert root[0].tag == "mi"
        assert root[0].text == "x"

    def test_collapsed_mrow_attributes_move_to_child(self):
        # A data-bk-* attribute on a singleton <mrow> must survive the
        # collapse onto the surviving child — backend dispatch reads these
        # off the tree (math-redesign §7 / math-boundaries §7.2).
        root = normalize('<math><mrow data-bk-span="3,7"><mi>x</mi></mrow></math>')
        assert root[0].tag == "mi"
        assert root[0].get("data-bk-span") == "3,7"

    def test_collapse_keeps_child_attribute_on_conflict(self):
        # If both the mrow and its child carry the same key, the child's
        # (more specific) value wins.
        root = normalize(
            '<math><mrow data-bk-span="0,9">'
            '<mi data-bk-span="2,3">x</mi></mrow></math>'
        )
        assert root[0].get("data-bk-span") == "2,3"

    def test_collapses_nested_singletons(self):
        src = "<math><mrow><mrow><mi>y</mi></mrow></mrow></math>"
        root = normalize(src)
        # Both wrappers collapse → root has a single <mi> child.
        assert len(root) == 1
        assert root[0].tag == "mi"

    def test_keeps_multi_child_mrow(self):
        src = "<math><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></math>"
        root = normalize(src)
        mrow = root[0]
        assert mrow.tag == "mrow"
        assert [c.tag for c in mrow] == ["mi", "mo", "mn"]

    def test_keeps_mrow_with_text(self):
        # An mrow with non-whitespace text isn't a pure wrapper; keep it.
        src = "<math><mrow>x<mi>y</mi></mrow></math>"
        root = normalize(src)
        assert root[0].tag == "mrow"


class TestWhitespaceStripping:
    def test_drops_whitespace_only_text(self):
        src = (
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            "  <mrow>  <mi>x</mi>  <mo>+</mo>  <mn>1</mn>  </mrow>  "
            "</math>"
        )
        root = normalize(src)
        mrow = root[0]
        # No stray whitespace text / tail leaks into the children.
        for child in mrow:
            assert child.tail is None or child.tail.strip() != ""
        assert mrow.text is None

    def test_preserves_meaningful_text(self):
        src = "<math><mtext>hello world</mtext></math>"
        root = normalize(src)
        assert root[0].text == "hello world"


class TestSoftFailures:
    def test_malformed_xml_yields_merror_tree(self):
        # Parse-error inputs are wrapped into <merror> via merror_wrap.
        root = normalize("<math><mi>x")  # missing close tags
        # Namespace gets stripped, so look for the local name.
        assert root.find(".//merror") is not None

    def test_empty_input_yields_merror(self):
        root = normalize("")
        assert root.find(".//merror") is not None

    def test_malformed_with_control_char_yields_merror(self):
        # An XML-1.0-illegal control char in the malformed source is
        # echoed into the <merror> wrapper; un-stripped it would make the
        # re-parse raise instead of soft-failing.
        root = normalize("<math>\x0c<mi>x")  # form-feed + missing close
        assert root.find(".//merror") is not None
