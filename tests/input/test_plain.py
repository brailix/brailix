"""Tests for :mod:`brailix.input.plain` — the paragraph-splitting wrapper.

The plain adapter hands the Pipeline a :class:`DocumentIR` whose blocks
are one :class:`Paragraph` per blank-line-separated paragraph. Tests pin:
span computation (None for empty, exact range otherwise), metadata
propagation, blank-line splitting, single-newline-stays-in-paragraph, and
the ``text[span] == block.text`` provenance invariant.
"""

from __future__ import annotations

from brailix.core.span import Span
from brailix.input.plain import parse_plain
from brailix.ir.document import Paragraph


class TestParsePlain:
    def test_single_paragraph_wraps_one_block_with_span(self):
        doc = parse_plain("我在重庆。")
        assert len(doc.blocks) == 1
        block = doc.blocks[0]
        assert isinstance(block, Paragraph)
        assert block.text == "我在重庆。"
        # Span covers the whole input — character indices, not bytes.
        assert block.span == Span(0, len("我在重庆。"))

    def test_empty_text_omits_span(self):
        # An empty string has nothing to point at; the wrapper keeps
        # span=None so downstream tooling doesn't render a zero-length
        # source range as if it were meaningful.
        doc = parse_plain("")
        assert len(doc.blocks) == 1
        block = doc.blocks[0]
        assert block.text == ""
        assert block.span is None

    def test_whitespace_only_falls_back_to_single_block(self):
        doc = parse_plain("   \n\n   ")
        assert len(doc.blocks) == 1
        assert doc.blocks[0].text == "   \n\n   "

    def test_metadata_carries_profile_and_language(self):
        doc = parse_plain("hi", language="en", profile="ueb")
        assert doc.metadata["language"] == "en"
        assert doc.metadata["profile"] == "ueb"

    def test_metadata_defaults_when_not_specified(self):
        doc = parse_plain("hi")
        assert "language" in doc.metadata
        assert "profile" in doc.metadata


class TestParagraphSplitting:
    def test_blank_line_splits_into_paragraphs(self):
        doc = parse_plain("第一段。\n\n第二段。")
        assert [b.text for b in doc.blocks] == ["第一段。", "第二段。"]
        # Spans point back to each paragraph's exact source range.
        assert doc.blocks[0].span == Span(0, 4)
        assert doc.blocks[1].span == Span(6, 10)

    def test_single_newline_stays_inside_paragraph(self):
        # One newline is a soft break — the paragraph keeps the newline
        # (the segmenter renders it as a word-boundary space later).
        doc = parse_plain("甲\n乙")
        assert len(doc.blocks) == 1
        assert doc.blocks[0].text == "甲\n乙"
        assert doc.blocks[0].span == Span(0, 3)

    def test_multiple_blank_lines_collapse_to_one_separator(self):
        doc = parse_plain("甲\n\n\n乙")
        assert [b.text for b in doc.blocks] == ["甲", "乙"]
        assert doc.blocks[1].span == Span(4, 5)

    def test_leading_and_trailing_blank_lines_dropped(self):
        doc = parse_plain("\n\n甲乙\n\n")
        assert [b.text for b in doc.blocks] == ["甲乙"]
        # Span still anchors to the real position after the leading blanks.
        assert doc.blocks[0].span == Span(2, 4)

    def test_blank_line_with_spaces_is_a_separator(self):
        doc = parse_plain("甲\n  \n乙")
        assert [b.text for b in doc.blocks] == ["甲", "乙"]

    def test_span_text_invariant_holds_for_every_block(self):
        # The proofread layer relies on text[span] == block.text so a
        # per-cell source offset maps back to the right character.
        src = "  缩进段落  \n\n第二段有内容。\n续行。\n\n\n第三段。"
        doc = parse_plain(src)
        for block in doc.blocks:
            assert block.span is not None
            assert src[block.span.start : block.span.end] == block.text
