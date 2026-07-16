"""Mixed braille + tactile page composition (the inline-graphics G3 path).

The body of :meth:`Pipeline.translate_document_to_pages`, extracted as a
module function so the ``Pipeline`` method stays a delegation — the same
shape as the module-level :func:`brailix.pipeline.translate_graphic`
(``ARCHITECTURE.md``). Page composition is its own concern:
it consumes the block-level compile primitive (:meth:`Pipeline.
translate_block`) and the tactile backend's compositor, and owns nothing
else — keeping it out of ``pipeline/__init__.py`` stops the orchestrator
module from absorbing layout logic.

Not public API: callers go through
:meth:`Pipeline.translate_document_to_pages`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brailix.core.errors import WarningCollector
from brailix.pipeline._results import TactilePageResult

if TYPE_CHECKING:
    from brailix.ir.document import DocumentIR
    from brailix.pipeline import Pipeline


def compose_document_pages(
    pipeline: Pipeline,
    doc: DocumentIR,
    *,
    tactile_profile: str | Any,
    margin_mm: float | None = None,
    item_gap_mm: float | None = None,
) -> TactilePageResult:
    """Compile ``doc`` block by block and lay the results onto tactile pages.

    See :meth:`Pipeline.translate_document_to_pages` for the full contract —
    this function is its body. Each block rides the same incremental
    pipeline as every other path (:meth:`Pipeline.translate_block`): text
    blocks yield braille cells that the layout renderer wraps to the page's
    cell width, figure blocks yield a :class:`~brailix.ir.tactile.
    TactileRaster`, and the tactile backend's page compositor stamps text
    as real braille dots and blits the figures into the flow.
    """
    from brailix.backend.tactile.page import (
        PageFigure,
        PageItem,
        PageText,
        compose_pages,
        line_width_cells,
    )
    from brailix.backend.tactile.profile import load_tactile_profile
    from brailix.ir.document import GraphicBlock
    from brailix.renderer.layout import LayoutOptions, LayoutRenderer

    tprof = (
        load_tactile_profile(tactile_profile)
        if isinstance(tactile_profile, str)
        else tactile_profile
    )
    # Wrap width = the one shared cells-per-line rule (``line_width_cells``);
    # the compositor stamps at the same cell advance, so the wrap width and
    # the stamp geometry agree.
    layout = LayoutRenderer(
        options=LayoutOptions(
            line_width=line_width_cells(tprof, margin_mm=margin_mm),
            page_height=None,
        )
    )

    warnings = WarningCollector(mode=pipeline.mode)
    items: list[PageItem] = []
    for block in doc.blocks:
        compiled = pipeline.translate_block(block)
        # Aggregate each block's diagnostics without re-running the
        # collector's mode logic (they are already final): append directly.
        warnings.warnings.extend(compiled.warnings)
        if isinstance(block, GraphicBlock):
            if compiled.raster is not None:
                items.append(PageFigure(raster=compiled.raster))
            continue
        for bblock in compiled.braille_blocks:
            lines = layout.lay_out_block(bblock)
            if lines:
                items.append(PageText(lines=lines))

    pages = compose_pages(
        items,
        tprof,
        margin_mm=margin_mm,
        item_gap_mm=item_gap_mm,
        warnings=warnings,
    )
    return TactilePageResult(pages=pages, warnings=warnings)
