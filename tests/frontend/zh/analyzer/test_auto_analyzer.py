"""Tests for the AutoChineseAnalyzer fallback chain.

The ``auto`` analyzer should try ``thulac`` first, then ``hanlp``, then
``jieba``, and finally the dependency-free ``char`` fallback. We
monkeypatch ``sys.modules`` to make the heavier candidates appear
unavailable so the lighter ones fire.
"""

from __future__ import annotations

import sys

import pytest

from brailix.frontend.zh.analyzer.registry import analyzer_registry


@pytest.fixture(autouse=True)
def _clear_analyzer_cache():
    analyzer_registry.clear_cache()
    yield
    analyzer_registry.clear_cache()


def test_auto_falls_back_to_char_when_real_tokenizers_missing(monkeypatch):
    # Pretend the optional packages aren't installed.
    monkeypatch.setitem(sys.modules, "thulac", None)
    monkeypatch.setitem(sys.modules, "hanlp", None)
    monkeypatch.setitem(sys.modules, "jieba", None)

    analyzer = analyzer_registry.get("auto")
    tokens = analyzer.analyze("我")
    # Char fallback produces one token per character.
    assert [t.surface for t in tokens] == ["我"]


def test_auto_caches_delegate_across_calls(monkeypatch):
    # First call picks a delegate; second call must reuse the cached
    # one without re-walking the preferred chain (the cache-hit branch
    # in ``_load_delegate``).
    monkeypatch.setitem(sys.modules, "thulac", None)
    monkeypatch.setitem(sys.modules, "hanlp", None)
    monkeypatch.setitem(sys.modules, "jieba", None)

    analyzer = analyzer_registry.get("auto")
    analyzer.analyze("我")
    cached = analyzer._delegate
    assert cached is not None
    analyzer.analyze("我")
    assert analyzer._delegate is cached


def test_auto_skips_self_name_in_preferred():
    # ``AutoChineseAnalyzer(preferred=("auto", "char"))`` should skip
    # the "auto" entry (avoiding recursion) and land on "char".
    from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer

    analyzer = AutoChineseAnalyzer(preferred=("auto", "char"))
    tokens = analyzer.analyze("我")
    assert [t.surface for t in tokens] == ["我"]


def test_auto_raises_keyerror_when_no_candidates():
    # Empty preferred → no iteration → no ``last_error`` to re-raise.
    # Falls through to the explicit "no candidates" KeyError.
    from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer

    analyzer = AutoChineseAnalyzer(preferred=())
    with pytest.raises(KeyError, match="no candidates"):
        analyzer.analyze("我")


def test_auto_re_raises_last_error_when_all_candidates_fail():
    # All entries unregistered → every iteration raises KeyError and
    # ``last_error`` carries the most recent one at the end.
    from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer

    analyzer = AutoChineseAnalyzer(preferred=("does_not_exist",))
    with pytest.raises(KeyError) as ei:
        analyzer.analyze("我")
    assert "does_not_exist" in str(ei.value)
