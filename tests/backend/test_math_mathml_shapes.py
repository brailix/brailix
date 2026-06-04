"""End-to-end MathML shape tests for the math backend.

Confirms that real-world MathML producer outputs (latex2mathml,
hand-written MathML, OMML-style trees) route correctly through the
new tag-based dispatcher.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import translate
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.frontend.math.normalizer import normalize
from brailix.ir.inline import MathInline


def mml(xml: str) -> ET.Element:
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


def roles(cells):
    return [c.role for c in cells]


# ---------------------------------------------------------------------------
# Singleton wrappers / unwrapped variants
# ---------------------------------------------------------------------------


class TestNamespaceVariants:
    def test_with_namespace(self, profile):
        cells, _ = emit(
            mml(
                '<math xmlns="http://www.w3.org/1998/Math/MathML">'
                "<mi>x</mi></math>"
            ),
            profile,
        )
        assert any(c.role == "math_identifier" for c in cells)

    def test_with_display_attribute(self, profile):
        cells, _ = emit(
            mml('<math display="block"><mn>5</mn></math>'),
            profile,
        )
        assert any(c.role == "math_digit" for c in cells)


# ---------------------------------------------------------------------------
# latex2mathml-style outputs (mrow-wrapped, stretchy attrs, etc.)
# ---------------------------------------------------------------------------


class TestLatex2MathmlShapes:
    def test_mrow_with_stretchy_parens(self, profile):
        # latex2mathml wraps parens with stretchy="false".
        cells, _ = emit(
            mml(
                "<math><mrow>"
                '<mo stretchy="false">(</mo><mi>x</mi>'
                '<mo stretchy="false">)</mo>'
                "</mrow></math>"
            ),
            profile,
        )
        # Two delim cells around the identifier.
        delims = [c for c in cells if c.role == "math_delim"]
        assert len(delims) == 2

    def test_multiple_nested_mrows(self, profile):
        # Deep wrapping that the normalizer flattens.
        cells, _ = emit(
            mml(
                "<math><mrow><mrow><mrow>"
                "<mi>x</mi>"
                "</mrow></mrow></mrow></math>"
            ),
            profile,
        )
        # All inner mrows collapse to just <mi>x</mi>.
        assert any(c.role == "math_identifier" for c in cells)


# ---------------------------------------------------------------------------
# Long expressions
# ---------------------------------------------------------------------------


class TestLongExpressions:
    def test_quadratic_formula(self, profile):
        # x = (-b ± √(b² - 4ac)) / (2a)
        cells, wc = emit(
            mml(
                "<math>"
                "<mi>x</mi><mo>=</mo>"
                "<mfrac>"
                "<mrow>"
                "<mo>−</mo><mi>b</mi><mo>±</mo>"
                "<msqrt>"
                "<mrow>"
                "<msup><mi>b</mi><mn>2</mn></msup>"
                "<mo>−</mo>"
                "<mn>4</mn><mi>a</mi><mi>c</mi>"
                "</mrow>"
                "</msqrt>"
                "</mrow>"
                "<mrow><mn>2</mn><mi>a</mi></mrow>"
                "</mfrac>"
                "</math>"
            ),
            profile,
        )
        # No unknown cells, no warnings.
        unknowns = [c for c in cells if c.role == "unknown"]
        assert unknowns == [], f"unexpected unknown cells: {unknowns}"
        bad = [w for w in wc if w.code.startswith("MATH_")]
        assert bad == []
        # Structural integrity:
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_sqrt_open" in r
        assert "math_superscript" in r

    def test_pythagorean(self, profile):
        # a² + b² = c²
        cells, _ = emit(
            mml(
                "<math>"
                "<msup><mi>a</mi><mn>2</mn></msup>"
                "<mo>+</mo>"
                "<msup><mi>b</mi><mn>2</mn></msup>"
                "<mo>=</mo>"
                "<msup><mi>c</mi><mn>2</mn></msup>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert r.count("math_superscript") == 3
        # All atomic — no closes.
        assert "math_script_close" not in r

    def test_circle_area(self, profile):
        # S = π r²
        cells, wc = emit(
            mml(
                "<math>"
                "<mi>S</mi><mo>=</mo>"
                "<mi>π</mi><msup><mi>r</mi><mn>2</mn></msup>"
                "</math>"
            ),
            profile,
        )
        bad = [w for w in wc if w.code.startswith("MATH_")]
        assert bad == []
        r = roles(cells)
        assert "math_rel" in r
        assert "math_superscript" in r
        # Both latin and greek identifiers present.
        ident_dots = {c.dots for c in cells if c.role == "math_identifier"}
        assert (6,) in ident_dots          # latin upper prefix
        assert (4, 6) in ident_dots        # greek lower prefix

    def test_integral_over_interval(self, profile):
        # ∫_0^1 x dx
        cells, _ = emit(
            mml(
                "<math>"
                "<msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup>"
                "<mi>x</mi>"
                "<mi>d</mi><mi>x</mi>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_big_op" in r
        assert "math_big_op_script_prefix" in r
        # Two prefix occurrences (sub + sup).
        assert r.count("math_big_op_script_prefix") == 2

    def test_summation(self, profile):
        # ∑_{i=1}^n i
        cells, _ = emit(
            mml(
                "<math>"
                "<msubsup><mo>∑</mo>"
                "<mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
                "<mi>n</mi>"
                "</msubsup>"
                "<mi>i</mi>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_big_op" in r
        # Sum now carries the 46-dot script prefix on its limits.
        assert "math_big_op_script_prefix" in r

    def test_limit_simple(self, profile):
        # lim_{n → ∞} 1/n
        cells, _ = emit(
            mml(
                "<math>"
                "<msub><mi>lim</mi>"
                "<mrow><mi>n</mi><mo>→</mo><mi>∞</mi></mrow>"
                "</msub>"
                "<mfrac><mn>1</mn><mi>n</mi></mfrac>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_big_op_script_prefix" in r
        assert "math_subscript" in r
        assert "math_fraction_bar" in r


# ---------------------------------------------------------------------------
# Mixed content
# ---------------------------------------------------------------------------


class TestMixedContent:
    def test_mtext_and_math_intermixed(self, profile):
        # <mtext> for "where" and math elsewhere.
        cells, _ = emit(
            mml(
                "<math>"
                "<mi>x</mi><mo>=</mo>"
                "<mi>n</mi><mtext>≤</mtext>"
                "<mi>m</mi>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_text" in r
        assert "math_identifier" in r

    def test_function_with_arguments(self, profile):
        # sin(x) — mi sin + delim ( + mi x + delim )
        cells, _ = emit(
            mml(
                "<math>"
                "<mi>sin</mi><mo>(</mo><mi>x</mi><mo>)</mo>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_function_name" in r
        assert "math_delim" in r
        assert "math_identifier" in r

    def test_nested_fractions(self, profile):
        # 1 / (1 + 1/x)
        cells, wc = emit(
            mml(
                "<math>"
                "<mfrac>"
                "<mn>1</mn>"
                "<mrow><mn>1</mn><mo>+</mo>"
                "<mfrac><mn>1</mn><mi>x</mi></mfrac>"
                "</mrow>"
                "</mfrac>"
                "</math>"
            ),
            profile,
        )
        # No warnings expected.
        bad = [w for w in wc if w.code.startswith("MATH_")]
        assert bad == []
        # The outer fraction is compound; the inner fraction is
        # simplified (1/x ⇒ slash bar).
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
        # There should be at least 2 bars total.
        assert r.count("math_fraction_bar") >= 2

    def test_nested_scripts(self, profile):
        # 2^{x^2}
        cells, _ = emit(
            mml(
                "<math>"
                "<msup>"
                "<mn>2</mn>"
                "<msup><mi>x</mi><mn>2</mn></msup>"
                "</msup>"
                "</math>"
            ),
            profile,
        )
        r = roles(cells)
        # Two superscript markers.
        assert r.count("math_superscript") == 2


# ---------------------------------------------------------------------------
# Boundary / defensive
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_lone_mfrac(self, profile):
        # Without a math wrapper — backend should still handle.
        elem = ET.Element("mfrac")
        num = ET.SubElement(elem, "mn")
        num.text = "1"
        den = ET.SubElement(elem, "mn")
        den.text = "2"
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        node = MathInline(surface="", math=elem)
        cells = translate(node, ctx, profile)
        # Antoine fires.
        assert any(c.role == "math_digit_lower" for c in cells)

    def test_unknown_tag(self, profile):
        # An entirely unknown tag.
        elem = ET.Element("foobar")
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        node = MathInline(surface="", math=elem)
        cells = translate(node, ctx, profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNSUPPORTED_ELEMENT" for w in wc)

    def test_tree_with_no_children_no_text(self, profile):
        # <math></math> — empty.
        cells, _ = emit(mml("<math></math>"), profile)
        assert cells == []
