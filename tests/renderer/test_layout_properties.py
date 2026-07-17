"""Property-based tests for the layout renderer's conservation contract.

Layout may wrap, indent, paginate and inject synthesized cells (indent
blanks, continuation hyphens) — but it must never lose, duplicate or
reorder the *content* it was given. Concretely, over any generated cell
stream (atoms sharing spans, span-less markers, blanks, forced line
breaks, hang regions — balanced or not) and any block kind / width:

* every non-blank input cell appears in the output exactly once, in input
  order, **by object identity** (cells ride through wrapping by reference
  — the documented ``lay_out_block`` contract editors key their
  cell→position maps on);
* every non-blank output cell that was NOT in the input is a continuation
  hyphen, and it only ever sits at the end of a line;
* no line exceeds the configured width (for widths that can fit the
  block's own indents);
* verbatim blocks (code / table rows) reproduce their cells per row
  exactly — line breaks honoured, zero-width hang sentinels dropped;
* pagination never stacks more than ``page_height`` content lines (+ the
  page number's own line) on a page;
* ``lay_out_block`` and ``render`` agree — one layout authority, so the
  on-screen wrap equals the exported file.

Rule-specific layout behaviour (which indent a heading gets, music
formats, BRF encoding) is example-tested in ``test_layout.py`` and
``test_music_layout.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core.span import Span
from brailix.ir.braille import (
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    blank_cell,
    hang_close_cell,
    hang_open_cell,
    line_break_cell,
)
from brailix.renderer._page_digits import page_number_chars
from brailix.renderer.layout import LayoutOptions, LayoutRenderer
from brailix.renderer.unicode_braille import cell_to_char, dots_to_char

_TEXT_FLOW_TYPES = ("paragraph", "heading", "list_item", "quote", "footnote", "callout")
_VERBATIM_TYPES = ("code_block", "table_row")


@st.composite
def cell_streams(draw: st.DrawFn) -> list[BrailleCell]:
    """A backend-shaped cell stream: span-sharing atoms, span-less markers,
    blanks, forced breaks, and (possibly unbalanced) hang regions."""
    items = draw(
        st.lists(
            st.sampled_from(
                # Atoms dominate; structure cells ride along.
                ["atom", "atom", "atom", "blank", "marker", "line_break", "hang_open", "hang_close"]
            ),
            max_size=14,
        )
    )
    cells: list[BrailleCell] = []
    cursor = 0
    for kind in items:
        boundary = Span(cursor, cursor)
        if kind == "atom":
            # One source syllable / structure: 1..3 cells sharing one span.
            span = Span(cursor, cursor + 1)
            cursor += 1
            for _ in range(draw(st.integers(1, 3))):
                dots = draw(st.sets(st.integers(1, 6), min_size=1, max_size=4))
                cells.append(
                    BrailleCell(
                        dots=tuple(dots),
                        role=draw(st.sampled_from([None, "zh_initial", "digit"])),
                        source_span=span,
                        source_text="x",
                    )
                )
        elif kind == "blank":
            cells.append(blank_cell(boundary))
        elif kind == "marker":
            # Synthesised-by-the-backend marker (number sign, list marker):
            # no span — must cling to a neighbour, never be dropped.
            cells.append(BrailleCell(dots=(3, 4, 5, 6), role="number_sign", source_span=None))
        elif kind == "line_break":
            cells.append(line_break_cell(boundary))
        elif kind == "hang_open":
            cells.append(hang_open_cell(boundary))
        else:
            cells.append(hang_close_cell(boundary))
    return cells


@st.composite
def text_flow_blocks(draw: st.DrawFn) -> BrailleBlock:
    block_type = draw(st.sampled_from(_TEXT_FLOW_TYPES))
    return BrailleBlock(
        block_type=block_type,
        cells=draw(cell_streams()),
        heading_level=draw(st.sampled_from([None, 1, 2, 3])) if block_type == "heading" else None,
        align=draw(st.sampled_from([None, "center", "right", "left"])),
    )


def _renderer(width: int, hyphen: bool = True) -> LayoutRenderer:
    return LayoutRenderer(
        options=LayoutOptions(
            line_width=width,
            continuation_hyphen=(3, 6) if hyphen else None,
        )
    )


def _content(cells: list[BrailleCell]) -> list[BrailleCell]:
    return [c for c in cells if c.dots]


class TestConservation:
    @settings(max_examples=120)
    @given(block=text_flow_blocks(), width=st.integers(0, 50), hyphen=st.booleans())
    def test_no_loss_no_duplication_no_reorder(
        self, block: BrailleBlock, width: int, hyphen: bool
    ) -> None:
        # width 0 included on purpose: the defensive non-positive-width
        # branch returns the cells untouched, and conservation must hold
        # there too (mutation testing found the branch unexercised).
        lines = _renderer(width, hyphen).lay_out_block(block)
        input_ids = [id(c) for c in _content(block.cells)]
        known = set(input_ids)
        flat = [c for line in lines for c in line]
        # Input content cells, by identity, in input order — exactly once.
        assert [id(c) for c in _content(flat) if id(c) in known] == input_ids
        # Anything else non-blank the layout added must be a continuation
        # hyphen; synthesized cells carry no source span (highlight logic
        # skips them by that rule).
        for cell in _content(flat):
            if id(cell) not in known:
                assert cell.role == "continuation_hyphen"
                assert cell.source_span is None

    @settings(max_examples=120)
    @given(block=text_flow_blocks(), width=st.integers(1, 50))
    def test_hyphen_only_ends_a_line(self, block: BrailleBlock, width: int) -> None:
        for line in _renderer(width).lay_out_block(block):
            for i, cell in enumerate(line):
                if cell.role == "continuation_hyphen":
                    assert i == len(line) - 1

    @settings(max_examples=120)
    @given(block=text_flow_blocks(), width=st.integers(1, 50), hyphen=st.booleans())
    def test_layout_is_deterministic(
        self, block: BrailleBlock, width: int, hyphen: bool
    ) -> None:
        first = _renderer(width, hyphen).lay_out_block(block)
        second = _renderer(width, hyphen).lay_out_block(block)
        assert [[c.to_dict() for c in line] for line in first] == [
            [c.to_dict() for c in line] for line in second
        ]


class TestWidthBound:
    @settings(max_examples=120)
    @given(block=text_flow_blocks(), width=st.integers(4, 50), hyphen=st.booleans())
    def test_no_line_exceeds_width(
        self, block: BrailleBlock, width: int, hyphen: bool
    ) -> None:
        # width >= 4 clears every configured indent (2) — below that the
        # documented last-resort behaviour may legitimately overflow.
        for line in _renderer(width, hyphen).lay_out_block(block):
            assert len(line) <= width

    @settings(max_examples=80)
    @given(block=text_flow_blocks(), width=st.integers(4, 50))
    def test_right_alignment_lands_on_the_right_edge(
        self, block: BrailleBlock, width: int
    ) -> None:
        block.align = "right"
        for line in _renderer(width).lay_out_block(block):
            if any(not c.is_blank for c in line):
                assert len(line) == width


class TestVerbatimBlocks:
    @settings(max_examples=120)
    @given(
        cells=cell_streams(),
        block_type=st.sampled_from(_VERBATIM_TYPES),
        width=st.integers(1, 20),
    )
    def test_rows_reproduce_cells_exactly(
        self, cells: list[BrailleCell], block_type: str, width: int
    ) -> None:
        # No soft wrap, no indent — but structural line breaks are honoured
        # and the zero-width hang sentinels are dropped. Everything else
        # (blanks included) must come through per row, by identity.
        expected_rows: list[list[BrailleCell]] = [[]]
        for cell in cells:
            if cell.role == "line_break":
                expected_rows.append([])
            elif cell.role not in ("hang_open", "hang_close"):
                expected_rows[-1].append(cell)
        if all(not row for row in expected_rows):
            expected_rows = []
        block = BrailleBlock(block_type=block_type, cells=cells)
        lines = _renderer(width).lay_out_block(block)
        got_tail = [[id(c) for c in line] for line in lines]
        want = [[id(c) for c in row] for row in expected_rows]
        # The implementation may drop a trailing empty row; both shapes
        # carry the same cells.
        assert got_tail == want or got_tail == want[:-1] and want and not want[-1]


class TestPagination:
    @settings(max_examples=60)
    @given(
        blocks=st.lists(text_flow_blocks(), max_size=3),
        width=st.integers(4, 30),
        height=st.integers(1, 8),
        numbers=st.booleans(),
        position=st.sampled_from(["top-right", "top-left", "bottom-right", "bottom-left"]),
    )
    def test_page_height_bound(
        self,
        blocks: list[BrailleBlock],
        width: int,
        height: int,
        numbers: bool,
        position: str,
    ) -> None:
        renderer = LayoutRenderer(
            options=LayoutOptions(
                line_width=width,
                page_height=height,
                show_page_numbers=numbers,
                page_number_position=position,
            )
        )
        out = renderer.render(BrailleDocument(blocks=blocks))
        assert isinstance(out, str)
        for page in out.split("\f"):
            # The page number is its OWN added line — a page never holds
            # more than page_height content lines plus that one.
            lines = page.split("\n")
            assert len(lines) <= height + (1 if numbers else 0)

    @settings(max_examples=60)
    @given(
        blocks=st.lists(text_flow_blocks(), max_size=3),
        width=st.integers(4, 30),
        height=st.integers(1, 8),
        position=st.sampled_from(["top-right", "top-left", "bottom-right", "bottom-left"]),
    )
    def test_page_numbers_add_but_never_destroy_content(
        self,
        blocks: list[BrailleBlock],
        width: int,
        height: int,
        position: str,
    ) -> None:
        # Enabling page numbers must be purely additive: the non-blank cell
        # count equals the numbers-off render plus exactly the page-number
        # cells, and no line exceeds the width. This is the invariant a
        # historical collision branch broke — a full-width line at a page
        # anchor silently lost its tail.
        def render(numbers: bool) -> str:
            out = LayoutRenderer(
                options=LayoutOptions(
                    line_width=width,
                    page_height=height,
                    show_page_numbers=numbers,
                    page_number_position=position,
                )
            ).render(BrailleDocument(blocks=blocks))
            assert isinstance(out, str)
            return out

        blank = dots_to_char(())

        def content(s: str) -> int:
            return sum(1 for ch in s if ch not in (blank, "\n", "\f"))

        plain = render(False)
        numbered = render(True)
        pages = plain.count("\f") + 1 if plain else 0
        pn_cells = sum(len(page_number_chars(i + 1)) for i in range(pages))
        assert content(numbered) == content(plain) + pn_cells
        for line in numbered.replace("\f", "\n").split("\n"):
            assert len(line) <= width


class TestOneLayoutAuthority:
    @settings(max_examples=80)
    @given(block=text_flow_blocks(), width=st.integers(4, 40))
    def test_render_equals_lay_out_block_encoding(
        self, block: BrailleBlock, width: int
    ) -> None:
        # The editor's on-screen wrap goes through lay_out_block; the export
        # goes through render. Both must be THE SAME layout — a divergence
        # here means the user proofreads one wrap and embosses another.
        renderer = _renderer(width)
        rendered = renderer.render(BrailleDocument(blocks=[block]))
        recomposed = "\n".join(
            "".join(cell_to_char(c) for c in line)
            for line in renderer.lay_out_block(block)
        )
        assert rendered == recomposed
