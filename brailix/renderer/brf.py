"""Render BrailleIR as Braille Ready Format (BRF) bytes.

BRF is the publishing-grade exchange format used by embossers and
braille translation chains. Each braille cell maps to one ASCII
character per the **North American Braille ASCII Code** (NABCC, ANSI/
NBA-Braille):

  ``mask`` = bitmask of dots 1..6 (``dot N -> 1 << (N-1)``)
  ``char`` = ``_NABCC[mask]``

Mapping table (64 entries; the same one that every BRF embosser
implements). For example::

  dots ()      → ' '   blank cell
  dots (1,)    → 'A'
  dots (1,2)   → 'B'
  dots (3,4,5,6) → '#'

Eight-dot cells: BRF was defined for the 6-dot system, so dots 7 / 8
have no standard ASCII representation. The renderer **strips** them
silently — emitting a warning would be too noisy when 8-dot data is
intentionally being downgraded for embossing — but flags the
:class:`BrailleCell.role` ``"eight_dot"`` mapping in the cell-level
output if you need to detect it via :mod:`renderer.cells`.

Line endings follow the BRF spec: each braille block ends with CR/LF
(``b"\\r\\n"``) and an optional form-feed at page breaks (handled by
the layout pass — this renderer only emits block-level CR/LF).

The output type is ``bytes`` — BRF files are conventionally written
in binary mode to preserve the CR/LF endings on every platform.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.ir.braille import BrailleCell, BrailleDocument, BrailleSequence

# NABCC lookup: index = 6-bit mask of dots 1..6, value = ASCII char.
# Hand-verified against the official table; do not "optimise" — the
# order is canonical and any swap would silently corrupt embosser output.
_NABCC: str = (
    " A1B'K2L@CIF/MSP"     # masks 0..15
    "\"E3H9O6R^DJG>NTQ"    # masks 16..31
    ",*5<-U8V.%[$+X!&"     # masks 32..47
    ";:4\\0Z7(_?W]#Y)="    # masks 48..63
)


def cell_to_brf(cell: BrailleCell) -> str:
    """Encode one cell as a single NABCC ASCII character.

    Dots 7 and 8 are dropped; only dots 1..6 contribute to the mask.
    For pure 6-dot pipelines this is a no-op; for 8-dot input it gives
    a best-effort approximation suitable for embossing.
    """
    mask = 0
    for d in cell.dots:
        if 1 <= d <= 6:
            mask |= 1 << (d - 1)
    return _NABCC[mask]


def dots_to_brf(dots: tuple[int, ...] | list[int]) -> str:
    """Convenience: BRF char for a bare dot tuple, without a Cell wrapper."""
    mask = 0
    for d in dots:
        if 1 <= d <= 6:
            mask |= 1 << (d - 1)
    return _NABCC[mask]


def brf_to_dots(ch: str) -> tuple[int, ...]:
    """Inverse of :func:`dots_to_brf` for one BRF character.

    Raises :class:`ValueError` for characters outside the NABCC set so
    callers that round-trip data can spot corruption fast.
    """
    if len(ch) != 1:
        raise ValueError(f"expected one character, got {len(ch)}")
    try:
        mask = _NABCC.index(ch)
    except ValueError:
        raise ValueError(f"not a BRF (NABCC) character: {ch!r}") from None
    return tuple(i + 1 for i in range(6) if mask & (1 << i))


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BrfRenderer:
    """Convert a :class:`BrailleDocument` or :class:`BrailleSequence`
    into BRF bytes.

    Block boundaries become ``CR LF`` per the BRF convention; cell
    separators are not inserted. The result is ASCII-only so it
    survives any text encoding round-trip.
    """

    name: str = "brf"
    # Line ending between blocks. BRF spec mandates CR+LF; some readers
    # accept LF only. Override at construction time if you need
    # a specific behaviour.
    line_terminator: bytes = b"\r\n"

    def render(self, source: BrailleDocument | BrailleSequence) -> bytes:
        if isinstance(source, BrailleSequence):
            return "".join(cell_to_brf(c) for c in source.cells).encode("ascii")
        return self.line_terminator.join(
            "".join(cell_to_brf(c) for c in block.cells).encode("ascii")
            for block in source.blocks
        )


def _load() -> BrfRenderer:
    return BrfRenderer()
