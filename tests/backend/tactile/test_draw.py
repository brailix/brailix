"""Tests for the zero-dependency raster drawing primitives."""

from __future__ import annotations

import time

from brailix.backend.tactile._draw import (
    draw_circle,
    draw_ellipse,
    draw_line,
    draw_polyline,
    stamp_disk,
)
from brailix.ir.tactile import TactileRaster


def _blank(w: int, h: int) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
    )


def _ascii(raster: TactileRaster) -> str:
    """Human-reviewable dump: raised cells as ``#``, flat as ``.``."""
    rows = []
    for y in range(raster.height):
        rows.append(
            "".join("#" if raster.get(x, y) else "." for x in range(raster.width))
        )
    return "\n".join(rows)


class TestStampDisk:
    def test_radius_zero_is_single_pixel(self):
        r = _blank(5, 5)
        stamp_disk(r, 2, 2, 0, 255)
        assert r.raised_count() == 1
        assert r.get(2, 2) == 255

    def test_radius_one_is_plus_shape(self):
        r = _blank(7, 7)
        stamp_disk(r, 3, 3, 1, 255)
        # dx^2 + dy^2 <= 1 → centre + 4 orthogonal neighbours.
        assert r.raised_count() == 5
        assert r.get(3, 3) and r.get(2, 3) and r.get(4, 3)
        assert r.get(3, 2) and r.get(3, 4)
        assert not r.get(2, 2)  # corner excluded

    def test_clips_at_edges(self):
        r = _blank(3, 3)
        stamp_disk(r, 0, 0, 2, 255)
        # No crash, and only in-bounds pixels are raised.
        assert r.get(0, 0) == 255


class TestDrawLine:
    def test_horizontal_is_continuous(self):
        r = _blank(10, 10)
        draw_line(r, 0, 5, 9, 5, 0, 255)
        assert r.raised_count() == 10
        assert all(r.get(x, 5) for x in range(10))

    def test_vertical_is_continuous(self):
        r = _blank(10, 10)
        draw_line(r, 4, 0, 4, 9, 0, 255)
        assert all(r.get(4, y) for y in range(10))

    def test_diagonal_is_gap_free(self):
        r = _blank(10, 10)
        draw_line(r, 0, 0, 9, 9, 0, 255)
        assert all(r.get(i, i) for i in range(10))

    def test_thickness_widens_line(self):
        thin = _blank(10, 10)
        draw_line(thin, 0, 5, 9, 5, 0, 255)
        thick = _blank(10, 10)
        draw_line(thick, 0, 5, 9, 5, 1, 255)
        assert thick.raised_count() > thin.raised_count()


class TestPolyline:
    def test_open_vs_closed(self):
        pts = [(1, 1), (8, 1), (8, 8)]
        opn = _blank(10, 10)
        draw_polyline(opn, pts, 0, 255, closed=False)
        closed = _blank(10, 10)
        draw_polyline(closed, pts, 0, 255, closed=True)
        # The closing segment back to (1, 1) raises extra cells.
        assert closed.raised_count() > opn.raised_count()
        # Closing edge present in closed, absent in open: (4, 4) lies on the
        # hypotenuse from (8, 8) back to (1, 1).
        assert closed.get(4, 4)
        assert not opn.get(4, 4)

    def test_single_point_stamps_dot(self):
        r = _blank(5, 5)
        draw_polyline(r, [(2, 2)], 0, 255, closed=False)
        assert r.raised_count() == 1


class TestCircleEllipse:
    def test_circle_is_a_ring(self):
        r = _blank(9, 9)
        draw_circle(r, 4, 4, 3, 0, 255)
        # Centre hollow, the four cardinal perimeter points raised.
        assert not r.get(4, 4)
        assert r.get(7, 4) and r.get(1, 4)
        assert r.get(4, 7) and r.get(4, 1)

    def test_ellipse_respects_axes(self):
        r = _blank(21, 11)
        draw_ellipse(r, 10, 5, 9, 4, 0, 255)
        assert r.get(19, 5) and r.get(1, 5)  # x-extent
        assert r.get(10, 9) and r.get(10, 1)  # y-extent
        assert not r.get(10, 5)  # hollow centre

    def test_degenerate_circle_is_a_dot(self):
        r = _blank(5, 5)
        draw_circle(r, 2, 2, 0, 0, 255)
        assert r.raised_count() == 1


class TestBoundedOnOutOfRangeGeometry:
    """A stroke with a huge device coordinate / radius must clip to the page
    up front instead of walking millions of off-page points (regression:
    unbounded stroke rasterization froze compilation)."""

    def test_huge_coordinate_line_is_bounded_and_correct(self):
        r = _blank(10, 10)
        t0 = time.perf_counter()
        draw_line(r, 0, 5, 10**6, 5, 0, 255)
        elapsed = time.perf_counter() - t0
        # Whole of row 5 is raised, nothing else — same as clipping to the
        # last on-page column.
        ref = _blank(10, 10)
        draw_line(ref, 0, 5, 9, 5, 0, 255)
        assert r.data == ref.data
        assert elapsed < 1.0  # unbounded version took >5s

    def test_huge_diagonal_line_only_lights_main_diagonal(self):
        r = _blank(10, 10)
        t0 = time.perf_counter()
        draw_line(r, -(10**6), -(10**6), 10**6, 10**6, 0, 255)
        elapsed = time.perf_counter() - t0
        assert all(r.get(i, i) for i in range(10))
        assert elapsed < 1.0

    def test_partial_out_of_bounds_line_matches_clipped_equivalent(self):
        # Liang-Barsky clip is exact for the on-page part.
        a = _blank(10, 10)
        draw_line(a, -5, 5, 15, 5, 0, 255)
        b = _blank(10, 10)
        draw_line(b, 0, 5, 9, 5, 0, 255)
        assert a.data == b.data

    def test_huge_radius_stamp_is_bounded(self):
        r = _blank(7, 7)
        t0 = time.perf_counter()
        stamp_disk(r, -1, -1, 10**5, 255)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0
        assert 0 < r.raised_count() <= 7 * 7

    def test_huge_circle_is_bounded(self):
        r = _blank(50, 50)
        t0 = time.perf_counter()
        draw_circle(r, 25, 25, 10**5, 1, 255)
        elapsed = time.perf_counter() - t0
        # Outline of radius 1e5 is entirely off a 50px page → nothing raised,
        # and no perimeter walk proportional to the radius.
        assert elapsed < 1.0
        assert r.raised_count() == 0

    def test_circle_straddling_page_edge_is_gap_free(self):
        # Centre far below the page; only the top arc crosses it. The windowed
        # sampler must still stamp a continuous (gap-free) arc.
        r = _blank(120, 120)
        draw_circle(r, 60, 60 + 400, 400, 1, 255)
        raised = {
            (i % r.width, i // r.width)
            for i, v in enumerate(r.data)
            if v
        }
        assert raised  # the top arc is on the page
        isolated = [
            (x, y)
            for (x, y) in raised
            if not any(
                (x + dx, y + dy) in raised
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx, dy) != (0, 0)
            )
        ]
        assert isolated == []
