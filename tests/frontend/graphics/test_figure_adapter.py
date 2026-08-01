"""Tests for the figure-generator source adapter."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

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


class TestUndrawableSpecs:
    """A spec with a known ``kind`` that still describes nothing drawable.

    These are the inputs that used to leave through this adapter as an
    exception rather than as a figure: ``OverflowError`` from ``round(inf)``
    deep inside a tick generator, or — worse, because nothing failed — a
    hundred megabytes of SVG built one tick at a time. The entry point above
    has a backstop that turned the first into a blank page, but a backstop
    reports "adapter failure: OverflowError(...)"; the author needs to know
    which field they wrote wrong.

    JSON carries all of this without complaint: Python's decoder reads the
    ``NaN`` and ``Infinity`` literals by default, so these are ordinary
    documents, not hand-built objects.
    """

    @staticmethod
    def _fail(spec: str) -> tuple[ET.Element, WarningCollector]:
        warn = WarningCollector()
        out = FigureSourceAdapter().to_svg(spec, GraphicsContext(warnings=warn))
        return ET.fromstring(out), warn

    @pytest.mark.parametrize(
        "spec",
        [
            '{"kind": "number_line", "min": 0, "max": Infinity, "step": 1}',
            '{"kind": "number_line", "min": 0, "max": NaN, "step": 1}',
            '{"kind": "number_line", "min": 0, "max": 10, "step": -Infinity}',
            '{"kind": "axes", "xmin": 0, "xmax": Infinity, "grid": true}',
            '{"kind": "line", "values": [1, Infinity, 3]}',
            '{"kind": "bar", "data": [{"label": "A", "value": NaN}]}',
        ],
    )
    def test_a_non_finite_value_anywhere_soft_fails_with_a_warning(self, spec):
        root, warn = self._fail(spec)
        assert root.get("data-bk-error") is not None
        assert any(w.code == "GRAPHICS_INVALID_SPEC" for w in warn)

    def test_the_warning_names_the_field(self):
        """A blind author has no canvas to look at, so "the figure could not be
        drawn" is not a diagnosis. The path is."""
        _, warn = self._fail(
            '{"kind": "line", "points": [[0, 0], [1, Infinity]]}'
        )
        message = next(w.message for w in warn if w.code == "GRAPHICS_INVALID_SPEC")
        assert "points[1][1]" in message

    def test_a_tick_count_over_the_budget_soft_fails(self):
        root, warn = self._fail(
            '{"kind": "number_line", "min": 0, "max": 1, "step": 1e-6}'
        )
        assert root.get("data-bk-error") is not None
        assert any(w.code == "GRAPHICS_INVALID_SPEC" for w in warn)

    def test_an_overflowing_range_no_longer_escapes_the_adapter(self):
        """The regression, stated as what a caller sees: this call used to
        raise ``OverflowError`` at the adapter boundary."""
        out = FigureSourceAdapter().to_svg(
            '{"kind": "number_line", "min": 0, "max": 1e308, "step": 1e-308}'
        )
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_soft_failure_without_a_context_still_returns_svg(self):
        """``ctx`` is optional throughout this adapter; nowhere may its absence
        turn a soft failure back into an exception."""
        out = FigureSourceAdapter().to_svg(
            '{"kind": "number_line", "min": 0, "max": NaN}'
        )
        assert ET.fromstring(out).get("data-bk-error") is not None

    def test_a_healthy_spec_is_not_reported(self):
        """The other half: a guard that rejects everything proves nothing."""
        warn = WarningCollector()
        out = FigureSourceAdapter().to_svg(
            '{"kind": "number_line", "min": 0, "max": 10, "step": 2}',
            GraphicsContext(warnings=warn),
        )
        root = ET.fromstring(out)
        assert root.get("data-bk-error") is None
        assert not list(warn)


class TestRegistry:
    def test_figure_registered_and_conforms(self):
        adapter = graphic_source_registry.get("figure")
        assert isinstance(adapter, GraphicSourceAdapter)
        assert adapter.source == "figure"
