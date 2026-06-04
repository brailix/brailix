"""Tests for :mod:`brailix.input.plain` — the single-paragraph wrapper.

The plain adapter is a tiny wrapper that hands the Pipeline a
:class:`DocumentIR` shell with one :class:`Paragraph`. Tests pin the
two real behaviours: span computation (None for empty input, full
range for non-empty) and metadata propagation.
"""

from __future__ import annotations

from brailix.core.span import Span
from brailix.input.plain import parse_plain
from brailix.ir.document import Paragraph


class TestParsePlain:
    def test_non_empty_text_wraps_one_paragraph_with_span(self):
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

    def test_metadata_carries_profile_and_language(self):
        doc = parse_plain("hi", language="en", profile="ueb")
        assert doc.metadata["language"] == "en"
        assert doc.metadata["profile"] == "ueb"

    def test_metadata_defaults_when_not_specified(self):
        doc = parse_plain("hi")
        assert "language" in doc.metadata
        assert "profile" in doc.metadata
