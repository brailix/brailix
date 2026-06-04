"""Braille IR: the cell-level output structure Backend writes and
Renderer consumes.

A :class:`BrailleCell` is the atomic unit — six (or eight) dot
positions plus enough metadata to:

* render to Unicode braille, BRF, or a cells array,
* trace each cell back to its source character span for proofreading,
* let a layout engine make line-break decisions per cell.

A :class:`BrailleSequence` is a flat list of cells (one paragraph
or one inline run). A :class:`BrailleDocument` mirrors
:class:`DocumentIR` at the block level so layout / page rules can
operate on structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brailix.core.span import Span

# ---------------------------------------------------------------------------
# BrailleCell
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class BrailleCell:
    """One braille cell.

    ``dots`` is a tuple of dot positions (1..8) — frozen so cells are
    hashable and safe to share between sequences. ``role`` is a short
    tag describing what the cell represents (``number_sign``,
    ``zh_initial``, ``zh_final``, ``tone``, ``punct``, ``math_op``,
    ...). ``source_span`` and ``source_text`` enable back-tracing for
    proofreading; both may be omitted for cells inserted by the
    Backend (e.g. number sign, capital indicator).
    """

    dots: tuple[int, ...] = ()
    role: str | None = None
    source_span: Span | None = None
    source_text: str | None = None

    def __post_init__(self) -> None:
        for d in self.dots:
            if not (1 <= d <= 8):
                raise ValueError(f"invalid dot {d}; must be 1..8")
        if len(set(self.dots)) != len(self.dots):
            raise ValueError(f"duplicate dots in {self.dots!r}")

    @property
    def is_blank(self) -> bool:
        return len(self.dots) == 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"dots": list(self.dots)}
        if self.role is not None:
            d["role"] = self.role
        if self.source_span is not None:
            d["source_span"] = list(self.source_span.to_tuple())
        if self.source_text is not None:
            d["source_text"] = self.source_text
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BrailleCell:
        span = payload.get("source_span")
        return cls(
            dots=tuple(payload.get("dots", [])),
            role=payload.get("role"),
            source_span=Span(int(span[0]), int(span[1])) if span else None,
            source_text=payload.get("source_text"),
        )


# A sentinel space cell (no dots) — backends and renderers use it
# instead of constructing a fresh BrailleCell each time.
BLANK_CELL = BrailleCell(dots=(), role="space")


# ---------------------------------------------------------------------------
# BrailleSequence (paragraph-level)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BrailleSequence:
    """Ordered list of braille cells representing one inline run or
    one whole paragraph."""

    cells: list[BrailleCell] = field(default_factory=list)

    def extend(self, other: BrailleSequence | list[BrailleCell]) -> None:
        if isinstance(other, BrailleSequence):
            self.cells.extend(other.cells)
        else:
            self.cells.extend(other)

    def append(self, cell: BrailleCell) -> None:
        self.cells.append(cell)

    def __len__(self) -> int:
        return len(self.cells)

    def __iter__(self):
        return iter(self.cells)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "braille_sequence",
            "cells": [c.to_dict() for c in self.cells],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BrailleSequence:
        return cls(cells=[BrailleCell.from_dict(c) for c in payload.get("cells", [])])


# ---------------------------------------------------------------------------
# BrailleBlock + BrailleDocument
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BrailleBlock:
    """A block of braille (paragraph / heading / list_item / ...).

    ``block_type`` mirrors :class:`brailix.ir.document.Block.type`
    so layout rules can be applied per block kind (heading centring,
    list indent, etc.). ``cells`` is the rendered cell sequence.
    """

    block_type: str = "paragraph"
    cells: list[BrailleCell] = field(default_factory=list)
    id: str | None = None
    heading_level: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "braille_block",
            "block_type": self.block_type,
            "cells": [c.to_dict() for c in self.cells],
        }
        if self.id is not None:
            d["id"] = self.id
        if self.heading_level is not None:
            d["heading_level"] = self.heading_level
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BrailleBlock:
        return cls(
            block_type=payload.get("block_type", "paragraph"),
            cells=[BrailleCell.from_dict(c) for c in payload.get("cells", [])],
            id=payload.get("id"),
            heading_level=payload.get("heading_level"),
        )


@dataclass(slots=True)
class BrailleDocument:
    """Root of the braille IR."""

    metadata: dict[str, Any] = field(default_factory=dict)
    blocks: list[BrailleBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "braille_document",
            "metadata": dict(self.metadata),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BrailleDocument:
        return cls(
            metadata=dict(payload.get("metadata", {})),
            blocks=[BrailleBlock.from_dict(b) for b in payload.get("blocks", [])],
        )

    def all_cells(self) -> list[BrailleCell]:
        """Flatten every block's cells into a single list. Layout-naive
        helper for early renderers / debugging."""
        out: list[BrailleCell] = []
        for b in self.blocks:
            out.extend(b.cells)
        return out
