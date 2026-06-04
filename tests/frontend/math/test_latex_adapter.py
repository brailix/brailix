"""Tests for the LaTeX adapter (latex2mathml-backed)."""

from __future__ import annotations

import sys
import types
import xml.etree.ElementTree as ET

import pytest

from brailix.core.errors import MissingExtraError
from brailix.frontend.math.adapters.latex import (
    LatexMathSourceAdapter,
    _strip_math_delimiters,
)
from brailix.frontend.math.registry import math_source_registry


@pytest.fixture
def reset_math_source_cache():
    """Clear ``math_source_registry`` cache before and after the test.

    Any test that monkeypatches ``sys.modules['latex2mathml']`` and
    calls ``clear_cache()`` poisons the registry: the next ``get()``
    re-imports against the fake module and caches that instance. When
    monkeypatch later restores ``sys.modules``, the cache still holds
    the fake. Clearing on teardown forces a fresh, real-module re-load
    for subsequent tests in the same session.
    """
    math_source_registry.clear_cache()
    yield
    math_source_registry.clear_cache()


# ---------------------------------------------------------------------------
# Lazy-import contract
# ---------------------------------------------------------------------------


def test_missing_latex2mathml_surfaces_missing_extra(
    monkeypatch, reset_math_source_cache
):
    """If ``latex2mathml`` cannot be imported, the registry must report
    MissingExtraError pointing at the ``latex`` extra."""
    monkeypatch.setitem(sys.modules, "latex2mathml", None)
    monkeypatch.setitem(sys.modules, "latex2mathml.converter", None)

    with pytest.raises(MissingExtraError) as ei:
        math_source_registry.get("latex")
    assert ei.value.extra == "latex"
    assert "pip install brailix[latex]" in str(ei.value)


# ---------------------------------------------------------------------------
# Conversion logic with injected converter
# ---------------------------------------------------------------------------


def _fake_converter(formula: str) -> str:
    return f'<math><mtext>{formula}</mtext></math>'


class TestConvert:
    def test_basic_passes_through_converter(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml("x^2")
        assert "<mtext>x^2</mtext>" in out

    def test_strips_dollar_delimiters(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        # ``$x^2$`` → converter should see ``x^2``, not ``$x^2$``.
        out = adapter.to_mathml("$x^2$")
        assert "<mtext>x^2</mtext>" in out

    def test_strips_paren_delimiters(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml("\\(x^2\\)")
        assert "<mtext>x^2</mtext>" in out

    def test_strips_bracket_delimiters(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml("\\[x^2\\]")
        assert "<mtext>x^2</mtext>" in out

    def test_bytes_input_decoded(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml(b"x^2")
        assert "<mtext>x^2</mtext>" in out


class TestSoftFailures:
    def test_empty_input_yields_merror(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml("")
        root = ET.fromstring(out)
        assert root.find(".//{http://www.w3.org/1998/Math/MathML}merror") is not None

    def test_converter_exception_caught(self):
        def broken(_: str) -> str:
            raise ValueError("nope")

        adapter = LatexMathSourceAdapter(converter=broken)
        out = adapter.to_mathml("x")
        root = ET.fromstring(out)
        err = root.find(".//{http://www.w3.org/1998/Math/MathML}merror")
        assert err is not None
        assert "latex2mathml error" in err.get("data-reason", "")

    def test_invalid_utf8_bytes_yields_merror(self):
        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        out = adapter.to_mathml(b"\xff\xfe")
        root = ET.fromstring(out)
        err = root.find(".//{http://www.w3.org/1998/Math/MathML}merror")
        assert err is not None
        assert err.get("data-reason") == "non-utf8 bytes"


class TestDelimiterHelper:
    @pytest.mark.parametrize(
        "wrapped,inner",
        [
            ("$x^2$", "x^2"),
            ("\\(x\\)", "x"),
            ("\\[y\\]", "y"),
            # No delimiters → unchanged.
            ("x+1", "x+1"),
            # Single ``$`` is not a wrap.
            ("$", "$"),
        ],
    )
    def test_strip(self, wrapped, inner):
        assert _strip_math_delimiters(wrapped) == inner


# ---------------------------------------------------------------------------
# Loader with fake module
# ---------------------------------------------------------------------------


class TestLoaderWithFakeModule:
    def test_load_wires_converter(self, monkeypatch, reset_math_source_cache):
        fake_converter_mod = types.ModuleType("latex2mathml.converter")

        def fake_convert(formula: str) -> str:
            return f"<math><mn>fake:{formula}</mn></math>"

        fake_converter_mod.convert = fake_convert

        fake_pkg = types.ModuleType("latex2mathml")
        fake_pkg.converter = fake_converter_mod

        monkeypatch.setitem(sys.modules, "latex2mathml", fake_pkg)
        monkeypatch.setitem(sys.modules, "latex2mathml.converter", fake_converter_mod)
        math_source_registry.clear_cache()

        adapter = math_source_registry.get("latex")
        assert isinstance(adapter, LatexMathSourceAdapter)
        out = adapter.to_mathml("y")
        assert "fake:y" in out


class TestProtocolConformance:
    def test_instance_conforms(self):
        from brailix.core.protocols import MathSourceAdapter

        adapter = LatexMathSourceAdapter(converter=_fake_converter)
        assert isinstance(adapter, MathSourceAdapter)
