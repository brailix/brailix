"""Tests for :class:`brailix.ir.tactile.TactileRaster`."""

from __future__ import annotations

import pytest

from brailix.ir.tactile import MAX_LEVEL, TactileRaster


def _raster(w: int = 4, h: int = 3) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=10.0, page_height_mm=8.0
    )


class TestConstruction:
    def test_blank_is_all_flat(self):
        r = _raster()
        assert r.width == 4
        assert r.height == 3
        assert len(r.data) == 12
        assert r.raised_count() == 0

    def test_empty_data_autofills_to_size(self):
        r = TactileRaster(
            width=2,
            height=2,
            dpi=100.0,
            page_width_mm=1.0,
            page_height_mm=1.0,
        )
        assert len(r.data) == 4

    def test_data_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            TactileRaster(
                width=2,
                height=2,
                dpi=100.0,
                page_width_mm=1.0,
                page_height_mm=1.0,
                data=bytearray(3),
            )

    def test_negative_dimensions_raise(self):
        with pytest.raises(ValueError):
            TactileRaster(
                width=-1,
                height=2,
                dpi=100.0,
                page_width_mm=1.0,
                page_height_mm=1.0,
            )

    def test_zero_dimensions_construct_but_are_not_renderable(self):
        # 0 is a valid IR value (a blank grid the max(1, round(...)) callers
        # rely on), so construction succeeds; but require_renderable() rejects
        # it, since no image format can encode a zero-area raster.
        for w, h in [(0, 0), (0, 5), (5, 0)]:
            r = TactileRaster.blank(
                w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
            )
            with pytest.raises(ValueError):
                r.require_renderable()

    def test_positive_raster_is_renderable(self):
        assert _raster().require_renderable() is None
        assert TactileRaster.blank(
            1, 1, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
        ).require_renderable() is None

    def test_carries_physical_metadata(self):
        r = _raster()
        assert r.dpi == 100.0
        assert r.page_width_mm == 10.0
        assert r.page_height_mm == 8.0
        assert r.bit_depth == 8


class TestPixelAccess:
    def test_set_and_get(self):
        r = _raster()
        r.set_raise(1, 2, 200)
        assert r.get(1, 2) == 200
        assert r.get(0, 0) == 0

    def test_set_raise_takes_maximum(self):
        r = _raster()
        r.set_raise(1, 1, 200)
        r.set_raise(1, 1, 50)  # lower value must not overwrite
        assert r.get(1, 1) == 200
        r.set_raise(1, 1, 255)
        assert r.get(1, 1) == 255

    def test_level_is_clamped(self):
        r = _raster()
        r.set_raise(0, 0, 9999)
        assert r.get(0, 0) == MAX_LEVEL
        r2 = _raster()
        r2.set_raise(0, 0, -5)
        assert r2.get(0, 0) == 0

    def test_out_of_bounds_write_is_ignored(self):
        r = _raster()
        r.set_raise(99, 99, 255)
        r.set_raise(-1, 0, 255)
        assert r.raised_count() == 0

    def test_out_of_bounds_read_returns_zero(self):
        r = _raster()
        assert r.get(99, 99) == 0
        assert r.get(-1, -1) == 0

    def test_in_bounds(self):
        r = _raster()
        assert r.in_bounds(0, 0)
        assert r.in_bounds(3, 2)
        assert not r.in_bounds(4, 2)
        assert not r.in_bounds(0, 3)

    def test_raised_count_threshold(self):
        r = _raster()
        r.set_raise(0, 0, 10)
        r.set_raise(1, 0, 200)
        assert r.raised_count() == 2
        assert r.raised_count(threshold=100) == 1
