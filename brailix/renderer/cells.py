"""Render BrailleIR as a JSON-friendly list-of-cells structure.

Where :mod:`brailix.renderer.unicode_braille` outputs a printable
string and :mod:`brailix.renderer.brf` outputs ASCII bytes, the
cells renderer emits the raw cell data — ideal for proofreading
tools, web UIs, and anything that wants to mark up specific cells
(e.g. "highlight the cell at source span 12..14").

Output shape for a :class:`BrailleSequence`::

    [
        {"dots": [1, 2, 4], "role": "zh_initial",
         "source_span": [0, 1], "source_text": "重"},
        ...
    ]

For a :class:`BrailleDocument` the result is a dict mirroring
``BrailleDocument.to_dict()`` but with cells expanded the same way.

Structural sentinel cells pass through verbatim. The backend emits a
few zero-width cells that carry layout meaning rather than ink: a forced
in-block line break (``role="line_break"``, between matrix /
equation-system rows) and the hanging-indent brackets
(``role="hang_open"`` / ``"hang_close"`` around a matrix or equation
system). Unlike the ``unicode`` / ``brf`` renderers — which interpret
those sentinels into a line terminator or nothing — the cells renderer
keeps them as raw entries (``{"dots": [], "role": "line_break"}`` and so
on) so a proofreading tool sees the full structure and decides what to
do by ``role``. They carry no ``source_span``, so span-based highlight /
click-to-correct logic skips them automatically. This is the same raw
cell stream
:meth:`~brailix.pipeline.TranslationResult.proofread_json` exposes under
``braille_ir``; a consumer that wants only inked content cells (no
spaces, no sentinels) sets ``include_blanks=False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brailix.ir.braille import BrailleCell, BrailleDocument, BrailleSequence


@dataclass(slots=True)
class CellsRenderer:
    """Emit each cell as a plain dict, plus block / document metadata.

    Output is intentionally JSON-serialisable so the result can be
    piped straight to a web tool or written to disk. ``include_blanks``
    controls whether dots-empty cells are kept (default: yes). Setting it
    to ``False`` drops *every* dots-empty cell — the ``space`` separators
    and the zero-width structural sentinels alike (``line_break`` /
    ``hang_open`` / ``hang_close``; see the module docstring) — leaving
    only inked content cells. Strip them only if you're feeding a tool
    that inserts its own word breaks and ignores layout structure.
    """

    name: str = "cells"
    include_blanks: bool = True

    def render(self, source: BrailleDocument | BrailleSequence) -> Any:
        if isinstance(source, BrailleSequence):
            return [self._cell(c) for c in source.cells if self._keep(c)]
        return {
            "type": "braille_document",
            "metadata": dict(source.metadata),
            "blocks": [
                {
                    "block_type": b.block_type,
                    "id": b.id,
                    "heading_level": b.heading_level,
                    "cells": [self._cell(c) for c in b.cells if self._keep(c)],
                }
                for b in source.blocks
            ],
        }

    def _keep(self, cell: BrailleCell) -> bool:
        return self.include_blanks or not cell.is_blank

    def _cell(self, cell: BrailleCell) -> dict[str, Any]:
        return cell.to_dict()


def _load() -> CellsRenderer:
    return CellsRenderer()
