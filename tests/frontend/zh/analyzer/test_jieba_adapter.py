"""Tests for the JiebaChineseAnalyzer wrapper.

The adapter accepts an injectable ``tokenize_fn`` so we don't have to
install jieba just to verify the conversion logic. Pinning the empty-
input fast path and a typical tuple-stream conversion here keeps the
wrapper honest even on bare installs.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.zh.analyzer.adapters.jieba import JiebaChineseAnalyzer


def _fake_tokenize(text: str):
    """Mimic jieba.tokenize's (word, start, end) triple stream."""
    out = []
    cursor = 0
    for ch in text:
        out.append((ch, cursor, cursor + 1))
        cursor += 1
    return out


class TestJiebaChineseAnalyzer:
    def test_empty_input_short_circuits(self):
        # Empty text must NOT call tokenize_fn — that's the contract,
        # because some hosts inject a tokenize_fn that raises on "".
        def boom(text: str):
            raise AssertionError("tokenize_fn should not run for empty input")

        analyzer = JiebaChineseAnalyzer(tokenize_fn=boom)
        assert analyzer.analyze("") == []

    def test_tuple_stream_becomes_tokens_with_spans(self):
        analyzer = JiebaChineseAnalyzer(tokenize_fn=_fake_tokenize)
        tokens = analyzer.analyze("abc")
        assert [t.surface for t in tokens] == ["a", "b", "c"]
        assert [t.span for t in tokens] == [Span(0, 1), Span(1, 2), Span(2, 3)]
        # Adapter never invents POS — that's the HanLP path's job.
        assert all(t.pos is None for t in tokens)

    def test_accepts_optional_context(self):
        analyzer = JiebaChineseAnalyzer(tokenize_fn=_fake_tokenize)
        ctx = FrontendContext()
        # The adapter ignores ctx but should accept it without raising.
        tokens = analyzer.analyze("x", ctx)
        assert tokens[0].surface == "x"
