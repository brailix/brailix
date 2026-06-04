"""Tests for the pass-through MathML adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.core.context import MathContext
from brailix.frontend.math.adapters.mathml import (
    MathMLSourceAdapter,
    merror_wrap,
)
from brailix.frontend.math.registry import math_source_registry


@pytest.fixture
def adapter() -> MathMLSourceAdapter:
    return MathMLSourceAdapter()


class TestRoundTrip:
    def test_valid_mathml_passes_through(self, adapter):
        src = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
        assert adapter.to_mathml(src) == src

    def test_strips_outer_whitespace(self, adapter):
        result = adapter.to_mathml("  <math><mi>x</mi></math>  ")
        assert result == "<math><mi>x</mi></math>"

    def test_bytes_decoded_as_utf8(self, adapter):
        src = b"<math><mn>1</mn></math>"
        assert adapter.to_mathml(src) == "<math><mn>1</mn></math>"


class TestSoftFailures:
    def test_empty_input_yields_merror(self, adapter):
        out = adapter.to_mathml("")
        root = ET.fromstring(out)
        assert root.find(".//{http://www.w3.org/1998/Math/MathML}merror") is not None

    def test_malformed_xml_yields_merror(self, adapter):
        out = adapter.to_mathml("<math><mi>x")
        root = ET.fromstring(out)
        err = root.find(".//{http://www.w3.org/1998/Math/MathML}merror")
        assert err is not None
        assert "parse error" in err.get("data-reason", "")

    def test_invalid_utf8_bytes_yields_merror(self, adapter):
        # Bytes that don't decode as UTF-8 still produce a tidy merror.
        out = adapter.to_mathml(b"\xff\xfeabc")
        root = ET.fromstring(out)
        err = root.find(".//{http://www.w3.org/1998/Math/MathML}merror")
        assert err is not None
        assert err.get("data-reason") == "non-utf8 bytes"

    def test_merror_wrap_escapes_xml_chars(self):
        out = merror_wrap("a & b < c", reason="testing")
        # Round-trip parse the result.
        root = ET.fromstring(out)
        text = root.find(".//{http://www.w3.org/1998/Math/MathML}mtext")
        assert text.text == "a & b < c"

    def test_merror_wrap_escapes_reason_attribute(self):
        reason = 'bad "x" & y < z'
        out = merror_wrap("surface", reason=reason)
        root = ET.fromstring(out)
        err = root.find(".//{http://www.w3.org/1998/Math/MathML}merror")
        assert err is not None
        assert err.get("data-reason") == reason


class TestRegistry:
    def test_registered_under_mathml(self):
        adapter = math_source_registry.get("mathml")
        assert isinstance(adapter, MathMLSourceAdapter)
        assert adapter.source == "mathml"

    def test_satisfies_protocol(self, adapter):
        from brailix.core.protocols import MathSourceAdapter

        assert isinstance(adapter, MathSourceAdapter)

    def test_context_argument_is_optional(self, adapter):
        # ``ctx`` may be omitted (None) — adapters don't rely on it for
        # pass-through; downstream phases use it.
        assert adapter.to_mathml("<math/>", None) == "<math/>"

    def test_context_argument_accepted(self, adapter):
        ctx = MathContext(source="mathml")
        assert adapter.to_mathml("<math/>", ctx) == "<math/>"
