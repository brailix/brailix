from __future__ import annotations

import sys
import types

import pytest

from brailix import Pipeline
from brailix.core.span import Span
from brailix.frontend.zh.analyzer.registry import analyzer_registry
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken, Word


@pytest.fixture(autouse=True)
def _clear_adapter_caches():
    # Resolver cache holds the loaded adapter instance; clear before
    # and after each test so the next monkeypatch of g2pw/pypinyin
    # forces a fresh import.
    resolver_registry.clear_cache()
    yield
    resolver_registry.clear_cache()


def test_default_pipeline_uses_pypinyin_when_available(monkeypatch):
    fake_module = types.ModuleType("pypinyin")

    class _Style:
        TONE3 = "TONE3"

    def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
        assert text == "\u6211"
        assert style == "TONE3"
        assert neutral_tone_with_five is True
        return ["wo3"]

    fake_module.Style = _Style
    fake_module.lazy_pinyin = fake_lazy_pinyin
    monkeypatch.setitem(sys.modules, "g2pM", None)  # note capital M
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", fake_module)

    result = Pipeline().translate_text("\u6211")
    child = result.ir.blocks[0].children[0]
    codes = {w.code for w in result.warnings}

    assert getattr(child, "reading", None) == "wo3"
    assert "MISSING_PINYIN" not in codes
    assert "\u2800" not in result.render()


def test_default_pipeline_uses_g2pw_when_available(monkeypatch):
    fake_g2pw = types.ModuleType("g2pw")

    class _FakeConverter:
        def __call__(self, text):
            assert text == "\u6211"
            return (["wo3"], [0.98])

    fake_g2pw.G2PWConverter = _FakeConverter
    monkeypatch.setitem(sys.modules, "g2pM", None)  # so g2pw wins the chain
    monkeypatch.setitem(sys.modules, "g2pw", fake_g2pw)
    monkeypatch.setitem(sys.modules, "pypinyin", None)

    result = Pipeline().translate_text("\u6211")
    child = result.ir.blocks[0].children[0]
    codes = {w.code for w in result.warnings}

    assert getattr(child, "reading", None) == "wo3"
    assert "MISSING_PINYIN" not in codes


def test_default_pipeline_falls_back_without_real_backends(monkeypatch):
    monkeypatch.setitem(sys.modules, "g2pM", None)
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", None)

    result = Pipeline().translate_text("\u6211")
    child = result.ir.blocks[0].children[0]

    assert getattr(child, "reading", None) is None
    assert any(w.code == "MISSING_PINYIN" for w in result.warnings)


def test_pipeline_preserves_multi_char_confidence():
    class _OneWordAnalyzer:
        name = "confidence-analyzer"

        def analyze(self, text, ctx):
            return [
                ChineseToken(
                    surface=text,
                    pos="n",
                    span=Span(0, len(text)),
                )
            ]

    class _ConfidenceResolver:
        name = "confidence-resolver"

        def resolve(self, tokens, ctx):
            return [
                ChineseToken(
                    surface=tokens[0].surface,
                    pos=tokens[0].pos,
                    span=tokens[0].span,
                    pinyin="ni3 hao3",
                    confidence=0.67,
                )
            ]

    analyzer_registry.register("confidence-test", _OneWordAnalyzer)
    resolver_registry.register("confidence-test", _ConfidenceResolver)
    try:
        result = Pipeline(
            analyzer="confidence-test",
            resolver="confidence-test",
        ).translate_text("\u4f60\u597d")
        child = result.ir.blocks[0].children[0]

        assert isinstance(child, Word)
        assert child.confidence == 0.67
    finally:
        analyzer_registry.unregister("confidence-test")
        resolver_registry.unregister("confidence-test")
