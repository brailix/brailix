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

Pagination: if ``page_height`` is set, every ``page_height`` rendered
lines emit a form feed character. Blank-line separators count toward
the page budget. ``None`` (default) means single continuous output.
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

``top-X`` anchors the number on the page's first rendered line;
``bottom-X`` on the last.  ``-right`` right-aligns within the line
(BANA / Current Chinese Braille default — see
:data:`LayoutOptions.page_number_position`); ``-left`` left-aligns (some
National Common Braille templates prefer this).  The four positions form
the 2×2 grid the user picks from in Settings / toolbar.
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
from brailix.renderer._page_digits import (
    page_number_width as _page_number_width,
)

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
    # 《盲文常用数学符号》§17 规则1: a table row that doesn't fit on one
    # line continues on the next indented by two cells. Forced row
    # breaks (``line_break`` cells) still start at the block's own
    # indent — only overflow continuations hang.
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
    # Default ``single_line`` (BANA §24.1); ``bar_over_bar`` /
    # ``line_by_line`` fall back to single_line until their backend
    # support lands.  Per-line indent is the scheme's concern.
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
            # Don't wrap or indent — caller wants byte-for-byte.
            if cells:
                out.append(list(cells))
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
            if not wrapped:
                # Empty music block — emit nothing (no stray blank lines).
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

        A source-declared :attr:`BrailleBlock.align` (``"center"`` /
        ``"right"``) wins. Absent that, a level-1 heading still centres by
        default (:attr:`LayoutOptions.heading_center_level_1`) — the
        historical behaviour, now expressed as one alignment rule rather
        than a heading-only special case. Everything else stays flush left.
        Because source alignment is honoured for any block kind, a Word
        paragraph the author centred renders centred, and a centred level-2
        heading (which the default rule leaves alone) is centred too.
        """
        if block.align in ("center", "right"):
            return block.align
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
        # Depth of nested hang_open…hang_close regions (matrix /
        # equation-system bodies). While > 0, WIDTH-overflow breaks
        # continue at ``hang_region_indent`` instead of ``cont_indent``.
        hang_depth = 0

        def overflow_indent() -> int | None:
            """Continuation indent for a width-overflow break — the
            hang region's indent inside one, the block default (None)
            outside."""
            return opts.hang_region_indent if hang_depth > 0 else None

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
            """
            nonlocal cur
            if not atoms:
                return
            total = sum(len(a) for a in atoms)
            remaining = opts.line_width - len(cur)
            if total <= remaining:
                for atom in atoms:
                    cur.extend(atom)
                return
            # Try a fresh line — that break is at a blank-equivalent
            # boundary (whatever preceded the word), no hyphen.
            if len(cur) > cur_indent:
                flush_line(with_hyphen=False, next_indent=overflow_indent())
                remaining = opts.line_width - len(cur)
                if total <= remaining:
                    for atom in atoms:
                        cur.extend(atom)
                    return
            # Multi-atom word still too wide — split between atoms
            # with hyphen.
            if len(atoms) > 1:
                slot = opts.line_width - len(cur) - hyphen_width
                placed_len = 0
                placed = 0
                for atom in atoms:
                    if placed_len + len(atom) <= slot:
                        placed_len += len(atom)
                        placed += 1
                    else:
                        break
                if placed > 0:
                    for atom in atoms[:placed]:
                        cur.extend(atom)
                    flush_line(
                        with_hyphen=True, next_indent=overflow_indent()
                    )
                    place_atoms(atoms[placed:])
                    return
                # Even the first atom alone doesn't fit when we
                # reserve a cell for the hyphen.  Before resorting
                # to mid-atom split, check whether the atom would
                # fit if we *omit* the hyphen reservation — the
                # degenerate "atom exactly equals line_width" case.
                # Omitting the hyphen breaks BANA strictly, but it
                # is the lesser evil compared to slicing a syllable
                # / first-letter-prefix that the user wanted whole.
                slot_no_hyphen = opts.line_width - len(cur)
                if len(atoms[0]) <= slot_no_hyphen:
                    cur.extend(atoms[0])
                    flush_line(
                        with_hyphen=False, next_indent=overflow_indent()
                    )
                    place_atoms(atoms[1:])
                    return
                # First atom truly exceeds line_width — fall through
                # to mid-atom split for it.
            # Mid-atom split — last resort.  Take as many cells as fit
            # (minus hyphen reservation), flush with hyphen, repeat.
            first = atoms[0]
            rest_cells = first
            while rest_cells:
                slot = opts.line_width - len(cur) - hyphen_width
                if slot <= 0:
                    # Pathological — line_width too small for even one
                    # cell plus the hyphen reservation.  Drop the
                    # reservation on this slice so forward progress
                    # is guaranteed.
                    slot = opts.line_width - len(cur)
                    if slot <= 0:
                        flush_line(
                            with_hyphen=False, next_indent=overflow_indent()
                        )
                        slot = opts.line_width - len(cur)
                    # A continuation indent >= line_width leaves slot <= 0
                    # even on a fresh line; force at least one cell so
                    # rest_cells strictly shrinks and we can't spin forever
                    # (the line overflows width, which is unavoidable when
                    # the indent alone exceeds it).
                    slot = max(1, slot)
                    take, rest_cells = rest_cells[:slot], rest_cells[slot:]
                    cur.extend(take)
                    if rest_cells:
                        flush_line(
                            with_hyphen=False, next_indent=overflow_indent()
                        )
                    continue
                take, rest_cells = rest_cells[:slot], rest_cells[slot:]
                cur.extend(take)
                if rest_cells:
                    flush_line(
                        with_hyphen=True, next_indent=overflow_indent()
                    )
            if len(atoms) > 1:
                place_atoms(atoms[1:])

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

        for cell in cells:
            if cell.role == "line_break":
                # Forced in-block break (matrix / equation-system row
                # boundary, bare ``\\``): flush whatever is pending and
                # start a fresh line — no continuation hyphen, the break
                # is content, not overflow, so the next row starts at
                # the block's own indent (NOT the hang indent). Checked
                # before ``is_blank`` (the sentinel has no dots, so it
                # IS blank).
                commit_word()
                flush_line(with_hyphen=False)
                continue
            if cell.role == "hang_open":
                # Width-overflow continuations hang from here on
                # (matrix / equation-system body). Zero-width — commit
                # the preceding word at the old depth, print nothing.
                commit_word()
                hang_depth += 1
                continue
            if cell.role == "hang_close":
                # Commit the table's last word while still inside the
                # region (its overflow continuation must hang too).
                commit_word()
                hang_depth = max(0, hang_depth - 1)
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
        opts = self.options
        encoded = [
            "".join(cell_to_brf(c) for c in line).encode("ascii")
            for line in lines
        ]
        if opts.page_height is None or opts.page_height <= 0:
            return b"\r\n".join(encoded)
        # Top vs bottom decides which line the page number lands on;
        # right vs left is the alignment within that line.  Splitting
        # the two axes here keeps the four positions a 2×2 pick rather
        # than four bespoke branches.
        anchor_top = opts.page_number_position.startswith("top")
        align_right = opts.page_number_position.endswith("right")
        if opts.show_page_numbers:
            encoded = _reflow_page_anchor_lines(
                encoded,
                page_height=opts.page_height,
                line_width=opts.line_width,
                anchor_top=anchor_top,
                blank=b" ",
            )
        pages: list[bytes] = []
        for page_idx, start in enumerate(
            range(0, len(encoded), opts.page_height)
        ):
            page_lines = list(encoded[start : start + opts.page_height])
            if opts.show_page_numbers and page_lines:
                idx = 0 if anchor_top else len(page_lines) - 1
                page_lines[idx] = _apply_page_number_brf(
                    page_lines[idx],
                    page_idx + 1,
                    opts.line_width,
                    align_right=align_right,
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
        anchor_top = opts.page_number_position.startswith("top")
        align_right = opts.page_number_position.endswith("right")
        if opts.show_page_numbers:
            encoded = _reflow_page_anchor_lines(
                encoded,
                page_height=opts.page_height,
                line_width=opts.line_width,
                anchor_top=anchor_top,
                blank=dots_to_char(()),
            )
        pages: list[str] = []
        for page_idx, start in enumerate(
            range(0, len(encoded), opts.page_height)
        ):
            page_lines = list(encoded[start : start + opts.page_height])
            if opts.show_page_numbers and page_lines:
                idx = 0 if anchor_top else len(page_lines) - 1
                page_lines[idx] = _apply_page_number_unicode(
                    page_lines[idx],
                    page_idx + 1,
                    opts.line_width,
                    align_right=align_right,
                )
            pages.append("\n".join(page_lines))
        return "\f".join(pages)


def _reflow_page_anchor_lines[LineT: (str, bytes)](
    encoded: list[LineT],
    *,
    page_height: int,
    line_width: int,
    anchor_top: bool,
    blank: LineT,
) -> list[LineT]:
    """Make room for the page number on every page-anchor line — by
    moving cells, never by dropping them.

    The ``_apply_page_number_*`` helpers below can only pad or, as a
    last resort, truncate the anchor line — and truncation silently
    destroys braille content.  With greedy wrapping a full-width line
    is the *common* case (Chinese prose fills lines; right-aligned
    blocks are padded to exactly the width), so every page top could
    lose its trailing cells.  This pass runs before pagination and
    guarantees each anchor line fits within
    ``line_width - page_number_width - 1``:

    * a line that fits once its edge blanks are stripped is just
      stripped — alignment padding is given up, content cells are not;
    * otherwise the line is split (at the last word boundary that
      fits, else hard at the limit) and the remainder is inserted as
      the following line, pushing the rest of the document down.  Page
      boundaries and page-number widths are recomputed as the walk
      advances, so the page count may grow — that is the point: paper
      is spent, content is kept.

    Generic over ``str`` (Unicode braille) and ``bytes`` (BRF);
    ``blank`` is the one-cell blank in the matching type.
    """
    out: list[LineT] = list(encoded)
    pos = 0
    page_num = 0
    while pos < len(out):
        page_num += 1
        pn_w = _page_number_width(page_num)
        avail = line_width - pn_w - 1
        anchor = pos if anchor_top else min(pos + page_height, len(out)) - 1
        if avail >= 0 and anchor < len(out) and len(out[anchor]) > avail:
            line = out[anchor].strip(blank)
            if len(line) <= avail:
                out[anchor] = line
            else:
                cut = line.rfind(blank, 0, avail + 1) if avail else -1
                if cut <= 0:
                    head, tail = line[:avail], line[avail:]
                else:
                    head, tail = line[:cut], line[cut + 1 :]
                out[anchor] = head
                out.insert(anchor + 1, tail)
        pos += page_height
    return out


def _apply_page_number_unicode(
    target_line: str,
    page_num: int,
    line_width: int,
    *,
    align_right: bool = True,
) -> str:
    """Inject the page number into ``target_line`` (Unicode flavour).

    Three cases depending on how much room the content left:

    * **Fits with a blank-cell gap** (content + 1 blank + page_no <=
      line_width): pad the content with blank braille cells so the
      whole row reaches ``line_width`` with the page number anchored
      at the chosen edge.  BANA layout — content on one side, page
      number on the opposite end, gap of at least one blank cell.
    * **Page number alone fits the line** but content collides:
      truncate ``target_line`` from the colliding edge (tail when
      right-aligned, head when left-aligned) and stitch the page
      number in.  Backstop only: the renderer runs
      :func:`_reflow_page_anchor_lines` first, which re-flows anchor
      lines so this branch is unreachable from ``render()`` — it keeps
      a well-defined (lossy) behaviour for direct callers.
    * **Page number wider than the whole line**: replace the entire
      line with the page number.  Pathological edge case
      (line_width < page_no_width), still well-defined.

    ``align_right=False`` mirrors the layout horizontally — page
    number flush left, content flush right.
    """
    blank = dots_to_char(())
    pn = _page_number_chars(page_num)
    pn_w = _page_number_width(page_num)
    if pn_w >= line_width:
        return pn[-line_width:] if pn_w > line_width else pn
    avail = line_width - pn_w - 1
    if len(target_line) <= avail:
        padding = blank * (line_width - len(target_line) - pn_w)
        if align_right:
            return target_line + padding + pn
        return pn + padding + target_line
    # Collision — give up cells from the side the page number sits on.
    # ``avail`` can be 0 (page number + gap exactly fills the line); guard
    # the left-aligned tail because ``target_line[-0:]`` is the whole line,
    # not an empty slice, which would overflow the width.
    if align_right:
        return target_line[:avail] + blank + pn
    return pn + blank + (target_line[-avail:] if avail else target_line[:0])


def _apply_page_number_brf(
    target_line: bytes,
    page_num: int,
    line_width: int,
    *,
    align_right: bool = True,
) -> bytes:
    """ASCII / NABCC equivalent of :func:`_apply_page_number_unicode`.

    BRF blanks are encoded as ASCII space (0x20) per the NABCC table.
    Same three branches; structure mirrored so a future BANA-template
    refactor can keep them in lock-step.
    """
    pn = _page_number_brf(page_num)
    pn_w = _page_number_width(page_num)
    if pn_w >= line_width:
        return pn[-line_width:] if pn_w > line_width else pn
    avail = line_width - pn_w - 1
    if len(target_line) <= avail:
        padding = b" " * (line_width - len(target_line) - pn_w)
        if align_right:
            return target_line + padding + pn
        return pn + padding + target_line
    if align_right:
        return target_line[:avail] + b" " + pn
    # ``avail == 0`` guard: ``target_line[-0:]`` is the whole line, not empty.
    return pn + b" " + (target_line[-avail:] if avail else target_line[:0])


def _load() -> LayoutRenderer:
    return LayoutRenderer()
