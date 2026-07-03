"""Tests for the pass-through SVG source adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core.protocols import GraphicSourceAdapter
from brailix.frontend.graphics.adapters.svg import (
    SVGSourceAdapter,
    svg_error_wrap,
)
from brailix.frontend.graphics.registry import graphic_source_registry


def _adapter() -> SVGSourceAdapter:
    return SVGSourceAdapter()


class TestPassThrough:
    def test_valid_svg_unchanged(self):
        src = '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'
        assert _adapter().to_svg(src) == src

    def test_strips_xml_declaration(self):
        out = _adapter().to_svg('<?xml version="1.0" encoding="UTF-8"?><svg/>')
        assert out == "<svg/>"

    def test_strips_doctype(self):
        src = '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd"><svg/>'
        assert _adapter().to_svg(src) == "<svg/>"

    def test_bytes_input_decoded(self):
        out = _adapter().to_svg(b"<svg><rect/></svg>")
        assert out == "<svg><rect/></svg>"

    def test_whitespace_trimmed(self):
        assert _adapter().to_svg("  <svg/>  ") == "<svg/>"


class TestSoftFailures:
    def test_empty_input(self):
        out = _adapter().to_svg("")
        root = ET.fromstring(out)
        assert root.get("data-bk-error") is not None

    def test_malformed_xml(self):
        out = _adapter().to_svg("<svg><rect></svg>")
        root = ET.fromstring(out)
        assert "parse error" in root.get("data-bk-error")

    def test_non_utf8_bytes(self):
        out = _adapter().to_svg(b"\xff\xfe not utf8")
        root = ET.fromstring(out)
        assert root.get("data-bk-error") is not None

    def test_error_wrap_is_parseable_svg(self):
        out = svg_error_wrap("<bad", reason="boom")
        root = ET.fromstring(out)
        assert root.tag == "svg"
        assert root.get("data-bk-error") == "boom"
        # original surface preserved (escaped) for proofread UIs
        assert root[0].tag == "desc"
        assert root[0].text == "<bad"


class TestRegistry:
    def test_svg_registered_and_conforms(self):
        adapter = graphic_source_registry.get("svg")
        assert isinstance(adapter, GraphicSourceAdapter)
        assert adapter.source == "svg"

    def test_svg_in_names(self):
        assert "svg" in graphic_source_registry.names()
