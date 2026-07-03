"""Tests for the geometry-primitives source adapter."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from brailix.core.context import GraphicsContext
from brailix.core.errors import WarningCollector
from brailix.core.protocols import GraphicSourceAdapter
from brailix.frontend.graphics.adapters.primitives import (
    PrimitivesSourceAdapter,
    primitives_to_svg,
)
from brailix.frontend.graphics.registry import graphic_source_registry


class TestBuilder:
    def test_canvas_and_viewbox(self):
        out = primitives_to_svg({"width": 100, "height": 80, "shapes": []})
        root = ET.fromstring(out)
        assert root.tag == "svg"
        assert root.get("viewBox") == "0 0 100 80"
        assert root.get("width") == "100mm"
        assert root.get("height") == "80mm"

    def test_all_shape_types(self):
        spec = {
            "width": 100,
            "height": 100,
            "shapes": [
                {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 10},
                {"type": "rect", "x": 1, "y": 2, "width": 3, "height": 4},
                {"type": "circle", "cx": 5, "cy": 5, "r": 2},
                {"type": "ellipse", "cx": 5, "cy": 5, "rx": 3, "ry": 2},
                {"type": "polyline", "points": [[0, 0], [1, 1], [2, 0]]},
                {"type": "polygon", "points": [[0, 0], [2, 0], [1, 2]]},
                {"type": "label", "x": 1, "y": 1, "text": "A"},
            ],
        }
        root = ET.fromstring(primitives_to_svg(spec))
        tags = [c.tag for c in root]
        assert tags == [
            "line", "rect", "circle", "ellipse", "polyline", "polygon", "text"
        ]

    def test_points_formatting(self):
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [
                {"type": "polyline", "points": [[1, 2], [3, 4]]}
            ]}
        )
        root = ET.fromstring(out)
        assert root[0].get("points") == "1,2 3,4"

    def test_label_text_escaped(self):
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [
                {"type": "label", "x": 0, "y": 0, "text": "A & B < C"}
            ]}
        )
        # Round-trips through XML parsing without error and preserves text.
        root = ET.fromstring(out)
        assert root[0].text == "A & B < C"

    def test_stroke_width_emitted(self):
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [
                {"type": "line", "x1": 0, "y1": 0, "x2": 9, "y2": 9, "stroke_width": 3}
            ]}
        )
        root = ET.fromstring(out)
        assert root[0].get("stroke-width") == "3"

    def test_fill_emitted(self):
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [
                {"type": "rect", "x": 0, "y": 0, "width": 9, "height": 9, "fill": "hatch"}
            ]}
        )
        root = ET.fromstring(out)
        assert root[0].get("fill") == "hatch"

    def test_integer_formatting(self):
        out = primitives_to_svg(
            {"width": 10.0, "height": 10.0, "shapes": [
                {"type": "circle", "cx": 5.0, "cy": 5.5, "r": 2}
            ]}
        )
        root = ET.fromstring(out)
        assert root.get("viewBox") == "0 0 10 10"  # 10.0 → "10"
        assert root[0].get("cx") == "5"
        assert root[0].get("cy") == "5.5"


class TestSoftFailures:
    def test_unknown_shape_warns_and_skips(self):
        warn = WarningCollector()
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [
                {"type": "blob", "x": 1},
                {"type": "circle", "cx": 5, "cy": 5, "r": 2},
            ]},
            warn,
        )
        root = ET.fromstring(out)
        assert [c.tag for c in root] == ["circle"]  # blob skipped
        assert any(w.code == "GRAPHICS_UNKNOWN_SHAPE" for w in warn)

    def test_non_dict_shape_warns(self):
        warn = WarningCollector()
        primitives_to_svg(
            {"width": 9, "height": 9, "shapes": ["nope"]}, warn
        )
        assert any(w.code == "GRAPHICS_UNKNOWN_SHAPE" for w in warn)

    def test_non_dict_spec_soft_fails(self):
        root = ET.fromstring(primitives_to_svg(["not", "a", "dict"]))
        assert root.get("data-bk-error") is not None

    def test_missing_size_omits_viewbox(self):
        root = ET.fromstring(primitives_to_svg({"shapes": []}))
        assert root.get("viewBox") is None


class TestAdapter:
    def test_json_string(self):
        spec = {"width": 10, "height": 10, "shapes": [
            {"type": "circle", "cx": 5, "cy": 5, "r": 4}
        ]}
        out = PrimitivesSourceAdapter().to_svg(json.dumps(spec))
        root = ET.fromstring(out)
        assert root[0].tag == "circle"

    def test_bytes_input(self):
        out = PrimitivesSourceAdapter().to_svg(b'{"width":9,"height":9,"shapes":[]}')
        assert ET.fromstring(out).tag == "svg"

    def test_invalid_json_soft_fails(self):
        out = PrimitivesSourceAdapter().to_svg("{not json")
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_empty_soft_fails(self):
        out = PrimitivesSourceAdapter().to_svg("")
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_warnings_via_context(self):
        warn = WarningCollector()
        ctx = GraphicsContext(warnings=warn)
        PrimitivesSourceAdapter().to_svg(
            '{"width":9,"height":9,"shapes":[{"type":"blob"}]}', ctx
        )
        assert any(w.code == "GRAPHICS_UNKNOWN_SHAPE" for w in warn)


class TestRegistry:
    def test_primitives_registered_and_conforms(self):
        adapter = graphic_source_registry.get("primitives")
        assert isinstance(adapter, GraphicSourceAdapter)
        assert adapter.source == "primitives"
