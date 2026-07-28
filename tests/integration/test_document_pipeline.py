"""Integration tests for the multi-block document path.

These exercise :meth:`Pipeline.translate_document` end-to-end via the
Markdown adapter, covering the frontend/backend boundary contracts:

* The Chinese frontend runs over text-bearing blocks (paragraph,
  heading, list_item, quote, footnote, table_cell) — those land with
  populated ``children``.
* :class:`MathBlock` and :class:`CodeBlock` deliberately bypass the
  Chinese frontend — their ``text`` is not language text, so Chinese
  tokens in ``children`` would pollute the IR. They are populated by
  their own vertical instead (the math frontend's ``MathInline``
  carrying a parsed MathML tree, a verbatim ``CodeInline``), and the
  backend consumes those children like any other inline node rather
  than re-reading ``block.text``.
* The block expander produces one :class:`BrailleBlock` per
  paragraph / heading / quote / footnote / image_alt / math_block /
  code_block, and multiple blocks per List / Table.
* Table cells carry **row-local** spans, and that stays true across a
  re-compile of an edited table (the coordinate contract on
  :class:`~brailix.ir.document.Block`).
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
# Backend produces cells for both math and code, from their own children
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


class TestTableCellSpanRebasingOnRecompile:
    """The rebase must survive a **re-compile of an edited table**, not only
    the first compile.

    Each cell decides on its own whether its children are stale (its text
    changed) — but a cell's row-local offset depends on the cells *before* it.
    Widening column 0 moves every later column even though their own text is
    untouched, and a cell whose text DID change must not have the previous
    compile's row-local span treated as a cell-local one and shifted twice.
    Both used to leave ``source_span`` pointing at the wrong column: braille
    still correct, provenance silently wrong (click-to-jump, cross-pane
    highlight, proofreading anchors).
    """

    @staticmethod
    def _row_text(row) -> str:
        # What the backend flattens a row into: cells joined by two spaces.
        return "  ".join(cell.text for cell in row.cells)

    def _assert_row_local(self, row) -> None:
        text = self._row_text(row)
        for cell in row.cells:
            assert cell.span is not None
            assert text[cell.span.start : cell.span.end] == cell.text
            for child in cell.children:
                assert child.span is not None
                assert text[child.span.start : child.span.end] == child.surface

    def _table(self, pipe, source: str):
        from brailix.ir.document import Table

        doc = parse_markdown(source, profile="cn_current", language="zh-CN")
        pipe.translate_document(doc)
        return doc, next(b for b in doc.blocks if isinstance(b, Table))

    def test_widening_first_column_moves_later_columns(self, pipe):
        doc, table = self._table(pipe, "| AB | CDE |\n| --- | --- |\n")
        self._assert_row_local(table.rows[0])

        # Column 0 grows by two characters; column 1's own text is untouched,
        # so its children are reused — they must still be rebased.
        table.rows[0].cells[0].text = "ABCD"
        pipe.translate_document(doc)
        self._assert_row_local(table.rows[0])
        c1 = table.rows[0].cells[1].children[0]
        assert (c1.span.start, c1.span.end) == (6, 9)  # was (4, 7)

    def test_editing_a_later_column_does_not_double_shift(self, pipe):
        doc, table = self._table(pipe, "| AB | CDE |\n| --- | --- |\n")
        # Column 1's text changes: its children are dropped and rebuilt, and
        # the span left over from the previous compile is already row-local —
        # shifting it again would land the cell past the end of the row.
        table.rows[0].cells[1].text = "XY"
        pipe.translate_document(doc)
        self._assert_row_local(table.rows[0])
        cell = table.rows[0].cells[1]
        assert (cell.span.start, cell.span.end) == (4, 6)

    def test_shrinking_first_column_pulls_later_columns_back(self, pipe):
        doc, table = self._table(pipe, "| ABCD | EF |\n| --- | --- |\n")
        table.rows[0].cells[0].text = "A"
        pipe.translate_document(doc)
        self._assert_row_local(table.rows[0])

    def test_repeated_translation_is_idempotent(self, pipe):
        doc, table = self._table(pipe, "| AB | CDE | F |\n| --- | --- | --- |\n")
        before = [
            (cell.span.start, cell.span.end) for cell in table.rows[0].cells
        ]
        pipe.translate_document(doc)
        pipe.translate_document(doc)
        after = [
            (cell.span.start, cell.span.end) for cell in table.rows[0].cells
        ]
        assert before == after
        self._assert_row_local(table.rows[0])

    def test_multi_row_multi_column_with_empty_cell(self, pipe):
        doc, table = self._table(
            pipe,
            "| AB | CDE |\n| --- | --- |\n|  | GH |\n| IJ | KL |\n",
        )
        table.rows[0].cells[0].text = "ABCDEF"
        table.rows[2].cells[0].text = "I"
        pipe.translate_document(doc)
        for row in table.rows:
            text = self._row_text(row)
            for cell in row.cells:
                if not cell.text:
                    continue
                assert text[cell.span.start : cell.span.end] == cell.text
                for child in cell.children:
                    assert (
                        text[child.span.start : child.span.end] == child.surface
                    )

    def test_braille_cell_source_text_matches_row_slice(self, pipe):
        doc, table = self._table(pipe, "| AB | CDE |\n| --- | --- |\n")
        table.rows[0].cells[0].text = "ABCD"
        result = pipe.translate_document(doc)
        row_text = self._row_text(table.rows[0])
        rows = [
            b for b in result.braille_ir.blocks if b.block_type == "table_row"
        ]
        assert rows
        checked = 0
        for cell in rows[0].cells:
            if cell.source_span is None or not cell.source_text:
                continue
            sliced = row_text[cell.source_span.start : cell.source_span.end]
            assert sliced == cell.source_text
            checked += 1
        assert checked, "no braille cell carried provenance to check"


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
# Block population edge cases — `FrontendDriver.populate_block` recursion
# through Table / List / hand-built DocumentIR without spans / surface
# reconstruction.
# ---------------------------------------------------------------------------


class TestPopulateBlockRecursion:
    def test_table_cells_run_through_frontend(self, pipe):
        # Build a Table directly (no Markdown shortcut) and confirm the
        # Chinese frontend reaches into TableRow.cells[].children. This
        # exercises the Table-recursion branch of ``populate_block``.
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
        # `populate_block` should synthesize one from len(text) so
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
# Requested vs resolved profile identity: metadata carries the RESOLVED
# name (BrailleProfile.name, the authoritative identity to persist) on both
# IRs, with the caller-supplied name preserved as ``profile_requested``
# only when it differs (aliases, user-folder profiles declaring their own
# ``name``). Regression: translate_* / parse_* used to write the requested
# string while the backend wrote the resolved one, so
# ``ir.metadata["profile"] != braille_ir.metadata["profile"]``.
# ---------------------------------------------------------------------------


class TestResolvedProfileIdentity:
    @pytest.fixture()
    def alias_dir(self, tmp_path):
        """A user profile drop: file named ``my_alias.json`` whose payload
        declares ``name: cn_current`` (loader keeps the payload's name)."""
        import json
        from pathlib import Path

        import brailix

        src = Path(brailix.__file__).parent / "profiles" / "cn_current.json"
        payload = json.loads(src.read_text(encoding="utf-8"))
        assert payload["name"] == "cn_current"
        (tmp_path / "my_alias.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return tmp_path

    def test_builtin_profile_metadata_shape_unchanged(self, pipe):
        result = pipe.translate_text("字")
        assert result.ir.metadata["profile"] == "cn_current"
        assert "profile_requested" not in result.ir.metadata

    def test_alias_resolves_to_authoritative_name_on_both_irs(self, alias_dir):
        pipe = Pipeline(
            profile="my_alias", extra_profile_paths=(str(alias_dir),)
        )
        assert pipe.profile_name == "cn_current"
        result = pipe.translate_text("字")
        assert result.ir.metadata["profile"] == "cn_current"
        assert result.braille_ir.metadata["profile"] == "cn_current"
        assert (
            result.ir.metadata["profile"]
            == result.braille_ir.metadata["profile"]
        )
        assert result.ir.metadata["profile_requested"] == "my_alias"

    def test_parse_text_matches_translate_identity(self, alias_dir):
        pipe = Pipeline(
            profile="my_alias", extra_profile_paths=(str(alias_dir),)
        )
        doc = pipe.parse_text("字")
        assert doc.metadata["profile"] == "cn_current"
        assert doc.metadata["profile_requested"] == "my_alias"

    def test_translate_document_replaces_stale_requested(self, alias_dir):
        # A doc first stamped by an alias pipeline, re-translated by the
        # plain one: profile_requested must not survive as a stale lie.
        alias_pipe = Pipeline(
            profile="my_alias", extra_profile_paths=(str(alias_dir),)
        )
        doc = alias_pipe.parse_text("字")
        assert doc.metadata["profile_requested"] == "my_alias"
        Pipeline(profile="cn_current").translate_document(doc)
        assert doc.metadata["profile"] == "cn_current"
        assert "profile_requested" not in doc.metadata


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
