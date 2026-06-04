from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters.null import NullPinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken


class TestNullResolver:
    def test_empty(self):
        assert NullPinyinResolver().resolve([]) == []

    def test_passes_through_without_adding_pinyin(self):
        tokens = [
            ChineseToken(surface="我", span=Span(0, 1)),
            ChineseToken(surface="在", span=Span(1, 2)),
        ]
        out = NullPinyinResolver().resolve(tokens)
        assert [t.pinyin for t in out] == [None, None]
        assert [t.surface for t in out] == ["我", "在"]

    def test_returns_new_list(self):
        tokens = [ChineseToken(surface="我")]
        out = NullPinyinResolver().resolve(tokens)
        assert out is not tokens


class TestRegistry:
    def test_null_registered(self):
        assert resolver_registry.has("null")
        inst = resolver_registry.get("null")
        assert inst.name == "null"

    def test_builtin_resolvers_registered(self):
        assert resolver_registry.has("auto")
        assert resolver_registry.has("g2pw")
        assert resolver_registry.has("null")
        assert resolver_registry.has("pypinyin")
