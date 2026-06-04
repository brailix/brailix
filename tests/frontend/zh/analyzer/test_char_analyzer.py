from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.zh.analyzer.adapters.char import CharChineseAnalyzer
from brailix.frontend.zh.analyzer.registry import analyzer_registry


class TestCharAnalyzer:
    def test_empty(self):
        assert CharChineseAnalyzer().analyze("") == []

    def test_each_char_becomes_token(self):
        tokens = CharChineseAnalyzer().analyze("我在重庆")
        assert [t.surface for t in tokens] == ["我", "在", "重", "庆"]

    def test_spans_are_per_character(self):
        tokens = CharChineseAnalyzer().analyze("abc")
        assert [t.span for t in tokens] == [Span(0, 1), Span(1, 2), Span(2, 3)]

    def test_pos_is_none(self):
        # char-level fallback knows no POS tags.
        for t in CharChineseAnalyzer().analyze("我"):
            assert t.pos is None

    def test_pinyin_is_none_until_resolver_runs(self):
        for t in CharChineseAnalyzer().analyze("我"):
            assert t.pinyin is None

    def test_accepts_context(self):
        ctx = FrontendContext()
        tokens = CharChineseAnalyzer().analyze("我", ctx)
        assert tokens[0].surface == "我"


class TestRegistry:
    def test_char_registered(self):
        assert analyzer_registry.has("char")
        inst = analyzer_registry.get("char")
        assert inst.name == "char"

    def test_char_is_zero_dependency(self):
        # Calling the loader must not raise — pure stdlib.
        inst = analyzer_registry.get("char")
        assert inst.analyze("好") == [
            type(inst.analyze("好")[0])(surface="好", span=Span(0, 1))
        ]

    def test_hanlp_registered_with_extra(self):
        assert analyzer_registry.has("hanlp")
