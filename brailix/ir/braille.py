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

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from brailix.core.span import Span

# ---------------------------------------------------------------------------
# BrailleCell
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class BrailleCell:
    """One braille cell.

    ``dots`` is a tuple of dot positions (1..8), normalised to ascending
    order in :meth:`__post_init__` so a cell's identity is its dot *set*:
    ``(2, 1)`` and ``(1, 2)`` compare equal and hash alike, matching the
    order-free unicode rendering. Frozen so cells are hashable and safe to
    share between sequences. ``role`` is a short
    tag describing what the cell represents (``number_sign``,
    ``zh_initial``, ``zh_final``, ``tone``, ``punct``, ``math_op``,
    ...). ``source_span`` and ``source_text`` enable back-tracing for
    proofreading. The fields stay ``Optional`` for deserialization and
    hand-built cells, but the Backend gives EVERY cell it emits a span —
    including the control / spacing cells it inserts (number sign, blanks,
    matrix row breaks, hanging-indent brackets), built via the span-carrying
    factories below (:func:`blank_cell` / :func:`line_break_cell` /
    :func:`hang_open_cell` / :func:`hang_close_cell`) — so a compiled document
    upholds "every cell maps to a source span" (ARCHITECTURE.md §3).

    **Coordinate contract**: ``source_span`` is **leaf-local** — offsets
    into the *owning leaf block's* ``text``, starting at 0 per block, NOT
    into the whole source document (which several input formats don't
    even have a character-level coordinate for; a ``.docx`` is a zip).
    To locate a cell in the original source, add the block's own
    ``span.start`` — exact whenever the block upholds the exact-slice
    contract ``source[block.span] == block.text`` (see
    :attr:`brailix.ir.document.Block.span` for which blocks do). A
    display-oriented consumer (an editor pane) instead rebases by its own
    per-leaf offsets into whatever text it renders.
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
        # Canonicalise dot order so equality / hashing match the cell's
        # rendering semantics: a cell *is* its dot set (the unicode renderer
        # OR-s the bits, order-free), so (2, 1) and (1, 2) must compare equal
        # and share a hash. frozen=True → write through object.__setattr__.
        ordered = tuple(sorted(self.dots))
        if ordered != self.dots:
            object.__setattr__(self, "dots", ordered)

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
            source_span=Span.from_tuple(span) if span is not None else None,
            source_text=payload.get("source_text"),
        )


# A sentinel space cell (no dots) — backends and renderers use it
# instead of constructing a fresh BrailleCell each time.
BLANK_CELL = BrailleCell(dots=(), role="space")

# A sentinel forced line break WITHIN a block — emitted by backends where
# the braille code mandates a new line mid-expression (each matrix /
# determinant / equation-system row starts on its own line; a bare ``\\``
# in math). Renderers consume it instead of printing a cell: the plain
# unicode / BRF renderers emit their line terminator, the layout renderer
# flushes the current line (no continuation hyphen). Note ``is_blank`` is
# True (no dots) — wrap logic must test ``role`` BEFORE blankness.
LINE_BREAK_CELL = BrailleCell(dots=(), role="line_break")

# Zero-width sentinels bracketing a hanging-indent region — emitted by the
# math backend around a whole matrix / determinant / equation system.
# Inside the region, a line the layout has to break for WIDTH (overflow)
# continues with the region's hanging indent; a FORCED
# break (LINE_BREAK_CELL — the next print row) still starts at the block's
# own indent. Regions nest (block matrices) — the layout keeps a depth
# count. The plain unicode / BRF renderers print nothing for them.
HANG_OPEN_CELL = BrailleCell(dots=(), role="hang_open")
HANG_CLOSE_CELL = BrailleCell(dots=(), role="hang_close")

# Zero-width sentinels bracketing an EQUATION-SYSTEM (``\begin{cases}``)
# region. Like the hang sentinels they mark a hanging-indent region (rows
# on their own lines, width overflow continuing two cells past the row
# start), but they additionally tell the layout to draw the multi-line
# brace segment-by-segment across the PHYSICAL braille lines the system
# spans: the ⠎ (234) top segment on the first line, ⠣ (126) bottom on the
# last, ⠇ (123) on every line between — so a row that wraps carries a
# middle segment on its continuation and the bottom segment always lands
# on the last braille line (not merely the last equation). Immediately
# after CASES_OPEN the backend emits three ``cases_palette`` cells — the
# first / middle / last brace segments — which the layout captures (and
# the plain renderers skip) so it can stamp the right segment on each
# physical line regardless of how many equations the region holds.
CASES_OPEN_CELL = BrailleCell(dots=(), role="cases_open")
CASES_CLOSE_CELL = BrailleCell(dots=(), role="cases_close")


# --- Span-carrying factories for the control / spacing cells ----------------
#
# Every BrailleCell must be back-traceable to a source span (ARCHITECTURE.md:
# "每个 BrailleIR cell 都有 source_span"). The zero-width sentinels above share
# one span-less instance, which breaks that contract for the cell a proofreader
# clicks — a blank between two words, a matrix row break — leaving it with no
# way back to source. These factories build the SAME role of cell (renderers
# key on ``role``, never on object identity) carrying the emitter's span. The
# ``source_span`` argument is mandatory — no default — so an emitter can't
# silently fall back to a span-less cell; pass the triggering node's span, or a
# zero-width span collapsed to the relevant boundary when the cell sits between
# source positions (mirroring :func:`brailix.backend.punct.translate_space`).
def blank_cell(source_span: Span | None) -> BrailleCell:
    """A blank (space) cell carrying ``source_span`` — the traceable
    counterpart of :data:`BLANK_CELL`."""
    return BrailleCell(dots=(), role="space", source_span=source_span, source_text="")


def line_break_cell(source_span: Span | None) -> BrailleCell:
    """A forced-line-break cell carrying ``source_span`` — the traceable
    counterpart of :data:`LINE_BREAK_CELL`."""
    return BrailleCell(
        dots=(), role="line_break", source_span=source_span, source_text=""
    )


def hang_open_cell(source_span: Span | None) -> BrailleCell:
    """A hanging-indent-open cell carrying ``source_span`` — the traceable
    counterpart of :data:`HANG_OPEN_CELL`."""
    return BrailleCell(
        dots=(), role="hang_open", source_span=source_span, source_text=""
    )


def hang_close_cell(source_span: Span | None) -> BrailleCell:
    """A hanging-indent-close cell carrying ``source_span`` — the traceable
    counterpart of :data:`HANG_CLOSE_CELL`."""
    return BrailleCell(
        dots=(), role="hang_close", source_span=source_span, source_text=""
    )


def cases_open_cell(source_span: Span | None) -> BrailleCell:
    """An equation-system-open cell carrying ``source_span`` — the
    traceable counterpart of :data:`CASES_OPEN_CELL`."""
    return BrailleCell(
        dots=(), role="cases_open", source_span=source_span, source_text=""
    )


def cases_close_cell(source_span: Span | None) -> BrailleCell:
    """An equation-system-close cell carrying ``source_span`` — the
    traceable counterpart of :data:`CASES_CLOSE_CELL`."""
    return BrailleCell(
        dots=(), role="cases_close", source_span=source_span, source_text=""
    )


def cases_palette_cell(dots: tuple[int, ...], source_span: Span | None) -> BrailleCell:
    """One brace-segment cell of an equation system's ``cases_palette`` —
    emitted (first, middle, last) right after :func:`cases_open_cell` so
    the layout can stamp the right segment on each physical braille line.
    Carries the segment's own dots but is skipped by the plain renderers
    (it is layout metadata, not part of the linear cell flow)."""
    return BrailleCell(
        dots=dots, role="cases_palette", source_span=source_span, source_text=""
    )


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

    def __iter__(self) -> Iterator[BrailleCell]:
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

    ``align`` carries a source-declared horizontal alignment the layout
    pass honours (``"center"`` / ``"right"``); ``None`` means the block
    uses the layout's per-type default. It mirrors
    :attr:`brailix.ir.document.Block.align`, stamped here by the backend
    so the renderer never has to reach back into the document IR.
    """

    block_type: str = "paragraph"
    cells: list[BrailleCell] = field(default_factory=list)
    id: str | None = None
    heading_level: int | None = None
    align: str | None = None

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
        if self.align is not None:
            d["align"] = self.align
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BrailleBlock:
        return cls(
            block_type=payload.get("block_type", "paragraph"),
            cells=[BrailleCell.from_dict(c) for c in payload.get("cells", [])],
            id=payload.get("id"),
            heading_level=payload.get("heading_level"),
            align=payload.get("align"),
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

    def validate_traceability(self) -> list[tuple[int, int]]:
        """``(block_index, cell_index)`` of every cell carrying no
        ``source_span`` — empty when the document upholds "every cell maps to a
        source span" (ARCHITECTURE.md §3), the basis of the proofreading
        system's click-a-cell → jump-to-source tracking.

        A *compiled* document satisfies this unconditionally: the Backend gives
        every cell it emits a span — including the control / spacing cells
        (number sign, word / column blanks, matrix row breaks, hanging-indent
        brackets) — through the span-carrying factories above (:func:`blank_cell`
        / :func:`line_break_cell` / …); no ``role`` is exempt, and the dispatch
        boundary enforces it (a translator — plugin or built-in — that returns a
        span-less cell for a span-carrying node raises
        :class:`~brailix.core.errors.BackendContractError` on the spot). This
        method turns that contract from a convention re-asserted by hand in the
        backend tests into a reusable, first-class check on the IR itself: the
        source-span contract suite runs it over real compiles, a proofreading UI
        can verify an externally supplied document before trusting its jumps,
        and a strict caller can gate on the result.

        Non-fatal by design — it reports, it never raises. ``source_span`` stays
        ``Optional`` on :class:`BrailleCell` so a hand-built or legacy-
        deserialized document still round-trips (:meth:`BrailleCell.from_dict`
        accepts a missing span); enforcement is the caller's choice (assert the
        list is empty in tests / strict mode, or inspect it), not a constructor
        invariant that would break compatibility."""
        return [
            (bi, ci)
            for bi, block in enumerate(self.blocks)
            for ci, cell in enumerate(block.cells)
            if cell.source_span is None
        ]
