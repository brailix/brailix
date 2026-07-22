"""Tests for :mod:`brailix.renderer.layout`.

The layout renderer's job: wrap cells at a configurable cell width,
apply per-block indent rules, and (optionally) paginate. Tests here
exercise each rule in isolation rather than asserting on full braille
glyph output — that's covered by golden tests downstream."""


from brailix.core.span import Span
from brailix.ir.braille import (
    BLANK_CELL,
    HANG_CLOSE_CELL,
    HANG_OPEN_CELL,
    LINE_BREAK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.renderer.layout import LayoutOptions, LayoutRenderer
from brailix.renderer.unicode_braille import dots_to_char


def _word(n: int) -> list[BrailleCell]:
    """Build an ``n``-cell "word" — every cell has dot 1 (uniform, no
    blanks). Distinct from BLANK_CELL so the wrapper treats them as
    content rather than separators."""
    return [BrailleCell(dots=(1,)) for _ in range(n)]


def _atom(n: int, span_start: int) -> list[BrailleCell]:
    """Build an ``n``-cell atomic group — every cell shares the same
    ``source_span``, so the wrapper treats them as indivisible."""
    span = Span(span_start, span_start + 1)
    return [BrailleCell(dots=(1,), source_span=span) for _ in range(n)]


def _hyphen_char() -> str:
    """Unicode glyph for the default continuation hyphen (dots 3-6)."""
    return dots_to_char((3, 6))


def _seq(*words_or_blanks):
    """Compose a sequence: ints are blank cells, lists are words."""
    out: list[BrailleCell] = []
    for piece in words_or_blanks:
        if isinstance(piece, int):
            out.extend([BLANK_CELL] * piece)
        else:
            out.extend(piece)
    return BrailleSequence(cells=out)


class TestWordWrapping:
    def test_short_line_passes_through(self):
        seq = _seq(_word(3))
        out = LayoutRenderer(options=LayoutOptions(line_width=40)).render(seq)
        assert "\n" not in out

    def test_wrap_breaks_at_word_boundary(self):
        # Three 5-cell words with single blanks: "AAAAA BBBBB CCCCC".
        # With line_width=10 and no indent, only the first word fits
        # before the wrap; the next word starts on a new line.
        seq = _seq(_word(5), 1, _word(5), 1, _word(5))
        out = LayoutRenderer(options=LayoutOptions(line_width=10, paragraph_indent=0)).render(
            BrailleDocument(blocks=[BrailleBlock(cells=seq.cells)])
        )
        lines = out.split("\n")
        # Three short words → three lines (each is one 5-cell word).
        assert len(lines) == 3
        for line in lines:
            assert len(line) <= 10

    def test_oversized_word_is_split_in_middle(self):
        # 30-cell single word (all cells share ``source_span=None`` so
        # they merge into one atom) with width=10 → mid-atom split
        # with a continuation hyphen at each break.  Lines: 9 + ⠤,
        # 9 + ⠤, 9 + ⠤, 3 — the source 30 cells are all preserved,
        # plus 3 hyphens at break points.
        seq = _seq(_word(30))
        out = LayoutRenderer(options=LayoutOptions(line_width=10, paragraph_indent=0)).render(
            BrailleDocument(blocks=[BrailleBlock(cells=seq.cells)])
        )
        lines = out.split("\n")
        assert all(len(line) <= 10 for line in lines)
        # All 30 source cells preserved (dot-1 glyph appears 30 times
        # across all lines; the ⠤ hyphen does not collide with it).
        dot1 = dots_to_char((1,))
        assert sum(line.count(dot1) for line in lines) == 30

    def test_oversized_word_split_silent_when_hyphen_disabled(self):
        # Same shape as above but with ``continuation_hyphen=None`` —
        # restores the legacy "split into N equal pieces, no hyphen"
        # behaviour for callers that opted out.
        seq = _seq(_word(30))
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, paragraph_indent=0, continuation_hyphen=None,
        )).render(
            BrailleDocument(blocks=[BrailleBlock(cells=seq.cells)])
        )
        lines = out.split("\n")
        assert all(len(line) <= 10 for line in lines)
        assert sum(len(line) for line in lines) == 30


class TestForcedLineBreak:
    """LINE_BREAK_CELL — the in-block forced break matrix /
    equation-system rows and bare ``\\`` emit. The wrapper flushes the
    pending line (no continuation hyphen) and the sentinel itself never
    prints."""

    def test_break_splits_line_even_when_width_remains(self):
        seq = _seq(_word(3))
        seq.cells.append(LINE_BREAK_CELL)
        seq.cells.extend(_word(3))
        out = LayoutRenderer(options=LayoutOptions(line_width=40)).render(seq)
        lines = out.split("\n")
        assert len(lines) == 2
        assert all(len(line) == 3 for line in lines)

    def test_break_adds_no_continuation_hyphen(self):
        seq = _seq(_word(3))
        seq.cells.append(LINE_BREAK_CELL)
        seq.cells.extend(_word(3))
        out = LayoutRenderer(options=LayoutOptions(line_width=40)).render(seq)
        assert _hyphen_char() not in out

    def test_continuation_lines_use_block_cont_indent(self):
        # In a list item (hanging indent 2) a forced break starts the
        # next row at the continuation indent, like any wrapped line.
        cells = _word(3) + [LINE_BREAK_CELL] + _word(3)
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="list_item", cells=cells),
        ])
        out = LayoutRenderer(
            options=LayoutOptions(line_width=40, list_hanging_indent=2)
        ).render(doc)
        first, second = out.split("\n")
        assert len(first) == 3
        assert second == dots_to_char(()) * 2 + dots_to_char((1,)) * 3

    def test_trailing_blank_before_break_is_stripped(self):
        # ``…⠀<break>…`` must not leave a dangling blank at line end.
        cells = _word(2) + [BLANK_CELL, LINE_BREAK_CELL] + _word(2)
        out = LayoutRenderer(options=LayoutOptions(line_width=40)).render(
            BrailleSequence(cells=cells)
        )
        first, second = out.split("\n")
        assert len(first) == 2
        assert len(second) == 2


class TestHangRegion:
    """hang_open … hang_close — the math backend brackets every matrix /
    determinant / equation system in these zero-width sentinels. WIDTH
    overflow inside the region continues with ``hang_region_indent``
    (a row too wide to fit continues two cells in on the next line);
    anything outside the region keeps the block's own indent. FORCED row
    breaks resume in the region's own column — see
    :class:`TestMatrixRowAlignment`."""

    @staticmethod
    def _render(cells, width=10):
        opts = LayoutOptions(line_width=width, paragraph_indent=0)
        return LayoutRenderer(options=opts).render(
            BrailleDocument(blocks=[BrailleBlock(cells=cells)])
        )

    def test_overflow_inside_region_hangs_two_cells(self):
        # One 16-cell "row" of 8 + 8 with a blank between, width 10:
        # the second word overflows → continuation indented 2 blanks.
        cells = (
            [HANG_OPEN_CELL]
            + _word(8) + [BLANK_CELL] + _word(8)
            + [HANG_CLOSE_CELL]
        )
        first, second = self._render(cells).split("\n")
        assert len(first) == 8
        assert second == dots_to_char(()) * 2 + dots_to_char((1,)) * 8

    def test_forced_row_break_inside_region_does_not_hang(self):
        # Row boundary (LINE_BREAK_CELL) starts the next ROW in the
        # region's own column — here the margin, since the region opens
        # the block — never at the overflow hanging indent.
        cells = (
            [HANG_OPEN_CELL]
            + _word(3) + [LINE_BREAK_CELL] + _word(3)
            + [HANG_CLOSE_CELL]
        )
        first, second = self._render(cells).split("\n")
        assert len(first) == 3
        assert second == dots_to_char((1,)) * 3  # no leading blanks

    def test_overflow_after_region_uses_block_indent(self):
        # Trailing prose past hang_close wraps at the block indent again.
        cells = (
            [HANG_OPEN_CELL] + _word(4) + [HANG_CLOSE_CELL]
            + [BLANK_CELL] + _word(8) + [BLANK_CELL] + _word(8)
        )
        lines = self._render(cells).split("\n")
        # 4 + blank + 8 won't fit in 10 → wrap; 8 + blank + 8 → wrap.
        assert len(lines) == 3
        for line in lines[1:]:
            assert not line.startswith(dots_to_char(()))

    def test_last_row_word_committed_at_close_still_hangs(self):
        # The word pending when hang_close arrives belongs to the last
        # row — its overflow continuation must hang.
        cells = (
            [HANG_OPEN_CELL]
            + _word(8) + [BLANK_CELL] + _word(8)
            + [HANG_CLOSE_CELL]
            + [BLANK_CELL] + _word(2)
        )
        lines = self._render(cells).split("\n")
        assert lines[1].startswith(dots_to_char(()) * 2)

    def test_nested_regions_keep_hanging_until_outer_close(self):
        # Block matrix: inner region closes, outer still open → still
        # hangs.
        cells = (
            [HANG_OPEN_CELL, HANG_OPEN_CELL]
            + _word(4)
            + [HANG_CLOSE_CELL]
            + [BLANK_CELL] + _word(8) + [BLANK_CELL] + _word(8)
            + [HANG_CLOSE_CELL]
        )
        lines = self._render(cells).split("\n")
        assert len(lines) >= 2
        for line in lines[1:]:
            assert line.startswith(dots_to_char(()) * 2)

    def test_sentinels_never_print(self):
        cells = [HANG_OPEN_CELL] + _word(3) + [HANG_CLOSE_CELL]
        out = self._render(cells)
        assert out == dots_to_char((1,)) * 3


class TestMatrixRowAlignment:
    """Chinese math braille (§17 规则1) gives every row of a matrix /
    determinant / equation system its own line, each opened by the row
    delimiter — which only reads as a column if the rows all start in
    one. So a MULTI-row hang region opens a line of its own, takes the
    operator that introduces it (the ``=`` of ``A = <matrix>``) down with
    it, and every row after the first resumes in the first row's column.
    A single-row region has nothing to line up and stays in the text
    flow."""

    _BLANK = dots_to_char(())
    _DOT1 = dots_to_char((1,))

    @staticmethod
    def _render(cells, width=20, indent=0):
        opts = LayoutOptions(line_width=width, paragraph_indent=indent)
        return LayoutRenderer(options=opts).render(
            BrailleDocument(blocks=[BrailleBlock(cells=cells)])
        )

    @staticmethod
    def _rows(row_widths):
        """A hang region of ``len(row_widths)`` rows."""
        cells = [HANG_OPEN_CELL]
        for i, w in enumerate(row_widths):
            if i:
                cells.append(LINE_BREAK_CELL)
            cells.extend(_word(w))
        cells.append(HANG_CLOSE_CELL)
        return cells

    def test_multirow_region_opens_a_new_line(self):
        # "stem ␣ = <2-row matrix>": the stem line ends at the stem.
        cells = _word(4) + [BLANK_CELL] + _word(1) + self._rows([3, 3])
        lines = self._render(cells).split("\n")
        assert lines[0] == self._DOT1 * 4

    def test_introducing_operator_moves_down_with_the_region(self):
        # The ``=`` pending when the region opens goes to the head of the
        # matrix's line, not the tail of the stem's — the break lands on
        # the blank before it, so no line-end marker is due.
        cells = _word(4) + [BLANK_CELL] + _word(1) + self._rows([3, 3])
        lines = self._render(cells).split("\n")
        assert lines[1] == self._DOT1 * 4  # "=" + row 1's 3 cells
        assert _hyphen_char() not in "".join(lines)

    def test_later_rows_resume_in_the_first_rows_column(self):
        # Row 1 opens one cell in (past the ``=``); row 2 lines up there.
        cells = _word(4) + [BLANK_CELL] + _word(1) + self._rows([3, 3])
        lines = self._render(cells).split("\n")
        assert lines[2] == self._BLANK + self._DOT1 * 3

    def test_region_opening_the_block_adds_no_blank_line(self):
        # Nothing precedes it → nothing to flush; the rows keep the
        # block's own first-line indent as their column.
        lines = self._render(self._rows([3, 3]), indent=2).split("\n")
        assert lines == [
            self._BLANK * 2 + self._DOT1 * 3,
            self._BLANK * 2 + self._DOT1 * 3,
        ]

    def test_single_row_region_stays_in_the_text_flow(self):
        cells = _word(4) + [BLANK_CELL] + _word(1) + self._rows([3])
        assert "\n" not in self._render(cells)

    def test_overflow_hangs_while_the_row_break_aligns(self):
        # The two continuation kinds are distinct: a row too wide to fit
        # continues at ``hang_region_indent`` (§17 规则1 「空两方」,
        # measured from the margin), while the next ROW resumes in the
        # region's column — one cell in, past the ``=``.
        cells = (
            _word(1)                                # the introducing "="
            + [HANG_OPEN_CELL]
            + _word(6) + [BLANK_CELL] + _word(6)    # row 1 — over width 10
            + [LINE_BREAK_CELL]
            + _word(3)                              # row 2
            + [HANG_CLOSE_CELL]
        )
        lines = self._render(cells, width=10).split("\n")
        assert lines == [
            self._DOT1 * 7,                          # "=" + first word
            self._BLANK * 2 + self._DOT1 * 6,        # overflow → hangs 2
            self._BLANK + self._DOT1 * 3,            # row 2 → aligns at 1
        ]


class TestParagraphIndent:
    def test_default_indent_is_two_cells(self):
        seq = BrailleDocument(blocks=[BrailleBlock(cells=_word(5))])
        out = LayoutRenderer().render(seq)
        # First line begins with two blank cells then the word.
        assert out.startswith(dots_to_char(()) * 2)

    def test_custom_indent_is_applied_to_first_line_only(self):
        seq = _seq(_word(5), 1, _word(5))
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, paragraph_indent=3
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=seq.cells)]))
        lines = out.split("\n")
        # First line: 3 blanks + first 5-cell word → 8 cells.
        assert len(lines[0]) == 8
        # Continuation line: just the next word, no indent.
        assert len(lines[1]) == 5


class TestBlockTypes:
    def test_heading_gets_blank_line_around(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="heading", cells=_word(3)),
        ])
        out = LayoutRenderer().render(doc)
        # Blank line above + content + blank line below — three lines total
        # (the trailing blank does count).
        lines = out.split("\n")
        assert len(lines) >= 3
        # First and last lines are blank (only the BLANK_CELL char).
        assert lines[0] == dots_to_char(())
        assert lines[-1] == dots_to_char(())

    def test_heading_level_1_is_centered(self):
        # 5-cell heading in a 21-cell line should be centered with
        # (21 - 5) // 2 = 8 leading blanks.
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="heading", heading_level=1, cells=_word(5)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        lines = out.split("\n")
        # The non-blank line should start with the centering padding.
        content_lines = [ln for ln in lines if any(c != dots_to_char(()) for c in ln)]
        assert content_lines
        assert content_lines[0].startswith(dots_to_char(()) * 8)

    def test_heading_level_2_stays_flush_left(self):
        # Deeper headings keep the visual hierarchy by NOT being centered.
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="heading", heading_level=2, cells=_word(5)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        lines = out.split("\n")
        content_lines = [ln for ln in lines if any(c != dots_to_char(()) for c in ln)]
        # First content character is the word's first cell, not padding.
        assert content_lines
        assert not content_lines[0].startswith(dots_to_char(()))

    def test_heading_without_level_is_not_centered(self):
        # Backward compatibility: a Heading the backend produced without
        # setting heading_level (heading_level=None) gets blank lines
        # but no centering — the layout doesn't take a position on
        # something the source IR didn't declare.
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="heading", cells=_word(5)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        lines = out.split("\n")
        content_lines = [ln for ln in lines if any(c != dots_to_char(()) for c in ln)]
        assert content_lines
        assert not content_lines[0].startswith(dots_to_char(()))

    def test_list_item_uses_hanging_indent(self):
        # Two-word list item; line_width forces a wrap. The second
        # line should start with the list hanging indent, not the
        # paragraph indent.
        cells = _word(8) + [BLANK_CELL] + _word(8)
        doc = BrailleDocument(blocks=[BrailleBlock(block_type="list_item", cells=cells)])
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, list_hanging_indent=2
        )).render(doc)
        lines = out.split("\n")
        # First line: no indent, second line: 2-cell indent.
        assert not lines[0].startswith(dots_to_char(()))
        assert lines[1].startswith(dots_to_char(()) * 2)

    def test_quote_indents_every_line(self):
        cells = _word(8) + [BLANK_CELL] + _word(8)
        doc = BrailleDocument(blocks=[BrailleBlock(block_type="quote", cells=cells)])
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, quote_indent=2
        )).render(doc)
        lines = out.split("\n")
        # Both lines start with the quote indent.
        for line in lines:
            assert line.startswith(dots_to_char(()) * 2)

    def test_code_block_is_verbatim(self):
        # A 50-cell run; with line_width=10 a normal block would wrap.
        # code_block must NOT wrap (verbatim by convention).
        cells = _word(50)
        doc = BrailleDocument(blocks=[BrailleBlock(block_type="code_block", cells=cells)])
        out = LayoutRenderer(options=LayoutOptions(line_width=10)).render(doc)
        assert "\n" not in out
        assert len(out) == 50

    def test_table_row_is_verbatim(self):
        cells = _word(20)
        doc = BrailleDocument(blocks=[BrailleBlock(block_type="table_row", cells=cells)])
        out = LayoutRenderer(options=LayoutOptions(line_width=10)).render(doc)
        assert "\n" not in out

    def test_verbatim_table_cell_keeps_matrix_row_breaks(self):
        # A matrix in a table cell: a hang region with two rows separated by a
        # LINE_BREAK_CELL. Verbatim skips *width* wrapping but must still honour
        # the hard structural break — the line break becomes a real newline and
        # the zero-width hang sentinels vanish. (Old code encoded all three
        # sentinels as blank cells, collapsing the matrix onto one line in the
        # only production export path.)
        cells = (
            [HANG_OPEN_CELL]
            + _word(3) + [LINE_BREAK_CELL] + _word(3)
            + [HANG_CLOSE_CELL]
        )
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="table_row", cells=cells)
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=80)).render(doc)
        lines = out.split("\n")
        # Two rows, each exactly its 3 content cells — no stray blank from the
        # dropped hang / line-break sentinels.
        assert len(lines) == 2
        assert all(len(ln) == 3 for ln in lines)

    def test_verbatim_table_cell_keeps_matrix_row_breaks_brf(self):
        # Same structure through the BRF encoder: rows separated by CRLF, not
        # collapsed onto one line with stray 0x20 spaces from the sentinels.
        cells = (
            [HANG_OPEN_CELL]
            + _word(3) + [LINE_BREAK_CELL] + _word(3)
            + [HANG_CLOSE_CELL]
        )
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="table_row", cells=cells)
        ])
        out = LayoutRenderer(
            options=LayoutOptions(line_width=80), format="brf"
        ).render(doc)
        rows = out.split(b"\r\n")
        assert len(rows) == 2
        assert all(len(r) == 3 for r in rows)


class TestBlockAlignment:
    """A source-declared ``BrailleBlock.align`` centres / right-aligns every
    wrapped line, independent of block kind, and overrides the block's normal
    first-line / hanging indent."""

    def _content_lines(self, out: str) -> list[str]:
        blank = dots_to_char(())
        return [ln for ln in out.split("\n") if any(c != blank for c in ln)]

    def test_centered_paragraph_pads_content(self):
        # 5-cell paragraph, width 21 → (21-5)//2 = 8 leading blanks, and
        # the paragraph's usual 2-cell first-line indent is dropped.
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="paragraph", align="center", cells=_word(5)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        content = self._content_lines(out)
        assert len(content) == 1
        assert content[0] == dots_to_char(()) * 8 + dots_to_char((1,)) * 5

    def test_right_aligned_paragraph_pads_content(self):
        # 5-cell paragraph, width 21 → flush right = 16 leading blanks.
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="paragraph", align="right", cells=_word(5)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        content = self._content_lines(out)
        assert len(content) == 1
        assert content[0] == dots_to_char(()) * 16 + dots_to_char((1,)) * 5

    def test_center_pads_every_wrapped_line(self):
        # Two 5-cell words, width 10 → each word wraps to its own line and
        # each is centred independently: (10-5)//2 = 2 leading blanks.
        seq = _seq(_word(5), 1, _word(5))
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="paragraph", align="center", cells=seq.cells),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=10)).render(doc)
        content = self._content_lines(out)
        assert len(content) == 2
        for ln in content:
            assert ln == dots_to_char(()) * 2 + dots_to_char((1,)) * 5

    def test_centered_level_2_heading_via_source_align(self):
        # The default rule centres only level-1 headings; an explicit source
        # align centres a level-2 heading too (8 leading blanks at width 21).
        doc = BrailleDocument(blocks=[
            BrailleBlock(
                block_type="heading", heading_level=2,
                align="center", cells=_word(5),
            ),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        content = self._content_lines(out)
        assert content
        assert content[0].startswith(dots_to_char(()) * 8)

    def test_explicit_left_align_overrides_heading_default_centering(self):
        # A level-1 heading centres by default, but an explicit source
        # align of "left" must suppress that — the author asked for flush
        # left, and any explicit alignment wins over the heading default.
        doc = BrailleDocument(blocks=[
            BrailleBlock(
                block_type="heading", heading_level=1,
                align="left", cells=_word(5),
            ),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=21)).render(doc)
        content = self._content_lines(out)
        assert content
        # First content cell is the word itself, not centring padding.
        assert not content[0].startswith(dots_to_char(()))

    def test_content_at_line_width_is_not_padded(self):
        # A line already filling the width gets no padding (never widened
        # past line_width).
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="paragraph", align="center", cells=_word(10)),
        ])
        out = LayoutRenderer(options=LayoutOptions(line_width=10)).render(doc)
        content = self._content_lines(out)
        assert content == [dots_to_char((1,)) * 10]


class TestPagination:
    def test_form_feed_inserted_at_page_height(self):
        # 5 single-line blocks, page_height=2 → 3 pages, 2 form feeds.
        blocks = [BrailleBlock(cells=_word(3)) for _ in range(5)]
        doc = BrailleDocument(blocks=blocks)
        out = LayoutRenderer(options=LayoutOptions(
            line_width=40, page_height=2
        )).render(doc)
        assert out.count("\f") == 2

    def test_no_pagination_when_height_unset(self):
        blocks = [BrailleBlock(cells=_word(3)) for _ in range(5)]
        doc = BrailleDocument(blocks=blocks)
        out = LayoutRenderer().render(doc)
        assert "\f" not in out

    def test_page_does_not_open_with_blank_separator(self):
        # Two paragraphs, a heading, a paragraph: the heading's blank_before
        # / blank_after framing lands at page tops (page_height=2). A page
        # must not open with a blank braille line.
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=_word(3)),
            BrailleBlock(cells=_word(3)),
            BrailleBlock(block_type="heading", cells=_word(3)),
            BrailleBlock(cells=_word(3)),
        ])
        out = LayoutRenderer(options=LayoutOptions(
            line_width=40, paragraph_indent=0, page_height=2,
        )).render(doc)
        pages = out.split("\f")
        assert len(pages) >= 3
        blank = dots_to_char(())
        for i, page in enumerate(pages):
            if i == 0 or page == "":
                continue
            first_line = page.split("\n")[0]
            assert first_line.strip(" " + blank) != "", (
                f"page {i} opens with a blank line: {page!r}"
            )


class TestBrfFormat:
    def test_brf_returns_bytes_with_crlf(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleBlock(cells=[BrailleCell(dots=(1, 2))]),
        ])
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0
        )).render(doc)
        assert isinstance(out, bytes)
        assert b"\r\n" in out

    def test_brf_pagination_uses_form_feed_byte(self):
        blocks = [BrailleBlock(cells=[BrailleCell(dots=(1,))]) for _ in range(4)]
        doc = BrailleDocument(blocks=blocks)
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0, page_height=2
        )).render(doc)
        assert out.count(b"\f") == 1


class TestFootnoteIndent:
    """Footnote blocks use a hanging indent: first line flush left,
    continuation lines indented by ``footnote_hanging_indent``."""

    def test_footnote_continuation_uses_hanging_indent(self):
        # Two-word footnote body wide enough to force a wrap.
        cells = _word(8) + [BLANK_CELL] + _word(8)
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="footnote", cells=cells),
        ])
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, footnote_hanging_indent=2,
        )).render(doc)
        lines = out.split("\n")
        # First line flush left (no leading blank cell), second line
        # starts with 2 cells of indent.
        assert not lines[0].startswith(dots_to_char(()))
        assert lines[1].startswith(dots_to_char(()) * 2)


class TestWrapEdgeCases:
    """Defensive paths in the wrapping engine — easy to forget, expensive
    when broken."""

    def test_empty_cells_produce_no_lines(self):
        # An empty BrailleBlock (no cells) must yield nothing — not
        # an empty line, not a crash. Layout doesn't fabricate output.
        doc = BrailleDocument(blocks=[BrailleBlock(cells=[])])
        out = LayoutRenderer(options=LayoutOptions(paragraph_indent=0)).render(doc)
        assert out == ""

    def test_empty_heading_emits_nothing_not_framing_blanks(self):
        # A heading frames its content with a blank line before and after.
        # An EMPTY heading must still yield nothing — not two stray blank
        # rows from that framing. (The score path guarded this; the text
        # path did not, so an empty heading rendered as "⠀\n⠀".)
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="heading", cells=[]),
        ])
        out = LayoutRenderer().render(doc)  # defaults frame headings
        assert out == ""

    def test_degenerate_verbatim_block_emits_no_stray_blank(self):
        # A verbatim block whose cells are only hang_open / hang_close
        # sentinels (no content, no line_break) used to leak one empty row;
        # the all-empty guard now suppresses it, matching the text-flow /
        # score paths.
        cells = [
            BrailleCell(dots=(), role="hang_open"),
            BrailleCell(dots=(), role="hang_close"),
        ]
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="code_block", cells=cells),
        ])
        out = LayoutRenderer(
            options=LayoutOptions(paragraph_indent=0)
        ).render(doc)
        assert out == ""

    def test_degenerate_score_block_emits_no_stray_blanks(self):
        # rl-1: a score block with only separator cells (no real measures)
        # wraps to [[]] (one empty line) — truthy, so the empty-block guard's
        # all-empty check is what stops its framing blanks + that empty line
        # from leaking in as stray rows.
        sep = BrailleCell(dots=(), role="music_measure_sep")
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="score", cells=[sep, sep]),
        ])
        out = LayoutRenderer(
            options=LayoutOptions(score_blank_before=1, score_blank_after=1)
        ).render(doc)
        assert out == ""

    def test_non_positive_line_width_emits_single_line(self):
        # A non-positive ``line_width`` would loop forever in a naive
        # greedy wrap; the defensive branch returns the cells as one
        # long line instead. We trigger it via a 0-cell line width.
        seq = BrailleSequence(cells=_word(6))
        out = LayoutRenderer(options=LayoutOptions(line_width=0)).render(seq)
        # All 6 cells land on the same (only) line.
        assert "\n" not in out
        assert len(out) == 6

    def test_cont_indent_ge_line_width_terminates(self):
        # Regression: a continuation indent >= line_width (e.g. a deeply
        # indented quote on a very narrow page) used to spin forever in
        # the mid-atom split — ``slot`` stayed <= 0 so ``rest_cells`` never
        # shrank.  The wrap must terminate (overflowing the width is the
        # lesser evil; hanging is not) and keep every content cell.
        import threading

        for line_width, quote_indent in ((3, 3), (2, 3)):
            cells = _atom(8, 0)  # one indivisible 8-cell atom
            doc = BrailleDocument(
                blocks=[BrailleBlock(block_type="quote", cells=cells)]
            )
            result: dict[str, str] = {}

            # Bind the loop vars as defaults so the closure captures this
            # iteration's values (ruff B023): the thread is joined before the
            # next iteration, so late binding wouldn't bite — but the lint
            # flags the pattern regardless, and binding is the canonical fix.
            def run(
                line_width=line_width,
                quote_indent=quote_indent,
                doc=doc,
                result=result,
            ) -> None:
                result["out"] = LayoutRenderer(options=LayoutOptions(
                    line_width=line_width, quote_indent=quote_indent,
                )).render(doc)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(timeout=5.0)
            assert not t.is_alive(), (
                f"render hung at line_width={line_width}, "
                f"quote_indent={quote_indent}"
            )
            content = sum(
                1 for ch in result["out"] if ch not in (dots_to_char(()), "\n")
            )
            assert content == 8
            # And no stray blank lines: the old double-flush in the
            # mid-atom split emitted one empty line per content cell when
            # the indent alone was >= line_width.  Every line now carries
            # content (it overflows the width — unavoidable when the
            # indent exceeds it — but is never blank).
            lines = result["out"].split("\n")
            assert all(ln != "" for ln in lines), (
                f"stray blank line at line_width={line_width}, "
                f"quote_indent={quote_indent}: {lines!r}"
            )

    def test_word_longer_than_remaining_but_fits_after_wrap(self):
        # Three cells already on the line + a 6-cell word, line width 8.
        # The word doesn't fit in the remaining 5 cells but does fit on
        # a fresh line — wrapper must flush, then place the whole word.
        seq = _seq(_word(3), 1, _word(6))
        out = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=seq.cells)]))
        lines = out.split("\n")
        # The 6-cell word ended up intact on a new line, not split.
        assert len(lines) == 2
        assert len(lines[0]) == 3
        assert len(lines[1]) == 6

    def test_long_unbroken_word_wraps_without_recursion_error(self):
        # A single break-point-free "word" of many distinct atoms wraps
        # across far more lines than Python's recursion limit. The atom
        # placer must iterate, not recurse, or this raises RecursionError
        # — reachable in practice via a very long number / break-point-free
        # run even at a normal line width.
        n = 10_000
        cells = [
            BrailleCell(dots=(1,), source_span=Span(i, i + 1))
            for i in range(n)
        ]
        doc = BrailleDocument(blocks=[BrailleBlock(cells=cells)])
        out = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        )).render(doc)
        lines = out.split("\n")
        # Far more continuation lines than the default recursion limit,
        # yet no crash.
        assert len(lines) > 1000
        # Every source cell survives; no line exceeds the width.
        dot1 = dots_to_char((1,))
        assert sum(line.count(dot1) for line in lines) == n
        assert all(len(line) <= 8 for line in lines)

    def test_long_unbroken_word_places_atoms_in_linear_time(self):
        # Regression: place_atoms used to re-slice the atom suffix
        # (``atoms = atoms[placed:]``) and re-sum its lengths every pass,
        # making a long break-point-free run of distinct-span atoms O(n²) —
        # ~30k cells took seconds, ~60k took ~12s. With an index cursor + a
        # running total it is linear (~70ms at 60k). The bound is deliberately
        # loose: linear finishes in well under a second, while the old
        # quadratic would blow past it by 100x+, so this catches a
        # re-quadratic regression without being wall-clock flaky on slow CI.
        import time

        n = 80_000
        cells = [
            BrailleCell(dots=(1,), source_span=Span(i, i + 1))
            for i in range(n)
        ]
        doc = BrailleDocument(blocks=[BrailleBlock(cells=cells)])
        renderer = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        ))
        start = time.perf_counter()
        out = renderer.render(doc)
        elapsed = time.perf_counter() - start
        # Correctness preserved at scale: every source cell still placed.
        assert sum(line.count(dots_to_char((1,))) for line in out.split("\n")) == n
        # O(n) finishes in tens of ms; O(n²) would take ~20s at this n.
        assert elapsed < 5.0, (
            f"place_atoms took {elapsed:.2f}s for n={n} — likely O(n²) again"
        )


class TestPageNumbers:
    """``show_page_numbers=True`` adds the page number on its OWN line
    (the page becomes ``page_height`` content lines + 1 number line),
    positioned per ``page_number_position``.  Only takes effect when
    ``page_height`` is set."""

    def _multi_line_doc(self, line_count: int, cells_per_line: int = 6) -> BrailleDocument:
        # One block per line so each line is exactly ``cells_per_line``
        # cells wide and we control pagination cleanly.
        return BrailleDocument(
            blocks=[
                BrailleBlock(cells=_word(cells_per_line)) for _ in range(line_count)
            ]
        )

    def test_unicode_page_one_carries_number_sign_and_digit_one(self):
        doc = self._multi_line_doc(line_count=2, cells_per_line=3)
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        # The first line should END with ⠼⠁.
        first_line = out.split("\n")[0]
        assert first_line.endswith(_page_number_chars(1))

    def test_unicode_page_two_carries_digit_two(self):
        doc = self._multi_line_doc(line_count=4, cells_per_line=3)
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        # Two pages joined by \f; the second page's top line ends with ⠼⠂.
        pages = out.split("\f")
        assert len(pages) == 2
        second_top = pages[1].split("\n")[0]
        assert second_top.endswith(_page_number_chars(2))

    def test_brf_page_one_uses_hash_a(self):
        """NABCC: number_sign → '#', digit 1 → 'A'."""
        doc = self._multi_line_doc(line_count=2, cells_per_line=3)
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        first_line = out.split(b"\r\n")[0]
        assert first_line.endswith(b"#A")

    def test_page_number_line_padded_to_width_when_right(self):
        """The (separate) page-number line is padded with blank cells so
        the number sits flush right at ``line_width``."""
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=_word(3)),
            BrailleBlock(cells=_word(3)),
        ])
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        first_line = out.split("\n")[0]  # top-right default → number line
        assert first_line.endswith(_page_number_chars(1))
        assert len(first_line) == 10

    def test_full_content_line_kept_with_separate_number_line(self):
        """The page number is its OWN added line, so a content line that
        already fills the width keeps every cell — never truncated, never
        reflowed onto the next line."""
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=_word(10)),  # exactly line_width
            BrailleBlock(cells=_word(3)),
        ])
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        blank = dots_to_char(())
        lines = out.split("\n")
        # 2 content lines + 1 separate number line (top-right default).
        assert len(lines) == 3
        assert lines[0].endswith(_page_number_chars(1))
        assert len(lines[1]) == 10  # full content line, intact
        assert len(lines[2]) == 3
        assert all(len(line) <= 10 for line in lines)
        # Conservation: all 13 content cells survive + the page number.
        content = sum(1 for ch in out if ch not in (blank, "\n", "\f"))
        assert content == 13 + len(_page_number_chars(1))

    def test_page_is_height_plus_one_lines_with_number(self):
        """Additive: a numbered page is ``page_height`` CONTENT lines PLUS
        its own number line (height + 1), so page numbers never steal
        content capacity."""
        doc = self._multi_line_doc(line_count=2, cells_per_line=3)
        numbered = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
        )).render(doc)
        plain = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=False,
        )).render(doc)
        assert len(numbered.split("\n")) == 3  # 2 content + 1 number
        assert len(plain.split("\n")) == 2  # 2 content only

    def test_no_pagination_skips_page_numbers(self):
        """``show_page_numbers`` is a no-op when ``page_height`` is
        unset — there are no pages to number."""
        doc = self._multi_line_doc(line_count=3, cells_per_line=3)
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=None, show_page_numbers=True,
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        # No \f either way; and no page number injected anywhere.
        assert _page_number_chars(1) not in out


class TestPageNumberPosition:
    """``page_number_position`` picks where the (separate) page-number
    line sits.  Four choices: top-right (default), top-left, bottom-right,
    bottom-left.  Top-X puts the number line first on the page, Bottom-X
    last; -right / -left aligns the number within that line (right pads to
    full width; left sits at column 0)."""

    def _two_line_doc(self) -> BrailleDocument:
        # Each block becomes one line of 3 cells under line_width=10 +
        # paragraph_indent=0.  Two blocks = two lines = one page when
        # ``page_height=2``.
        return BrailleDocument(
            blocks=[
                BrailleBlock(cells=_word(3)),
                BrailleBlock(cells=_word(3)),
            ]
        )

    # --- top-right (the V1 default, still works after the refactor) ---

    def test_top_right_anchors_first_line(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="top-right",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        assert lines[0].endswith(_page_number_chars(1))
        assert _page_number_chars(1) not in lines[1]

    # --- top-left -----------------------------------------------------

    def test_top_left_anchors_first_line_left(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="top-left",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        assert lines[0].startswith(_page_number_chars(1))
        # Bottom row stays untouched.
        assert _page_number_chars(1) not in lines[1]

    def test_top_left_brf_uses_hash_a_at_start(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="top-left",
        )).render(doc)
        first_line = out.split(b"\r\n")[0]
        assert first_line.startswith(b"#A")

    def test_top_left_number_line_is_bare_number_at_left(self):
        """Top-left: the number is its own first line, flush left, with no
        padding (a braille line stops at its last cell)."""
        doc = self._two_line_doc()
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="top-left",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        assert lines[0] == _page_number_chars(1)
        assert len(lines) == 3  # number line + 2 content lines

    def test_top_left_keeps_full_content_line(self):
        """Top-left with a width-filling content line: the number is its
        own line, so content is never reflowed or truncated."""
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=_word(10)),  # fills the line
            BrailleBlock(cells=_word(3)),
        ])
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="top-left",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        assert lines[0] == _page_number_chars(1)  # number line, no padding
        assert len(lines) == 3
        assert len(lines[1]) == 10  # full content line, intact
        assert len(lines[2]) == 3

    def test_page_number_line_alignment_helper(self):
        """``_page_number_line``: left-aligned is the bare number; right-
        aligned pads to full width; an over-narrow line never drops it."""
        from brailix.renderer.layout import (
            _page_number_chars,
            _page_number_line,
        )

        blank = dots_to_char(())
        pn = _page_number_chars(7)
        left = _page_number_line(pn, 10, align_right=False, blank=blank)
        assert left == pn
        right = _page_number_line(pn, 10, align_right=True, blank=blank)
        assert right.endswith(pn)
        assert len(right) == 10
        # Pathological: number wider than the line overflows, never dropped.
        big = _page_number_chars(999)
        assert big in _page_number_line(big, 2, align_right=True, blank=blank)

    # --- bottom-right -------------------------------------------------

    def test_bottom_right_anchors_last_line(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="bottom-right",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        # Number is its own last line, after the 2 content lines.
        assert len(lines) == 3
        assert _page_number_chars(1) not in lines[0]
        assert _page_number_chars(1) not in lines[1]
        assert lines[-1].endswith(_page_number_chars(1))
        assert len(lines[-1]) == 10  # right-aligned, padded to width

    def test_bottom_right_brf_uses_hash_a_at_end_of_last_line(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="bottom-right",
        )).render(doc)
        last_line = out.split(b"\r\n")[-1]
        assert last_line.endswith(b"#A")


    # --- bottom-left --------------------------------------------------

    def test_bottom_left_anchors_last_line_left(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="bottom-left",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        lines = out.split("\n")
        # Number is its own last line, flush left (bare number).
        assert len(lines) == 3
        assert _page_number_chars(1) not in lines[0]
        assert _page_number_chars(1) not in lines[1]
        assert lines[-1] == _page_number_chars(1)

    def test_bottom_left_brf_at_start_of_last_line(self):
        doc = self._two_line_doc()
        out = LayoutRenderer(format="brf", options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="bottom-left",
        )).render(doc)
        last_line = out.split(b"\r\n")[-1]
        assert last_line.startswith(b"#A")

    # --- multi-page sanity --------------------------------------------

    def test_bottom_right_carries_each_page_number(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=_word(3)) for _ in range(4)
        ])
        out = LayoutRenderer(options=LayoutOptions(
            paragraph_indent=0, line_width=10,
            page_height=2, show_page_numbers=True,
            page_number_position="bottom-right",
        )).render(doc)
        from brailix.renderer.layout import _page_number_chars

        pages = out.split("\f")
        assert len(pages) == 2
        # Each page's last line ends with its number.
        assert pages[0].split("\n")[-1].endswith(_page_number_chars(1))
        assert pages[1].split("\n")[-1].endswith(_page_number_chars(2))


class TestAtomicWrap:
    """Atomic-group wrapping + continuation hyphen.

    Cells that share a non-None ``source_span`` form one indivisible
    atom (a Chinese syllable / a Latin prefix+letter / the inside of a
    single math structure). Cells with ``source_span=None`` (synthesised
    markers like ``number_sign``) cling to the next non-None atom so they
    never float off alone.  Wrap breaks at atom boundaries (with a hyphen)
    before falling back to mid-atom split.
    """

    def test_single_atom_wraps_to_fresh_line_without_hyphen(self):
        # A 3-cell prior word + blank + 5-cell single-atom word at
        # line_width=7.  The 5-cell atom doesn't fit alongside the
        # prior word's trailing blank (5 cells > 3 remaining), so it
        # wraps to a fresh line.  Because the wrap point is the blank
        # separator (not inside the atom), no hyphen is emitted —
        # confirms the algorithm doesn't sprinkle hyphens at every
        # break, only at non-blank ones.
        cells = [*_word(3), BLANK_CELL, *_atom(5, 0)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=7, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        assert len(lines) == 2
        assert len(lines[0]) == 3
        assert _hyphen_char() not in lines[0]
        assert len(lines[1]) == 5
        assert _hyphen_char() not in lines[1]

    def test_multi_atom_word_splits_between_atoms_with_hyphen(self):
        # Two 5-cell atoms in one word (no blank between), line_width=8.
        # Whole word (10 cells) > line_width: must split between the
        # two atoms.  The split adds a hyphen on the broken line.
        cells = [*_atom(5, 0), *_atom(5, 1)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        assert len(lines) == 2
        # Line 1: first 5-cell atom + hyphen = 6 cells.
        assert len(lines[0]) == 6
        assert lines[0].endswith(_hyphen_char())
        # Line 2: second atom intact.
        assert len(lines[1]) == 5
        assert _hyphen_char() not in lines[1]

    def test_blank_break_emits_no_hyphen(self):
        # Two 5-cell words separated by a blank, line_width=10.  The
        # second word doesn't fit on line 1, so we break at the blank
        # (word boundary).  No hyphen — that's only for non-blank
        # breaks.
        cells = [*_atom(5, 0), BLANK_CELL, *_atom(5, 1)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        assert len(lines) == 2
        assert len(lines[0]) == 5
        assert _hyphen_char() not in lines[0]
        assert len(lines[1]) == 5
        assert _hyphen_char() not in lines[1]

    def test_hyphen_consumes_one_cell_of_line_width(self):
        # Two 5-cell atoms, line_width=10.  Total cells = 10 — would
        # fit exactly if hyphen weren't reserved.  But splitting
        # reserves one cell for the hyphen, so only the first 4 cells
        # of available space can hold an atom… and the first atom
        # alone is 5 cells, so it fits without splitting the second
        # atom up front.  Actually both atoms fit on one line (10/10),
        # so this test asserts: when the *combined* width exactly
        # equals line_width, no hyphen is emitted — placement wins
        # over splitting.
        cells = [*_atom(5, 0), *_atom(5, 1)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=10, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        # All 10 cells fit on one line, no hyphen.
        assert len(lines) == 1
        assert len(lines[0]) == 10
        assert _hyphen_char() not in lines[0]

    def test_hyphen_reservation_when_split_required(self):
        # Two 5-cell atoms, line_width=9.  Total=10 doesn't fit on
        # line_width=9.  Must split between atoms: first atom (5) +
        # hyphen (1) = 6 cells on line 1; second atom (5) on line 2.
        cells = [*_atom(5, 0), *_atom(5, 1)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=9, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        assert len(lines) == 2
        assert lines[0] == dots_to_char((1,)) * 5 + _hyphen_char()
        assert lines[1] == dots_to_char((1,)) * 5

    def test_none_span_marker_clings_to_next_atom(self):
        # Simulates a number_sign + 4 digits run: marker has no
        # source_span; each digit has its own narrow span.  At
        # line_width=3, the algorithm must keep the marker bound to
        # the first digit (one atom = [marker, digit1]) and split
        # between subsequent digit atoms.
        marker = BrailleCell(dots=(3, 4, 5, 6), role="number_sign")
        d1 = BrailleCell(dots=(1,),     source_span=Span(0, 1))
        d2 = BrailleCell(dots=(1, 2),   source_span=Span(1, 2))
        d3 = BrailleCell(dots=(1, 4),   source_span=Span(2, 3))
        d4 = BrailleCell(dots=(1, 4, 5),source_span=Span(3, 4))
        cells = [marker, d1, d2, d3, d4]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=3, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        # Line 1 must contain *both* the marker and the first digit
        # (marker clings to d1).  Hyphen makes the break visible.
        assert lines[0].startswith(dots_to_char((3, 4, 5, 6)) + dots_to_char((1,)))
        assert lines[0].endswith(_hyphen_char())
        # No hyphen on the final line (it didn't break further).
        assert _hyphen_char() not in lines[-1]

    def test_continuation_hyphen_none_disables_hyphen_emission(self):
        # When the proofreader explicitly opts out, multi-atom split still
        # happens but without the trailing ⠤ — useful for callers
        # that want the legacy "long word splits silently" behaviour
        # (or for raw-cell tests).
        cells = [*_atom(5, 0), *_atom(5, 1)]
        out = LayoutRenderer(options=LayoutOptions(
            line_width=9,
            paragraph_indent=0,
            continuation_hyphen=None,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        assert len(lines) == 2
        assert _hyphen_char() not in out
        # Without hyphen reservation, line 1 takes the whole 5-cell
        # atom (still can't fit both atoms on a 9-cell line).
        assert len(lines[0]) == 5
        assert len(lines[1]) == 5

    def test_single_atom_wider_than_line_width_mid_breaks_with_hyphen(self):
        # A single 12-cell atom (same source_span throughout) on a
        # line_width=8 line.  No internal break point — last-resort
        # mid-atom split.  Per BANA convention, the hyphen still
        # marks each break.
        cells = _atom(12, 0)
        out = LayoutRenderer(options=LayoutOptions(
            line_width=8, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        # Line 1: 7 cells + hyphen = 8.  Line 2: 5 cells (12 - 7).
        assert lines[0] == dots_to_char((1,)) * 7 + _hyphen_char()
        assert lines[1] == dots_to_char((1,)) * 5

    def test_chinese_syllable_initial_final_tone_stay_together(self):
        # A 3-cell syllable (initial + final + tone, all sharing the
        # source char's span), followed by another 3-cell syllable.
        # On a line_width=3 line the second syllable can't fit
        # alongside the first — but neither syllable should be split
        # internally.  Each lands on its own line.
        syl1 = [
            BrailleCell(dots=(1, 3, 5), role="zh_initial",
                        source_span=Span(0, 1), source_text="中"),
            BrailleCell(dots=(2, 4, 5, 6), role="zh_final",
                        source_span=Span(0, 1), source_text="中"),
            BrailleCell(dots=(2, 3, 6), role="zh_tone",
                        source_span=Span(0, 1), source_text="中"),
        ]
        syl2 = [
            BrailleCell(dots=(2, 4, 5), role="zh_initial",
                        source_span=Span(1, 2), source_text="国"),
            BrailleCell(dots=(1, 2, 3, 5, 6), role="zh_final",
                        source_span=Span(1, 2), source_text="国"),
            BrailleCell(dots=(2, 5, 6), role="zh_tone",
                        source_span=Span(1, 2), source_text="国"),
        ]
        cells = syl1 + syl2
        out = LayoutRenderer(options=LayoutOptions(
            line_width=3, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        # Two lines, three glyphs each — no syllable internal split.
        # Hyphen lands on line 1 because we broke between atoms (not
        # at a blank).  Wait — at line_width=3 the hyphen would push
        # the line to 4 cells (3 syllable + 1 hyphen).  The algorithm
        # reserves a cell for the hyphen, so only 2 cells of the first
        # syllable would fit — but that would split the syllable!
        # Instead, the fresh-line attempt + multi-atom path means we
        # flush the first syllable intact (its own 3-cell atom fits a
        # fresh 3-cell line), with hyphen.  Actually the first
        # syllable is 1 atom (same span), so we never split within
        # it; line 1 = syl1 cells (no hyphen — fits exactly on fresh
        # line), line 2 = syl2.
        assert len(lines) == 2
        assert len(lines[0]) == 3  # syl1 intact
        assert len(lines[1]) == 3  # syl2 intact

    def test_latin_word_breaks_between_letters_with_hyphen(self):
        # "Hello" — first letter is prefix+H (same source_span, two
        # cells); subsequent letters each have their own span (one
        # cell each).  At line_width=4, the word doesn't fit on one
        # line; split between letters with hyphen.
        prefix = BrailleCell(dots=(4, 6), role="latin_letter",
                              source_span=Span(0, 1), source_text="H")
        h = BrailleCell(dots=(1, 2, 5), role="latin_letter",
                        source_span=Span(0, 1), source_text="H")
        e = BrailleCell(dots=(1, 5), role="latin_letter",
                        source_span=Span(1, 2), source_text="e")
        l1 = BrailleCell(dots=(1, 2, 3), role="latin_letter",
                         source_span=Span(2, 3), source_text="l")
        l2 = BrailleCell(dots=(1, 2, 3), role="latin_letter",
                         source_span=Span(3, 4), source_text="l")
        o = BrailleCell(dots=(1, 3, 5), role="latin_letter",
                        source_span=Span(4, 5), source_text="o")
        cells = [prefix, h, e, l1, l2, o]  # 6 cells total
        out = LayoutRenderer(options=LayoutOptions(
            line_width=4, paragraph_indent=0,
        )).render(BrailleDocument(blocks=[BrailleBlock(cells=cells)]))
        lines = out.split("\n")
        # Line 1 must include the prefix + H together (one atom);
        # ends with hyphen because we split before line end.
        assert lines[0].startswith(dots_to_char((4, 6)) + dots_to_char((1, 2, 5)))
        assert lines[0].endswith(_hyphen_char())
        # Total cells = 6 source + N hyphens; the prefix is not
        # separated from H on any line.
        for ln in lines:
            # The bare prefix cell never appears alone on a line.
            assert ln != dots_to_char((4, 6))


class TestRegistration:
    def test_registered_by_name(self):
        from brailix.renderer import renderer_registry

        assert renderer_registry.has("layout")
        r = renderer_registry.get("layout")
        # Returns a string by default.
        seq = BrailleSequence(cells=[BrailleCell(dots=(1,))])
        assert isinstance(r.render(seq), str)
