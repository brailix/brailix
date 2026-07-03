"""Tests for the braille label stamper."""

from __future__ import annotations

from brailix.backend.tactile._labels import LabelStamper
from brailix.ir.braille import BLANK_CELL, LINE_BREAK_CELL, BrailleCell
from brailix.ir.tactile import TactileRaster


def _raster(w: int = 80, h: int = 40) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
    )


def _stamper(cells: list[BrailleCell]) -> LabelStamper:
    # radius 0 → one pixel per dot, so dot counts are exact. Integer
    # spacings keep the placement assertions clean.
    return LabelStamper(
        translate=lambda _text: cells,
        dot_radius=0,
        dot_dx=10.0,
        dot_dy=10.0,
        cell_dx=30.0,
    )


class TestDotPlacement:
    def test_single_dot_one_at_origin(self):
        r = _raster()
        n = _stamper([BrailleCell(dots=(1,))]).stamp(r, 0, 0, "x")
        assert n == 1
        assert r.raised_count() == 1
        assert r.get(0, 0)

    def test_full_cell_all_eight_dots(self):
        r = _raster()
        _stamper([BrailleCell(dots=(1, 2, 3, 4, 5, 6, 7, 8))]).stamp(r, 0, 0, "x")
        assert r.raised_count() == 8
        # Column 0 (dots 1,2,3,7) and column 1 (dots 4,5,6,8).
        assert r.get(0, 0) and r.get(0, 10) and r.get(0, 20) and r.get(0, 30)
        assert r.get(10, 0) and r.get(10, 10) and r.get(10, 20) and r.get(10, 30)

    def test_cell_advance(self):
        r = _raster()
        cells = [BrailleCell(dots=(1,)), BrailleCell(dots=(1,))]
        _stamper(cells).stamp(r, 0, 0, "xy")
        assert r.get(0, 0) and r.get(30, 0)
        assert r.raised_count() == 2

    def test_anchor_offset(self):
        r = _raster()
        _stamper([BrailleCell(dots=(1,))]).stamp(r, 5, 7, "x")
        assert r.get(5, 7)


class TestSpacingAndSkips:
    def test_blank_cell_advances_cursor(self):
        r = _raster()
        cells = [BrailleCell(dots=(1,)), BLANK_CELL, BrailleCell(dots=(1,))]
        _stamper(cells).stamp(r, 0, 0, "x x")
        assert r.get(0, 0)  # cell 0
        assert not r.get(30, 0)  # blank — no ink
        assert r.get(60, 0)  # cell 2 advanced past the blank
        assert r.raised_count() == 2

    def test_structural_sentinel_skipped_without_advance(self):
        r = _raster()
        cells = [LINE_BREAK_CELL, BrailleCell(dots=(1,))]
        n = _stamper(cells).stamp(r, 0, 0, "x")
        # The line-break sentinel is skipped and does NOT advance the cursor,
        # so the real cell still lands at the origin.
        assert n == 1
        assert r.get(0, 0)

    def test_empty_translation(self):
        r = _raster()
        n = _stamper([]).stamp(r, 0, 0, "")
        assert n == 0
        assert r.raised_count() == 0

    def test_translate_receives_text(self):
        seen: list[str] = []

        def translate(text: str) -> list[BrailleCell]:
            seen.append(text)
            return []

        stamper = LabelStamper(
            translate=translate,
            dot_radius=0,
            dot_dx=10.0,
            dot_dy=10.0,
            cell_dx=30.0,
        )
        stamper.stamp(_raster(), 0, 0, "hello")
        assert seen == ["hello"]


class TestFigureUnderDots:
    """The label-over-figure check samples each dot's whole disk, not just its
    centre pixel — a stroke crossing the disk but missing the centre still
    fuses the dot with the figure."""

    def _stamper_r3(self) -> LabelStamper:
        return LabelStamper(
            translate=lambda _t: [],
            dot_radius=3,
            dot_dx=10.0,
            dot_dy=10.0,
            cell_dx=30.0,
        )

    def test_ink_off_centre_but_inside_disk_is_detected(self):
        r = _raster()
        # A raised figure pixel 2 px from the dot centre — inside the radius-3
        # disk but NOT on the centre pixel a naive check would probe.
        r.set_raise(12, 10, 255)
        assert r.get(10, 10) == 0  # centre-only sampling would miss it
        assert self._stamper_r3().figure_under_dots(r, [(10, 10)]) is True

    def test_ink_outside_disk_is_not_detected(self):
        r = _raster()
        r.set_raise(16, 10, 255)  # 6 px from centre, outside the radius-3 disk
        assert self._stamper_r3().figure_under_dots(r, [(10, 10)]) is False

    def test_clear_raster_no_overlap(self):
        assert self._stamper_r3().figure_under_dots(_raster(), [(10, 10)]) is False
