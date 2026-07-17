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
from brailix.ir.document import Block, Footnote, MathBlock
from brailix.ir.document import List as ListBlock


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


# ---------------------------------------------------------------------------
# Span ACCURACY: composing the two coordinate levels recovers the source
# ---------------------------------------------------------------------------


def _leaves(blocks: Iterable[Block]) -> Iterator[Block]:
    """Leaf blocks in backend expansion order (a List expands per item),
    so leaves zip positionally with ``braille_ir.blocks``."""
    for b in blocks:
        if isinstance(b, ListBlock):
            yield from b.items
        else:
            yield b


class TestSourceSpanAccuracy:
    """Presence (above) is not enough — a span must point at the RIGHT
    source. The coordinate contract (``Block.span`` / ARCHITECTURE §4.4):
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
