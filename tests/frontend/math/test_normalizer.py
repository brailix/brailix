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


class TestPresentationalWrappers:
    """``<mstyle>`` / ``<mpadded>`` are renamed to ``<mrow>`` (typographic
    hints only — latex2mathml wraps every ``\\displaystyle`` formula in
    one); ``<mspace>`` / ``<mphantom>`` are removed (print-space
    occupiers with no braille meaning) — except
    ``<mspace linebreak="newline">``, latex2mathml's output for a bare
    ``\\\\`` line break, which separates content and is kept for the
    backend.  Regression guard: these used to reach the backend's
    unsupported-element fallback, which drops the whole subtree — a
    ``\\displaystyle`` fraction vanished into a single unknown cell."""

    def test_mstyle_renamed_to_mrow_and_collapsed(self):
        root = normalize(
            '<math><mstyle displaystyle="true">'
            "<mfrac><mn>1</mn><mn>2</mn></mfrac></mstyle></math>"
        )
        # Renamed to mrow, then collapsed as a singleton — the fraction
        # hangs directly off <math>.
        assert _tags(root) == ["math", "mfrac", "mn", "mn"]

    def test_mstyle_with_multiple_children_becomes_mrow(self):
        root = normalize(
            '<math><mstyle scriptlevel="1">'
            "<mi>x</mi><mo>+</mo><mn>1</mn></mstyle></math>"
        )
        assert root[0].tag == "mrow"
        assert [c.tag for c in root[0]] == ["mi", "mo", "mn"]
        # Presentational attributes are dropped with the rename.
        assert "scriptlevel" not in root[0].attrib

    def test_mpadded_renamed_like_mstyle(self):
        root = normalize(
            '<math><mpadded width="2em"><mi>x</mi></mpadded></math>'
        )
        assert _tags(root) == ["math", "mi"]

    def test_data_bk_attributes_survive_the_rename(self):
        root = normalize(
            '<math><mstyle data-bk-span="1,3">'
            "<mi>x</mi><mo>!</mo></mstyle></math>"
        )
        assert root[0].tag == "mrow"
        assert root[0].get("data-bk-span") == "1,3"

    def test_mspace_and_mphantom_removed(self):
        root = normalize(
            '<math><mi>a</mi><mspace width="1em" /><mi>b</mi>'
            "<mphantom><mi>c</mi></mphantom></math>"
        )
        assert _tags(root) == ["math", "mi", "mi"]

    def test_newline_mspace_kept(self):
        # A bare ``\\`` line break (latex2mathml: <mspace
        # linebreak="newline">) separates content — dropping it would
        # fuse the flanking expressions. The backend renders it as a
        # blank-cell separator.
        root = normalize(
            "<math><mi>a</mi>"
            '<mspace linebreak="newline" /><mi>b</mi></math>'
        )
        assert _tags(root) == ["math", "mi", "mspace", "mi"]
        assert root[1].get("linebreak") == "newline"

    def test_phantom_inside_style_unwraps_cleanly(self):
        root = normalize(
            "<math><mstyle><mphantom><mi>x</mi></mphantom>"
            "<mi>y</mi></mstyle></math>"
        )
        assert _tags(root) == ["math", "mi"]
        assert root[0].text == "y"


class TestInvisibleOperators:
    """An ``<mo>`` holding a Unicode invisible operator (U+2061
    function application / U+2062 invisible times / U+2063 invisible
    separator / U+2064 invisible plus) renders as nothing and is
    dropped. The OMML ``m:func`` adapter emits U+2061 between a function
    name and its argument — before this drop it degraded into an
    unknown cell with a ``MATH_UNKNOWN_SYMBOL`` warning, and it hid the
    name/argument adjacency the backend's function-argument fraction
    rule keys off."""

    def test_apply_function_mo_dropped(self):
        root = normalize(
            "<math><mrow><mi>cos</mi><mo>&#x2061;</mo><mi>x</mi></mrow></math>"
        )
        assert _tags(root) == ["math", "mrow", "mi", "mi"]

    def test_apply_function_before_mfrac_dropped(self):
        # The OMML m:func + fraction-argument shape: after the drop the
        # <mfrac> is the function name's direct sibling.
        root = normalize(
            "<math><mrow><mi>cos</mi><mo>&#x2061;</mo>"
            "<mfrac><mi>x</mi><mi>y</mi></mfrac></mrow></math>"
        )
        mrow = root[0]
        assert [c.tag for c in mrow] == ["mi", "mfrac"]

    def test_invisible_times_separator_plus_dropped(self):
        root = normalize(
            "<math><mrow><mn>2</mn><mo>&#x2062;</mo><mi>x</mi>"
            "<mo>&#x2063;</mo><mi>y</mi><mo>&#x2064;</mo><mn>1</mn>"
            "</mrow></math>"
        )
        assert [c.tag for c in root[0]] == ["mn", "mi", "mi", "mn"]

    def test_visible_mo_kept(self):
        root = normalize(
            "<math><mrow><mn>2</mn><mo>+</mo><mi>x</mi></mrow></math>"
        )
        assert [c.tag for c in root[0]] == ["mn", "mo", "mi"]


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


class TestDegreeCircleRewrite:
    def test_numeric_msup_ring_rewritten_to_degree_sign(self):
        root = normalize("<math><msup><mn>144</mn><mo>∘</mo></msup></math>")

        assert [child.tag for child in root] == ["mn", "mo"]
        assert root[0].text == "144"
        assert root[1].text == "°"

    def test_symbolic_msup_ring_left_unchanged(self):
        root = normalize("<math><msup><mi>A</mi><mo>∘</mo></msup></math>")

        assert len(root) == 1
        assert root[0].tag == "msup"
        assert [child.tag for child in root[0]] == ["mi", "mo"]


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
