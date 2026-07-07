"""Integration tests for the multi-block document path.

These exercise :meth:`Pipeline.translate_document` end-to-end via the
Markdown adapter, covering the frontend/backend boundary contracts:

* The Chinese frontend runs over text-bearing blocks (paragraph,
  heading, list_item, quote, footnote, table_cell) — those land with
  populated ``children``.
* It deliberately skips :class:`MathBlock` and :class:`CodeBlock` —
  their ``text`` is not language text, so populating ``children``
  with Chinese tokens would pollute the IR. The backend's block
  expander reads ``block.text`` directly for both.
* The block expander produces one :class:`BrailleBlock` per
  paragraph / heading / quote / footnote / image_alt / math_block /
  code_block, and multiple blocks per List / Table.
* The layout renderer honors ``heading_level`` (level 1 centred,
  deeper levels flush left).
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.input.markdown import parse_markdown
from brailix.ir.document import CodeBlock, MathBlock
from brailix.renderer.layout import LayoutOptions, LayoutRenderer
from brailix.renderer.unicode_braille import dots_to_char


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    # ``auto`` picks up whatever zh analyzer + pinyin resolver are
    # installed; without them this fixture still works because the
    # frontend gracefully falls back to char-level tokenization.
    return Pipeline(profile="cn_current")


# ---------------------------------------------------------------------------
# Block-pollution boundary: math/code don't get Chinese children
# ---------------------------------------------------------------------------


class TestNoFrontendPollution:
    @pytest.mark.requires("latex2mathml")
    def test_math_block_uses_math_frontend_not_chinese(self, pipe):
        from brailix.ir.inline import MathInline

        doc = parse_markdown("$$x + y = z$$", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        math_blocks = [b for b in result.ir.blocks if isinstance(b, MathBlock)]
        assert math_blocks
        # Original text preserved verbatim — no Chinese tokenization.
        assert math_blocks[0].text == "x + y = z"
        # Children populated by the *math* frontend (one MathInline
        # carrying the parsed MathML tree), not by the Chinese
        # tokenizer (which would have spat out HanziChar/Word garbage).
        children = math_blocks[0].children
        assert len(children) == 1
        assert isinstance(children[0], MathInline)
        assert children[0].math is not None

    def test_code_block_wrapped_as_codeinline_not_tokenized(self, pipe):
        from brailix.ir.inline import CodeInline

        doc = parse_markdown("```python\nx = 1\n```", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        code_blocks = [b for b in result.ir.blocks if isinstance(b, CodeBlock)]
        assert code_blocks
        # Original text preserved; no Chinese tokenization.
        assert code_blocks[0].text == "x = 1"
        assert code_blocks[0].language == "python"
        # Children: a single CodeInline carrying the verbatim text,
        # so the backend's punct path emits one cell per source char.
        children = code_blocks[0].children
        assert len(children) == 1
        assert isinstance(children[0], CodeInline)
        assert children[0].surface == "x = 1"

    def test_paragraph_still_populates_children(self, pipe):
        # Paragraphs DO run through the frontend — confirm the
        # math/code skip didn't accidentally short-circuit text blocks.
        doc = parse_markdown("一段中文。", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        assert result.ir.blocks[0].children


# ---------------------------------------------------------------------------
# Backend produces cells for both math/code despite empty children
# ---------------------------------------------------------------------------


class TestBackendEmitsForMathCode:
    def test_math_block_emits_cells(self, pipe):
        doc = parse_markdown("$$x + y$$", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        math_braille_blocks = [
            b for b in result.braille_ir.blocks if b.block_type == "math_block"
        ]
        assert math_braille_blocks
        assert math_braille_blocks[0].cells, "math backend should produce cells"

    def test_code_block_emits_one_cell_per_source_char(self, pipe):
        doc = parse_markdown("```\nabc\n```", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        code_braille_blocks = [
            b for b in result.braille_ir.blocks if b.block_type == "code_block"
        ]
        assert code_braille_blocks
        # "abc" → 3 cells (whatever the punct table maps them to).
        assert len(code_braille_blocks[0].cells) == 3


# ---------------------------------------------------------------------------
# Table cells: spans rebased to the row coordinate, not cell-local
# ---------------------------------------------------------------------------


class TestTableCellSpanRebasing:
    """Each table cell is tokenised in isolation, so its inline spans are
    local to the cell. A row flattens its cells into one source string joined
    by two spaces; the spans must be rebased into that row coordinate, else a
    non-first cell's inline node / braille cell points at the wrong column."""

    def test_inline_spans_are_row_local(self, pipe):
        from brailix.ir.document import Table

        doc = parse_markdown(
            "| AB | CDE |\n| --- | --- |\n| FG | HI |\n",
            profile="cn_current",
            language="zh-CN",
        )
        pipe.translate_document(doc)
        table = next(b for b in doc.blocks if isinstance(b, Table))
        header = table.rows[0]
        # Row text "AB  CDE": cell0 at 0, cell1 at len("AB") + 2 == 4.
        c0 = header.cells[0].children[0]
        c1 = header.cells[1].children[0]
        assert (c0.span.start, c0.span.end) == (0, 2)  # "AB"
        assert (c1.span.start, c1.span.end) == (4, 7)  # "CDE", not (0, 3)

    def test_braille_cell_source_spans_are_row_local(self, pipe):
        doc = parse_markdown(
            "| AB | CDE |\n| --- | --- |\n",
            profile="cn_current",
            language="zh-CN",
        )
        result = pipe.translate_document(doc)
        rows = [
            b for b in result.braille_ir.blocks if b.block_type == "table_row"
        ]
        assert rows
        starts = [
            c.source_span.start for c in rows[0].cells if c.source_span is not None
        ]
        # Second column ("CDE") braille cells carry row-local spans starting
        # at offset 4, not cell-local 0.
        assert max(starts) >= 4

    def test_single_cell_row_unchanged(self, pipe):
        # A one-cell row has no separator, so cell0 stays at offset 0 — the
        # rebase must not shift the first (or only) cell.
        from brailix.ir.document import Table

        doc = parse_markdown(
            "| AB |\n| --- |\n",
            profile="cn_current",
            language="zh-CN",
        )
        pipe.translate_document(doc)
        table = next(b for b in doc.blocks if isinstance(b, Table))
        child = table.rows[0].cells[0].children[0]
        assert child.span.start == 0


# ---------------------------------------------------------------------------
# Layout honors heading_level metadata
# ---------------------------------------------------------------------------


class TestHeadingLevelThroughPipeline:
    def test_level_1_heading_centered(self, pipe):
        doc = parse_markdown("# 一", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        out = LayoutRenderer(options=LayoutOptions(line_width=20)).render(
            result.braille_ir
        )
        # Find the non-blank line and check it has leading blank padding
        # (centering). The heading is short so substantial padding is
        # expected.
        lines = out.split("\n")
        content_lines = [ln for ln in lines if any(c != dots_to_char(()) for c in ln)]
        assert content_lines
        assert content_lines[0].startswith(dots_to_char(()))

    def test_level_2_heading_flush_left(self, pipe):
        doc = parse_markdown("## 一", profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        out = LayoutRenderer(options=LayoutOptions(line_width=20)).render(
            result.braille_ir
        )
        lines = out.split("\n")
        content_lines = [ln for ln in lines if any(c != dots_to_char(()) for c in ln)]
        assert content_lines
        # Level 2 has no centering padding.
        assert not content_lines[0].startswith(dots_to_char(()))


# ---------------------------------------------------------------------------
# Full kitchen-sink markdown round-trip
# ---------------------------------------------------------------------------


class TestKitchenSinkDocument:
    def test_mixed_document_produces_each_block_kind(self, pipe):
        src = "\n\n".join(
            [
                "# 标题",
                "一段正文。",
                "- 项一\n- 项二",
                "> 引文",
                "```\ncode\n```",
                "$$a + b$$",
            ]
        )
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        block_types = {b.block_type for b in result.braille_ir.blocks}
        # Heading + paragraph + 2 list_items + quote + code_block + math_block.
        assert "heading" in block_types
        assert "paragraph" in block_types
        assert "list_item" in block_types
        assert "quote" in block_types
        assert "code_block" in block_types
        assert "math_block" in block_types

    def test_layout_renders_without_crashing(self, pipe):
        # Smoke test: feeding the full document through layout +
        # pagination must produce a string. We don't assert on exact
        # content — that's a job for golden tests.
        src = "# 标题\n\n一段正文。\n\n- 项一\n\n> 引文"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        result = pipe.translate_document(doc)
        out = LayoutRenderer(options=LayoutOptions(
            line_width=40, page_height=20
        )).render(result.braille_ir)
        assert isinstance(out, str)
        assert out  # non-empty


# ---------------------------------------------------------------------------
# Block population edge cases — `_populate_block` recursion through
# Table / List / hand-built DocumentIR without spans / surface
# reconstruction.
# ---------------------------------------------------------------------------


class TestPopulateBlockRecursion:
    def test_table_cells_run_through_frontend(self, pipe):
        # Build a Table directly (no Markdown shortcut) and confirm the
        # Chinese frontend reaches into TableRow.cells[].children. This
        # exercises the Table-recursion branch of ``_populate_block``.
        from brailix.ir.document import (
            DocumentIR,
            Table,
            TableCell,
            TableRow,
        )

        doc = DocumentIR(
            metadata={"language": "zh-CN", "profile": "cn_current"},
            blocks=[
                Table(rows=[
                    TableRow(cells=[
                        TableCell(text="甲"),
                        TableCell(text="乙"),
                    ]),
                ]),
            ],
        )
        result = pipe.translate_document(doc)
        # Frontend populated each cell's children.
        table = result.ir.blocks[0]
        assert all(cell.children for row in table.rows for cell in row.cells)
        # Result text is a "cell1 | cell2" reconstruction (see
        # _block_surface for Tables).
        assert "甲" in result.text and "乙" in result.text
        assert " | " in result.text

    def test_mathblock_without_span_gets_synthesized_span(self, pipe):
        # When a caller hands the pipeline a MathBlock with no span,
        # `_populate_block` should synthesize one from len(text) so
        # downstream proofread output doesn't have a None hole.
        from brailix.ir.document import DocumentIR, MathBlock

        mb = MathBlock(source="latex", text="x+y")
        assert mb.span is None
        doc = DocumentIR(
            metadata={"language": "zh-CN", "profile": "cn_current"},
            blocks=[mb],
        )
        pipe.translate_document(doc)
        assert mb.span is not None
        assert mb.span.start == 0
        assert mb.span.end == len("x+y")

    def test_paragraph_without_span_gets_synthesized_span(self, pipe):
        # Same contract for text-bearing blocks: bare text + no span +
        # no children should land with the span populated to (0, len).
        from brailix.ir.document import DocumentIR, Paragraph

        p = Paragraph(text="一段")
        assert p.span is None
        doc = DocumentIR(
            metadata={"language": "zh-CN", "profile": "cn_current"},
            blocks=[p],
        )
        pipe.translate_document(doc)
        assert p.span is not None
        assert p.span.start == 0
        assert p.span.end == len("一段")

    def test_prepopulated_block_with_text_gets_span_synthesized(self, pipe):
        # A block arriving with children AND raw text but no span lands a
        # span too — the same treatment math / code / score blocks already
        # got, now uniform for prose (previously the one branch that
        # silently left span=None for a populated text-bearing block).
        from brailix.ir.document import DocumentIR, Paragraph
        from brailix.ir.inline import HanziChar

        p = Paragraph(children=[HanziChar(surface="字")], text="字", span=None)
        assert p.span is None
        doc = DocumentIR(blocks=[p])
        pipe.translate_document(doc)
        assert p.span is not None
        assert p.span.start == 0
        assert p.span.end == len("字")
        # Pre-populated children left intact (frontend didn't re-run).
        assert len(p.children) == 1
        assert p.children[0].surface == "字"


# ---------------------------------------------------------------------------
# translate_document stamps the pipeline's identity onto the IR metadata
# (parity with translate_text / parse_*), even for a hand-built doc.
# ---------------------------------------------------------------------------


class TestTranslateDocumentMetadata:
    def test_handbuilt_doc_gets_pipeline_identity_stamped(self, pipe):
        from brailix.ir.document import DocumentIR, Paragraph

        doc = DocumentIR(blocks=[Paragraph(text="字")])
        assert doc.metadata == {}
        result = pipe.translate_document(doc)
        assert result.ir.metadata["profile"] == pipe.profile
        assert result.ir.metadata["language"] == pipe.profile_language

    def test_other_metadata_keys_preserved(self, pipe):
        # Stamping identity must not wipe unrelated caller metadata.
        from brailix.ir.document import DocumentIR, Paragraph

        doc = DocumentIR(
            metadata={"custom": "keep-me"},
            blocks=[Paragraph(text="字")],
        )
        pipe.translate_document(doc)
        assert doc.metadata["custom"] == "keep-me"
        assert doc.metadata["profile"] == pipe.profile


# ---------------------------------------------------------------------------
# Pipeline.translate_file — file → IR → braille shortcut. parse_file
# itself is unit-tested in tests/input/test_file.py; here we only pin
# the composition (file path goes in, TranslationResult comes out, and
# the markdown branch produces multi-block output as expected).
# ---------------------------------------------------------------------------


class TestPipelineTranslateFile:
    def test_md_file_produces_multi_block_result(self, pipe, tmp_path):
        from brailix.ir.document import Heading, Paragraph

        path = tmp_path / "doc.md"
        path.write_text("# 标题\n\n正文一段。\n", encoding="utf-8")
        result = pipe.translate_file(path)
        # Markdown branch was hit: heading + paragraph, not a single
        # lumped paragraph.
        assert len(result.ir.blocks) == 2
        assert isinstance(result.ir.blocks[0], Heading)
        assert isinstance(result.ir.blocks[1], Paragraph)
        # Frontend ran over both text-bearing blocks.
        assert result.ir.blocks[0].children
        assert result.ir.blocks[1].children
        # Braille IR was produced for each.
        assert len(result.braille_ir.blocks) >= 2

    def test_txt_file_produces_single_paragraph_result(self, pipe, tmp_path):
        from brailix.ir.document import Paragraph

        path = tmp_path / "doc.txt"
        path.write_text("我在重庆。", encoding="utf-8")
        result = pipe.translate_file(path)
        assert len(result.ir.blocks) == 1
        assert isinstance(result.ir.blocks[0], Paragraph)
        # render() works the same as on translate_text output.
        assert isinstance(result.render(), str)

    def test_metadata_reflects_pipeline_profile(self, pipe, tmp_path):
        # parse_file's own defaults shouldn't leak through — the
        # Pipeline propagates its own profile name into the IR so a
        # downstream consumer sees a self-consistent document.
        path = tmp_path / "doc.txt"
        path.write_text("hi", encoding="utf-8")
        result = pipe.translate_file(path)
        assert result.ir.metadata["profile"] == pipe.profile
