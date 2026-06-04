"""Tests for :mod:`brailix.input.markdown` — the Markdown subset
parser. Focus: each block type lands as the right IR class with
plausible ``text`` content. Inline parsing is intentionally not
performed here so we don't assert on inline shape."""

import pytest

from brailix.input.markdown import parse_markdown
from brailix.ir.document import (
    CodeBlock,
    Heading,
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
        doc = parse_markdown(src)
        assert isinstance(doc.blocks[0], Heading)
        assert doc.blocks[0].level == level
        assert doc.blocks[0].text == expected_text


class TestParagraph:
    def test_single_line(self):
        doc = parse_markdown("简单一段")
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "简单一段"

    def test_multiline_joined_with_spaces(self):
        doc = parse_markdown("第一行\n第二行")
        # Soft-break: lines join with a single space.
        assert doc.blocks[0].text == "第一行 第二行"

    def test_blank_line_separates_paragraphs(self):
        doc = parse_markdown("段一\n\n段二")
        assert len(doc.blocks) == 2
        assert all(isinstance(b, Paragraph) for b in doc.blocks)


class TestList:
    def test_unordered_list_with_dash(self):
        src = "- 一项\n- 二项\n- 三项"
        doc = parse_markdown(src)
        lst = doc.blocks[0]
        assert isinstance(lst, List)
        assert not lst.ordered
        assert [it.text for it in lst.items] == ["一项", "二项", "三项"]

    def test_unordered_list_with_asterisk(self):
        src = "* item one\n* item two"
        doc = parse_markdown(src)
        assert isinstance(doc.blocks[0], List)
        assert [it.text for it in doc.blocks[0].items] == ["item one", "item two"]

    def test_unordered_list_with_plus(self):
        doc = parse_markdown("+ a\n+ b")
        assert isinstance(doc.blocks[0], List)
        assert len(doc.blocks[0].items) == 2

    def test_ordered_list_with_period(self):
        doc = parse_markdown("1. 一\n2. 二\n3. 三")
        lst = doc.blocks[0]
        assert isinstance(lst, List)
        assert lst.ordered
        assert [it.text for it in lst.items] == ["一", "二", "三"]

    def test_ordered_list_with_paren(self):
        doc = parse_markdown("1) first\n2) second")
        lst = doc.blocks[0]
        assert isinstance(lst, List) and lst.ordered

    def test_blank_line_terminates_list(self):
        doc = parse_markdown("- a\n- b\n\nparagraph")
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], List)
        assert isinstance(doc.blocks[1], Paragraph)


class TestQuote:
    def test_single_line_quote(self):
        doc = parse_markdown("> 引用一句")
        assert isinstance(doc.blocks[0], Quote)
        assert doc.blocks[0].text == "引用一句"

    def test_multiline_quote(self):
        doc = parse_markdown("> 第一行\n> 第二行")
        assert doc.blocks[0].text == "第一行\n第二行"

    def test_blank_line_terminates_quote(self):
        doc = parse_markdown("> 引文\n\nparagraph")
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], Quote)
        assert isinstance(doc.blocks[1], Paragraph)


class TestCodeBlock:
    def test_fenced_code_block_basic(self):
        src = "```\nprint(1)\nprint(2)\n```"
        doc = parse_markdown(src)
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.text == "print(1)\nprint(2)"

    def test_fenced_code_block_with_language(self):
        src = "```python\nx = 1\n```"
        doc = parse_markdown(src)
        cb = doc.blocks[0]
        assert isinstance(cb, CodeBlock)
        assert cb.language == "python"
        assert cb.text == "x = 1"

    def test_unterminated_fence_consumes_to_eof(self):
        # No closing fence: we still parse the block and continue past EOF
        # gracefully (no crash on dangling code).
        src = "```\nno close"
        doc = parse_markdown(src)
        assert isinstance(doc.blocks[0], CodeBlock)


class TestMathBlock:
    def test_single_line_dollar_dollar(self):
        doc = parse_markdown("$$x + y = z$$")
        assert isinstance(doc.blocks[0], MathBlock)
        assert doc.blocks[0].source == "latex"
        assert doc.blocks[0].text == "x + y = z"

    def test_multiline_dollar_dollar(self):
        src = "$$\n\\frac{1}{2}\n$$"
        doc = parse_markdown(src)
        mb = doc.blocks[0]
        assert isinstance(mb, MathBlock)
        assert "\\frac" in mb.text

    def test_unterminated_dollar_treated_as_paragraph(self):
        # Opening $$ without a close → fall back to paragraph so we
        # don't swallow the rest of the document silently.
        doc = parse_markdown("$$ no close\nnext line")
        # Either a paragraph or a code-like block — we just assert
        # it's not a runaway math block that ate everything.
        assert not isinstance(doc.blocks[0], MathBlock)


class TestTable:
    def test_basic_table_two_rows(self):
        src = "| A | B |\n| - | - |\n| 1 | 2 |"
        doc = parse_markdown(src)
        t = doc.blocks[0]
        assert isinstance(t, Table)
        # Separator row dropped → 2 content rows.
        assert len(t.rows) == 2
        assert [c.text for c in t.rows[0].cells] == ["A", "B"]
        assert [c.text for c in t.rows[1].cells] == ["1", "2"]

    def test_table_without_separator(self):
        src = "| a | b |\n| c | d |"
        doc = parse_markdown(src)
        t = doc.blocks[0]
        assert isinstance(t, Table)
        assert len(t.rows) == 2

    def test_blank_terminates_table(self):
        src = "| a | b |\n\nparagraph"
        doc = parse_markdown(src)
        assert isinstance(doc.blocks[0], Table)
        assert isinstance(doc.blocks[1], Paragraph)


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
        doc = parse_markdown(src)
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
        doc = parse_markdown(text)
        assert doc.blocks[0].span is not None
        assert doc.blocks[0].span.start == 0

    def test_blocks_have_distinct_spans(self):
        text = "段一\n\n段二"
        doc = parse_markdown(text)
        assert doc.blocks[0].span.end <= doc.blocks[1].span.start


class TestMetadata:
    def test_metadata_defaults(self):
        doc = parse_markdown("test")
        assert doc.metadata["language"] == "zh-CN"
        assert doc.metadata["profile"] == "cn_current"

    def test_metadata_override(self):
        doc = parse_markdown("hi", language="en-US", profile="ueb_math")
        assert doc.metadata["language"] == "en-US"
        assert doc.metadata["profile"] == "ueb_math"
