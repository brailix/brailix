"""Tests for the figure-generator source adapter."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from brailix.core.context import GraphicsContext
from brailix.core.errors import WarningCollector
from brailix.core.protocols import GraphicSourceAdapter
from brailix.frontend.graphics.adapters.figure import FigureSourceAdapter
from brailix.frontend.graphics.registry import graphic_source_registry


def _svg(spec: dict) -> ET.Element:
    return ET.fromstring(FigureSourceAdapter().to_svg(json.dumps(spec)))


class TestDispatch:
    def test_bar_produces_svg(self):
        root = _svg({"kind": "bar", "data": [{"label": "A", "value": 1}]})
        assert root.tag == "svg"
        assert root.get("viewBox") is not None
        assert any(c.tag == "rect" for c in root)

    def test_number_line_produces_svg(self):
        root = _svg({"kind": "number_line", "min": 0, "max": 5})
        assert any(c.tag == "line" for c in root)

    def test_table_produces_svg(self):
        root = _svg({"kind": "table", "rows": [["a", "b"]]})
        assert any(c.tag == "line" for c in root)


class TestSoftFailures:
    def test_unknown_kind_warns(self):
        warn = WarningCollector()
        ctx = GraphicsContext(warnings=warn)
        out = FigureSourceAdapter().to_svg('{"kind": "pie"}', ctx)
        root = ET.fromstring(out)
        assert root.get("data-bk-error") is not None
        assert any(w.code == "GRAPHICS_UNKNOWN_FIGURE" for w in warn)

    def test_missing_kind(self):
        out = FigureSourceAdapter().to_svg('{"data": []}')
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_invalid_json(self):
        out = FigureSourceAdapter().to_svg("{nope")
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_empty(self):
        out = FigureSourceAdapter().to_svg("")
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_non_object_spec(self):
        out = FigureSourceAdapter().to_svg("[1, 2, 3]")
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_bytes_input(self):
        out = FigureSourceAdapter().to_svg(b'{"kind": "number_line"}')
        assert ET.fromstring(out).tag == "svg"


class TestRegistry:
    def test_figure_registered_and_conforms(self):
        adapter = graphic_source_registry.get("figure")
        assert isinstance(adapter, GraphicSourceAdapter)
        assert adapter.source == "figure"
