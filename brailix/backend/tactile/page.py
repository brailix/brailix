"""Compose a mixed tactile page: braille text (real dots) + figure rasters.

The mixed-layout typesetter (``ARCHITECTURE.md`` G3). A braille
document that embeds figures (``ARCHITECTURE.md`` G1/G2) compiles
to a stream of two kinds of item — runs of braille **text** (already
translated to cells and wrapped into display lines) and **figure** rasters —
and this module lays that stream onto one or more page
:class:`~brailix.ir.tactile.TactileRaster`\\ s.

Output model **A** (decided 2026-07-01, plan §3): a page *is* a tactile
raster. Braille text is stamped as **real braille dots** at the profile's
physical dot geometry (reusing :class:`~brailix.backend.tactile._labels.LabelStamper`,
the same stamper a graphic's own labels use), and figures are blitted into
the flow scaled to fit the page. The whole page then exports through the
existing tactile renderers (``bmp`` / ``png`` / ``pdf`` / ``tactile_preview``)
— there is no separate mixed-page export path. A mixed page does **not**
round-trip to BRF: a cell stream can't hold a 2-D figure, so the raster is
the authoritative form.

Layering: this stays in the tactile **backend** — it produces a
``TactileRaster`` (a backend product) and reuses ``LabelStamper``, importing
neither the renderer nor the frontend. The caller (``Pipeline``) does the
cell **wrapping** with the layout renderer and hands the finished lines here,
so the renderer→backend dependency the compositor would otherwise need never
forms (ARCHITECTURE §12: ``Backend → BrailleIR → Renderer``, one direction).

Coordinate model matches :mod:`brailix.ir.tactile`: origin top-left, ``y``
downward, physical millimetres mapped to device pixels through the profile's
single ``dpi`` dial.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.backend.tactile._labels import LabelStamper
from brailix.backend.tactile._raster_cap import (
    _MAX_RASTER_PIXELS,
    clamp_raster_pixels,
)
from brailix.backend.tactile.profile import TactileProfile
from brailix.core.errors import WarningCollector
from brailix.ir.braille import BrailleCell
from brailix.ir.tactile import TactileRaster

__all__ = ("PageText", "PageFigure", "PageItem", "compose_pages")

_MM_PER_INCH = 25.4


# ---------------------------------------------------------------------------
# Page items — the ordered stream the compositor flows onto pages
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PageText:
    """A run of braille text: display lines already wrapped to page width.

    ``lines`` is a list of lines, each a list of :class:`BrailleCell` — exactly
    the shape :meth:`brailix.renderer.LayoutRenderer.lay_out_block` returns
    (indent blanks, continuation hyphens, and separators included). The
    compositor stamps each line as physical braille dots and advances one line
    pitch; it does **not** re-wrap, so the caller owns the line width.
    """

    lines: list[list[BrailleCell]]


@dataclass(slots=True)
class PageFigure:
    """A figure: a already-rasterised :class:`TactileRaster` to place inline.

    Its own ``page_width_mm`` / ``page_height_mm`` give the figure's intended
    physical size; the compositor scales it to fit the usable page area,
    preserving aspect ratio, and blits it into the text flow.
    """

    raster: TactileRaster


PageItem = PageText | PageFigure


# ---------------------------------------------------------------------------
# Raster blit
# ---------------------------------------------------------------------------


def _blit_max(
    dst: TactileRaster,
    src: TactileRaster,
    dst_x: int,
    dst_y: int,
    dst_w: int,
    dst_h: int,
) -> None:
    """Copy ``src`` into ``dst``'s ``dst_w``×``dst_h`` box at ``(dst_x, dst_y)``.

    Area **max**-sampling: each destination pixel takes the maximum raise level
    over the source region it covers, so a thin raised line survives
    downscaling instead of being averaged away (the same reasoning
    :func:`brailix.renderer.tactile_preview.raster_to_braille` uses when it
    max-pools). Writes go through :meth:`TactileRaster.set_raise` (also a max),
    so the figure only ever adds height and clips cleanly at the page edge.
    Upscaling degenerates to nearest-neighbour (each source cell covers one
    destination cell).
    """
    sw, sh = src.width, src.height
    if sw <= 0 or sh <= 0 or dst_w <= 0 or dst_h <= 0:
        return
    data = src.data
    for ty in range(dst_h):
        py = dst_y + ty
        if py < 0 or py >= dst.height:
            continue
        sy0 = ty * sh // dst_h
        sy1 = max(sy0 + 1, (ty + 1) * sh // dst_h)
        for tx in range(dst_w):
            px = dst_x + tx
            if px < 0 or px >= dst.width:
                continue
            sx0 = tx * sw // dst_w
            sx1 = max(sx0 + 1, (tx + 1) * sw // dst_w)
            level = 0
            for sy in range(sy0, sy1):
                base = sy * sw
                for sx in range(sx0, sx1):
                    v = data[base + sx]
                    if v > level:
                        level = v
            if level > 0:
                dst.set_raise(px, py, level)


# ---------------------------------------------------------------------------
# Compositor
# ---------------------------------------------------------------------------


def _unused_translate(_text: str) -> list[BrailleCell]:
    """Sentinel ``LabelTranslator`` for the compositor's stamper.

    :meth:`LabelStamper.stamp_cells` never calls ``translate`` (it is handed
    cells, not text), so wiring a real text→braille translator here would be
    dead weight. This sentinel makes the contract explicit: if the compositor
    ever reaches the translate path it is a bug, not a silent empty label."""
    raise RuntimeError(
        "compose_pages stamps already-translated cells; the LabelStamper's "
        "text translator must never be called"
    )


def _line_is_blank(line: list[BrailleCell]) -> bool:
    """True when a display line carries no ink (only indent / separator blanks)
    — a leading run of these is trimmed off the top of a fresh page so a
    heading's ``blank_before`` never opens a page with an empty line."""
    return all(c.is_blank for c in line)


def compose_pages(
    items: list[PageItem],
    profile: TactileProfile,
    *,
    margin_mm: float | None = None,
    item_gap_mm: float | None = None,
    warnings: WarningCollector | None = None,
) -> list[TactileRaster]:
    """Lay a stream of text runs + figures onto paginated tactile pages.

    ``profile`` supplies the page size, DPI, braille dot geometry, and
    interline pitch. Text lines (already wrapped to width by the caller) are
    stamped as physical braille dots; figures are scaled to fit the usable
    width (and, if still too tall, the usable height) and blitted centred into
    the flow. Content that overruns the page starts a new page.

    ``margin_mm`` defaults to one braille cell advance
    (:attr:`TactileProfile.braille_cell_spacing_mm`); ``item_gap_mm`` — the
    vertical space placed **around figures** (text runs flow line-to-line; a
    figure gets one gap before and after) — defaults to one interline pitch.
    Returns one raster per page — empty when ``items`` is empty. Never raises.
    """
    ppm = profile.dpi / _MM_PER_INCH
    page_w = max(1, round(profile.page_width_mm * ppm))
    page_h = max(1, round(profile.page_height_mm * ppm))
    # Cap the per-page buffer like rasterize does — a pathological DPI × page
    # size would otherwise allocate an enormous bytearray per page (× page
    # count). On clamp, reduce the working scale so every layout measurement
    # (margins, pitch, dot geometry, figure fit) shrinks with the page.
    page_w, page_h, _clamped = clamp_raster_pixels(
        page_w, page_h, warnings, max_pixels=_MAX_RASTER_PIXELS
    )
    eff_dpi = profile.dpi
    if _clamped:
        ppm = page_w / profile.page_width_mm
        eff_dpi = ppm * _MM_PER_INCH

    margin = profile.braille_cell_spacing_mm if margin_mm is None else margin_mm
    margin_px = max(0, round(margin * ppm))
    gap = profile.braille_line_spacing_mm if item_gap_mm is None else item_gap_mm
    gap_px = max(0.0, gap * ppm)

    left = margin_px
    top = margin_px
    usable_w = max(1, page_w - 2 * margin_px)
    usable_bottom = page_h - margin_px
    usable_h = max(1, usable_bottom - top)

    line_pitch = max(1.0, profile.braille_line_spacing_mm * ppm)
    # An 8-dot cell spans four dot rows (three gaps); a text line's dots reach
    # at most this far below its top. Used only for the "does the line's ink
    # fit above the bottom margin" test — the cursor still advances by the full
    # line pitch.
    line_ink_h = 3 * profile.braille_dot_spacing_mm * ppm

    labeler = LabelStamper(
        translate=_unused_translate,
        dot_radius=max(0, round(profile.braille_dot_radius_mm * ppm)),
        dot_dx=profile.braille_dot_spacing_mm * ppm,
        dot_dy=profile.braille_dot_spacing_mm * ppm,
        cell_dx=profile.braille_cell_spacing_mm * ppm,
    )

    pages: list[TactileRaster] = []
    cur: TactileRaster | None = None
    y = float(top)
    page_has_content = False

    def start_page() -> None:
        nonlocal cur, y, page_has_content
        cur = TactileRaster.blank(
            page_w,
            page_h,
            dpi=eff_dpi,
            page_width_mm=profile.page_width_mm,
            page_height_mm=profile.page_height_mm,
        )
        pages.append(cur)
        y = float(top)
        page_has_content = False

    def place_text(lines: list[list[BrailleCell]]) -> None:
        nonlocal y, page_has_content
        assert cur is not None
        for line in lines:
            if _line_is_blank(line):
                # A blank separator line (a heading's blank_before / blank_after)
                # must never open a page nor land on a fresh page's first row.
                # Handle it before the pagination check so it can't slip past a
                # page break and get stamped at the top of the next page (which
                # produced an all-blank trailing page, or shoved the next page's
                # content down a row).
                if not page_has_content:
                    continue  # trim at page top (heading blank_before)
                if y + line_pitch > usable_bottom:
                    continue  # would spill past the bottom → drop it; the next
                    #            real line's page break does the separating
                y += line_pitch  # vertical spacing within the current page
                continue
            if page_has_content and y + line_ink_h > usable_bottom:
                start_page()
            labeler.stamp_cells(cur, line, left, round(y))
            y += line_pitch
            page_has_content = True

    def place_figure(src: TactileRaster) -> None:
        nonlocal y, page_has_content
        assert cur is not None
        if src.width <= 0 or src.height <= 0:
            return
        # Physical size of the figure (fall back to its pixel size at the page
        # DPI if it carries no millimetre metadata).
        src_w_mm = src.page_width_mm if src.page_width_mm > 0 else src.width / ppm
        src_h_mm = (
            src.page_height_mm if src.page_height_mm > 0 else src.height / ppm
        )
        tgt_w = max(1, round(src_w_mm * ppm))
        tgt_h = max(1, round(src_h_mm * ppm))
        # Fit width, preserving aspect ratio.
        if tgt_w > usable_w:
            scale = usable_w / tgt_w
            tgt_w = usable_w
            tgt_h = max(1, round(tgt_h * scale))
        # Still taller than a whole page's usable height → fit height too.
        if tgt_h > usable_h:
            scale = usable_h / tgt_h
            tgt_h = usable_h
            tgt_w = max(1, round(tgt_w * scale))
        if page_has_content and y + tgt_h > usable_bottom:
            start_page()
        x0 = left + max(0, (usable_w - tgt_w) // 2)
        _blit_max(cur, src, x0, round(y), tgt_w, tgt_h)
        y += tgt_h
        page_has_content = True

    # The vertical gap is inserted only around figures: consecutive text runs
    # flow line-to-line (paragraphs are already set off by their first-line
    # indent, headings by the blank lines the layout renderer emits), so
    # stacking a gap between every block would double-space prose. A figure,
    # which carries no such framing, gets one gap before and after.
    prev_was_figure = False
    for item in items:
        is_figure = isinstance(item, PageFigure)
        if cur is None:
            start_page()
        elif page_has_content and (is_figure or prev_was_figure):
            y += gap_px
        # Direct isinstance in each branch (not the ``is_figure`` flag) so the
        # type checker narrows the union to the right member's fields.
        if isinstance(item, PageFigure):
            place_figure(item.raster)
        else:
            place_text(item.lines)
        prev_was_figure = is_figure

    return pages
