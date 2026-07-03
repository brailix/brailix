"""Stamp translated braille cells onto a tactile raster as raised dots.

The tactile vertical's one overlap with the main braille pipeline
(``ARCHITECTURE.md``): a graphic's text labels are
translated to braille cells by the ordinary text→braille backend, then
those cells are stamped onto the raise grid here as physically-sized
braille dots. The translation itself is injected as a callable
(``LabelTranslator``) so the tactile backend never imports the text
frontend — the orchestrator wires a real
:class:`~brailix.Pipeline`-backed translator, mirroring the
``InlineTextTranslator`` dependency-injection seam.

Braille is laid out at **physical** size (millimetres → pixels via the
raster DPI), independent of the drawing's logical scale, because braille
must keep its standard dot/cell spacing to stay readable no matter how the
picture is scaled.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from brailix.backend.tactile._draw import stamp_disk
from brailix.ir.braille import BrailleCell
from brailix.ir.tactile import TactileRaster

# text → braille cells (the text→braille backend, injected; never imported).
LabelTranslator = Callable[[str], list[BrailleCell]]

# 8-dot braille dot number → (column-in-cell, row-in-cell) — the inverse of
# the preview's mapping. Column 0 holds dots 1/2/3/7, column 1 dots 4/5/6/8.
_DOT_POS: dict[int, tuple[int, int]] = {
    1: (0, 0), 2: (0, 1), 3: (0, 2), 7: (0, 3),
    4: (1, 0), 5: (1, 1), 6: (1, 2), 8: (1, 3),
}

# Zero-width structural sentinels the backend may emit; they carry no ink
# and must not advance the cell cursor (they never appear in plain label
# prose, but skipping them keeps a stray one from punching a gap).
_SKIP_ROLES = frozenset({"line_break", "hang_open", "hang_close"})


@dataclass(slots=True)
class LabelStamper:
    """Translate a label and paint its braille cells onto a raster.

    All measurements are in device pixels: ``dot_radius`` is the raised
    dot size, ``dot_dx`` / ``dot_dy`` the within-cell dot spacing, and
    ``cell_dx`` the advance from one cell's origin to the next.
    """

    translate: LabelTranslator
    dot_radius: int
    dot_dx: float
    dot_dy: float
    cell_dx: float
    level: int = 255

    def stamp(
        self, raster: TactileRaster, x_px: int, y_px: int, text: str
    ) -> int:
        """Translate ``text`` and stamp its cells starting at ``(x_px,
        y_px)`` (the top-left of the first cell). Returns the number of
        cells placed."""
        return self.stamp_cells(raster, self.translate(text), x_px, y_px)

    def dot_centers(
        self, x_px: int, y_px: int, text: str
    ) -> list[tuple[int, int]]:
        """Device-pixel centres of every dot this label *would* stamp, without
        painting — so the backend can check whether a label collides with the
        figure (or another label) before placing it (the separability pass)."""
        return self.dot_centers_from_cells(self.translate(text), x_px, y_px)

    def stamp_cells(
        self,
        raster: TactileRaster,
        cells: list[BrailleCell],
        x_px: int,
        y_px: int,
    ) -> int:
        """Stamp already-translated ``cells`` — lets the separability pass
        translate once, then probe (:meth:`dot_centers_from_cells`) and paint
        from the same cells instead of translating twice."""
        col = 0
        for cx, cy, col_count in self._dot_positions(cells, x_px, y_px):
            stamp_disk(raster, cx, cy, self.dot_radius, self.level)
            col = col_count
        return col

    def dot_centers_from_cells(
        self, cells: list[BrailleCell], x_px: int, y_px: int
    ) -> list[tuple[int, int]]:
        """Dot centres for already-translated ``cells`` (see
        :meth:`stamp_cells`)."""
        return [(cx, cy) for cx, cy, _col in self._dot_positions(cells, x_px, y_px)]

    def figure_under_dots(
        self, raster: TactileRaster, centers: list[tuple[int, int]]
    ) -> bool:
        """Whether any raised figure pixel lies under a braille dot's disk.

        A braille dot is a raised disk of ``dot_radius`` px, not a single
        pixel; a figure stroke that crosses the disk but misses its exact
        centre still fuses with the dot and makes the cell unreadable.
        Sampling the whole disk (not just the centre pixel) catches that.
        ``raster.get`` returns 0 out of bounds, so the scan is safe and
        bounded (~πr² per centre)."""
        r = self.dot_radius
        r2 = r * r
        for cx, cy in centers:
            for dy in range(-r, r + 1):
                dy2 = dy * dy
                for dx in range(-r, r + 1):
                    if dx * dx + dy2 <= r2 and raster.get(cx + dx, cy + dy) > 0:
                        return True
        return False

    def _dot_positions(
        self, cells: list[BrailleCell], x_px: int, y_px: int
    ):
        """Yield ``(cx, cy, cells_so_far)`` for each raised dot of ``cells`` —
        the shared layout behind paint + probe (no translation here)."""
        col = 0
        for cell in cells:
            if cell.role in _SKIP_ROLES:
                continue
            ox = x_px + col * self.cell_dx
            col += 1
            for dot in cell.dots:
                pos = _DOT_POS.get(dot)
                if pos is None:
                    continue
                cx = round(ox + pos[0] * self.dot_dx)
                cy = round(y_px + pos[1] * self.dot_dy)
                yield cx, cy, col
