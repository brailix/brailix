"""Tests for :mod:`brailix.input.markdown` — the Markdown subset
parser. Focus: each block type lands as the right IR class with
plausible ``text`` content. Inline parsing is intentionally not
performed here so we don't assert on inline shape."""

import pytest

from brailix.input.markdown import (
    image_alt_line,
    parse_markdown,
    safe_block_insertion_offset,
)
from brailix.ir.document import (
    CodeBlock,
    Heading,
    ImageAlt,
    List,
    MathBlock,
    Paragraph,
    Quote,
    Table,
)


class TestHeading:
    @pytest.mark.parametrize(
        "src, level, expected_text",
        [
            ("# Title", 1, "Title"),
            ("## Level two", 2, "Level two"),
            ("### Three", 3, "Three"),
            ("###### Six", 6, "Six"),
            ("# 中文标题 ###", 1, "中文标题"),  # trailing # is decoration
        ],
    )
    def test_heading_level_and_text(self, src, level, expected_text):
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)
        assert doc.blocks[0].level == level
        assert doc.blocks[0].text == expected_text


class TestAlignment:
    """Trailing ``{align=center|right}`` attribute → ``Block.align``,
    stripped from the text. This is the channel that carries a centred /
    right-aligned Word block through a docx→markdown→re-parse import
    round-trip."""

    def test_centered_heading(self):
        doc = parse_markdown("# 通知 {align=center}", profile="cn_current", language="zh-CN")
        h = doc.blocks[0]
        assert isinstance(h, Heading)
        assert h.level == 1
        assert h.text == "通知"
        assert h.align == "center"

    def test_right_aligned_paragraph(self):
        doc = parse_markdown("二〇二六年五月 {align=right}", profile="cn_current", language="zh-CN")
        p = doc.blocks[0]
        assert isinstance(p, Paragraph)
        assert p.text == "二〇二六年五月"
        assert p.align == "right"

    def test_plain_block_has_no_align(self):
        assert parse_markdown("# 标题", profile="cn_current", language="zh-CN").blocks[0].align is None
        assert parse_markdown("普通段落", profile="cn_current", language="zh-CN").blocks[0].align is None

    def test_unrecognised_align_value_stays_literal(self):
        # Only center / right are alignment; anything else is left as text
        # so prose mentioning braces isn't silently eaten.
        p = parse_markdown("正文 {align=justify}", profile="cn_current", language="zh-CN").blocks[0]
        assert p.align is None
        assert p.text == "正文 {align=justify}"

    def test_align_on_last_line_of_multiline_paragraph(self):
        # The emitter appends the marker at the paragraph's end; after the
        # soft-break join it sits at the tail of the body and is stripped.
        p = parse_markdown("第一行\n第二行 {align=center}", profile="cn_current", language="zh-CN").blocks[0]
        assert p.text == "第一行 第二行"
        assert p.align == "center"


class TestParagraph:
    def test_single_line(self):
        doc = parse_markdown("简单一段", profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "简单一段"

    def test_multiline_joined_with_spaces(self):
        doc = parse_markdown("第一行\n第二行", profile="cn_current", language="zh-CN")
        # Soft-break: lines join with a single space.
        assert doc.blocks[0].text == "第一行 第二行"

    def test_blank_line_separates_paragraphs(self):
        doc = parse_markdown("段一\n\n段二", profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 2
        assert all(isinstance(b, Paragraph) for b in doc.blocks)


class TestList:
    def test_unordered_list_with_dash(self):
        src = "- 一项\n- 二项\n- 三项"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        lst = doc.blocks[0]
        assert isinstance(lst, List)
        assert not lst.ordered
        assert [it.text for it in lst.items] == ["一项", "二项", "三项"]

    def test_unordered_list_with_asterisk(self):
        src = "* item one\n* item two"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], List)
        assert [it.text for it in doc.blocks[0].items] == ["item one", "item two"]

    def test_unordered_list_with_plus(self):
        doc = parse_markdown("+ a\n+ b", profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], List)
        assert len(doc.blocks[0].items) == 2

    def test_ordered_list_with_period(self):
        doc = parse_markdown("1. 一\n2. 二\n3. 三", profile="cn_current", language="zh-CN")
        lst = doc.blocks[0]
        assert isinstance(lst, List)
        assert lst.ordered
        assert [it.text for it in lst.items] == ["一", "二", "三"]

    def test_ordered_list_with_paren(self):
        doc = parse_markdown("1) first\n2) second", profile="cn_current", language="zh-CN")
        lst = doc.blocks[0]
        assert isinstance(lst, List) and lst.ordered

    def test_blank_line_terminates_list(self):
        doc = parse_markdown("- a\n- b\n\nparagraph", profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], List)
        assert isinstance(doc.blocks[1], Paragraph)


class TestQuote:
    def test_single_line_quote(self):
        doc = parse_markdown("> 引用一句", profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Quote)
        assert doc.blocks[0].text == "引用一句"

    def test_multiline_quote(self):
        doc = parse_markdown("> 第一行\n> 第二行", profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "第一行\n第二行"

    def test_blank_line_terminates_quote(self):
        doc = parse_markdown("> 引文\n\nparagraph", profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], Quote)
        assert isinstance(doc.blocks[1], Paragraph)


class TestCodeBlock:
    def test_fenced_code_block_basic(self):
        src = "```\nprint(1)\nprint(2)\n```"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.text == "print(1)\nprint(2)"

    def test_fenced_code_block_with_language(self):
        src = "```python\nx = 1\n```"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.language == "python"
        assert cb.text == "x = 1"

    def test_unterminated_fence_consumes_to_eof(self):
        # No closing fence: we still parse the block and continue past EOF
        # gracefully (no crash on dangling code).
        src = "```\nno close"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], CodeBlock)


class TestMathBlock:
    def test_single_line_dollar_dollar(self):
        doc = parse_markdown("$$x + y = z$$", profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], MathBlock)
        assert doc.blocks[0].source == "latex"
        assert doc.blocks[0].text == "x + y = z"

    def test_multiline_dollar_dollar(self):
        src = "$$\n\\frac{1}{2}\n$$"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        mb = doc.blocks[0]
        assert isinstance(mb, MathBlock)
        assert "\\frac" in mb.text

    def test_unterminated_dollar_treated_as_paragraph(self):
        # Opening $$ without a close → fall back to paragraph so we
        # don't swallow the rest of the document silently.
        doc = parse_markdown("$$ no close\nnext line", profile="cn_current", language="zh-CN")
        # Either a paragraph or a code-like block — we just assert
        # it's not a runaway math block that ate everything.
        assert not isinstance(doc.blocks[0], MathBlock)


class TestTable:
    def test_basic_table_two_rows(self):
        src = "| A | B |\n| - | - |\n| 1 | 2 |"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        t = doc.blocks[0]
        assert isinstance(t, Table)
        # Separator row dropped → 2 content rows.
        assert len(t.rows) == 2
        assert [c.text for c in t.rows[0].cells] == ["A", "B"]
        assert [c.text for c in t.rows[1].cells] == ["1", "2"]

    def test_table_without_separator(self):
        src = "| a | b |\n| c | d |"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        t = doc.blocks[0]
        assert isinstance(t, Table)
        assert len(t.rows) == 2

    def test_blank_terminates_table(self):
        src = "| a | b |\n\nparagraph"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Table)
        assert isinstance(doc.blocks[1], Paragraph)

    def test_separator_only_line_does_not_crash(self):
        # Regression: a lone separator row yields no body rows. The table
        # consumer used to advance the cursor and then return None,
        # stranding it past EOF and crashing span_of with IndexError.
        # Now it leaves the cursor untouched and the line falls through to
        # a paragraph carrying the literal text.
        doc = parse_markdown("| --- |", profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "| --- |"

    def test_separator_only_at_eof_after_paragraph(self):
        doc = parse_markdown("para\n\n| --- |", profile="cn_current", language="zh-CN")
        assert [type(b).__name__ for b in doc.blocks] == ["Paragraph", "Paragraph"]
        assert doc.blocks[1].text == "| --- |"

    def test_separator_only_then_content_no_ghost_paragraph(self):
        # The separator falls through to a paragraph; the real content
        # that follows is its own block. No empty Paragraph is injected.
        doc = parse_markdown("| --- |\n\nhello", profile="cn_current", language="zh-CN")
        assert [type(b).__name__ for b in doc.blocks] == ["Paragraph", "Paragraph"]
        assert doc.blocks[0].text == "| --- |"
        assert doc.blocks[1].text == "hello"
        assert all(b.text for b in doc.blocks)  # no ghost empty paragraph

    def test_body_row_of_dashes_kept_as_data(self):
        # A later body row that is all dashes (placeholder "-" cells) must
        # NOT be mistaken for the header/body separator and dropped — only
        # the separator at row index 1 is removed.
        src = "| A | B |\n| - | - |\n| 1 | 2 |\n| - | - |"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        t = doc.blocks[0]
        assert isinstance(t, Table)
        # header + separator(dropped) + 2 body rows (incl. the all-dash one)
        assert len(t.rows) == 3
        assert [c.text for c in t.rows[0].cells] == ["A", "B"]
        assert [c.text for c in t.rows[1].cells] == ["1", "2"]
        assert [c.text for c in t.rows[2].cells] == ["-", "-"]


class TestTableParagraphInteraction:
    def test_table_immediately_after_paragraph_is_absorbed(self):
        # Known limitation: _starts_block deliberately omits the table prefix
        # (a stray "|" in prose shouldn't end a paragraph), so a table line
        # directly after a paragraph line — no blank line between — joins into
        # the paragraph instead of starting a Table.
        doc = parse_markdown("正文一段\n| A | B |\n| 1 | 2 |", profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], Paragraph)

    def test_blank_line_before_table_is_recognised(self):
        # With the blank line, the table is its own block.
        doc = parse_markdown("正文一段\n\n| A | B |\n| 1 | 2 |", profile="cn_current", language="zh-CN")
        assert [type(b).__name__ for b in doc.blocks] == ["Paragraph", "Table"]


class TestMixedDocument:
    def test_kitchen_sink(self):
        src = """# 大标题

这是一段引言。

## 二级标题

- 列表项一
- 列表项二

> 一段引文

```python
x = 1
```

$$x = y$$

| 列一 | 列二 |
| --- | --- |
| 1 | 2 |
"""
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        types = [type(b).__name__ for b in doc.blocks]
        # Heading, Paragraph, Heading, List, Quote, CodeBlock, MathBlock, Table
        assert "Heading" in types
        assert "Paragraph" in types
        assert "List" in types
        assert "Quote" in types
        assert "CodeBlock" in types
        assert "MathBlock" in types
        assert "Table" in types


class TestSpans:
    def test_paragraph_span_covers_source(self):
        text = "一段话"
        doc = parse_markdown(text, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].span is not None
        assert doc.blocks[0].span.start == 0

    def test_blocks_have_distinct_spans(self):
        text = "段一\n\n段二"
        doc = parse_markdown(text, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].span.end <= doc.blocks[1].span.start


CIRCLE = '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'


class TestSafeBlockInsertionOffset:
    def test_caret_inside_graphic_fence_snaps_past_it(self):
        src = f"intro\n\n```graphic-svg\n{CIRCLE}\n```\n\nafter"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        fence = next(b for b in doc.blocks if b.span and "```" in src[b.span.start : b.span.end])
        inside = src.index("<circle")
        assert fence.span.start < inside < fence.span.end
        assert safe_block_insertion_offset(src, inside) == fence.span.end

    def test_caret_in_interblock_gap_is_unchanged(self):
        src = "para one\n\npara two"
        gap = src.index("\n\npara two")
        assert safe_block_insertion_offset(src, gap) == gap

    def test_caret_inside_paragraph_snaps_to_its_end(self):
        src = "a long paragraph here\n\ntail"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        para = doc.blocks[0]
        mid = 5
        assert para.span.start < mid < para.span.end
        assert safe_block_insertion_offset(src, mid) == para.span.end

    def test_out_of_range_is_clamped(self):
        src = "abc"
        assert safe_block_insertion_offset(src, -5) == 0
        assert safe_block_insertion_offset(src, 999) == len(src)

    def test_empty_source_returns_clamped(self):
        assert safe_block_insertion_offset("", 0) == 0
        assert safe_block_insertion_offset("   ", 1) == 1


class TestImagePlaceholder:
    """``![alt](target)`` on its own line → :class:`ImageAlt` (the
    not-yet-converted-image placeholder, ARCHITECTURE.md).
    A figure is always a fence; this is a *placeholder*, not graphics syntax."""

    def test_line_becomes_image_alt(self):
        doc = parse_markdown(
            "![一张地图](media/image1.png)", profile="cn_current", language="zh-CN"
        )
        (block,) = doc.blocks
        assert isinstance(block, ImageAlt)
        assert block.text == "一张地图"
        assert block.target == "media/image1.png"

    def test_empty_target_is_none(self):
        # An alt-only placeholder (image bytes were unrecoverable at import).
        doc = parse_markdown("![描述]()", profile="cn_current", language="zh-CN")
        (block,) = doc.blocks
        assert isinstance(block, ImageAlt)
        assert block.text == "描述"
        assert block.target is None

    def test_span_covers_the_line(self):
        doc = parse_markdown(
            "![x](p.png)", profile="cn_current", language="zh-CN"
        )
        assert doc.blocks[0].span is not None
        assert doc.blocks[0].span.start == 0

    def test_inline_image_in_prose_stays_paragraph(self):
        # Only a whole-line image is a placeholder; an inline ``![...]`` is
        # ordinary text (the subset rule), never a block.
        doc = parse_markdown(
            "见 ![图](p.png) 所示", profile="cn_current", language="zh-CN"
        )
        (block,) = doc.blocks
        assert isinstance(block, Paragraph)
        assert not any(isinstance(b, ImageAlt) for b in doc.blocks)

    def test_image_line_splits_a_preceding_paragraph(self):
        # An image line right after a paragraph line (no blank line) still
        # starts its own block — _starts_block knows the image prefix.
        doc = parse_markdown(
            "正文\n![图](p.png)", profile="cn_current", language="zh-CN"
        )
        kinds = [type(b).__name__ for b in doc.blocks]
        assert kinds == ["Paragraph", "ImageAlt"]

    def test_target_with_paren_survives(self):
        # The greedy target group ends at the line's final paren, so a path
        # containing ')' round-trips.
        doc = parse_markdown(
            "![p](a(1).png)", profile="cn_current", language="zh-CN"
        )
        assert doc.blocks[0].target == "a(1).png"


class TestImageAltLine:
    """``image_alt_line`` — the compose direction, single owner of the
    grammar (mirrors ``graphic_fence_open``)."""

    def test_round_trips_through_parse(self):
        line = image_alt_line("说明文字", "media/image2.jpeg")
        doc = parse_markdown(line, profile="cn_current", language="zh-CN")
        (block,) = doc.blocks
        assert isinstance(block, ImageAlt)
        assert block.text == "说明文字"
        assert block.target == "media/image2.jpeg"

    def test_none_target_emits_alt_only_form(self):
        assert image_alt_line("alt", None) == "![alt]()"

    def test_newline_and_bracket_in_alt_are_flattened(self):
        # The parse direction can't read a newline (line-based) or a ']'
        # (unescapable), so compose collapses / substitutes them and the
        # result must still round-trip to one placeholder.
        line = image_alt_line("多\n行[标签]", "p.png")
        assert "\n" not in line
        doc = parse_markdown(line, profile="cn_current", language="zh-CN")
        (block,) = doc.blocks
        assert isinstance(block, ImageAlt)
        assert block.target == "p.png"


class TestMetadata:
    def test_metadata_defaults(self):
        doc = parse_markdown("test", profile="cn_current", language="zh-CN")
        assert doc.metadata["language"] == "zh-CN"
        assert doc.metadata["profile"] == "cn_current"

    def test_metadata_override(self):
        doc = parse_markdown("hi", language="en-US", profile="ueb_math")
        assert doc.metadata["language"] == "en-US"
        assert doc.metadata["profile"] == "ueb_math"
