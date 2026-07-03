"""Tests for the tactile texture-fill primitives."""

from __future__ import annotations

from brailix.backend.tactile import _State
from brailix.backend.tactile._fill import (
    TEXTURES,
    _hit,
    _point_in_polygon,
    fill_ellipse,
    fill_polygon,
    fill_rect,
    normalize_texture,
)
from brailix.ir.tactile import TactileRaster


def _raster(w: int = 12, h: int = 12) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
    )


class TestNormalizeTexture:
    def test_aliases(self):
        assert normalize_texture("hatch") == "hatch_forward"
        assert normalize_texture("dots") == "stipple"
        assert normalize_texture("cross") == "cross_hatch"
        assert normalize_texture("lines") == "hatch_horizontal"

    def test_direct_texture_name(self):
        assert normalize_texture("hatch_back") == "hatch_back"
        assert normalize_texture("STIPPLE") == "stipple"

    def test_arbitrary_value_unmapped(self):
        assert normalize_texture("red") is None
        assert normalize_texture("#00ff00") is None


class TestHit:
    def test_horizontal_lines(self):
        # y % spacing < thickness → rows 0 and 4 with spacing 4, thickness 1.
        assert _hit("hatch_horizontal", 5, 0, 4, 1)
        assert not _hit("hatch_horizontal", 5, 1, 4, 1)
        assert _hit("hatch_horizontal", 5, 4, 4, 1)

    def test_vertical_lines(self):
        assert _hit("hatch_vertical", 0, 5, 4, 1)
        assert not _hit("hatch_vertical", 1, 5, 4, 1)

    def test_stipple_is_grid(self):
        assert _hit("stipple", 0, 0, 4, 1)
        assert not _hit("stipple", 1, 0, 4, 1)
        assert not _hit("stipple", 0, 1, 4, 1)

    def test_unknown_texture_is_blank(self):
        assert not _hit("nope", 0, 0, 4, 1)

    def test_zero_spacing_safe(self):
        assert not _hit("hatch_forward", 1, 1, 0, 1)


class TestPointInPolygon:
    SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_inside(self):
        assert _point_in_polygon(5, 5, self.SQUARE)

    def test_outside(self):
        assert not _point_in_polygon(15, 5, self.SQUARE)
        assert not _point_in_polygon(-1, 5, self.SQUARE)


class TestFillRect:
    def test_horizontal_hatch(self):
        r = _raster(10, 10)
        fill_rect(r, 0, 0, 9, 9, "hatch_horizontal", 4, 1, 255)
        # rows 0, 4, 8 fully raised → 30 pixels.
        assert r.raised_count() == 30
        assert r.get(5, 0) and r.get(5, 4) and r.get(5, 8)
        assert not r.get(5, 1)

    def test_clipped_to_bounds(self):
        r = _raster(10, 10)
        fill_rect(r, -5, -5, 100, 100, "hatch_horizontal", 4, 1, 255)
        # No crash; rows 0/4/8 across the whole 10-wide raster.
        assert r.raised_count() == 30


class TestFillEllipse:
    def test_pattern_clipped_to_interior(self):
        r = _raster(21, 21)
        fill_ellipse(r, 10, 10, 8, 8, "hatch_horizontal", 4, 1, 255)
        assert r.get(10, 8)  # inside, on a hatch row
        assert not r.get(10, 9)  # inside, off the hatch row
        assert not r.get(10, 0)  # outside the ellipse

    def test_degenerate_noop(self):
        r = _raster()
        fill_ellipse(r, 5, 5, 0, 5, "stipple", 4, 1, 255)
        assert r.raised_count() == 0


class TestFillPolygon:
    def test_triangle_interior(self):
        r = _raster(12, 12)
        tri = [(0, 0), (11, 0), (0, 11)]
        fill_polygon(r, tri, "hatch_horizontal", 2, 1, 255)
        assert r.raised_count() > 0
        assert not r.get(10, 10)  # outside the hypotenuse

    def test_too_few_points_noop(self):
        r = _raster()
        fill_polygon(r, [(0, 0), (5, 5)], "stipple", 4, 1, 255)
        assert r.raised_count() == 0


class TestTextureMapping:
    def _state(self) -> _State:
        return _State(
            minx=0.0, miny=0.0, sx=1.0, sy=1.0, min_radius=1, scale=1.0,
            level=255, warnings=None, warned=set(), labeler=None,
            tex_spacing=10, tex_thickness=1, fill_map={},
        )

    def test_alias_resolves_directly(self):
        assert self._state().texture_for("hatch") == "hatch_forward"

    def test_distinct_fills_get_distinct_textures(self):
        st = self._state()
        assert st.texture_for("red") == TEXTURES[0]
        assert st.texture_for("green") == TEXTURES[1]
        assert st.texture_for("red") == TEXTURES[0]  # stable

    def test_named_texture_not_consuming_map(self):
        st = self._state()
        st.texture_for("stipple")  # direct name, must not consume a slot
        assert st.texture_for("red") == TEXTURES[0]


def test_fill_polygons_even_odd_hole():
    from brailix.backend.tactile._fill import fill_polygons
    from brailix.ir.tactile import TactileRaster

    r = TactileRaster.blank(40, 40, dpi=100.0, page_width_mm=10.0, page_height_mm=10.0)
    outer = [(5, 5), (35, 5), (35, 35), (5, 35)]
    inner = [(15, 15), (25, 15), (25, 25), (15, 25)]
    fill_polygons(r, [outer, inner], "hatch_horizontal", 2, 1, 255)
    assert r.get(20, 20) == 0  # inside both rings -> even -> hole
    assert r.get(10, 20) > 0   # inside outer only -> odd -> filled band
