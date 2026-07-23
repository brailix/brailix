"""Layout renderer: word-wrap, block indent, optional pagination.

Where :mod:`renderer.unicode_braille` and :mod:`renderer.brf` produce
one long line per block, this renderer makes the output **page-ready**:
line-wrapped at a configurable cell width, indented per block type,
and optionally paginated.

Output formats:

* ``"unicode"`` (default) — a ``str`` of Unicode braille separated by
  ``\\n`` between lines and (when paginated) ``\\x0c`` (form feed)
  between pages. Suits screen reading and copy-paste into editors.
* ``"brf"`` — ``bytes`` in the BRF (NABCC ASCII) encoding, ``\\r\\n``
  line ends per the BRF spec, form feed between pages. Suits sending
  straight to an embosser or saving a ``.brf`` file.

Block-level rules (defaults; per-block overrides via :class:`LayoutOptions`):

* ``paragraph``  — first-line indent 2 cells, continuation flush left.
* ``heading``    — one blank line before and after; level-1 centred,
  deeper levels flush left with no indent.
* ``list_item``  — first line flush left, continuation indented 2 cells
  (hanging indent past the marker that the backend already emitted).
* ``quote``      — entire block indented 2 cells.
* ``code_block`` — emit verbatim (no wrapping, no indent).
* ``table_row``  — emit verbatim (column alignment is the backend's job).
* ``footnote``   — first line flush left, continuation indented 2.
* ``score`` / ``music_block`` — laid out by the active BANA *format*
  (:mod:`brailix.renderer.music_layout`, chosen via
  :attr:`LayoutOptions.music_scheme`).  The default ``single_line``
  (BANA §24.1) breaks only at measure-separator cells, never mid-measure
  (BANA Pars. 11 / 17) and never hyphenates; a measure too wide for the
  line runs over.  ``score`` is framed with one blank line before and
  after, ``music_block`` none.

Any text block may additionally carry a source-declared **alignment**
(:attr:`BrailleBlock.align` — ``"center"`` / ``"right"``, e.g. a Word
paragraph the author centred or right-aligned). When set it overrides the
block's usual first-line / hanging indent: every wrapped line is left-padded
from the line width so its content sits centred or flush right. This is the
same mechanism that centres level-1 headings — that default is just the
``align``-less fallback applied to ``heading`` level 1.

Wrapping picks blank cells as break points (word boundaries) — and
when a single "word" (run of non-blank cells) doesn't fit, breaks at
**atomic-group** boundaries inside it: cells that share the same
non-None ``source_span`` are one atom (one hanzi syllable / a Latin
first-letter prefix+letter / cells inside the same math structure all
share a span), and a cell with ``source_span=None`` (synthesised marker
like ``number_sign`` / ``list_marker``) clings to its right-hand
neighbour so it never floats off alone. Each non-blank break injects a
**continuation hyphen** at the end of the broken line — defaults to ⠤
(dots 3-6), per the BANA / Current Chinese Braille / NCB convention,
shared by Chinese and English embedded text. Set
``LayoutOptions.continuation_hyphen=None`` to disable.

If a single atom itself is wider than the line we still mid-atom break
as a last resort (silently — the user would already see the runaway
output and can restructure the source).

A multi-row matrix / determinant / equation system (the math backend
brackets each one in ``hang_open`` … ``hang_close`` and separates its
rows with ``line_break``) opens a line of its own, taking the operator
that introduces it — the ``=`` of ``A = <matrix>`` — down with it, and
every row after the first resumes at the column the first row's opening
delimiter sits in.  So the row delimiters stack in one column instead of
the first row trailing a long stem while the rest start at the margin.

Pagination: if ``page_height`` is set, the rendered lines are split into
pages of ``page_height`` lines, joined by a form feed character; blank-
line separators count toward that budget. ``None`` (default) means single
continuous output. With ``show_page_numbers`` on, each page additionally
carries the page number on its OWN line (a numbered page is ``page_height``
content lines + 1) — never sharing or shrinking a content line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from brailix.core.span import Span
from brailix.ir.braille import (
    BLANK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.renderer.brf import cell_to_brf
from brailix.renderer.music_layout import get_scheme
from brailix.renderer.unicode_braille import cell_to_char, dots_to_char

OutputFormat = Literal["unicode", "brf"]
PageNumberPosition = Literal[
    "top-right", "top-left", "bottom-right", "bottom-left"
]
"""Where the page number sits on a paginated page.

``top-X`` puts the page number's own line first on the page; ``bottom-X``
last.  ``-right`` right-aligns the number within that line (pads to the
full width — Current Chinese Braille default; see
:data:`LayoutOptions.page_number_position`); ``-left`` puts it at column 0
(some National Common Braille templates prefer this).  The four positions
form the 2×2 grid the user picks from in Settings / toolbar.
"""

# Page-number digit cells come from the builtin universal numbers
# resource — one authority shared with the number / math backends; the
# rationale lives in :mod:`brailix.renderer._page_digits` so it's not
# woven through the rest of this module.
from brailix.renderer._page_digits import (
    page_number_brf as _page_number_brf,
)
from brailix.renderer._page_digits import (
    page_number_chars as _page_number_chars,
)

# Zero-width region sentinels + the cases palette carry no glyph of their
# own in the linear flow — the layout consumes them for structure and the
# verbatim (table-cell) copy-through drops them. ``cases_palette`` DOES
# carry dots (the brace segments) but is layout metadata, stamped onto
# physical lines rather than placed inline.
_REGION_SENTINEL_ROLES = frozenset(
    {"hang_open", "hang_close", "cases_open", "cases_close", "cases_palette"}
)


@dataclass(slots=True)
class _CasesRegion:
    """One open equation-system region during wrapping.

    ``gutter`` is the column the brace segment is stamped in (where each
    row's placeholder segment / continuation indent begins); ``palette``
    accumulates the region's ``cases_palette`` cells (first, middle, last
    brace segment); ``first_line`` is the index in the finished-lines list
    where the region's first physical line lands. On ``cases_close`` every
    physical line of the region is re-stamped with the segment matching
    its PHYSICAL position, so the bottom segment (c_126) always lands on
    the last braille line — not merely the last equation.
    """

    gutter: int
    palette: list[BrailleCell] = field(default_factory=list)
    first_line: int = 0


# ---------------------------------------------------------------------------
# LayoutOptions
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LayoutOptions:
    """Per-block indent + blank-line rules.

    Numbers are in **cells** (not characters). A "blank line" in
    pagination accounting is one line containing only a single blank
    cell — so ``heading_blank_before=1`` adds one blank line ahead of
    the heading.
    """

    line_width: int = 40
    page_height: int | None = None  # None = no pagination
    # Render page numbers on each page.  Only takes effect when
    # ``page_height`` is set; ignored for un-paginated output (where
    # there's no "top" / "bottom" of a page to anchor the number).
    # Pages are numbered starting at 1 — no header / TOC offset
    # support in V1.
    show_page_numbers: bool = False
    # Which corner the page number sits in.  Default ``"top-right"``
    # matches BANA / Current Chinese Braille publishing convention so
    # older callers (and the layout-state V3→V4 migration default) get
    # the historical behaviour without opting in.  See
    # :data:`PageNumberPosition`.
    page_number_position: PageNumberPosition = "top-right"

    paragraph_indent: int = 2
    heading_blank_before: int = 1
    heading_blank_after: int = 1
    heading_center_level_1: bool = True
    list_hanging_indent: int = 2
    quote_indent: int = 2
    footnote_hanging_indent: int = 2

    # Continuation hyphen emitted at the end of a line whenever the
    # wrap algorithm has to break *inside* a word (between two atomic
    # groups, or — as a last resort — inside a single runaway atom).
    # Defaults to dots 3-6 (⠤ / NABCC ``-``), the BANA / Current Chinese
    # Braille / NCB shared convention for both Chinese and
    # embedded-English runs.
    # Set to ``None`` to disable hyphen emission entirely — useful for
    # callers that want the legacy "long word splits silently" behaviour
    # or for non-text streams (raw cell arrays in tests).
    continuation_hyphen: tuple[int, ...] | None = (3, 6)

    # Hanging indent applied to WIDTH-overflow continuation lines inside
    # a ``hang_open`` … ``hang_close`` region (the math backend brackets
    # every matrix / determinant / equation system in those sentinels).
    # A table row that doesn't fit on one
    # line continues on the next indented by two cells.  Forced row
    # breaks (``line_break`` cells) are NOT overflow: each row resumes at
    # the column its region started at, so the row delimiters stack up.
    hang_region_indent: int = 2

    # Block kinds we copy through verbatim (no wrap, no indent). Tables
    # rely on backend-emitted spacing; code blocks must stay exact.
    # ``score`` / ``music_block`` are deliberately NOT here — they go
    # through the active BANA layout format
    # (:mod:`brailix.renderer.music_layout`), which breaks only at
    # measure-separator cells so an in-accord / repeat sequence never
    # splits mid-measure (BANA Pars. 11 / 17).
    verbatim_block_types: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "code_block", "table_row", "table",
        })
    )

    # Cell roles that the single_line scheme treats as legal break
    # points. The music backend emits ``music_measure_sep`` at every
    # measure boundary and ``music_part_sep`` between parts (see
    # :mod:`brailix.backend.music.handlers.containers`). single_line
    # breaks at either (and only these), keeping each measure
    # indivisible. Named here rather than hard-coded in the wrap loop so
    # a profile / future backend can rebind the contract in one place.
    # (``bar_over_bar`` reads the two roles directly to build parallels,
    # so it doesn't consult this set.)
    measure_break_roles: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"music_measure_sep", "music_part_sep"}
        )
    )

    # Which BANA layout *format* a score / music_block uses.  Resolved
    # against :mod:`brailix.renderer.music_layout`'s registry and applied
    # by the scheme strategy — the renderer does not branch on it.
    # Default ``single_line`` (BANA §24.1); ``bar_over_bar`` is also
    # implemented, while ``line_by_line`` still falls back to
    # single_line until its backend support lands.  Per-line indent is
    # the scheme's concern.
    music_scheme: str = "single_line"
    # Block framing: ``score`` is a display block (blank line before /
    # after, like a heading); ``music_block`` is the inline-ish analogue
    # (no surrounding blanks).
    score_blank_before: int = 1
    score_blank_after: int = 1
    music_block_blank_before: int = 0
    music_block_blank_after: int = 0


# ---------------------------------------------------------------------------
# LayoutRenderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LayoutRenderer:
    """Word-wrap a :class:`BrailleDocument` into laid-out output.

    Configure via :attr:`options`. ``format`` chooses between Unicode
    braille (``str``) and BRF (``bytes``). The same wrapping /
    pagination logic drives both — only the per-cell encoding differs.
    """

    name: str = "layout"
    options: LayoutOptions = field(default_factory=LayoutOptions)
    format: OutputFormat = "unicode"

    def render(self, source: BrailleDocument | BrailleSequence) -> str | bytes:
        lines = self._lay_out(source)
        return self._encode(lines)

    def lay_out_block(self, block: BrailleBlock) -> list[list[BrailleCell]]:
        """Lay out one block into display lines (lists of cells) — the
        same wrap / indent / blank-line rules :meth:`render` applies,
        but **without** encoding to str/bytes or paginating.

        This is the seam a front-end's braille view renders through so the
        on-screen wrap matches the exported file: one layout authority
        instead of a second, mechanical wrapper that silently ignores
        indent / word / measure boundaries (and makes those layout
        settings feel like decoration on screen).

        Cells in the returned lines are the **same objects** as
        ``block.cells`` — carried by reference through wrapping — plus
        synthesised cells the layout inserts (indent blanks, the
        continuation hyphen, kept measure separators).  Callers that
        need a cell→position map recover the originals by object
        identity; synthesised cells carry no ``source_span`` and are
        skipped by highlight logic.
        """
        return self._lay_out_block(block)

    # ---- pipeline stages -------------------------------------------------

    def _lay_out(self, source: BrailleDocument | BrailleSequence) -> list[list[BrailleCell]]:
        """Turn the source into a list of finished lines (lists of cells)."""
        if isinstance(source, BrailleSequence):
            # Treat a bare sequence as a single paragraph.
            return self._wrap_block_cells(
                source.cells, first_indent=0, cont_indent=0
            )
        lines: list[list[BrailleCell]] = []
        for block in source.blocks:
            block_lines = self._lay_out_block(block)
            lines.extend(block_lines)
        return lines

    def _lay_out_block(
        self, block: BrailleBlock
    ) -> list[list[BrailleCell]]:
        """Apply block-specific indent + blank-line rules and wrap.

        Takes the whole :class:`BrailleBlock` so per-block metadata
        (:attr:`BrailleBlock.heading_level`, :attr:`BrailleBlock.align`)
        is visible to the layout rules without re-walking the source IR.
        """
        opts = self.options
        block_type = block.block_type
        cells = block.cells
        out: list[list[BrailleCell]] = []

        if block_type in opts.verbatim_block_types:
            # Don't soft-wrap or indent for width — but a width-verbatim block
            # (table cell / code) is not *content*-verbatim: honour the hard
            # structural line breaks the backend emitted (e.g. a matrix in a
            # table cell brackets its rows with LINE_BREAK_CELL and wraps the
            # body in hang sentinels). Split on the line break into display
            # rows and drop the zero-width hang sentinels — the same role
            # handling the plain renderers do. Without this the sentinels
            # encode as blank cells and the matrix collapses to one line, and
            # LayoutRenderer is the only production export path.
            if cells:
                row: list[BrailleCell] = []
                for c in cells:
                    if c.role == "line_break":
                        out.append(row)
                        row = []
                    elif c.role not in _REGION_SENTINEL_ROLES:
                        row.append(c)
                if row or not out:
                    out.append(row)
            if all(not line for line in out):
                # Degenerate block (e.g. cells were all hang sentinels, no
                # content, no line_break): emit nothing, matching the
                # text-flow / music all-empty guard, instead of leaking a
                # stray blank line.
                return []
            return out

        if block_type in ("score", "music_block"):
            if block_type == "score":
                blank_before = opts.score_blank_before
                blank_after = opts.score_blank_after
            else:
                blank_before = opts.music_block_blank_before
                blank_after = opts.music_block_blank_after
            # The active BANA format lays out the measures (registry
            # lookup, not a branch here); this method only frames the
            # block with blank lines.
            wrapped = get_scheme(opts.music_scheme).lay_out(cells, opts)
            if not wrapped or all(not line for line in wrapped):
                # Empty (or all-empty-line) music block — emit nothing. The
                # wrapper returns [[]] for a degenerate block (only separator
                # cells, no real measures), which is truthy; without the
                # all-empty check its framing blanks + that empty line would
                # leak in as stray rows.
                return out
            for _ in range(blank_before):
                out.append([BLANK_CELL])
            out.extend(wrapped)
            for _ in range(blank_after):
                out.append([BLANK_CELL])
            return out

        # Text-flow blocks (paragraph / heading / list_item / quote /
        # footnote / list-container / anything else) share one path: pick
        # the indent + blank-line framing for the kind, wrap, then apply
        # the block's effective alignment to every wrapped line.
        blank_before = blank_after = 0
        first_indent = opts.paragraph_indent
        cont_indent = 0
        if block_type == "heading":
            blank_before = opts.heading_blank_before
            blank_after = opts.heading_blank_after
            first_indent = cont_indent = 0
        elif block_type == "list_item":
            first_indent = 0
            cont_indent = opts.list_hanging_indent
        elif block_type == "quote":
            first_indent = cont_indent = opts.quote_indent
        elif block_type == "footnote":
            first_indent = 0
            cont_indent = opts.footnote_hanging_indent

        align = self._effective_align(block)
        if align is not None:
            # Centred / right text measures its padding from the line
            # width, so a first-line or hanging indent would only fight
            # it — drop both and let the alignment own the placement.
            first_indent = cont_indent = 0

        wrapped = self._wrap_block_cells(
            cells, first_indent=first_indent, cont_indent=cont_indent
        )
        if not wrapped or all(not line for line in wrapped):
            # Empty (or all-empty-line) text block — emit nothing, not the
            # framing blank lines a heading's blank_before / blank_after would
            # otherwise surface as stray blank rows. The wrapper returns [[]]
            # for an all-blank block (truthy), so the all-empty check is needed
            # too. Mirrors the score path's empty-block guard above.
            return out
        if align is not None:
            wrapped = [self._align_line(line, align) for line in wrapped]

        for _ in range(blank_before):
            out.append([BLANK_CELL])
        out.extend(wrapped)
        for _ in range(blank_after):
            out.append([BLANK_CELL])
        return out

    def _effective_align(self, block: BrailleBlock) -> str | None:
        """The alignment to apply to ``block``'s wrapped lines, or ``None``.

        A source-declared :attr:`BrailleBlock.align` always wins:
        ``"center"`` / ``"right"`` select that alignment, and any other
        explicit value (e.g. ``"left"``) suppresses the default centring,
        leaving the block flush left. Only when the source declares *no*
        alignment does a level-1 heading still centre by default
        (:attr:`LayoutOptions.heading_center_level_1`) — the historical
        behaviour, expressed as one alignment rule rather than a
        heading-only special case. Because source alignment is honoured
        for any block kind, a Word paragraph the author centred renders
        centred, a centred level-2 heading is centred too, and an
        explicitly left-aligned level-1 heading is *not* force-centred.
        """
        if block.align in ("center", "right"):
            return block.align
        if block.align is not None:
            # An explicit alignment we don't pad for (e.g. "left") still
            # counts as the source taking a position — honour it as
            # "flush left" rather than letting a level-1 heading fall
            # through to the default centring below.
            return None
        if (
            block.block_type == "heading"
            and block.heading_level == 1
            and self.options.heading_center_level_1
        ):
            return "center"
        return None

    def _align_line(
        self, line: list[BrailleCell], align: str
    ) -> list[BrailleCell]:
        """Left-pad one wrapped line so its content sits centred / right.

        Padding is blank cells measured from the configured line width.
        A line already at or past the width, an empty line, or one that's
        only blanks (a separator) is returned untouched so alignment never
        widens a line past ``line_width`` or shifts a blank spacer. ``align``
        is ``"center"`` or ``"right"``; any other value is a no-op.
        """
        width = self.options.line_width
        visible = len(line)
        if visible == 0 or visible >= width or all(c.is_blank for c in line):
            return line
        if align == "right":
            pad = width - visible
        elif align == "center":
            pad = (width - visible) // 2
        else:
            return line
        return [BLANK_CELL] * pad + line if pad > 0 else line

    # ---- wrapping -------------------------------------------------------

    def _wrap_block_cells(
        self,
        cells: list[BrailleCell],
        first_indent: int,
        cont_indent: int,
    ) -> list[list[BrailleCell]]:
        """Greedy fill at ``line_width``, breaking on blank cells.

        Word boundaries (blank cells) are the preferred break points —
        no hyphen. When a "word" (run of non-blank cells) doesn't fit
        even on a fresh line, the algorithm breaks at **atomic-group**
        boundaries inside the word: cells sharing the same non-None
        ``source_span`` form one atom (a hanzi syllable / a Latin
        first-letter prefix+letter / cells inside the same math structure
        all share a span), and a cell with ``source_span=None``
        (synthesised marker like ``number_sign`` / ``list_marker``)
        clings to its right-hand neighbour so it never floats off alone.
        Each non-blank break
        appends a continuation hyphen (default ⠤, dots 3-6) to the end
        of the broken line — see
        :attr:`LayoutOptions.continuation_hyphen` to override or
        disable.

        ``first_indent`` cells of leading blank are prepended to the
        first line; ``cont_indent`` to every continuation line.  A
        single atom wider than the line width is mid-atom split as a
        last resort (silent — restructuring the source is the
        user's job).
        """
        if not cells:
            return []
        opts = self.options
        if opts.line_width <= 0:
            # Defensive — a non-positive width would loop forever.
            return [list(cells)]

        hyphen_dots = opts.continuation_hyphen
        hyphen_cell: BrailleCell | None = (
            BrailleCell(dots=tuple(hyphen_dots), role="continuation_hyphen")
            if hyphen_dots
            else None
        )
        hyphen_width = 1 if hyphen_cell is not None else 0

        lines: list[list[BrailleCell]] = []
        cur: list[BrailleCell] = [BLANK_CELL] * first_indent
        cur_indent = first_indent
        # Column the rows of each open hang_open…hang_close region
        # (matrix / determinant / equation-system body) start at,
        # innermost last. A forced row break resumes at that column so
        # every row's opening delimiter lines up under the first row's.
        # While the stack is non-empty, WIDTH-overflow breaks continue at
        # ``hang_region_indent`` instead of ``cont_indent``.
        hang_cols: list[int] = []
        # Equation-system (cases) regions currently open, innermost last.
        cases_regions: list[_CasesRegion] = []

        def overflow_indent() -> int | None:
            """Continuation indent for a width-overflow break — the
            hang region's indent inside one, the block default (None)
            outside.

            «空两方» — the two-cell hang a Chinese-Braille matrix / cases
            row takes when it can't finish on one line — is measured from
            the row's own start column (the delimiter / brace column the
            region resumes rows in), NOT from the page margin: a row that
            spills continues two cells past where it began, so its overflow
            tail can't drift left of (or collide with) the delimiter when a
            stem or block indent has pushed the region rightward. Clamp
            below the width so a region opening near the right edge can't
            push the indent to the margin and stall the wrap."""
            if not hang_cols:
                return None
            return min(
                hang_cols[-1] + opts.hang_region_indent, opts.line_width - 1
            )

        def flush_line(
            *, with_hyphen: bool, next_indent: int | None = None
        ) -> None:
            """Close out ``cur``; the next line starts indented by
            ``next_indent`` (None → the block's ``cont_indent``)."""
            nonlocal cur, cur_indent
            # Strip trailing blanks for tidiness.
            while cur and cur[-1].is_blank:
                cur.pop()
            if with_hyphen and hyphen_cell is not None:
                cur.append(hyphen_cell)
            lines.append(cur)
            indent = cont_indent if next_indent is None else next_indent
            cur = [BLANK_CELL] * indent
            cur_indent = indent

        def place_atoms(atoms: list[list[BrailleCell]]) -> None:
            """Place a run of atoms ("one word") onto the output.

            Atoms are kept indivisible whenever possible; a hyphen is
            inserted at each non-blank break.  Falls back to mid-atom
            splitting only when a single atom alone exceeds the line
            width (the caller's source had no internal break point —
            the user sees it as a runaway line and can restructure).

            Iterative, not recursive: a single break-point-free word
            (e.g. a very long number) wraps across one line per atom-run,
            so on a long enough word the old tail-recursion blew Python's
            recursion limit (RecursionError) at the default line width.
            Each branch that used to recurse on a suffix of ``atoms`` now
            rebinds ``atoms`` and loops instead.
            """
            nonlocal cur
            # Walk ``atoms`` with an index cursor + a running total instead of
            # re-slicing the suffix and re-summing it every pass.  A word with
            # no break points places only a few atoms per line, so the old
            # ``atoms = atoms[placed:]`` + ``sum(len(a) for a in atoms)`` were
            # both O(n) per pass — O(n²) overall (a ~30k-cell unbroken run took
            # seconds).  ``start`` advances; ``remaining_total`` is kept equal
            # to ``sum(len(a) for a in atoms[start:])``.  The placement scan
            # iterates by index (not ``atoms[start:]``) so its early break
            # isn't preceded by a full-suffix slice copy.
            start = 0
            n_atoms = len(atoms)
            remaining_total = sum(len(a) for a in atoms)
            while start < n_atoms:
                remaining = opts.line_width - len(cur)
                if remaining_total <= remaining:
                    for k in range(start, n_atoms):
                        cur.extend(atoms[k])
                    return
                # Try a fresh line — that break is at a blank-equivalent
                # boundary (whatever preceded the word), no hyphen.
                if len(cur) > cur_indent:
                    flush_line(
                        with_hyphen=False, next_indent=overflow_indent()
                    )
                    remaining = opts.line_width - len(cur)
                    if remaining_total <= remaining:
                        for k in range(start, n_atoms):
                            cur.extend(atoms[k])
                        return
                # Multi-atom word still too wide — split between atoms
                # with hyphen.
                if n_atoms - start > 1:
                    slot = opts.line_width - len(cur) - hyphen_width
                    placed_len = 0
                    placed = 0
                    for k in range(start, n_atoms):
                        alen = len(atoms[k])
                        if placed_len + alen <= slot:
                            placed_len += alen
                            placed += 1
                        else:
                            break
                    if placed > 0:
                        for k in range(start, start + placed):
                            cur.extend(atoms[k])
                        flush_line(
                            with_hyphen=True, next_indent=overflow_indent()
                        )
                        start += placed
                        remaining_total -= placed_len
                        continue
                    # Even the first atom alone doesn't fit when we
                    # reserve a cell for the hyphen.  Before resorting
                    # to mid-atom split, check whether the atom would
                    # fit if we *omit* the hyphen reservation — the
                    # degenerate "atom exactly equals line_width" case.
                    # Omitting the hyphen breaks BANA strictly, but it
                    # is the lesser evil compared to slicing a syllable
                    # / first-letter-prefix that the user wanted whole.
                    slot_no_hyphen = opts.line_width - len(cur)
                    if len(atoms[start]) <= slot_no_hyphen:
                        cur.extend(atoms[start])
                        flush_line(
                            with_hyphen=False, next_indent=overflow_indent()
                        )
                        remaining_total -= len(atoms[start])
                        start += 1
                        continue
                    # First atom truly exceeds line_width — fall through
                    # to mid-atom split for it.
                # Mid-atom split — last resort.  Take as many cells as fit
                # (minus hyphen reservation), flush with hyphen, repeat.
                first = atoms[start]
                rest_cells = first
                while rest_cells:
                    slot = opts.line_width - len(cur) - hyphen_width
                    if slot <= 0:
                        # Not enough room for even one cell plus the
                        # hyphen reservation.  Drop the reservation first.
                        slot = opts.line_width - len(cur)
                        if slot <= 0:
                            # Still no room.  If the line already holds
                            # content, close it and retry on a fresh line.
                            if len(cur) > cur_indent:
                                flush_line(
                                    with_hyphen=False,
                                    next_indent=overflow_indent(),
                                )
                                continue
                            # Otherwise the continuation indent alone is
                            # >= line_width: flushing this indent-only
                            # line would emit a stray blank line (one per
                            # cell — the old double-flush bug), so force a
                            # single cell onto this over-wide line
                            # instead.  The width overflow is unavoidable
                            # when the indent exceeds it, but rest_cells
                            # still shrinks so we can't spin forever.
                            slot = 1
                        take, rest_cells = rest_cells[:slot], rest_cells[slot:]
                        cur.extend(take)
                        if rest_cells:
                            flush_line(
                                with_hyphen=False,
                                next_indent=overflow_indent(),
                            )
                        continue
                    take, rest_cells = rest_cells[:slot], rest_cells[slot:]
                    cur.extend(take)
                    if rest_cells:
                        flush_line(
                            with_hyphen=True, next_indent=overflow_indent()
                        )
                # atoms[start] is now fully placed; advance to the remainder
                # (start == n_atoms -> while exits) instead of recursing.
                remaining_total -= len(atoms[start])
                start += 1

        # --- atom-stream pass --------------------------------------
        pending_atom: list[BrailleCell] = []
        pending_marker: list[BrailleCell] = []  # source_span=None — cling right
        pending_word: list[list[BrailleCell]] = []
        current_span: Span | None = None

        def close_atom() -> None:
            nonlocal current_span
            if pending_atom:
                pending_word.append(list(pending_atom))
                pending_atom.clear()
            current_span = None

        def commit_word() -> None:
            """Push the accumulated atoms downstream as one word."""
            close_atom()
            if pending_marker:
                # No right-hand atom to cling to — degrade markers
                # into their own atom so they don't get dropped.
                pending_word.append(list(pending_marker))
                pending_marker.clear()
            if pending_word:
                place_atoms(list(pending_word))
                pending_word.clear()

        for idx, cell in enumerate(cells):
            if cell.role == "line_break":
                # Forced in-block break (matrix / equation-system row
                # boundary, bare ``\\``): flush whatever is pending and
                # start a fresh line — no continuation hyphen, the break
                # is content, not overflow. Inside a hang region the next
                # row resumes at the region's own start column so the row
                # delimiters line up; outside one it starts at the
                # block's indent (NOT the hang indent). Checked before
                # ``is_blank`` (the sentinel has no dots, so it IS
                # blank).
                commit_word()
                flush_line(
                    with_hyphen=False,
                    next_indent=hang_cols[-1] if hang_cols else None,
                )
                continue
            if cell.role == "hang_open":
                # Width-overflow continuations hang from here on
                # (matrix / equation-system body). Zero-width — commit
                # the preceding word at the old depth, print nothing.
                if not hang_cols and _region_is_multiline(cells, idx):
                    # A multi-row matrix / determinant / equation system
                    # opens its own line. Its rows only line up if the
                    # first one starts where the rest can follow, and the
                    # tail of a long stem leaves no such column. The
                    # operator still pending (the ``=`` ahead of the
                    # matrix) moves down with it: the break then falls on
                    # the blank BEFORE that operator — a break point that
                    # needs no line-end marker — and the first row's
                    # delimiter still opens the column the later rows
                    # align to.
                    if len(cur) > cur_indent:
                        flush_line(with_hyphen=False)
                commit_word()
                # The recorded column comes from content, not from a
                # configured indent, so it can sit at (or past) the right
                # edge — a stem word that exactly fills the line, or a page
                # narrow enough that the introducing operator alone reaches
                # the margin. Resuming rows there would push every one of
                # them over the width. No row can line up in a column that
                # holds nothing, so fall back to the indent the region's
                # overflow continuations already use.
                row_col = len(cur)
                if row_col >= opts.line_width:
                    row_col = min(opts.hang_region_indent, opts.line_width - 1)
                hang_cols.append(row_col)
                continue
            if cell.role == "hang_close":
                # Commit the table's last word while still inside the
                # region (its overflow continuation must hang too).
                commit_word()
                if hang_cols:
                    hang_cols.pop()
                continue
            if cell.role == "cases_open":
                # An equation system always spans multiple lines, so it
                # opens a line of its own (taking any pending introducing
                # operator down with it) exactly like a multi-row matrix.
                # It hangs like one too — the same hang_cols column drives
                # row alignment (line_break) and «空两方» overflow — but it
                # additionally records a cases region so the brace segments
                # get stamped per PHYSICAL line at close.
                if not hang_cols and len(cur) > cur_indent:
                    flush_line(with_hyphen=False)
                commit_word()
                gutter = len(cur)
                if gutter >= opts.line_width:
                    gutter = min(opts.hang_region_indent, opts.line_width - 1)
                hang_cols.append(gutter)
                cases_regions.append(
                    _CasesRegion(gutter=gutter, first_line=len(lines))
                )
                continue
            if cell.role == "cases_palette":
                # A brace-segment template — captured for the close-time
                # per-line stamp, never placed inline.
                if cases_regions:
                    cases_regions[-1].palette.append(cell)
                continue
            if cell.role == "cases_close":
                # Finish the system's last physical line, then re-stamp
                # every line of the region with the segment for its
                # physical position (top ⠎ / middle ⠇ / bottom ⠣) so the
                # bottom segment lands on the last braille line.
                commit_word()
                if len(cur) > cur_indent:
                    flush_line(with_hyphen=False)
                if hang_cols:
                    hang_cols.pop()
                if cases_regions:
                    _stamp_cases_segments(lines, cases_regions.pop())
                continue
            if cell.is_blank:
                commit_word()
                # Append the blank as a separator if there's content;
                # drop leading blanks at the start of a line.
                if len(cur) > cur_indent and len(cur) < opts.line_width:
                    cur.append(cell)
                continue
            if cell.source_span is None:
                # Synthesised marker — cling to the next non-None atom.
                close_atom()
                pending_marker.append(cell)
                continue
            if current_span is not None and cell.source_span == current_span:
                pending_atom.append(cell)
                continue
            # New atom starts — pending markers (if any) cling here.
            close_atom()
            pending_atom.extend(pending_marker)
            pending_marker.clear()
            pending_atom.append(cell)
            current_span = cell.source_span

        commit_word()
        if len(cur) > cur_indent or not lines:
            flush_line(with_hyphen=False)
        return lines

    # ---- encoding -------------------------------------------------------

    def _encode(self, lines: list[list[BrailleCell]]) -> str | bytes:
        # Split per-format to keep the joined value's element type
        # consistent — mixing str and bytes through the same locals
        # confuses both mypy and human readers.
        if self.format == "brf":
            return self._encode_brf(lines)
        return self._encode_unicode(lines)

    def _encode_brf(self, lines: list[list[BrailleCell]]) -> bytes:
        # BRF mandates CR/LF between lines and a form feed between pages.
        # The layout path fixes both per the spec — unlike the raw
        # BrfRenderer, whose line_terminator is configurable for the rare
        # reader that only accepts LF.  Publishing-grade output follows
        # the standard; a caller needing a non-standard terminator uses
        # BrfRenderer (no layout) instead.
        opts = self.options
        encoded = [
            "".join(cell_to_brf(c) for c in line).encode("ascii")
            for line in lines
        ]
        if opts.page_height is None or opts.page_height <= 0:
            return b"\r\n".join(encoded)
        # The page number is its OWN line, ADDED to each page (height + 1) —
        # it never shares or reflows a content line, so the page holds a
        # full ``page_height`` content lines.  Top vs bottom picks which end
        # the number line sits at; right vs left aligns it within that line.
        anchor_top = opts.page_number_position.startswith("top")
        align_right = opts.page_number_position.endswith("right")
        pages: list[bytes] = []
        for page_idx, start in enumerate(
            range(0, len(encoded), opts.page_height)
        ):
            page_lines = list(encoded[start : start + opts.page_height])
            if page_idx > 0:
                # A blank block separator at a page boundary must not open
                # the page with an empty line (the first page may legitimately
                # start with content of its own).
                _trim_leading_blank_lines(page_lines, b" ")
            if opts.show_page_numbers:
                num_line = _page_number_line(
                    _page_number_brf(page_idx + 1),
                    opts.line_width,
                    align_right=align_right,
                    blank=b" ",
                )
                page_lines = (
                    [num_line, *page_lines]
                    if anchor_top
                    else [*page_lines, num_line]
                )
            pages.append(b"\r\n".join(page_lines))
        return b"\f".join(pages)

    def _encode_unicode(self, lines: list[list[BrailleCell]]) -> str:
        opts = self.options
        encoded = [
            "".join(cell_to_char(c) for c in line) for line in lines
        ]
        if opts.page_height is None or opts.page_height <= 0:
            return "\n".join(encoded)
        # See :meth:`_encode_brf` — the page number is its own ADDED line
        # (height + 1), never sharing or reflowing a content line.
        anchor_top = opts.page_number_position.startswith("top")
        align_right = opts.page_number_position.endswith("right")
        pages: list[str] = []
        for page_idx, start in enumerate(
            range(0, len(encoded), opts.page_height)
        ):
            page_lines = list(encoded[start : start + opts.page_height])
            if page_idx > 0:
                # As in _encode_brf: don't open a page with a blank
                # separator line. Strip both a space and the U+2800 blank
                # braille pattern an all-blank cell line encodes to.
                _trim_leading_blank_lines(page_lines, " " + dots_to_char(()))
            if opts.show_page_numbers:
                num_line = _page_number_line(
                    _page_number_chars(page_idx + 1),
                    opts.line_width,
                    align_right=align_right,
                    blank=dots_to_char(()),
                )
                page_lines = (
                    [num_line, *page_lines]
                    if anchor_top
                    else [*page_lines, num_line]
                )
            pages.append("\n".join(page_lines))
        return "\f".join(pages)


def _region_is_multiline(cells: list[BrailleCell], open_idx: int) -> bool:
    """Does the hang region opened at ``open_idx`` span more than one
    display line — i.e. does it hold a forced row break before its
    matching ``hang_close``?

    A one-row matrix has nothing to align and reads as an ordinary
    bracketed expression, so it stays in the text flow; a multi-row one
    claims its own line (see the ``hang_open`` branch of
    :meth:`LayoutRenderer._wrap_block_cells`).  A break inside a NESTED
    region counts too: it still puts the outer region's rows on
    different lines.  An unclosed region (malformed cell stream) counts
    every break to its end.
    """
    depth = 0
    # Indexed, not sliced: a block with many matrices would otherwise
    # copy its tail once per region.
    for i in range(open_idx + 1, len(cells)):
        role = cells[i].role
        if role == "line_break":
            return True
        if role in ("hang_open", "cases_open"):
            depth += 1
        elif role in ("hang_close", "cases_close"):
            if depth == 0:
                return False
            depth -= 1
    return False


def _stamp_cases_segments(
    lines: list[list[BrailleCell]], region: _CasesRegion
) -> None:
    """Re-stamp the brace segment on every physical line of a finished
    equation-system region.

    The backend writes each equation with a per-equation segment
    PLACEHOLDER at the gutter (kept so the raw stream and the plain
    non-wrapping renderers stay correct); once wrapping is done, the true
    mapping is physical: the top segment (⠎) on the region's first braille
    line, the bottom (⠣ / c_126) on its last, the middle (⠇) on every line
    between — so a wrapped equation's continuation carries a middle
    segment and the bottom segment always lands on the last braille line.
    Each ``cases.*`` segment is a single cell (Chinese profile), so the
    palette is exactly ``[first, middle, last]``; on any other shape the
    per-equation placeholders are left as the best available fallback.
    """
    if len(region.palette) != 3:
        return
    first_seg, middle_seg, last_seg = region.palette
    region_lines = lines[region.first_line :]
    last = len(region_lines) - 1
    gutter = region.gutter
    for j, line in enumerate(region_lines):
        if j == 0:
            src = first_seg
        elif j == last:
            src = last_seg
        else:
            src = middle_seg
        # Build a FRESH visible delimiter cell per line (a distinct object
        # so a cell→position map can't alias the same segment across
        # lines; role ``math_delim`` like the placeholders and a matrix's
        # own delimiters; the region's source span carried through for
        # click-to-source). Overwrite the gutter cell — the placeholder
        # segment on an equation head, or an indent blank on a wrap
        # continuation. Guard the (degenerate) short line so the stamp
        # never runs off the end.
        seg = BrailleCell(
            dots=src.dots, role="math_delim", source_span=src.source_span
        )
        if gutter < len(line):
            line[gutter] = seg
        else:
            line.extend([BLANK_CELL] * (gutter - len(line)))
            line.append(seg)


def _trim_leading_blank_lines[LineT: (str, bytes)](
    page_lines: list[LineT], blank: LineT
) -> None:
    """Drop leading all-blank lines from a page, in place.

    A blank block separator (a heading's blank_before / inter-block
    spacing) that happens to land at the top of a new page would otherwise
    open that page with an empty braille line. BANA / Current Chinese
    Braille paginated output must not start a page blank — for a
    point-display / embosser user it reads as a spurious empty line at
    every affected page top. ``blank`` is the set of "empty" characters to
    strip (a space, plus the U+2800 blank-braille pattern for the Unicode
    encoding); an empty line strips to empty too.
    """
    while page_lines and not page_lines[0].strip(blank):
        page_lines.pop(0)


def _page_number_line[LineT: (str, bytes)](
    pn: LineT,
    line_width: int,
    *,
    align_right: bool,
    blank: LineT,
) -> LineT:
    """The page number on a line of its OWN — placed at the left or right
    edge of an otherwise-blank ``line_width``-cell line.

    Right-aligned: blanks then the number, so it ends at the right edge.
    Left-aligned: the number at column 0 (no trailing blanks — a braille
    line carries no meaning past its last cell).  The number is never
    dropped: on a line too narrow to hold it (pathological ``line_width <
    page_number_width``) it simply overflows.

    Generic over ``str`` (Unicode braille) and ``bytes`` (BRF); ``blank``
    is the one-cell blank in the matching type.
    """
    pad = line_width - len(pn)
    if pad <= 0:
        return pn
    return blank * pad + pn if align_right else pn


def _load() -> LayoutRenderer:
    return LayoutRenderer()
