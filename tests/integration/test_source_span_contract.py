"""Every BrailleIR cell must carry a source span.

ARCHITECTURE.md: "每个 BrailleIR cell 都有 source_span" — the basis of the
proofreading system's bidirectional tracking (click any braille cell → jump
to the source it came from). This holds not only for cells derived from a
source character, but also for the control / spacing cells the backend
inserts: the number sign, word / column / punctuation-spacing blanks, matrix
and equation-system row breaks (``line_break``), and the hanging-indent
brackets (``hang_open`` / ``hang_close``). Those used to share span-less
sentinel instances (``BLANK_CELL`` …) or, for the number sign, be built with
no span at all — leaving the very cell a proofreader might click with no way
back to source. This is the regression guard for the fix that routes every
such cell through the span-carrying factories in ``brailix.ir.braille``.

The invariant is unconditional — *no* role is exempt (a ``role`` white-list
would drift, since ``role`` is a display tag, not a provenance contract). If a
new emitter forgets to pass a span, one of these compiles surfaces it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pytest

from brailix import Pipeline
from brailix.core.span import Span
from brailix.ir.braille import BrailleDocument
from brailix.ir.document import (
    Block,
    CodeBlock,
    Footnote,
    GraphicBlock,
    MathBlock,
    MusicBlock,
)
from brailix.ir.document import List as ListBlock

MUSICXML_ONE_NOTE = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "</measure></part>"
    "</score-partwise>"
)


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    # ``null`` resolver keeps the prose cases independent of the optional
    # pinyin extras; span coverage doesn't depend on the reading being right.
    return Pipeline(profile="cn_current", resolver="null")


def _missing_spans(braille_ir: BrailleDocument) -> list[tuple[str | None, str | None]]:
    # Delegate discovery to the IR's own first-class check
    # (:meth:`BrailleDocument.validate_traceability`), then map each reported
    # position back to (role, source_text) for a readable failure message that
    # names which emitter forgot a span.
    return [
        (braille_ir.blocks[bi].cells[ci].role,
         braille_ir.blocks[bi].cells[ci].source_text)
        for bi, ci in braille_ir.validate_traceability()
    ]


class TestSourceSpanContract:
    def test_prose_number_latin_punct_space_all_have_span(self, pipe: Pipeline) -> None:
        # Number sign, punctuation auto-spacing, and word-boundary blanks are
        # the prose control cells that used to arrive span-less.
        for text in [
            "123",
            "3.14 47cm 100%",
            "Hello WORLD CPU MW",
            "第1个。你好，世界；再见！",
            "a b c 2026年5月17日",
            "abc123def",
        ]:
            missing = _missing_spans(pipe.translate_text(text).braille_ir)
            assert not missing, f"{text!r} → span-less cells: {missing}"

    def test_math_matrix_fraction_chem_all_have_span(self, pipe: Pipeline) -> None:
        pytest.importorskip("latex2mathml")
        for latex in [
            r"\frac{1}{2}",
            r"123 + 45",
            r"\begin{matrix}1&2\\3&4\end{matrix}",  # HANG_OPEN/CLOSE, LINE_BREAK, BLANK
            r"\begin{cases}x=1\\y=2\end{cases}",
            r"\begin{vmatrix}a&b\\c&d\end{vmatrix}",
            r"\ce{2H2 + O2 -> 2H2O}",
        ]:
            cb = pipe.translate_block(MathBlock(text=latex, source="latex"))
            missing = _missing_spans(BrailleDocument(blocks=list(cb.braille_blocks)))
            assert not missing, f"{latex!r} → span-less cells: {missing}"

    def test_list_and_table_markers_all_have_span(self, pipe: Pipeline) -> None:
        md = (
            "- 甲项\n- 乙项\n\n"
            "1. 第一\n2. 第二\n\n"
            "| 表头甲 | 表头乙 |\n|---|---|\n| 甲1 | 乙2 |\n"
        )
        doc = pipe.parse_text(md, format="markdown")
        missing = _missing_spans(pipe.translate_document(doc).braille_ir)
        assert not missing, f"list/table → span-less cells: {missing}"

    def test_footnote_ref_all_have_span(self, pipe: Pipeline) -> None:
        for ref in ("1", "1a2", "a", "*"):
            cb = pipe.translate_block(
                Footnote(ref=ref, text="脚注内容", span=Span(0, len(ref)))
            )
            missing = _missing_spans(BrailleDocument(blocks=list(cb.braille_blocks)))
            assert not missing, f"footnote {ref!r} → span-less cells: {missing}"

    def test_footnote_ref_anchors_leaf_local_at_any_offset(
        self, pipe: Pipeline
    ) -> None:
        # Presence is not enough: the ref is synthesised print structure, so
        # its cells anchor to the body text's leading edge in LEAF-LOCAL
        # coordinates — the same convention a list marker uses. They used to
        # walk ``Footnote.span``, a document coordinate describing the body
        # rather than the ref, so every ref cell claimed a body character it
        # never came from, displaced by wherever the footnote sat in the
        # source.
        body = "脚注内容"
        for offset in (0, 250):
            blk = Footnote(
                ref="1a2", text=body, span=Span(offset, offset + len(body))
            )
            cb = pipe.translate_block(blk)
            marker = [
                c
                for c in cb.braille_blocks[0].cells
                if c.role in ("footnote_ref", "number_sign")
                or (c.role == "space" and c.source_text == "")
            ]
            assert marker, "no marker cells emitted"
            for cell in cb.braille_blocks[0].cells:
                assert cell.source_span is not None
                assert cell.source_span.end <= len(body), (
                    f"cell {cell.role!r} span {cell.source_span} runs past "
                    f"the footnote's own text — a document coordinate "
                    f"leaked into the leaf-local cell sequence"
                )
            assert all(
                c.source_span == Span(0, 0)
                for c in cb.braille_blocks[0].cells
                if c.role in ("footnote_ref", "number_sign")
            )


# ---------------------------------------------------------------------------
# Span ACCURACY: composing the two coordinate levels recovers the source
# ---------------------------------------------------------------------------


def _leaves(blocks: Iterable[Block]) -> Iterator[Block]:
    """Leaf blocks in backend expansion order (a List expands per item),
    so leaves zip positionally with ``braille_ir.blocks``.

    A :class:`~brailix.ir.document.Table` is deliberately NOT expanded here:
    its cells are the documented **row-local** exception to the coordinate
    contract (see :class:`~brailix.ir.document.Block`), so composing them
    with a block span cannot recover the source — a row's joined text is not
    a slice of the Markdown source, whose ``|`` separators the adapter
    strips. Table provenance accuracy is checked against the row coordinate
    instead, in ``tests/integration/test_document_pipeline.py``
    (``TestTableCellSpanRebasingOnRecompile``). Presence of a span on table
    cells is covered above, by the whole-document traceability check.
    """
    for b in blocks:
        if isinstance(b, ListBlock):
            yield from b.blocks
        else:
            yield b


class TestSourceSpanAccuracy:
    """Presence (above) is not enough — a span must point at the RIGHT
    source. The coordinate contract (``Block.span`` / ARCHITECTURE#arch-braille-ir):
    a cell's ``source_span`` is leaf-local, ``Block.span`` locates the
    block, and wherever the exact-slice contract
    ``source[block.span] == block.text`` holds, composing the two recovers
    the exact original characters:

        source[block.span.start + cell.span.start :
               block.span.start + cell.span.end] == cell.source_text

    The regression this pins: a consumer composing the documented way must
    never land on the wrong character — e.g. a second plain-text line
    reading the first line's characters, or a Markdown heading's cells
    landing on its ``# `` marker.
    """

    def _assert_composition_recovers_source(
        self, src: str, result: object
    ) -> None:
        checked = 0
        leaves = list(_leaves(result.ir.blocks))
        braille = list(result.braille_ir.blocks)
        assert len(leaves) == len(braille)
        for blk, bb in zip(leaves, braille, strict=True):
            assert blk.span is not None
            # The block level: exact-slice contract.
            assert src[blk.span.start : blk.span.end] == (blk.text or "")
            base = blk.span.start
            for cell in bb.cells:
                sp = cell.source_span
                if sp is None:
                    continue
                if sp.start == sp.end:
                    # Zero-width anchor — synthesised content (number sign,
                    # word-boundary blank, list marker): nothing to recover,
                    # but the anchor must sit inside the leaf's text.
                    assert 0 <= sp.start <= len(blk.text or ""), (
                        f"anchor {sp} of {cell.role!r} outside block "
                        f"{blk.text!r} — a block-level coordinate leaked "
                        f"into the leaf-local cell sequence"
                    )
                    continue
                if cell.source_text is None:
                    continue
                sliced = src[base + sp.start : base + sp.end]
                assert sliced == cell.source_text, (
                    f"cell {cell.role!r} of block {blk.text!r}: composed "
                    f"slice {sliced!r} != source_text {cell.source_text!r}"
                )
                checked += 1
        assert checked, "no span-bearing cells were exercised"

    def test_plain_multiline_with_leading_whitespace(
        self, pipe: Pipeline
    ) -> None:
        # Second and third lines are the historical trap: their cells'
        # leaf-local spans start at 0, and only the block-span composition
        # maps them back to the right characters. Leading whitespace and a
        # number run (number sign + digits + punct + word blanks) cover
        # the synthesised control cells too.
        src = "甲\n  乙23,同志们好。\nabc def"
        doc = pipe.parse_text(src, format="plain")
        self._assert_composition_recovers_source(
            src, pipe.translate_document(doc)
        )

    def test_markdown_heading_list_and_paragraph(self, pipe: Pipeline) -> None:
        # Heading / list-item / single-line-paragraph text is a verbatim
        # source slice with the marker OUTSIDE the span — the exact-slice
        # side; composing recovers the source for every cell.
        md = "# 标题甲\n\n正文段落。\n\n- 项甲\n- 项乙\n\n1. 第一\n2. 第二"
        doc = pipe.parse_text(md, format="markdown")
        self._assert_composition_recovers_source(
            md, pipe.translate_document(doc)
        )

    def test_markdown_heading_span_excludes_marker_and_align(
        self, pipe: Pipeline
    ) -> None:
        md = "# 标题 {align=center}"
        doc = pipe.parse_text(md, format="markdown")
        h = doc.blocks[0]
        assert h.text == "标题"
        assert md[h.span.start : h.span.end] == "标题"
        assert h.align == "center"

    def test_multiline_paragraph_keeps_line_range_span(
        self, pipe: Pipeline
    ) -> None:
        # The joined text is NOT a source slice (soft-break becomes a
        # space); the block deliberately keeps a line-range span — located,
        # no per-character promise. Pinned so the single-line tightening
        # never silently pretends otherwise.
        md = "第一行\n第二行"
        doc = pipe.parse_text(md, format="markdown")
        p = doc.blocks[0]
        assert p.text == "第一行 第二行"
        assert (p.span.start, p.span.end) == (0, len(md))
        assert md[p.span.start : p.span.end] != p.text


# ---------------------------------------------------------------------------
# The specialised verticals: math / code / music / graphic carriers
# ---------------------------------------------------------------------------


class TestSpecialBlockLeafLocalSpans:
    """A math / code / music / graphic block's cells carry **leaf-local**
    spans — offsets into the block's own ``text`` — like every coordinate
    below the block boundary.

    This is the coordinate confusion the class exists to pin. The populate
    handlers used to hand the block's *document* span down, which is the value
    they also need for other purposes, and every single-block test agreed with
    them because a document's first block starts at 0. From the second block
    on, a consumer following the documented contract (add ``block.span.start``
    to a leaf-local offset) landed at twice the offset.

    A code block still populates one carrier inline node — verbatim text
    genuinely is a run of characters the punct path walks. Math, music and
    graphics no longer have one: the parsed tree lives on the block, and the
    leaf-local extent is what the backend hands its translator.
    """

    @staticmethod
    def _cells(pipe: Pipeline, block: Block):
        return list(pipe.translate_block(block).braille_blocks[0].cells)

    @pytest.mark.parametrize("offset", [0, 100])
    def test_code_block_carrier_is_leaf_local(
        self, pipe: Pipeline, offset: int
    ) -> None:
        text = "x = 1"
        blk = CodeBlock(
            language="python", text=text, span=Span(offset, offset + len(text))
        )
        cb = pipe.translate_block(blk)
        assert len(blk.inlines) == 1
        assert blk.inlines[0].span == Span(0, len(text))
        # The punct path walks the carrier span one character at a time, so
        # the document offset would have shown up on every cell.
        assert [c.source_span for c in cb.braille_blocks[0].cells] == [
            Span(i, i + 1) for i in range(len(text))
        ]

    @pytest.mark.parametrize("offset", [0, 100])
    def test_math_block_carrier_is_leaf_local(
        self, pipe: Pipeline, offset: int
    ) -> None:
        pytest.importorskip("latex2mathml")
        text = "x + y"
        blk = MathBlock(
            source="latex", text=text, span=Span(offset, offset + len(text))
        )
        cells = self._cells(pipe, blk)
        assert cells
        for cell in cells:
            assert cell.source_span is not None
            assert cell.source_span.end <= len(text), (
                f"cell {cell.role!r} span {cell.source_span} runs past the "
                f"block's own text ({len(text)} chars) — a document "
                f"coordinate leaked into the leaf-local cell sequence"
            )

    @pytest.mark.parametrize("offset", [0, 100])
    def test_music_block_carrier_is_leaf_local(
        self, pipe: Pipeline, offset: int
    ) -> None:
        text = MUSICXML_ONE_NOTE
        blk = MusicBlock(
            source="musicxml", text=text, span=Span(offset, offset + len(text))
        )
        for cell in self._cells(pipe, blk):
            if cell.source_span is not None:
                assert cell.source_span.end <= len(text)

    @pytest.mark.parametrize("offset", [0, 100])
    def test_graphic_block_carrier_is_leaf_local(
        self, pipe: Pipeline, offset: int
    ) -> None:
        text = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        blk = GraphicBlock(
            source="svg", text=text, span=Span(offset, offset + len(text))
        )
        # A figure emits no cells at all — its dots ride on the raster — so
        # what there is to pin is that the block still holds its place and
        # leaks no document coordinate into the flow.
        cb = pipe.translate_block(blk)
        assert cb.braille_blocks[0].cells == []
        assert cb.raster is not None

    def test_markdown_document_second_fence_is_not_double_offset(
        self, pipe: Pipeline
    ) -> None:
        # The end-to-end shape: a fence that is NOT the document's first
        # block. Its cells must stay inside the fence body's own length.
        md = (
            "开头一段话。\n\n"
            "```python\nx = 1\n```\n\n"
            "$$\na+b\n$$\n"
        )
        doc = pipe.parse_text(md, format="markdown")
        result = pipe.translate_document(doc)
        for blk, bb in zip(doc.blocks, result.braille_ir.blocks, strict=True):
            if not isinstance(blk, (CodeBlock, MathBlock)):
                continue
            assert blk.span.start > 0, "fence should not start the document"
            for cell in bb.cells:
                assert cell.source_span is not None
                assert cell.source_span.end <= len(blk.text or ""), (
                    f"{type(blk).__name__} cell {cell.role!r} span "
                    f"{cell.source_span} exceeds its own text "
                    f"{blk.text!r} — the block's document offset leaked in"
                )
