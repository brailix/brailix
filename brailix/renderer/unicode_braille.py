"""Render BrailleIR into Unicode braille characters (U+2800..U+28FF).

Encoding (per the Unicode standard):

* Each cell maps to one code point in the Braille Patterns block.
* Code point = ``0x2800 + bitmask``.
* Dot N contributes ``1 << (N - 1)`` to the bitmask:

  ===== ============= =======
  Dot   bit position  hex
  ===== ============= =======
  1     0             0x01
  2     1             0x02
  3     2             0x04
  4     3             0x08
  5     4             0x10
  6     5             0x20
  7     6             0x40
  8     7             0x80
  ===== ============= =======

Blank cell → ``U+2800`` (⠀). Eight-dot cells are supported.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.ir.braille import BrailleCell, BrailleDocument, BrailleSequence

BRAILLE_BASE = 0x2800


def cell_to_char(cell: BrailleCell) -> str:
    """Encode one cell as a single Unicode braille code point."""
    mask = 0
    for d in cell.dots:
        mask |= 1 << (d - 1)
    return chr(BRAILLE_BASE + mask)


def dots_to_char(dots: tuple[int, ...] | list[int]) -> str:
    """Convenience: encode a bare dot tuple without constructing a cell."""
    mask = 0
    for d in dots:
        mask |= 1 << (d - 1)
    return chr(BRAILLE_BASE + mask)


def char_to_dots(ch: str) -> tuple[int, ...]:
    """Decode one Unicode braille char back to a dot tuple. Useful for
    round-trip tests and debugging."""
    if len(ch) != 1:
        raise ValueError(f"expected one character, got {len(ch)}")
    cp = ord(ch)
    if not (BRAILLE_BASE <= cp <= BRAILLE_BASE + 0xFF):
        raise ValueError(f"not a Unicode braille char: U+{cp:04X}")
    mask = cp - BRAILLE_BASE
    return tuple(i + 1 for i in range(8) if mask & (1 << i))


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UnicodeBrailleRenderer:
    """Convert a :class:`BrailleDocument` or :class:`BrailleSequence`
    into a Unicode braille string.

    Block boundaries become a single ``\n``; cell separators are not
    inserted (cells map 1:1 to characters). A forced in-block line break
    (:data:`~brailix.ir.braille.LINE_BREAK_CELL`, emitted between matrix /
    equation-system rows) also renders as ``\n`` — the row structure is
    part of the braille notation, not a layout nicety.
    """

    name: str = "unicode"

    def render(self, source: BrailleDocument | BrailleSequence) -> str:
        if isinstance(source, BrailleSequence):
            return _cells_to_str(source.cells)
        # Document: join blocks with newline.
        return "\n".join(
            _cells_to_str(block.cells) for block in source.blocks
        )


def _cells_to_str(cells: list[BrailleCell]) -> str:
    # line_break → newline; the zero-width hang-region sentinels (layout
    # metadata only) print nothing.
    out: list[str] = []
    for c in cells:
        if c.role == "line_break":
            out.append("\n")
        elif c.role not in ("hang_open", "hang_close"):
            out.append(cell_to_char(c))
    return "".join(out)


def _load() -> UnicodeBrailleRenderer:
    """Loader used by :data:`brailix.renderer.renderer_registry`.

    Kept symmetric with the other adapter modules — the registry calls
    this to materialize the instance lazily.
    """
    return UnicodeBrailleRenderer()
