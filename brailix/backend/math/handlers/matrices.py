"""Matrix / determinant handlers for the math backend.

Implements 《盲文常用数学符号》§17 linear notation for ``<mtable>`` — both the
fenced form (``<mo>(</mo><mtable/><mo>)</mo>`` and its ``[]`` / ``||``
variants, recognised inside a child sequence) and the bare ``<mtable>``.

Cross-imports :func:`_emit_as_mo` from :mod:`.leaves` to emit the per-row
delimiters through the shared ``<mo>`` machinery.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.handlers.leaves import _emit_as_mo
from brailix.ir.braille import BLANK_CELL, BrailleCell

# Fence chars that delimit a matrix / determinant. The matching close char
# is taken from the actual sibling <mo>, so we only need membership sets to
# recognise the `<mo>fence</mo><mtable/><mo>fence</mo>` shape.
_MATRIX_FENCE_OPEN: frozenset[str] = frozenset({"(", "[", "|"})
_MATRIX_FENCE_CLOSE: frozenset[str] = frozenset({")", "]", "|"})


def _is_fence_mo(node: ET.Element | None, charset: frozenset[str]) -> bool:
    return (
        node is not None
        and node.tag == "mo"
        and (node.text or "").strip() in charset
    )


def _emit_children_with_matrix(
    cells: list[BrailleCell], mctx: MathBrailleContext, kids: list[ET.Element]
) -> None:
    """Emit a child sequence, recognising a fenced matrix / determinant
    (``<mo>(</mo><mtable/><mo>)</mo>`` and the ``[]`` / ``||`` variants).

    The fence delimiters wrap the WHOLE matrix in MathML but, per
    《盲文常用数学符号》§17 linear notation, each braille row carries its own
    delimiter — so we consume the flanking ``<mo>`` and apply the matched
    delimiter per row. Non-matrix children emit normally."""
    n = len(kids)
    i = 0
    while i < n:
        if (
            i + 2 < n
            and kids[i + 1].tag == "mtable"
            and _is_fence_mo(kids[i], _MATRIX_FENCE_OPEN)
            and _is_fence_mo(kids[i + 2], _MATRIX_FENCE_CLOSE)
        ):
            _emit_mtable_linear(
                cells, mctx, kids[i + 1],
                (kids[i].text or "").strip(),
                (kids[i + 2].text or "").strip(),
            )
            i += 3
            continue
        _emit_element(cells, mctx, kids[i])
        i += 1


def _emit_mtable_linear(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    mtable: ET.Element,
    open_char: str,
    close_char: str,
) -> None:
    """《盲文常用数学符号》§17 linear notation: write row by row, each row
    enclosed in paired delimiters (parentheses ⠣⠜ / square brackets ⠷⠾ /
    determinant vertical bars ⠸⠸), elements within a row separated by blank
    cells, rows laid out in sequence (no forced line breaks — the renderer
    wraps naturally by line width). The delimiter cells reuse the profile's
    lpar/rpar/lbrack/rbrack/verbar symbols. Block matrices / diagonal
    shorthand / two-dimensional layout are deferred."""
    for row in mtable:
        if row.tag != "mtr":
            continue
        _emit_as_mo(cells, mctx, open_char)
        mctx.need_number_sign = True
        first = True
        for tcell in row:
            if tcell.tag != "mtd":
                continue
            if not first:
                cells.append(BLANK_CELL)
                mctx.need_number_sign = True
            first = False
            for child in list(tcell):
                _emit_element(cells, mctx, child)
        _emit_as_mo(cells, mctx, close_char)
    mctx.need_number_sign = True


def _emit_mtable(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Bare ``<mtable>`` (no surrounding fence) → default parentheses linear
    notation."""
    _emit_mtable_linear(cells, mctx, elem, "(", ")")


_DISPATCH_PARTIAL = {
    "mtable": _emit_mtable,
}
