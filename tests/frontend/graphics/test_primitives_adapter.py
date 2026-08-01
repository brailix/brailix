"""Tests for the geometry-primitives source adapter."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET

import pytest

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


class TestNonFiniteCoordinates:
    """``inf`` and ``NaN`` are not coordinates, and they used to be written as
    though they were.

    ``float("inf")`` converts without complaint, so the old formatter emitted
    the literal attribute ``cx="inf"`` — well-formed XML, not geometry, handed
    to the backend as if it were a point. Nothing raised and nothing warned;
    the figure came back blank. JSON reaches here carrying either literal, so
    a spec typed by hand or produced by a buggy tool arrives this way without
    anyone constructing a float.
    """

    @staticmethod
    def _build(shape: dict) -> tuple[ET.Element, WarningCollector]:
        warn = WarningCollector()
        out = primitives_to_svg(
            {"width": 9, "height": 9, "shapes": [shape]}, warn
        )
        return ET.fromstring(out), warn

    @pytest.mark.parametrize(
        "shape",
        [
            {"type": "circle", "cx": math.inf, "cy": 5, "r": 2},
            {"type": "circle", "cx": 5, "cy": 5, "r": math.nan},
            {"type": "line", "x1": 0, "y1": 0, "x2": -math.inf, "y2": 1},
            {"type": "rect", "x": 0, "y": 0, "width": math.inf, "height": 1},
            {"type": "polyline", "points": [[0, 0], [math.nan, 1]]},
            {"type": "label", "x": math.inf, "y": 1, "text": "A"},
            {"type": "circle", "cx": 1, "cy": 1, "r": 1, "stroke_width": math.inf},
        ],
    )
    def test_the_shape_is_skipped_with_a_warning(self, shape):
        root, warn = self._build(shape)
        assert list(root) == [], "a shape with no drawable coordinate was drawn"
        assert any(w.code == "GRAPHICS_INVALID_SPEC" for w in warn)

    def test_no_non_finite_literal_reaches_the_svg(self):
        """The property that matters downstream, checked on the text itself:
        whatever the skip logic does, ``inf`` / ``nan`` must not appear in an
        attribute the backend will parse as a number."""
        out = primitives_to_svg(
            {
                "width": 9,
                "height": 9,
                "shapes": [
                    {"type": "circle", "cx": math.inf, "cy": 0, "r": math.nan},
                    {"type": "circle", "cx": 4, "cy": 4, "r": 2},
                ],
            }
        )
        assert "inf" not in out and "nan" not in out
        assert [c.tag for c in ET.fromstring(out)] == ["circle"]

    def test_a_non_finite_canvas_omits_the_page_size(self):
        """``inf > 0`` is True, so an infinite canvas used to set
        ``width="infmm"`` on the root — which is where a page size comes
        from."""
        root = ET.fromstring(
            primitives_to_svg({"width": math.inf, "height": 9, "shapes": []})
        )
        assert root.get("viewBox") is None
        assert root.get("width") is None

    def test_an_unknown_type_still_reports_the_unknown_type(self):
        """Diagnostic priority: told about one thing, an author should be told
        the thing they can act on. A shape whose ``type`` is not a shape is not
        usefully described as having a bad coordinate."""
        _, warn = self._build({"type": "blob", "x": math.inf})
        codes = {w.code for w in warn}
        assert codes == {"GRAPHICS_UNKNOWN_SHAPE"}

    def test_finite_shapes_are_untouched(self):
        root, warn = self._build({"type": "circle", "cx": 5, "cy": 5, "r": 2})
        assert [c.tag for c in root] == ["circle"]
        assert not list(warn)


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
