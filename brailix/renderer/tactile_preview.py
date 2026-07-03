"""Render a :class:`~brailix.ir.tactile.TactileRaster` as a Unicode
braille preview (U+2800..U+28FF).

This is the loop-closing renderer for the tactile-graphics vertical: it
downsamples the raise grid into 8-dot braille cells so a blind author can
**read the outline of their own drawing on a refreshable braille display
or through NVDA** — verifying the result without sight
(``ARCHITECTURE.md``). It reuses
:func:`brailix.renderer.unicode_braille.dots_to_char`, the same 8-dot →
code-point encoder the text pipeline uses.

Each braille cell packs a 2-wide × 4-tall block of dots, numbered::

    1 4
    2 5
    3 6
    7 8

The raster is resampled to a dot grid ``width_cells * 2`` dots wide (its
height following the raster's aspect ratio, rounded to a whole number of
4-dot cells). A dot turns on when any raster pixel falling in its region
is raised at or above ``threshold`` (max-pooling — it preserves thin
lines that average-pooling would wash out). The result is a newline-
joined block of braille characters: one line per cell row.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.ir.tactile import TactileRaster
from brailix.renderer.unicode_braille import dots_to_char

# (column-in-cell, row-in-cell) → 8-dot braille dot number.
_DOT_NUMBER: dict[tuple[int, int], int] = {
    (0, 0): 1, (0, 1): 2, (0, 2): 3, (0, 3): 7,
    (1, 0): 4, (1, 1): 5, (1, 2): 6, (1, 3): 8,
}


def _preview_dims(w: int, h: int, width_cells: int) -> tuple[int, int, int, int]:
    """The downsampled dot / cell grid for a ``w``×``h`` raster at
    ``width_cells`` cells wide: ``(dot_cols, dot_rows, cells_x, cells_y)``.

    Shared by :func:`raster_to_braille` (which paints the grid) and
    :func:`provenance_cells` (which maps an element's pixels into the *same*
    grid) so the preview text and a highlight stay pixel-for-pixel aligned."""
    dot_cols = max(2, width_cells * 2)
    # Square dots: scale height by the same dots-per-pixel ratio, then pad up
    # to a whole number of 4-dot cell rows.
    dot_rows = max(4, round(h * dot_cols / w))
    if dot_rows % 4:
        dot_rows += 4 - (dot_rows % 4)
    return dot_cols, dot_rows, dot_cols // 2, dot_rows // 4


def provenance_cells(
    raster: TactileRaster, gid: str | None, *, width_cells: int = 40
) -> set[tuple[int, int]]:
    """Preview ``(cell_row, cell_col)`` positions an SVG element covers.

    Maps the element ``gid``'s recorded pixels (``raster.provenance``, the
    element→pixel trace from ``record_provenance``) into the same braille-cell
    grid :func:`raster_to_braille` produced at ``width_cells``, so the editor
    can highlight "where this element is" in the dot preview (the cross-pane
    highlight, ``ARCHITECTURE.md`` H2/H3). Empty when
    provenance wasn't recorded, the gid is unknown, or it drew nothing.

    ``width_cells`` must match the preview's — the editor renders and
    highlights at the same width."""
    prov = raster.provenance
    w, h = raster.width, raster.height
    if not prov or gid is None or w <= 0 or h <= 0 or width_cells <= 0:
        return set()
    pixels = prov.get(gid)
    if not pixels:
        return set()
    dot_cols, dot_rows, _cells_x, _cells_y = _preview_dims(w, h, width_cells)
    cells: set[tuple[int, int]] = set()
    for i in pixels:
        x, y = i % w, i // w
        cells.add(((y * dot_rows // h) >> 2, (x * dot_cols // w) >> 1))
    return cells


def raster_to_braille(
    raster: TactileRaster, *, width_cells: int = 40, threshold: int = 1
) -> str:
    """Resample a tactile raster into a Unicode braille preview string.

    ``width_cells`` is the target width in braille cells; the height
    follows the raster's aspect ratio. ``threshold`` is the raise level at
    which a sampled region counts as raised. Returns ``""`` for an empty
    raster.
    """
    w, h = raster.width, raster.height
    if w <= 0 or h <= 0 or width_cells <= 0:
        return ""
    dot_cols, dot_rows, cells_x, cells_y = _preview_dims(w, h, width_cells)

    grid: list[list[set[int]]] = [
        [set() for _ in range(cells_x)] for _ in range(cells_y)
    ]
    data = raster.data
    for y in range(h):
        dy = y * dot_rows // h
        cy = dy >> 2  # // 4
        row_in_cell = dy & 3
        base = y * w
        for x in range(w):
            if data[base + x] >= threshold:
                dx = x * dot_cols // w
                grid[cy][dx >> 1].add(_DOT_NUMBER[(dx & 1, row_in_cell)])

    return "\n".join(
        "".join(dots_to_char(tuple(sorted(cell))) for cell in row)
        for row in grid
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TactilePreviewRenderer:
    """Encode a tactile raster as a Unicode braille preview string."""

    name: str = "tactile_preview"
    # Consumes a tactile raster, not a braille IR (see
    # ``brailix.renderer.braille_renderer_names``).
    consumes: str = "tactile_raster"
    width_cells: int = 40
    threshold: int = 1

    def render(self, raster: TactileRaster) -> str:
        return raster_to_braille(
            raster, width_cells=self.width_cells, threshold=self.threshold
        )


def _load() -> TactilePreviewRenderer:
    return TactilePreviewRenderer()
