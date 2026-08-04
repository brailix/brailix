from __future__ import annotations

import sys
import types

import pytest

from brailix import Pipeline
from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.frontend.zh.tokens import ChineseToken


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
        FrontendContext(profile="cn_current"),
    )

    assert [t.pinyin for t in out] == ["wo3", "zai4"]


def test_auto_prefers_g2pw_when_available(monkeypatch):
    fake_g2pw = types.ModuleType("g2pw")
    fake_pypinyin = types.ModuleType("pypinyin")

    class _FakeConverter:
        # Mirrors the real ``G2PWConverter``: constructed with a style, called
        # with a LIST of sentences, and answering with one result list per
        # sentence and no confidences at all.
        def __init__(self, *, style: str = "bopomofo", **_: object) -> None:
            assert style == "pinyin", (
                f"the adapter must ask for pinyin; the converter's own default "
                f"is bopomofo, which no consumer of ChineseToken.pinyin reads "
                f"(got {style!r})"
            )

        def __call__(self, sentences):
            assert sentences == ["\u6211\u5728"], sentences
            return [["wo3", "zai4"]]

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
        FrontendContext(profile="cn_current"),
    )

    assert [t.pinyin for t in out] == ["wo3", "zai4"]
    # None, not a number: the shipped converter computes confidences and then
    # returns only the predictions, so nothing real ever fills this in.
    assert [t.confidence for t in out] == [None, None]


def test_auto_falls_through_when_g2pw_model_load_fails(monkeypatch):
    # g2pw is importable but its model fails to load on construction (network /
    # IO failure). _load translates that into MissingExtraError, so auto must
    # degrade to pypinyin instead of letting the raw error crash translation.
    fake_g2pw = types.ModuleType("g2pw")

    class _BoomConverter:
        def __init__(self) -> None:
            raise RuntimeError("model download failed")

    fake_g2pw.G2PWConverter = _BoomConverter

    fake_pypinyin = types.ModuleType("pypinyin")

    class _Style:
        TONE3 = "TONE3"

    def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
        return ["wo3", "zai4"]

    fake_pypinyin.Style = _Style
    fake_pypinyin.lazy_pinyin = fake_lazy_pinyin
    monkeypatch.setitem(sys.modules, "g2pM", None)  # so g2pw leads the chain
    monkeypatch.setitem(sys.modules, "g2pw", fake_g2pw)
    monkeypatch.setitem(sys.modules, "pypinyin", fake_pypinyin)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve(
        [ChineseToken(surface="我"), ChineseToken(surface="在")],
        FrontendContext(profile="cn_current"),
    )
    # g2pw skipped (model load failed → MissingExtraError); pypinyin ran.
    assert [t.pinyin for t in out] == ["wo3", "zai4"]


def test_auto_falls_through_when_g2pm_model_load_fails(monkeypatch):
    # g2pM is importable but its bundled-weight construction fails (corrupt
    # .pkl / numpy mismatch / a frozen build missing the data file). _load
    # translates that into MissingExtraError, so auto — which prefers g2pm as
    # the default — must degrade to pypinyin instead of crashing translation.
    fake_g2pm = types.ModuleType("g2pM")

    class _BoomModel:
        def __init__(self) -> None:
            raise RuntimeError("corrupt bundled weights")

    fake_g2pm.G2pM = _BoomModel

    fake_pypinyin = types.ModuleType("pypinyin")

    class _Style:
        TONE3 = "TONE3"

    def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
        return ["wo3", "zai4"]

    fake_pypinyin.Style = _Style
    fake_pypinyin.lazy_pinyin = fake_lazy_pinyin
    monkeypatch.setitem(sys.modules, "g2pM", fake_g2pm)
    monkeypatch.setitem(sys.modules, "g2pw", None)  # skip g2pw too
    monkeypatch.setitem(sys.modules, "pypinyin", fake_pypinyin)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve(
        [ChineseToken(surface="我"), ChineseToken(surface="在")],
        FrontendContext(profile="cn_current"),
    )
    # g2pm skipped (weights load failed → MissingExtraError); pypinyin ran.
    assert [t.pinyin for t in out] == ["wo3", "zai4"]


def test_auto_falls_back_to_null_when_real_backends_are_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "g2pM", None)
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", None)

    resolver = resolver_registry.get("auto")
    out = resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))

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
    resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))
    cached = resolver._delegate
    assert cached is not None
    # Second call returns the same instance \u2014 the cache short-circuit
    # fires before any registry lookup happens.
    resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))
    assert resolver._delegate is cached


def test_auto_skips_self_name_in_preferred():
    # If someone constructs an AutoPinyinResolver whose ``preferred``
    # tuple lists ``"auto"`` itself, that entry must be skipped (no
    # infinite recursion) and the next candidate wins.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=("auto", "null"))
    out = resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))
    # The null resolver leaves pinyin as None \u2014 that's how we know we
    # reached it instead of looping back into auto.
    assert len(out) == 1
    assert out[0].surface == "我"
    assert out[0].pinyin is None  # null ran (the auto self-entry was skipped)


def test_auto_raises_keyerror_when_no_candidates():
    # Empty preferred tuple \u2192 no candidate ever ran \u2192 no last_error to
    # re-raise. The fallback path raises a fresh KeyError.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=())
    with pytest.raises(KeyError, match="no candidates"):
        resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))


def test_auto_re_raises_last_error_when_all_candidates_fail():
    # All candidates are unregistered \u2192 every iteration raises
    # KeyError and ``last_error`` is non-None at the end. The adapter
    # surfaces that last failure rather than the "no candidates"
    # KeyError.
    from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver

    resolver = AutoPinyinResolver(preferred=("does_not_exist",))
    with pytest.raises(KeyError) as ei:
        resolver.resolve([ChineseToken(surface="\u6211")], FrontendContext(profile="cn_current"))
    # The last_error wins \u2014 its message names the missing adapter.
    assert "does_not_exist" in str(ei.value)


class TestNoEngineIsSaidOnce:
    """Landing on ``null`` is not a weaker reading — it is no reading, and a
    Chinese word without one produces no cells at all.

    So the whole document comes out blank. What explained that was one
    ``MISSING_PINYIN`` per character — thousands of small problems standing in
    for the single configuration one — and nothing anywhere saying "there is
    no pinyin engine installed". The segmentation chain has no equivalent
    hazard: its last link, ``char``, still produces tokens.
    """

    @staticmethod
    def _no_engines(monkeypatch) -> None:
        # Same shape the tests above use: a ``None`` in sys.modules makes the
        # import raise, which is what a bundle without the wheel looks like.
        for module in ("g2pM", "g2pw", "pypinyin"):
            monkeypatch.setitem(sys.modules, module, None)

    def test_it_warns_when_the_chain_reaches_null(self, monkeypatch) -> None:
        self._no_engines(monkeypatch)
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="auto")
        result = pipe.translate_text("我在重庆。")
        codes = [w.code for w in result.warnings]
        assert "NO_PINYIN_ENGINE" in codes

    def test_it_warns_once_across_a_whole_document(self, monkeypatch) -> None:
        # One resolver instance serves the pipeline's lifetime and resolves
        # per block, so a per-call warning would bury the message under one
        # copy per block — the very shape it exists to replace.
        self._no_engines(monkeypatch)
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="auto")
        doc = pipe.parse_text("第一段。\n\n第二段。\n\n第三段。", format="plain")
        result = pipe.translate_document(doc)
        codes = [w.code for w in result.warnings]
        assert codes.count("NO_PINYIN_ENGINE") == 1
        assert len(doc.blocks) == 3

    def test_a_working_engine_does_not_warn(self) -> None:
        pytest.importorskip("pypinyin")
        pipe = Pipeline(
            profile="cn_current", analyzer="char", resolver="pypinyin"
        )
        result = pipe.translate_text("我在重庆。")
        assert "NO_PINYIN_ENGINE" not in [w.code for w in result.warnings]

    def test_selecting_null_outright_does_not_warn(self) -> None:
        # Asking for ``null`` by name is a deliberate choice (a test harness
        # pinning readings out of the picture); the warning is about ``auto``
        # having nothing left to fall back to.
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="null")
        result = pipe.translate_text("我在重庆。")
        assert "NO_PINYIN_ENGINE" not in [w.code for w in result.warnings]
