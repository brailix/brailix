"""Tests for the parametric figure generators."""

from __future__ import annotations

from brailix.frontend.graphics.generate import (
    _fmt,
    _ticks,
    generator_kinds,
    get_generator,
)


def _types(prim: dict) -> list[str]:
    return [s["type"] for s in prim["shapes"]]


def _gen(kind: str, spec: dict) -> dict:
    return get_generator(kind)(spec)


class TestRegistry:
    def test_kinds_registered(self):
        assert set(generator_kinds()) == {
            "bar", "line", "number_line", "axes", "table"
        }

    def test_unknown_kind(self):
        assert get_generator("pie") is None


class TestHelpers:
    def test_ticks_inclusive(self):
        assert _ticks(0, 10, 2) == [0, 2, 4, 6, 8, 10]

    def test_ticks_degenerate(self):
        assert _ticks(0, 10, 0) == []
        assert _ticks(5, 5, 1) == []

    def test_fmt_integers(self):
        assert _fmt(3.0) == "3"
        assert _fmt(2.5) == "2.5"


class TestBar:
    SPEC = {"kind": "bar", "data": [
        {"label": "A", "value": 3}, {"label": "B", "value": 7}]}

    def test_axes_bars_and_labels(self):
        t = _types(_gen("bar", self.SPEC))
        assert t.count("line") == 2  # x + y axes
        assert t.count("rect") == 2  # one per datum
        assert t.count("label") == 2  # one category label per datum

    def test_title_prepended(self):
        prim = _gen("bar", {**self.SPEC, "title": "Sales"})
        assert prim["shapes"][0]["type"] == "label"
        assert prim["shapes"][0]["text"] == "Sales"

    def test_tallest_bar_uses_full_height(self):
        prim = _gen("bar", self.SPEC)
        rects = [s for s in prim["shapes"] if s["type"] == "rect"]
        # The larger value (7) yields a taller bar than the smaller (3).
        assert rects[1]["height"] > rects[0]["height"]

    def test_empty_data_just_axes(self):
        t = _types(_gen("bar", {"kind": "bar", "data": []}))
        assert t == ["line", "line"]


class TestLine:
    def test_polyline_and_points(self):
        t = _types(_gen("line", {"kind": "line", "values": [1, 4, 2, 6, 3]}))
        assert t.count("line") == 2  # axes
        assert t.count("polyline") == 1
        assert t.count("circle") == 5  # marked data points

    def test_explicit_points(self):
        prim = _gen("line", {"kind": "line", "points": [[0, 0], [10, 10]]})
        assert any(s["type"] == "polyline" for s in prim["shapes"])


class TestNumberLine:
    def test_ticks_arrows_and_points(self):
        prim = _gen(
            "number_line",
            {"kind": "number_line", "min": 0, "max": 10, "step": 2, "points": [3, 7]},
        )
        t = _types(prim)
        # 1 main line + 4 arrowhead segments + 6 tick marks = 11 lines.
        assert t.count("line") == 11
        assert t.count("label") == 6  # one per tick
        assert t.count("circle") == 2  # marked points

    def test_marked_points_are_filled(self):
        prim = _gen("number_line", {"kind": "number_line", "points": [5]})
        dot = next(s for s in prim["shapes"] if s["type"] == "circle")
        assert dot.get("fill") == "dots"


class TestAxes:
    def test_axes_and_ticks(self):
        prim = _gen(
            "axes",
            {"kind": "axes", "xmin": -3, "xmax": 3, "ymin": -2, "ymax": 2},
        )
        t = _types(prim)
        # 2 axes + (6 x-ticks + 4 y-ticks, excluding origin) = 12 lines.
        assert t.count("line") == 12
        assert t.count("label") == 10

    def test_grid_adds_lines(self):
        plain = _gen("axes", {"kind": "axes", "xmin": -3, "xmax": 3, "ymin": -2, "ymax": 2})
        grid = _gen(
            "axes",
            {"kind": "axes", "xmin": -3, "xmax": 3, "ymin": -2, "ymax": 2, "grid": True},
        )
        assert _types(grid).count("line") > _types(plain).count("line")


class TestTable:
    def test_grid_and_cells(self):
        prim = _gen(
            "table",
            {"kind": "table", "rows": [["x", "y"], ["1", "2"], ["3", "4"]]},
        )
        t = _types(prim)
        # (cols+1) verticals + (rows+1) horizontals = 3 + 4 = 7 lines.
        assert t.count("line") == 7
        assert t.count("label") == 6  # 3 rows x 2 cols

    def test_empty_rows(self):
        assert _gen("table", {"kind": "table", "rows": []})["shapes"] == []
