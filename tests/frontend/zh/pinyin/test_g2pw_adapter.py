"""Tests for the g2pW adapter (lazy import + token mapping + warnings)."""

from __future__ import annotations

import sys
import types

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError, RunMode
from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters.g2pw import (
    G2pwPinyinResolver,
    _normalize_predictor_output,
)
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken

# ---------------------------------------------------------------------------
# Lazy-import / missing-extra contract
# ---------------------------------------------------------------------------


def test_missing_g2pw_surfaces_missing_extra_error(monkeypatch):
    resolver_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "g2pw", None)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("g2pw")
    assert ei.value.extra == "g2pw"
    assert "pip install brailix[g2pw]" in str(ei.value)


# ---------------------------------------------------------------------------
# Predictor output normalization
# ---------------------------------------------------------------------------


class TestNormalizePredictorOutput:
    def test_tuple_form(self):
        py, conf = _normalize_predictor_output((["wo3", "zai4"], [0.9, 0.95]))
        assert py == ["wo3", "zai4"]
        assert conf == [0.9, 0.95]

    def test_list_only_form(self):
        py, conf = _normalize_predictor_output(["wo3", "zai4"])
        assert py == ["wo3", "zai4"]
        assert conf is None

    def test_tuple_with_none_confidence(self):
        py, conf = _normalize_predictor_output((["a"], None))
        assert conf is None


# ---------------------------------------------------------------------------
# Token mapping
# ---------------------------------------------------------------------------


def _predictor(syllables, confidences=None):
    def call(_text):
        if confidences is None:
            return syllables
        return (syllables, confidences)

    return call


class TestResolve:
    def test_single_char_tokens(self):
        tokens = [
            ChineseToken(surface="我", span=Span(0, 1)),
            ChineseToken(surface="在", span=Span(1, 2)),
        ]
        adapter = G2pwPinyinResolver(predictor=_predictor(["wo3", "zai4"]))
        out = adapter.resolve(tokens)
        assert [t.pinyin for t in out] == ["wo3", "zai4"]
        # original tokens are not mutated
        assert tokens[0].pinyin is None

    def test_multi_char_token_joins_syllables(self):
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
        ]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chong2", "qing4"]),
        )
        out = adapter.resolve(tokens)
        assert out[0].pinyin == "chong2 qing4"

    def test_empty(self):
        assert G2pwPinyinResolver(predictor=_predictor([])).resolve([]) == []

    def test_confidence_propagates_minimum(self):
        tokens = [ChineseToken(surface="重庆", span=Span(0, 2))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chong2", "qing4"], [0.99, 0.8]),
        )
        out = adapter.resolve(tokens)
        assert out[0].confidence == 0.8


class TestLowConfidenceWarning:
    def test_low_confidence_emits_warning(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="单于", span=Span(0, 2))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chan2", "yu2"], [0.5, 0.55]),
        )
        adapter.resolve(tokens, ctx)
        warnings = list(ctx.warnings)
        assert len(warnings) == 1
        assert warnings[0].code == "LOW_CONFIDENCE_PINYIN"
        assert warnings[0].surface == "单于"
        assert warnings[0].source == "pinyin.g2pw"

    def test_high_confidence_emits_nothing(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["wo3"], [0.99]),
        )
        adapter.resolve(tokens, ctx)
        assert len(ctx.warnings) == 0

    def test_threshold_is_configurable(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["wo3"], [0.6]),
            low_confidence_threshold=0.5,
        )
        adapter.resolve(tokens, ctx)
        assert len(ctx.warnings) == 0

    def test_no_ctx_no_warning(self):
        # When ctx is None we still process tokens, just silently.
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(predictor=_predictor(["wo3"], [0.1]))
        out = adapter.resolve(tokens)
        assert out[0].pinyin == "wo3"


class TestProtocolConformance:
    def test_satisfies_protocol(self):
        from brailix.core.protocols import PinyinResolver

        adapter = G2pwPinyinResolver(predictor=_predictor([]))
        assert isinstance(adapter, PinyinResolver)


class TestLoaderWithFakeModule:
    def test_load_wraps_g2pwconverter(self, monkeypatch):
        """When ``g2pw`` is importable, ``_load`` builds the resolver
        around a fresh :class:`G2PWConverter` instance."""
        fake_module = types.ModuleType("g2pw")

        class _FakeConverter:
            def __call__(self, _text: str):
                return (["wo3"], [0.95])

        fake_module.G2PWConverter = _FakeConverter
        monkeypatch.setitem(sys.modules, "g2pw", fake_module)
        resolver_registry.clear_cache()

        # Trigger lazy load via the registry — the same path users hit.
        adapter = resolver_registry.get("g2pw")
        assert isinstance(adapter, G2pwPinyinResolver)
        assert callable(adapter.predictor)
        py, conf = _normalize_predictor_output(adapter.predictor("我"))
        assert py == ["wo3"]
        assert conf == [0.95]
