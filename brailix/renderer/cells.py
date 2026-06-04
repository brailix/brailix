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
    controls whether dots-empty ``space`` cells are kept (default: yes)
    — strip them only if you're feeding a tool that already inserts
    word breaks itself.
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
