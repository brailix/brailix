"""Tests for :mod:`brailix.renderer.tactile_preview`.

The preview's correctness is the dot→cell mapping: a 2×4 raster maps 1:1
onto one 8-dot braille cell, so exact glyphs can be asserted.
"""

from __future__ import annotations

from brailix.ir.tactile import TactileRaster
from brailix.renderer.tactile_preview import (
    TactilePreviewRenderer,
    provenance_cells,
    raster_to_braille,
)

BLANK = chr(0x2800)


def _cell(w: int, h: int) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
    )


class TestExactMapping:
    def test_empty_raster(self):
        assert raster_to_braille(_cell(0, 0)) == ""

    def test_single_dot(self):
        # 2×4 raster, width_cells=1 → one cell, pixels map 1:1 to dots.
        r = _cell(2, 4)
        r.set_raise(0, 0, 255)  # top-left → dot 1
        assert raster_to_braille(r, width_cells=1) == chr(0x2800 | 0x01)

    def test_dot_seven_and_eight(self):
        r = _cell(2, 4)
        r.set_raise(0, 3, 255)  # bottom-left → dot 7
        r.set_raise(1, 3, 255)  # bottom-right → dot 8
        expected = chr(0x2800 | (1 << 6) | (1 << 7))
        assert raster_to_braille(r, width_cells=1) == expected

    def test_full_cell(self):
        r = _cell(2, 4)
        for x in range(2):
            for y in range(4):
                r.set_raise(x, y, 255)
        assert raster_to_braille(r, width_cells=1) == chr(0x28FF)

    def test_blank_cell(self):
        assert raster_to_braille(_cell(2, 4), width_cells=1) == BLANK

    def test_two_cells_wide(self):
        r = _cell(4, 4)
        r.set_raise(0, 0, 255)  # left cell, dot 1
        r.set_raise(2, 0, 255)  # right cell, dot 1
        out = raster_to_braille(r, width_cells=2)
        assert out == chr(0x2801) + chr(0x2801)


class TestThreshold:
    def test_below_threshold_is_off(self):
        r = _cell(2, 4)
        r.set_raise(0, 0, 100)
        assert raster_to_braille(r, width_cells=1, threshold=128) == BLANK

    def test_at_threshold_is_on(self):
        r = _cell(2, 4)
        r.set_raise(0, 0, 128)
        assert raster_to_braille(r, width_cells=1, threshold=128) == chr(0x2801)


class TestRowsAndShape:
    def test_top_band_fills_top_cell_row(self):
        # 8×8 raster, width_cells=4 → 4×2 cells, pixels 1:1 to dots.
        r = _cell(8, 8)
        for x in range(8):
            for y in range(4):  # top half raised
                r.set_raise(x, y, 255)
        lines = raster_to_braille(r, width_cells=4).split("\n")
        assert len(lines) == 2
        assert lines[0] == chr(0x28FF) * 4  # top cell row full
        assert lines[1] == BLANK * 4  # bottom cell row blank

    def test_aspect_ratio_controls_height(self):
        # Wide raster → fewer cell rows than a tall one for the same width.
        wide = _cell(80, 20)
        tall = _cell(20, 80)
        wide_rows = len(raster_to_braille(wide, width_cells=10).split("\n"))
        tall_rows = len(raster_to_braille(tall, width_cells=10).split("\n"))
        assert tall_rows > wide_rows


class TestRenderer:
    def test_renderer_matches_function(self):
        r = _cell(2, 4)
        r.set_raise(1, 1, 255)
        assert TactilePreviewRenderer().render(r) == raster_to_braille(r)

    def test_renderer_name(self):
        assert TactilePreviewRenderer().name == "tactile_preview"


class TestProvenanceCells:
    """Element → preview-cell map for the cross-pane highlight. The cells must
    land in the same grid raster_to_braille paints (1:1 at width_cells=4 over
    an 8×8 raster: dot_cols=8 / cells_x=4, dot_rows=8 / cells_y=2)."""

    def _traced(self) -> TactileRaster:
        r = _cell(8, 8)
        r.enable_provenance()
        r.begin_element("el")
        r.set_raise(0, 0, 255)  # pixel (0,0) → cell (0,0)
        r.set_raise(7, 7, 255)  # pixel (7,7) → cell (1,3)
        r.begin_element(None)
        return r

    def test_maps_pixels_to_cells(self):
        cells = provenance_cells(self._traced(), "el", width_cells=4)
        assert cells == {(0, 0), (1, 3)}

    def test_no_provenance_is_empty(self):
        # Provenance never enabled → nothing recorded.
        assert provenance_cells(_cell(8, 8), "el", width_cells=4) == set()

    def test_unknown_gid_is_empty(self):
        assert provenance_cells(self._traced(), "nope", width_cells=4) == set()

    def test_none_gid_is_empty(self):
        assert provenance_cells(self._traced(), None, width_cells=4) == set()

    def test_cells_lie_within_the_rendered_grid(self):
        # Whatever the aspect, every mapped cell is a valid (row, col) of the
        # text raster_to_braille produced at the same width.
        r = _cell(37, 19)
        r.enable_provenance()
        r.begin_element("e")
        for i in range(0, 37 * 19, 11):
            r.set_raise(i % 37, i // 37, 255)
        r.begin_element(None)
        lines = raster_to_braille(r, width_cells=12).split("\n")
        for row, col in provenance_cells(r, "e", width_cells=12):
            assert 0 <= row < len(lines) and 0 <= col < len(lines[0])
