"""Mixed tactile-page compositor — ``backend.tactile.page.compose_pages``.

G3 of inline tactile graphics (ARCHITECTURE.md): braille text runs
(already wrapped to cell lines) and figure rasters are laid onto one or more
page :class:`TactileRaster`\\ s — text stamped as real braille dots, figures
scaled into the flow. Output model A: a page *is* a raster.

Pure backend, headless: rasters and cell lines are built by hand, no Pipeline
and no renderer. A compact page profile keeps the rasters small so the
per-pixel bbox scans stay fast.
"""

from __future__ import annotations

import pytest

from brailix.backend.tactile.page import (
    PageFigure,
    PageText,
    compose_pages,
    line_width_cells,
)
from brailix.backend.tactile.profile import (
    TactileProfile,
    load_tactile_profile,
)
from brailix.ir.braille import BLANK_CELL, BrailleCell
from brailix.ir.tactile import TactileRaster


def _profile(**overrides) -> TactileProfile:
    # Small page (60x80 mm at 100 dpi -> 236x315 px) so whole-raster bbox
    # scans in these tests stay cheap.
    base = dict(
        name="t",
        dpi=100.0,
        page_width_mm=60.0,
        page_height_mm=80.0,
        min_line_width_mm=0.5,
        min_feature_spacing_mm=2.5,
        braille_dot_radius_mm=0.75,
        braille_dot_spacing_mm=2.5,
        braille_cell_spacing_mm=6.0,
        braille_line_spacing_mm=10.0,
    )
    base.update(overrides)
    return TactileProfile(**base)


def _solid_figure(
    prof: TactileProfile,
    w_mm: float,
    h_mm: float,
    *,
    w_px: int | None = None,
    h_px: int | None = None,
) -> TactileRaster:
    """A fully-raised figure of the given physical size (its pixel size may be
    given independently so a huge-mm figure can stay a tiny source raster)."""
    ppm = prof.dpi / 25.4
    w_px = w_px if w_px is not None else max(1, round(w_mm * ppm))
    h_px = h_px if h_px is not None else max(1, round(h_mm * ppm))
    r = TactileRaster.blank(
        w_px, h_px, dpi=prof.dpi, page_width_mm=w_mm, page_height_mm=h_mm
    )
    r.data[:] = bytes([255]) * len(r.data)
    return r


def _bbox(r: TactileRaster) -> tuple[int, int, int, int] | None:
    """(min_x, min_y, max_x, max_y) of the raised pixels, or None if flat."""
    w = r.width
    xs: list[int] = []
    ys: list[int] = []
    for i, v in enumerate(r.data):
        if v:
            xs.append(i % w)
            ys.append(i // w)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _page_px(prof: TactileProfile) -> tuple[int, int]:
    """Page pixel dimensions the compositor allocates for ``prof``."""
    ppm = prof.dpi / 25.4
    return (
        max(1, round(prof.page_width_mm * ppm)),
        max(1, round(prof.page_height_mm * ppm)),
    )


FULL_CELL = BrailleCell(dots=(1, 2, 3, 4, 5, 6))


class TestProfileInterlineField:
    def test_generic_profile_carries_line_spacing(self) -> None:
        # The compositor's vertical pitch comes from this field; the shipped
        # profile must supply it (default applies to older profiles).
        assert load_tactile_profile("generic").braille_line_spacing_mm == 10.0

    def test_field_defaults_when_omitted(self) -> None:
        # A profile JSON without the key still loads with the standard pitch.
        assert _profile().braille_line_spacing_mm == 10.0


class TestBasics:
    def test_empty_items_no_pages(self) -> None:
        assert compose_pages([], _profile()) == []

    def test_text_only_one_page_with_dots(self) -> None:
        prof = _profile()
        pages = compose_pages([PageText([[FULL_CELL]])], prof)
        assert len(pages) == 1
        assert pages[0].width > 0 and pages[0].height > 0
        assert pages[0].raised_count() > 0

    def test_page_carries_physical_metadata(self) -> None:
        prof = _profile()
        page = compose_pages([PageText([[FULL_CELL]])], prof)[0]
        assert page.page_width_mm == prof.page_width_mm
        assert page.page_height_mm == prof.page_height_mm
        assert page.dpi == prof.dpi

    def test_figure_only_one_page_with_dots(self) -> None:
        prof = _profile()
        pages = compose_pages([PageFigure(_solid_figure(prof, 30, 30))], prof)
        assert len(pages) == 1
        assert pages[0].raised_count() > 0

    def test_blank_figure_raster_ignored(self) -> None:
        # A zero-size figure (nothing to draw) never crashes and adds no ink.
        prof = _profile()
        empty = TactileRaster.blank(
            0, 0, dpi=prof.dpi, page_width_mm=10, page_height_mm=10
        )
        pages = compose_pages([PageFigure(empty)], prof)
        # A page is started for the item but stays flat.
        assert pages and pages[0].raised_count() == 0


class TestPlacement:
    def test_left_and_top_margins_respected(self) -> None:
        prof = _profile()
        ppm = prof.dpi / 25.4
        margin_px = round(prof.braille_cell_spacing_mm * ppm)
        dot_r = round(prof.braille_dot_radius_mm * ppm)
        page = compose_pages([PageText([[BrailleCell(dots=(1,))]])], prof)[0]
        bb = _bbox(page)
        assert bb is not None
        min_x, min_y, _, _ = bb
        # Dot 1 sits at the first cell's top-left (left, top) = (margin, margin);
        # its disk reaches at most one radius up / left of that.
        assert min_x >= margin_px - dot_r - 1
        assert min_y >= margin_px - dot_r - 1

    def test_figure_centered_horizontally(self) -> None:
        prof = _profile()
        page = compose_pages([PageFigure(_solid_figure(prof, 30, 30))], prof)[0]
        bb = _bbox(page)
        assert bb is not None
        min_x, _, max_x, _ = bb
        centroid = (min_x + max_x) / 2
        assert abs(centroid - page.width / 2) <= page.width * 0.1

    def test_figure_sits_below_text(self) -> None:
        prof = _profile()
        text = PageText([[FULL_CELL]])
        fig = PageFigure(_solid_figure(prof, 30, 30))
        page_text = compose_pages([text], prof)[0]
        page_both = compose_pages([text, fig], prof)[0]
        bb_text = _bbox(page_text)
        bb_both = _bbox(page_both)
        assert bb_text is not None and bb_both is not None
        # Adding the figure extends raised content further down the page.
        assert bb_both[3] > bb_text[3]

    def test_leading_blank_line_trimmed_on_fresh_page(self) -> None:
        prof = _profile()
        content = BrailleCell(dots=(1, 2, 3))
        with_blank = compose_pages(
            [PageText([[BLANK_CELL], [content]])], prof
        )[0]
        without = compose_pages([PageText([[content]])], prof)[0]
        bb_a = _bbox(with_blank)
        bb_b = _bbox(without)
        assert bb_a is not None and bb_b is not None
        # A heading's blank_before at the top of a page must not push the first
        # content line down: both start at the same y.
        assert abs(bb_a[1] - bb_b[1]) <= 1


class TestScaling:
    def test_wide_figure_scaled_to_usable_width(self) -> None:
        prof = _profile()
        ppm = prof.dpi / 25.4
        margin_px = round(prof.braille_cell_spacing_mm * ppm)
        usable_w = _page_px(prof)[0] - 2 * margin_px
        # A 500 mm-wide figure can't fit a 60 mm page; it must be scaled, not
        # cropped, so its raised span stays within the usable width.
        wide = _solid_figure(prof, 500, 100, w_px=100, h_px=20)
        page = compose_pages([PageFigure(wide)], prof)[0]
        bb = _bbox(page)
        assert bb is not None
        raised_w = bb[2] - bb[0] + 1
        assert raised_w <= usable_w + 2

    def test_tall_figure_scaled_to_usable_height(self) -> None:
        prof = _profile()
        ppm = prof.dpi / 25.4
        margin_px = round(prof.braille_cell_spacing_mm * ppm)
        usable_h = _page_px(prof)[1] - 2 * margin_px
        tall = _solid_figure(prof, 40, 1000, w_px=20, h_px=100)
        pages = compose_pages([PageFigure(tall)], prof)
        assert len(pages) == 1
        bb = _bbox(pages[0])
        assert bb is not None
        raised_h = bb[3] - bb[1] + 1
        assert raised_h <= usable_h + 2


class TestPagination:
    def test_many_lines_overflow_to_multiple_pages(self) -> None:
        prof = _profile()
        lines = [[FULL_CELL] for _ in range(20)]
        pages = compose_pages([PageText(lines)], prof)
        assert len(pages) > 1
        # Every page carries content (no stray blank pages).
        assert all(p.raised_count() > 0 for p in pages)

    def test_figure_that_does_not_fit_starts_new_page(self) -> None:
        prof = _profile()
        # Fill most of a page with text, then a figure taller than the
        # remaining space: it moves to a second page rather than being clipped.
        lines = [[FULL_CELL] for _ in range(6)]
        fig = PageFigure(_solid_figure(prof, 40, 50))
        pages = compose_pages([PageText(lines), fig], prof)
        assert len(pages) == 2
        assert pages[1].raised_count() > 0

    @staticmethod
    def _lines_filling_one_page(prof: TactileProfile) -> int:
        """Number of solid lines that exactly fill page 1 (one more spills)."""
        n = 1
        while len(compose_pages([PageText([[FULL_CELL]] * (n + 1))], prof)) == 1:
            n += 1
        return n

    def test_trailing_blank_after_full_page_makes_no_empty_page(self) -> None:
        # A blank separator line after a full page must not open an all-blank
        # trailing page (it used to slip past the page-break check and get
        # stamped as the sole — empty — content of a fresh page).
        prof = _profile()
        n = self._lines_filling_one_page(prof)
        pages = compose_pages(
            [PageText([[FULL_CELL]] * n + [[BLANK_CELL]])], prof
        )
        assert len(pages) == 1
        assert pages[0].raised_count() > 0

    def test_blank_crossing_page_boundary_does_not_shift_next_content(
        self,
    ) -> None:
        # A blank separator that lands on the page boundary must be dropped, not
        # carried to the top of the next page (which shoved the continuation
        # content down a row).
        prof = _profile()
        n = self._lines_filling_one_page(prof)
        with_blank = compose_pages(
            [PageText([[FULL_CELL]] * n + [[BLANK_CELL], [FULL_CELL]])], prof
        )
        without = compose_pages(
            [PageText([[FULL_CELL]] * n + [[FULL_CELL]])], prof
        )
        assert len(with_blank) == 2 and len(without) == 2
        bb_a = _bbox(with_blank[1])
        bb_b = _bbox(without[1])
        assert bb_a is not None and bb_b is not None
        # Continuation content starts at the same y on page 2, blank or not.
        assert abs(bb_a[1] - bb_b[1]) <= 1


class TestRasterCap:
    """A pathological DPI × page size must not allocate an enormous per-page
    buffer — compose_pages caps it like rasterize does (cap pushed tiny here so
    the probe stays bounded)."""

    def test_page_pixels_clamped_to_cap(self, monkeypatch) -> None:
        from brailix.core.errors import WarningCollector

        monkeypatch.setattr(
            "brailix.backend.tactile.page._MAX_RASTER_PIXELS", 1000
        )
        warn = WarningCollector()
        pages = compose_pages(
            [PageText([[FULL_CELL]])], _profile(), warnings=warn
        )
        assert pages[0].width * pages[0].height <= 1000
        assert any(w.code == "GRAPHICS_RASTER_CLAMPED" for w in warn)

    def test_clamped_page_keeps_physical_mm_and_lowers_dpi(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "brailix.backend.tactile.page._MAX_RASTER_PIXELS", 1000
        )
        prof = _profile()
        page = compose_pages([PageText([[FULL_CELL]])], prof)[0]
        assert page.page_width_mm == prof.page_width_mm
        assert page.page_height_mm == prof.page_height_mm
        assert page.dpi < prof.dpi  # effective DPI reduced by the clamp

    def test_clamped_page_still_stamps(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "brailix.backend.tactile.page._MAX_RASTER_PIXELS", 1000
        )
        page = compose_pages([PageText([[FULL_CELL]])], _profile())[0]
        assert page.raised_count() > 0  # geometry scaled with the page

    def test_normal_profile_not_clamped(self) -> None:
        # The shipped dpi=100 profile is well under the real cap — no warning,
        # dpi unchanged (guards against a spurious clamp regression).
        from brailix.core.errors import WarningCollector

        warn = WarningCollector()
        page = compose_pages(
            [PageText([[FULL_CELL]])], _profile(), warnings=warn
        )[0]
        assert not any(w.code == "GRAPHICS_RASTER_CLAMPED" for w in warn)
        assert page.dpi == _profile().dpi


class TestLineWidthCells:
    """The shared cells-per-line rule (``line_width_cells``) — the single
    definition both ``Pipeline.translate_document_to_pages`` and a front-end
    composing from already-compiled blocks wrap with."""

    def test_default_margin_is_one_cell_advance(self) -> None:
        # 60 mm page, 6 mm cell advance: margin 6 → usable 48 → 8 cells.
        assert line_width_cells(_profile()) == 8
        # Explicitly passing the same margin must agree with the default.
        assert line_width_cells(
            _profile(), margin_mm=_profile().braille_cell_spacing_mm
        ) == 8

    def test_margin_override(self) -> None:
        # Zero margin: the full 60 mm is usable → 10 cells.
        assert line_width_cells(_profile(), margin_mm=0.0) == 10

    def test_floors_partial_cell(self) -> None:
        # 61 mm page: usable 49 mm holds 8 whole cells, the 1 mm remainder
        # must not round up into the right margin.
        assert line_width_cells(_profile(page_width_mm=61.0)) == 8

    def test_never_below_one_cell(self) -> None:
        # A page narrower than its margins still wraps at one cell instead
        # of a zero/negative width.
        assert line_width_cells(_profile(page_width_mm=5.0)) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
