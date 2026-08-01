"""Tests for the parametric figure generators."""

from __future__ import annotations

import math

import pytest

from brailix.frontend.graphics.generate import (
    _MAX_TICKS,
    FigureSpecError,
    _fmt,
    _num,
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


class TestTicksStayInRange:
    """The stated upper bound is the real one.

    A divisible range hides this entirely, and a divisible range is all the
    happy-path test above ever asked for: with ``round()``, ``_ticks(0, 10, 2)``
    and ``_ticks(0, 11, 3)`` are the same code path and only the second one is
    wrong.
    """

    def test_a_range_the_step_does_not_divide(self):
        # round(11/3) == 4, which put a tick at 12 — mapped past the end of the
        # plot area and labelled, on an axis whose author wrote 11.
        assert _ticks(0, 11, 3) == [0, 3, 6, 9]

    @pytest.mark.parametrize(
        ("lo", "hi", "step"),
        [
            (0, 11, 3),
            (0, 10, 3),
            (0, 1, 0.3),
            (-5, 5, 3),
            (-7.5, 2.5, 1.5),
            (0.1, 0.9, 0.25),
            (1e6, 1e6 + 7, 2),
        ],
    )
    def test_no_tick_ever_exceeds_the_upper_bound(self, lo, hi, step):
        ticks = _ticks(lo, hi, step)
        assert ticks, "a representable range should produce at least one tick"
        assert max(ticks) <= hi
        assert min(ticks) >= lo

    def test_a_representable_endpoint_is_still_included(self):
        """The other half — flooring must not start dropping the last tick.

        ``(0.3 - 0) / 0.1`` is 2.9999999999999996 in binary floating point, so
        a bare ``floor`` would stop at 0.2 and an axis labelled "0 to 0.3"
        would end at 0.2.
        """
        ticks = _ticks(0, 0.3, 0.1)
        assert len(ticks) == 4
        assert ticks[-1] == pytest.approx(0.3)

    def test_the_divisible_case_is_unchanged(self):
        assert _ticks(-5, 5, 1) == [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]


class TestTicksRefuseWhatCannotBeDrawn:
    """Non-finite and unbounded requests, which used to leave through the
    adapter as an ``OverflowError`` or as a multi-hundred-megabyte list."""

    @pytest.mark.parametrize(
        ("lo", "hi", "step"),
        [
            (0.0, math.inf, 1.0),
            (0.0, math.nan, 1.0),
            (-math.inf, 0.0, 1.0),
            (0.0, 10.0, math.nan),
            (0.0, 10.0, math.inf),
        ],
    )
    def test_a_non_finite_bound_is_an_axis_with_no_ticks(self, lo, hi, step):
        assert _ticks(lo, hi, step) == []

    def test_finite_endpoints_whose_quotient_overflows(self):
        """Both operands are ordinary floats; only the division is not.
        ``int(round(inf))`` raised ``OverflowError`` from inside a drawing
        routine, out through an adapter documented to soft-fail."""
        with pytest.raises(FigureSpecError):
            _ticks(0.0, 1e308, 1e-308)

    def test_finite_endpoints_whose_span_overflows(self):
        with pytest.raises(FigureSpecError):
            _ticks(-1e308, 1e308, 1.0)

    def test_a_step_too_small_for_the_range_is_refused(self):
        """Refused, not truncated: drawing the first ten thousand of a hundred
        million ticks yields an axis labelled 0 to 0.0001 for a figure whose
        author wrote 0 to 1, and a wrong chart reads like a right one."""
        with pytest.raises(FigureSpecError) as excinfo:
            _ticks(0.0, 1.0, 1e-8)
        assert "tick" in str(excinfo.value)

    def test_the_budget_boundary(self):
        """Exactly at the limit is allowed; one past it is not — so the bound
        is a decision, not an accident of where the arithmetic lands."""
        assert len(_ticks(0.0, float(_MAX_TICKS - 1), 1.0)) == _MAX_TICKS
        with pytest.raises(FigureSpecError):
            _ticks(0.0, float(_MAX_TICKS), 1.0)

    def test_a_plausible_form_entry_is_refused(self):
        """Not only a hostile-input bound. A form offering a range and a step
        as ordinary numeric fields — say -9999 to 9999 by 0.01 — is two million
        ticks from two entries that look reasonable apart, and it used to run
        for minutes before returning anything."""
        with pytest.raises(FigureSpecError):
            _ticks(-9999.0, 9999.0, 0.01)


class TestNum:
    """``_num`` is the gate every spec field passes through."""

    def test_reads_numbers_and_numeric_strings(self):
        assert _num(3) == 3.0
        assert _num("2.5") == 2.5

    def test_falls_back_for_unreadable_values(self):
        assert _num(None, 7.0) == 7.0
        assert _num("abc", 7.0) == 7.0
        assert _num({}, 7.0) == 7.0

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_non_finite_is_unreadable_not_a_number(self, value):
        """It used to convert cleanly and propagate: one infinite value in a
        line chart's data made every mapped coordinate ``NaN``, and the figure
        rasterised, warned about nothing, and showed nothing."""
        assert _num(value, 7.0) == 7.0

    def test_bool_is_not_a_measurement(self):
        assert _num(True, 7.0) == 7.0


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
