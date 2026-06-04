"""Tests for the g2pM pinyin adapter.

Covers the lazy-import / missing-extra contract, per-token alignment,
the length-mismatch guard, and — most importantly — the ``u:`` → ``ü``
normalization the backend's :func:`parse_pinyin` depends on (g2pM
spells ü as ``u:``, which the parser can't read). The alignment logic
uses an injected converter; the normalization is locked against the
*real* model (a small, bundled, numpy-only checkpoint) so a future g2pM
output-format change can't silently break braille.
"""

from __future__ import annotations

import sys

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError, RunMode
from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters.g2pm import G2pmPinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken


def test_missing_g2pm_surfaces_missing_extra_error(monkeypatch):
    resolver_registry.clear_cache()
    # The adapter does ``import g2pM`` (capital M = the PyPI import name),
    # so that's the module key we have to knock out.
    monkeypatch.setitem(sys.modules, "g2pM", None)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("g2pm")
    assert ei.value.extra == "g2pm"
    assert "pip install brailix[g2pm]" in str(ei.value)


class TestResolve:
    def test_empty(self):
        assert G2pmPinyinResolver(converter=lambda _: []).resolve([]) == []

    def test_single_char_tokens(self):
        tokens = [
            ChineseToken(surface="我", span=Span(0, 1)),
            ChineseToken(surface="在", span=Span(1, 2)),
        ]
        adapter = G2pmPinyinResolver(converter=lambda _: ["wo3", "zai4"])
        out = adapter.resolve(tokens)
        assert [t.pinyin for t in out] == ["wo3", "zai4"]

    def test_multi_char_token_joins(self):
        tokens = [ChineseToken(surface="重庆", span=Span(0, 2))]
        adapter = G2pmPinyinResolver(converter=lambda _: ["chong2", "qing4"])
        assert adapter.resolve(tokens)[0].pinyin == "chong2 qing4"

    def test_confidence_always_none(self):
        # g2pM, like pypinyin, exposes no confidence scores.
        adapter = G2pmPinyinResolver(converter=lambda _: ["wo3"])
        out = adapter.resolve([ChineseToken(surface="我", span=Span(0, 1))])
        assert out[0].confidence is None

    def test_does_not_mutate_input(self):
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        G2pmPinyinResolver(converter=lambda _: ["wo3"]).resolve(tokens)
        assert tokens[0].pinyin is None


class TestLengthMismatch:
    def test_mismatch_drops_pinyin_and_warns(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="重庆", span=Span(0, 2))]
        # Converter returns one syllable for a two-char sentence.
        adapter = G2pmPinyinResolver(converter=lambda _: ["chong2"])
        out = adapter.resolve(tokens, ctx)
        assert out[0].pinyin is None
        codes = {w.code for w in ctx.warnings}
        assert "PINYIN_LENGTH_MISMATCH" in codes


class TestProtocolConformance:
    def test_satisfies_protocol(self):
        from brailix.core.protocols import PinyinResolver

        adapter = G2pmPinyinResolver(converter=lambda _: [])
        assert isinstance(adapter, PinyinResolver)


class TestLoaderWithRealModel:
    """Lock the ``u:`` → ``ü`` normalization against the real model.

    g2pM ships as a small numpy-only checkpoint (bundled in the wheel,
    no download), so loading it in a test is cheap. Skips cleanly if the
    optional package isn't installed.
    """

    def test_load_normalizes_u_colon_and_disambiguates(self):
        pytest.importorskip("g2pM")
        resolver_registry.clear_cache()
        try:
            adapter = resolver_registry.get("g2pm")
            assert isinstance(adapter, G2pmPinyinResolver)
            # 女 is ``nu:3`` straight out of g2pM; the converter must
            # rewrite it to ``nü3`` or the finals lookup misses.
            assert adapter.converter("女") == ["nü3"]
            # End to end: a ü-rime word resolves to a parser-ready form.
            out = adapter.resolve([ChineseToken(surface="略", span=Span(0, 1))])
            assert out[0].pinyin == "lüe4"
        finally:
            resolver_registry.clear_cache()
