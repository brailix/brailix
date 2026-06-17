"""Matrix / determinant / equation-system handlers for the math backend.

Implements row-by-row notation for ``<mtable>`` —
the fenced form (``<mo>(</mo><mtable/><mo>)</mo>`` and its ``[]`` / ``||``
variants, recognised inside a child sequence), the bare ``<mtable>``, and
the equation-system form (``\\begin{cases}`` / ``\\left\\{…\\right.``):
a ``{`` prefix fence with **no** closing fence, where each braille row is
prefixed with the matching segment of the multi-line brace — ⠎ (234) first
row, ⠇ (123) middle rows, ⠣ (126) last row — with no row-end marker.

Every print row lands on its own braille line: rows are separated by
:data:`~brailix.ir.braille.LINE_BREAK_CELL`, which the renderers turn
into a real line break, one row after another in row order.

Cross-imports :func:`_emit_as_mo` from :mod:`.leaves` to emit the per-row
delimiters through the shared ``<mo>`` machinery.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.handlers.leaves import _emit_as_mo
from brailix.backend.math.utils import (
    _emit_structure,
    _is_function_head,
    _is_typed_slash_mrow,
)
from brailix.ir.braille import (
    BLANK_CELL,
    HANG_CLOSE_CELL,
    HANG_OPEN_CELL,
    LINE_BREAK_CELL,
    BrailleCell,
)

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


def _is_empty_mo(node: ET.Element | None) -> bool:
    """A fence ``<mo>`` with no text — latex2mathml's ``\\right.``
    placeholder (invisible right delimiter)."""
    return (
        node is not None
        and node.tag == "mo"
        and not (node.text or "").strip()
    )


def _emit_children_with_matrix(
    cells: list[BrailleCell], mctx: MathBrailleContext, kids: list[ET.Element]
) -> None:
    """Emit a child sequence, recognising a fenced matrix / determinant
    (``<mo>(</mo><mtable/><mo>)</mo>`` and the ``[]`` / ``||`` variants).

    The fence delimiters wrap the WHOLE matrix in MathML but, per
    the row-by-row linear notation, each braille row carries its own
    delimiter — so we consume the flanking ``<mo>`` and apply the matched
    delimiter per row. Non-matrix children emit normally.

    The walker also recognises a fraction in *function-argument*
    position — an ``<mfrac>`` (or typed-slash ``a / b`` mrow) whose
    immediately preceding sibling is a function head (``cos`` /
    ``log₂`` / ``lim`` …). It raises the one-shot
    ``mctx.fraction_is_function_arg`` flag so the fraction handler keeps
    the compound ⠆…⠰ form: without the brackets, cos of α/a would
    collapse into the same cells as (cos α)/a."""
    n = len(kids)
    i = 0
    prev: ET.Element | None = None
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
            prev = kids[i + 2]
            i += 3
            continue
        if (
            i + 1 < n
            and kids[i + 1].tag == "mtable"
            and _is_fence_mo(kids[i], frozenset({"{"}))
            and (i + 2 >= n or _is_empty_mo(kids[i + 2]))
        ):
            # Equation system (\begin{cases} / \left\{…\right.): an
            # opening brace with no visible closing fence. latex2mathml
            # emits no postfix <mo> for the cases environment and an
            # empty-text postfix <mo> for \right. — consume it either way.
            _emit_mtable_cases(cells, mctx, kids[i + 1])
            consumed = 3 if i + 2 < n else 2
            prev = kids[i + consumed - 1]
            i += consumed
            continue
        kid = kids[i]
        if (
            kid.tag == "mfrac" or _is_typed_slash_mrow(kid)
        ) and _is_function_head(prev, mctx.profile):
            mctx.fraction_is_function_arg = True
        _emit_element(cells, mctx, kid)
        prev = kid
        i += 1


def _emit_mtable_linear(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    mtable: ET.Element,
    open_char: str,
    close_char: str,
) -> None:
    """Row-by-row notation: each print row is one
    braille line (LINE_BREAK_CELL between rows — one row after another),
    enclosed in paired delimiters (parentheses ⠣⠜ / square
    brackets ⠷⠾ / determinant vertical bars ⠸⠸), elements within a row
    separated by blank cells. The whole table is bracketed in
    HANG_OPEN/CLOSE so a row the layout must break for width continues
    with the hanging indent (a row too wide to fit continues two cells in on the next line).
    Content before the first row / after the last row stays on those
    rows' lines (trailing operators attach to the last row).
    The delimiter cells reuse the profile's lpar/rpar/lbrack/rbrack/
    verbar symbols. Block matrices / diagonal shorthand /
    two-dimensional layout are deferred."""
    cells.append(HANG_OPEN_CELL)
    first_row = True
    for row in mtable:
        if row.tag != "mtr":
            continue
        if not first_row:
            cells.append(LINE_BREAK_CELL)
        first_row = False
        _emit_as_mo(cells, mctx, open_char)
        mctx.need_number_sign = True
        _emit_row_cells(cells, mctx, row)
        _emit_as_mo(cells, mctx, close_char)
    cells.append(HANG_CLOSE_CELL)
    mctx.need_number_sign = True


def _emit_row_cells(
    cells: list[BrailleCell], mctx: MathBrailleContext, row: ET.Element
) -> None:
    """Emit one ``<mtr>``'s cells: ``<mtd>`` contents in order, blank-cell
    separated (the element separator within a row).

    Each ``<mtd>``'s children go through :func:`_emit_children_with_matrix`
    — the same walker the top-level mrow uses — so a function applied to a
    fraction inside a cell (``\\cos\\frac{a}{b}``) still raises
    ``fraction_is_function_arg`` and keeps the disambiguating compound
    ⠆…⠰ form. Emitting each child straight through ``_emit_element`` would
    bypass that detection and collapse it into the same cells as the simple
    bar form of ``(cos a)/b``. It also lets a cell carry its own nested
    fenced matrix.
    """
    first = True
    for tcell in row:
        if tcell.tag != "mtd":
            continue
        if not first:
            cells.append(BLANK_CELL)
            mctx.need_number_sign = True
        first = False
        _emit_children_with_matrix(cells, mctx, list(tcell))


def _emit_mtable_cases(
    cells: list[BrailleCell], mctx: MathBrailleContext, mtable: ET.Element
) -> None:
    """Equation system: a ``{``-fenced ``<mtable>`` with no closing fence
    (``\\begin{cases}`` / ``\\left\\{…\\right.``).

    The print brace spans every row; braille splits it into its segments,
    one per row head — ⠎ (``cases.first``) on the first row, ⠇
    (``cases.middle``) on each middle row, ⠣ (``cases.last``) on the
    last, each followed by one blank cell (the segments are MARKS, not
    brackets — written solid they would read as the letters s / l / a
    cell shapes) — each row on its own line (LINE_BREAK_CELL between
    rows), no row-end marker, the whole system bracketed in
    HANG_OPEN/CLOSE so an over-wide row continues with the hanging
    indent. A single-row table degrades to the plain left brace ⠪ (an
    ordinary bracket — no blank after it): the print form is an
    ordinary one-line ``{``, not a multi-line brace.
    """
    rows = [row for row in mtable if row.tag == "mtr"]
    if len(rows) == 1:
        _emit_as_mo(cells, mctx, "{")
        mctx.need_number_sign = True
        _emit_row_cells(cells, mctx, rows[0])
        mctx.need_number_sign = True
        return
    cells.append(HANG_OPEN_CELL)
    last = len(rows) - 1
    for idx, row in enumerate(rows):
        if idx:
            cells.append(LINE_BREAK_CELL)
        if idx == 0:
            segment = "cases.first"
        elif idx == last:
            segment = "cases.last"
        else:
            segment = "cases.middle"
        _emit_structure(cells, mctx, segment, role="math_delim")
        cells.append(BLANK_CELL)
        mctx.need_number_sign = True
        _emit_row_cells(cells, mctx, row)
    cells.append(HANG_CLOSE_CELL)
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
