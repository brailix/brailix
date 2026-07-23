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
    BrailleCell,
    blank_cell,
    cases_close_cell,
    cases_open_cell,
    cases_palette_cell,
    hang_close_cell,
    hang_open_cell,
    line_break_cell,
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


def _mtd_is_omitted(tcell: ET.Element) -> bool:
    """An ``<mtd/>`` standing for an omitted (0) element — no element
    children and no text. ``\\begin{pmatrix} a & & \\\\ … \\end{pmatrix}``
    (blank cells for the zeros) produces these."""
    return len(list(tcell)) == 0 and not (tcell.text or "").strip()


def _mtable_has_omitted_cell(mtable: ET.Element) -> bool:
    """Does any row carry an omitted (empty) ``<mtd/>``? Such a matrix /
    determinant is written with its zeros left out, so its non-zero
    elements must keep their COLUMN alignment (see
    :func:`_emit_mtable_aligned`)."""
    return any(
        _mtd_is_omitted(td)
        for row in mtable
        if row.tag == "mtr"
        for td in row
        if td.tag == "mtd"
    )


def _render_mtd(mctx: MathBrailleContext, tcell: ET.Element) -> list[BrailleCell]:
    """Render one ``<mtd>``'s content into a fresh cell buffer, isolated
    like a matrix column entry (its own number sign, no letter run shared
    across the column boundary). Used by the aligned path to MEASURE each
    cell before placing it."""
    buf: list[BrailleCell] = []
    mctx.need_number_sign = True
    mctx.break_letter_run()
    _emit_children_with_matrix(buf, mctx, list(tcell))
    return buf


def _measure_aligned_cells(
    mctx: MathBrailleContext, rows: list[ET.Element]
) -> tuple[list[list[list[BrailleCell]]], list[int]]:
    """Render every ``<mtd>`` of an omitted-zero table into its own cell
    buffer (isolated as a column entry; ``in_matrix_cell`` so a polynomial
    cell keeps the ⠐ operator mark) and return ``(row_buffers, col_widths)``
    where each column's width is that of its widest element."""
    saved_in_cell = mctx.in_matrix_cell
    mctx.in_matrix_cell = True
    row_bufs: list[list[list[BrailleCell]]] = [
        [_render_mtd(mctx, td) for td in row if td.tag == "mtd"] for row in rows
    ]
    mctx.in_matrix_cell = saved_in_cell
    ncols = max((len(bufs) for bufs in row_bufs), default=0)
    col_w = [0] * ncols
    for bufs in row_bufs:
        for c, buf in enumerate(bufs):
            col_w[c] = max(col_w[c], len(buf))
    return row_bufs, col_w


def _emit_aligned_row(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    bufs: list[list[BrailleCell]],
    col_w: list[int],
) -> None:
    """Place one row's pre-measured cell buffers column-aligned: an omitted
    cell is ``col_w`` blanks, columns are one blank apart, each written
    column is padded to its width so the next lines up, and TRAILING
    omitted cells are dropped (write stops after the last non-empty cell)."""
    last = max((c for c, buf in enumerate(bufs) if buf), default=-1)
    for c in range(last + 1):
        if c > 0:
            cells.append(blank_cell(mctx.span))  # column separator
        buf = bufs[c]
        cells.extend(buf)
        if c < last:  # pad to the column width so the next one aligns
            cells.extend(
                blank_cell(mctx.span) for _ in range(col_w[c] - len(buf))
            )


def _emit_mtable_aligned(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    mtable: ET.Element,
    open_char: str,
    close_char: str,
) -> None:
    """Row-by-row notation for a matrix / determinant whose zeros are
    OMITTED (empty ``<mtd/>`` cells) — a diagonal / triangular / tridiagonal
    / arrow / block-diagonal shape, etc.

    Unlike the plain path (elements blank-separated, no column alignment),
    an omitted-zero matrix must keep every non-zero element under its
    column so the reader can place it: a diagonal element cascades one
    column right per row, an upper-triangular row's first non-zero lines up
    with the same column above, and a lower-triangular row (zeros only
    trailing) is unchanged. Column widths come from
    :func:`_measure_aligned_cells`; each row is placed by
    :func:`_emit_aligned_row`. If the zeros are NOT omitted the caller keeps
    the plain path (normal translation). The staircase equation system
    (阶梯型方程组) reuses the same two helpers from the cases path."""
    rows = [row for row in mtable if row.tag == "mtr"]
    row_bufs, col_w = _measure_aligned_cells(mctx, rows)
    cells.append(hang_open_cell(mctx.span))
    first_row = True
    for bufs in row_bufs:
        if not first_row:
            cells.append(line_break_cell(mctx.span))
        first_row = False
        _emit_as_mo(cells, mctx, open_char)
        mctx.need_number_sign = True
        _emit_aligned_row(cells, mctx, bufs, col_w)
        _emit_as_mo(cells, mctx, close_char)
    cells.append(hang_close_cell(mctx.span))
    mctx.need_number_sign = True


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
    verbar symbols. A matrix / determinant whose zeros are OMITTED (empty
    ``<mtd/>`` cells) instead goes through :func:`_emit_mtable_aligned` so
    its non-zero elements keep their column alignment. Block matrices /
    two-dimensional layout are otherwise deferred."""
    if not any(child.tag == "mtr" for child in mtable):
        # A malformed / empty <mtable> (no rows) would otherwise emit a pair of
        # empty hanging delimiters — meaningless cells with no warning. Flag and
        # emit nothing. (latex2mathml / MTEF never produce this; a hand-built or
        # corrupt tree can.)
        mctx.backend.warnings.warn(
            code="MATH_UNSUPPORTED_ELEMENT",
            message="<mtable> has no <mtr> rows; skipping empty matrix",
            span=mctx.span,
            source="backend.math",
        )
        return
    if _mtable_has_omitted_cell(mtable):
        _emit_mtable_aligned(cells, mctx, mtable, open_char, close_char)
        return
    cells.append(hang_open_cell(mctx.span))
    first_row = True
    for row in mtable:
        if row.tag != "mtr":
            continue
        if not first_row:
            cells.append(line_break_cell(mctx.span))
        first_row = False
        _emit_as_mo(cells, mctx, open_char)
        mctx.need_number_sign = True
        # Row elements are blank-separated, so an operator's space_before
        # inside a cell must bind as the matrix operator mark (⠐) instead
        # of a blank (see MathBrailleContext.in_matrix_cell). Saved/restored
        # so a nested matrix restores the outer flag, not a bare False.
        saved_in_cell = mctx.in_matrix_cell
        mctx.in_matrix_cell = True
        _emit_row_cells(cells, mctx, row)
        mctx.in_matrix_cell = saved_in_cell
        _emit_as_mo(cells, mctx, close_char)
    cells.append(hang_close_cell(mctx.span))
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
            cells.append(blank_cell(mctx.span))
            mctx.need_number_sign = True
            # The blank cell separates columns: adjacent cells are distinct
            # entries, so a letter ending one cell and a letter starting the
            # next must not share a letter sign.
            mctx.break_letter_run()
        first = False
        _emit_children_with_matrix(cells, mctx, list(tcell))


def _emit_cases_palette(
    cells: list[BrailleCell], mctx: MathBrailleContext
) -> None:
    """Emit the three brace-segment cells (first, middle, last) that
    follow ``cases_open``, tagged ``cases_palette`` so the layout can
    stamp the right one on each PHYSICAL braille line (and the plain
    renderers skip them). Each ``cases.*`` segment is a single cell in the
    Chinese profile; the layout's per-line stamping assumes that (one
    gutter cell per line), so flatten each segment's cell sequence into
    the palette in first / middle / last order."""
    for name in ("cases.first", "cases.middle", "cases.last"):
        for dots in mctx.profile.math_structure(name):
            cells.append(cases_palette_cell(dots, mctx.span))


def _emit_mtable_cases(
    cells: list[BrailleCell], mctx: MathBrailleContext, mtable: ET.Element
) -> None:
    """Equation system: a ``{``-fenced ``<mtable>`` with no closing fence
    (``\\begin{cases}`` / ``\\left\\{…\\right.``).

    The print brace spans the whole system; braille redraws it with a
    segment on every PHYSICAL braille line the system occupies — ⠎
    (``cases.first``) on the first line, ⠣ (``cases.last``) on the last,
    ⠇ (``cases.middle``) on each line between — each followed by one
    blank cell (the segments are MARKS, not brackets — written solid they
    would read as the letters s / l / a cell shapes). Because a single
    equation may wrap to more than one braille line, that segment-to-line
    mapping can only be finished once the width-wrap is known, so it is
    the LAYOUT's job: this emitter brackets the system in
    CASES_OPEN/CLOSE, publishes the three segments as a ``cases_palette``
    right after CASES_OPEN, then writes each equation on its own line
    (LINE_BREAK_CELL between them) with the per-equation segment as a
    PLACEHOLDER the layout overwrites per physical line. The placeholder
    keeps the raw cell stream self-describing and the plain
    (non-wrapping) renderers correct. Rows continue two cells past the
    row start on width overflow, exactly like a matrix. A single-row
    table degrades to the plain left brace ⠪ (an ordinary bracket — no
    blank after it): the print form is an ordinary one-line ``{``, not a
    multi-line brace.
    """
    rows = [row for row in mtable if row.tag == "mtr"]
    if not rows:
        mctx.backend.warnings.warn(
            code="MATH_UNSUPPORTED_ELEMENT",
            message="<mtable> cases has no <mtr> rows; skipping empty system",
            span=mctx.span,
            source="backend.math",
        )
        return
    if len(rows) == 1:
        _emit_as_mo(cells, mctx, "{")
        mctx.need_number_sign = True
        _emit_row_cells(cells, mctx, rows[0])
        mctx.need_number_sign = True
        return
    # A staircase system (阶梯型方程组) — an aligned array with omitted
    # (empty <mtd/>) leading coefficients — keeps its columns aligned, same
    # as an omitted-zero matrix; the brace segment + blank still lead every
    # row and, being the same width on all rows, don't disturb the columns.
    aligned = _mtable_has_omitted_cell(mtable)
    row_bufs, col_w = (
        _measure_aligned_cells(mctx, rows) if aligned else ([], [])
    )
    cells.append(cases_open_cell(mctx.span))
    _emit_cases_palette(cells, mctx)
    last = len(rows) - 1
    for idx, row in enumerate(rows):
        if idx:
            cells.append(line_break_cell(mctx.span))
        if idx == 0:
            segment = "cases.first"
        elif idx == last:
            segment = "cases.last"
        else:
            segment = "cases.middle"
        _emit_structure(cells, mctx, segment, role="math_delim")
        cells.append(blank_cell(mctx.span))
        mctx.need_number_sign = True
        if aligned:
            _emit_aligned_row(cells, mctx, row_bufs[idx], col_w)
        else:
            _emit_row_cells(cells, mctx, row)
    cells.append(cases_close_cell(mctx.span))
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
