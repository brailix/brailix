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


def test_auto_falls_through_model_not_installed_candidate():
    # A candidate that imports fine but whose model isn't downloaded raises
    # ModelNotInstalledError (e.g. hanlp under managed download). The chain
    # must treat it as "unavailable" and degrade to char rather than
    # crashing the whole compile.
    from brailix.core.errors import ModelNotInstalledError
    from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer

    def _needs_model():
        raise ModelNotInstalledError("fake-model", "/nowhere")

    with analyzer_registry.overriding("fake_mni", _needs_model):
        analyzer = AutoChineseAnalyzer(preferred=("fake_mni", "char"))
        tokens = analyzer.analyze("我")
        assert [t.surface for t in tokens] == ["我"]  # degraded to char


def test_hanlp_load_failure_surfaces_as_model_not_installed(monkeypatch, tmp_path):
    # In the standalone (non-managed) path hanlp.load auto-downloads the
    # model; a network / IO failure there must surface as
    # ModelNotInstalledError — which the ``auto`` chain catches (see
    # test_auto_falls_through_model_not_installed_candidate) — rather than the
    # raw OSError that would crash the whole compile.
    import types
    from unittest.mock import MagicMock

    from brailix.core.errors import ModelNotInstalledError
    from brailix.frontend.zh.analyzer.adapters import hanlp as hanlp_adapter

    fake = types.ModuleType("hanlp")
    fake.pretrained = MagicMock()  # any hanlp.pretrained.mtl.* attribute
    fake.load = MagicMock(side_effect=OSError("network unreachable"))
    monkeypatch.setitem(sys.modules, "hanlp", fake)
    monkeypatch.setattr(hanlp_adapter, "get_model_dir", lambda _name: tmp_path)
    monkeypatch.setattr(hanlp_adapter, "is_managed_download", lambda: False)

    with pytest.raises(ModelNotInstalledError) as ei:
        hanlp_adapter._load()
    # The underlying download failure is chained, not swallowed.
    assert isinstance(ei.value.__cause__, OSError)
