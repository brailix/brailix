"""Tests for the g2pM pinyin adapter.

Covers the lazy-import / missing-extra contract and — most importantly —
the ``u:`` → ``ü`` normalization the backend's :func:`parse_pinyin`
depends on (g2pM spells ü as ``u:``, which the parser can't read),
locked against the *real* model (a small, bundled, numpy-only
checkpoint) so a future g2pM output-format change can't silently break
braille.

Per-token alignment, the length-mismatch guard and the structural
resolver contract are property-tested for every registered resolver in
``test_resolver_contract_properties.py`` — no per-adapter copies here.
"""

from __future__ import annotations

import sys

import pytest

from brailix.core.errors import MissingExtraError
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


def test_g2pm_model_load_failure_surfaces_missing_extra_error(monkeypatch):
    # g2pM imports fine but its bundled-weight construction blows up (corrupt
    # .pkl / numpy mismatch / a frozen build missing the data file). That must
    # surface as MissingExtraError — not the raw RuntimeError — so the auto
    # chain can catch it and degrade instead of crashing the translation.
    import types

    fake_g2pm = types.ModuleType("g2pM")

    class _BoomModel:
        def __init__(self) -> None:
            raise RuntimeError("corrupt bundled weights")

    fake_g2pm.G2pM = _BoomModel
    resolver_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "g2pM", fake_g2pm)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("g2pm")
    assert ei.value.extra == "g2pm"


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
