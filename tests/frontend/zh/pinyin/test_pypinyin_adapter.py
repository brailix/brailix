from __future__ import annotations

import sys
import types

import pytest

from brailix.core.errors import MissingExtraError
from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters.pypinyin import PypinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken


def test_missing_pypinyin_surfaces_missing_extra_error(monkeypatch):
    resolver_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "pypinyin", None)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("pypinyin")
    assert ei.value.extra == "pypinyin"
    assert "pip install brailix[pypinyin]" in str(ei.value)


class TestResolve:
    def test_empty(self):
        adapter = PypinyinResolver(converter=lambda _: [])
        assert adapter.resolve([]) == []

    def test_single_char_tokens(self):
        tokens = [
            ChineseToken(surface="我", span=Span(0, 1)),
            ChineseToken(surface="在", span=Span(1, 2)),
        ]
        adapter = PypinyinResolver(converter=lambda _: ["wo3", "zai4"])
        out = adapter.resolve(tokens)
        assert [t.pinyin for t in out] == ["wo3", "zai4"]

    def test_multi_char_token_joins(self):
        tokens = [ChineseToken(surface="重庆", span=Span(0, 2))]
        adapter = PypinyinResolver(converter=lambda _: ["chong2", "qing4"])
        out = adapter.resolve(tokens)
        assert out[0].pinyin == "chong2 qing4"

    def test_confidence_always_none(self):
        # pypinyin doesn't expose confidence scores.
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = PypinyinResolver(converter=lambda _: ["wo3"])
        out = adapter.resolve(tokens)
        assert out[0].confidence is None

    def test_does_not_mutate_input(self):
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = PypinyinResolver(converter=lambda _: ["wo3"])
        adapter.resolve(tokens)
        assert tokens[0].pinyin is None


class TestProtocolConformance:
    def test_satisfies_protocol(self):
        from brailix.core.protocols import PinyinResolver

        adapter = PypinyinResolver(converter=lambda _: [])
        assert isinstance(adapter, PinyinResolver)


class TestLoaderWithFakeModule:
    def test_load_wires_lazy_pinyin(self, monkeypatch):
        """``_load`` must produce a converter that delegates to
        ``pypinyin.lazy_pinyin`` with the expected style flags."""
        fake_module = types.ModuleType("pypinyin")

        class _Style:
            TONE3 = "TONE3"

        captured: dict = {}

        def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
            captured["text"] = text
            captured["style"] = style
            captured["neutral_tone_with_five"] = neutral_tone_with_five
            return ["wo3"]

        fake_module.Style = _Style
        fake_module.lazy_pinyin = fake_lazy_pinyin
        monkeypatch.setitem(sys.modules, "pypinyin", fake_module)
        resolver_registry.clear_cache()

        adapter = resolver_registry.get("pypinyin")
        assert isinstance(adapter, PypinyinResolver)
        # Run the converter — it should call our fake with TONE3.
        assert adapter.converter("我") == ["wo3"]
        assert captured == {
            "text": "我",
            "style": "TONE3",
            "neutral_tone_with_five": True,
        }
