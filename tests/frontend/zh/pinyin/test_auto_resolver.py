from __future__ import annotations

import sys
import types

import pytest

from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    resolver_registry.clear_cache()
    yield
    resolver_registry.clear_cache()


def test_auto_uses_pypinyin_when_available(monkeypatch):
    fake_module = types.ModuleType("pypinyin")

    class _Style:
        TONE3 = "TONE3"

    def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
        assert text == "\u6211\u5728"
        assert style == "TONE3"
        assert neutral_tone_with_five is True
        return ["wo3", "zai4"]

    fake_module.Style = _Style
    fake_module.lazy_pinyin = fake_lazy_pinyin
    monkeypatch.setitem(sys.modules, "g2pM", None)  # note capital M
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", fake_module)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve(
        [ChineseToken(surface="\u6211"), ChineseToken(surface="\u5728")],
        FrontendContext(),
    )

    assert [t.pinyin for t in out] == ["wo3", "zai4"]


def test_auto_prefers_g2pw_when_available(monkeypatch):
    fake_g2pw = types.ModuleType("g2pw")
    fake_pypinyin = types.ModuleType("pypinyin")

    class _FakeConverter:
        def __call__(self, text):
            assert text == "\u6211\u5728"
            return (["wo3", "zai4"], [0.96, 0.95])

    def fail_if_called(*args, **kwargs):
        raise AssertionError("pypinyin should not be used when g2pw is available")

    fake_g2pw.G2PWConverter = _FakeConverter
    fake_pypinyin.lazy_pinyin = fail_if_called
    monkeypatch.setitem(sys.modules, "g2pM", None)  # so g2pw wins the chain
    monkeypatch.setitem(sys.modules, "g2pw", fake_g2pw)
    monkeypatch.setitem(sys.modules, "pypinyin", fake_pypinyin)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve(
        [ChineseToken(surface="\u6211"), ChineseToken(surface="\u5728")],
        FrontendContext(),
    )

    assert [t.pinyin for t in out] == ["wo3", "zai4"]
    assert [t.confidence for t in out] == [0.96, 0.95]


def test_auto_falls_back_to_null_when_real_backends_are_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "g2pM", None)
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", None)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())

    assert out[0].pinyin is None


def test_auto_caches_delegate_across_calls(monkeypatch):
    # First call picks a delegate; second call must reuse the cached
    # one without re-walking the preferred chain (the cache-hit branch
    # in ``_load_delegate``).
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", None)

    monkeypatch.setitem(sys.modules, "g2pM", None)
    resolver = resolver_registry.get("auto")
    # Drive once to populate ``_delegate``.
    resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())
    cached = resolver._delegate
    assert cached is not None
    # Second call returns the same instance \u2014 the cache short-circuit
    # fires before any registry lookup happens.
    resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())
    assert resolver._delegate is cached


def test_auto_skips_self_name_in_preferred():
    # If someone constructs an AutoPinyinResolver whose ``preferred``
    # tuple lists ``"auto"`` itself, that entry must be skipped (no
    # infinite recursion) and the next candidate wins.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=("auto", "null"))
    out = resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())
    # The null resolver leaves pinyin as None \u2014 that's how we know we
    # reached it instead of looping back into auto.
    assert out == [ChineseToken(surface="\u6211")] or out[0].pinyin is None


def test_auto_raises_keyerror_when_no_candidates():
    # Empty preferred tuple \u2192 no candidate ever ran \u2192 no last_error to
    # re-raise. The fallback path raises a fresh KeyError.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=())
    with pytest.raises(KeyError, match="no candidates"):
        resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())


def test_auto_re_raises_last_error_when_all_candidates_fail():
    # All candidates are unregistered \u2192 every iteration raises
    # KeyError and ``last_error`` is non-None at the end. The adapter
    # surfaces that last failure rather than the "no candidates"
    # KeyError.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=("does_not_exist",))
    with pytest.raises(KeyError) as ei:
        resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext())
    # The last_error wins \u2014 its message names the missing adapter.
    assert "does_not_exist" in str(ei.value)
