"""Tests for the SVG normalizer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend.graphics.normalizer import normalize


def _tags(elem: ET.Element) -> list[str]:
    return [elem.tag] + [t for child in elem for t in _tags(child)]


class TestNamespaceStripping:
    def test_strips_default_svg_namespace(self):
        src = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect x="0" y="0"/></svg>'
        )
        root = normalize(src)
        assert root.tag == "svg"
        assert root[0].tag == "rect"

    def test_passes_through_unnamespaced(self):
        root = normalize('<svg><circle r="1"/></svg>')
        assert root.tag == "svg"
        assert root[0].tag == "circle"

    def test_preserves_geometry_attributes(self):
        root = normalize(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9">'
            '<line x1="1" y1="2" x2="3" y2="4"/></svg>'
        )
        assert root.get("viewBox") == "0 0 9 9"
        line = root[0]
        assert line.get("x1") == "1"
        assert line.get("y2") == "4"


class TestWhitespace:
    def test_pure_whitespace_text_nulled(self):
        root = normalize("<svg>\n  <rect/>\n</svg>")
        assert root.text is None
        assert root[0].tag == "rect"


class TestSoftFailure:
    def test_malformed_never_raises(self):
        root = normalize("<svg><rect></svg>")
        assert root.tag == "svg"
        assert root.get("data-bk-error") is not None

    def test_empty_string(self):
        root = normalize("")
        assert root.tag == "svg"
        assert root.get("data-bk-error") is not None

    def test_too_deep_degrades(self):
        deep = "<svg>" + "<g>" * 250 + "</g>" * 250 + "</svg>"
        root = normalize(deep)
        assert root.tag == "svg"
        assert root.get("data-bk-error") is not None
