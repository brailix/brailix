"""Tests for :func:`brailix.frontend.math.parse_math_tree`.

The math frontend is just: source adapter → normalizer.
``parse_math_tree`` wraps that chain and returns the normalised
:class:`ET.Element` tree (no IR-builder step).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.core.context import MathContext
from brailix.frontend.math import parse_math_tree
from brailix.frontend.math.registry import math_source_registry


@pytest.fixture(autouse=True)
def reset_source_cache():
    math_source_registry.clear_cache()
    yield
    math_source_registry.clear_cache()


# ---------------------------------------------------------------------------
# Direct MathML path — no extras required
# ---------------------------------------------------------------------------


class TestMathmlInput:
    def test_mi_passthrough(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree("<math><mi>x</mi></math>", ctx)
        assert isinstance(tree, ET.Element)
        assert tree.tag == "math"
        # Single-child mi survives at the math root (no singleton-mrow
        # collapse here because <math> is not <mrow>).
        assert tree[0].tag == "mi"
        assert (tree[0].text or "").strip() == "x"

    def test_namespace_stripped(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree(
            '<math xmlns="http://www.w3.org/1998/Math/MathML">'
            "<mi>y</mi></math>",
            ctx,
        )
        # Namespace prefix removed by the normalizer.
        assert tree.tag == "math"
        assert tree[0].tag == "mi"

    def test_singleton_mrow_collapsed(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree(
            "<math><mrow><mi>x</mi></mrow></math>", ctx
        )
        # The mrow with one child collapses.
        assert tree[0].tag == "mi"

    def test_nested_singleton_mrows_collapse(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree(
            "<math><mrow><mrow><mi>x</mi></mrow></mrow></math>", ctx
        )
        assert tree[0].tag == "mi"

    def test_whitespace_text_stripped(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree(
            "<math>   <mi>x</mi>   <mn>1</mn>   </math>", ctx
        )
        # Only whitespace text nodes between elements are dropped.
        assert [c.tag for c in tree] == ["mi", "mn"]

    def test_invalid_xml_yields_merror(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree("<math><not-closed>", ctx)
        # The normalizer wraps parse errors in <merror>.
        assert tree.tag == "math"
        assert tree[0].tag == "merror"

    def test_complex_mfrac(self):
        ctx = MathContext(source="mathml")
        tree = parse_math_tree(
            "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>", ctx
        )
        frac = tree[0]
        assert frac.tag == "mfrac"
        assert [c.tag for c in frac] == ["mn", "mn"]
        assert [c.text for c in frac] == ["1", "2"]


# ---------------------------------------------------------------------------
# Adapter selection / missing adapter
# ---------------------------------------------------------------------------


class TestAdapterSelection:
    def test_plain_source_emits_missing_warning(self):
        ctx = MathContext(source="plain")
        result = parse_math_tree("x", ctx)
        assert result is None
        warnings = ctx.warnings.by_code("MATH_ADAPTER_MISSING")
        assert len(warnings) == 1

    def test_unknown_source_emits_missing_warning(self):
        ctx = MathContext(source="nonsuch")
        result = parse_math_tree("x", ctx)
        assert result is None
        warnings = ctx.warnings.by_code("MATH_ADAPTER_MISSING")
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Soft-failure backstop — a raising adapter must not crash the caller
# ---------------------------------------------------------------------------


class TestRaisingAdapterBackstop:
    def test_raising_adapter_degrades_to_merror(self):
        # The registry is open to third-party adapters; one that raises
        # must degrade to the standard <merror> tree (the backend
        # renders an unknown cell + MATH_ERROR warning), never crash
        # the pipeline.
        class _Boom:
            source = "boom-raise-test"

            def to_mathml(self, formula, ctx=None):
                raise RuntimeError("boom")

        math_source_registry.register("boom-raise-test", _Boom)
        try:
            ctx = MathContext(source="boom-raise-test")
            tree = parse_math_tree("x + 1", ctx)
            assert tree is not None
            assert tree.find(".//merror") is not None
        finally:
            # Don't leak the test adapter into the process-wide registry;
            # clear_cache() keeps registered loaders.
            math_source_registry.unregister("boom-raise-test")

    def test_warning_carries_source_string(self):
        ctx = MathContext(source="weird")
        parse_math_tree("x", ctx)
        warnings = ctx.warnings.by_code("MATH_ADAPTER_MISSING")
        assert any("weird" in w.message for w in warnings)


# ---------------------------------------------------------------------------
# Round-trip: build a tree, serialise as MathML string, re-parse.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_serialise_then_reparse_preserves_structure(self):
        ctx = MathContext(source="mathml")
        original = (
            "<math><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></math>"
        )
        tree = parse_math_tree(original, ctx)
        serialised = ET.tostring(tree, encoding="unicode")
        tree2 = parse_math_tree(serialised, ctx)
        assert ET.tostring(tree, encoding="unicode") == ET.tostring(
            tree2, encoding="unicode"
        )
