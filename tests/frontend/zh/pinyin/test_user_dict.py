"""Personal pinyin dictionary post-pass in
:func:`brailix.frontend.zh.pinyin.annotate`.

The dictionary is a thin ``surface → reading`` override layer applied
*after* whichever resolver runs, so the user's explicit, persisted
choice wins for every document.  Policy: multi-character surfaces only
— single characters are too context-dependent to force globally.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin import _apply_user_dict, annotate
from brailix.ir.inline import ChineseToken


class TestApplyUserDict:
    """The post-pass itself, isolated from any resolver's behaviour."""

    def test_overrides_multi_char_reading(self) -> None:
        # Dict wins over a reading the resolver would have produced.
        tokens = [ChineseToken(surface="重庆", pinyin="zhong4 qing4")]
        _apply_user_dict(tokens, {"重庆": "chong2 qing4"})
        assert tokens[0].pinyin == "chong2 qing4"

    def test_fills_when_reading_unset(self) -> None:
        tokens = [ChineseToken(surface="重庆")]
        _apply_user_dict(tokens, {"重庆": "chong2 qing4"})
        assert tokens[0].pinyin == "chong2 qing4"

    def test_single_char_never_overridden(self) -> None:
        # Policy guard: even with a single-char key present, a one-char
        # surface is left alone (the dictionary never stores them, this
        # is belt-and-suspenders).
        tokens = [ChineseToken(surface="行", pinyin="hang2")]
        _apply_user_dict(tokens, {"行": "xing2"})
        assert tokens[0].pinyin == "hang2"

    def test_miss_leaves_token_untouched(self) -> None:
        tokens = [ChineseToken(surface="北京", pinyin="bei3 jing1")]
        _apply_user_dict(tokens, {"重庆": "chong2 qing4"})
        assert tokens[0].pinyin == "bei3 jing1"

    def test_only_matching_token_in_a_run(self) -> None:
        tokens = [
            ChineseToken(surface="我", pinyin="wo3"),
            ChineseToken(surface="重庆", pinyin="zhong4 qing4"),
            ChineseToken(surface="人", pinyin="ren2"),
        ]
        _apply_user_dict(tokens, {"重庆": "chong2 qing4"})
        assert [t.pinyin for t in tokens] == ["wo3", "chong2 qing4", "ren2"]

    def test_does_not_mutate_caller_token_objects(self) -> None:
        # The override replaces the list entry with a fresh token rather than
        # writing into the caller's object — the null resolver hands back the
        # caller's own objects, so an in-place write would leak upstream.
        original = ChineseToken(surface="重庆", pinyin=None)
        tokens = [original]
        _apply_user_dict(tokens, {"重庆": "chong2 qing4"})
        assert tokens[0].pinyin == "chong2 qing4"  # list entry updated
        assert original.pinyin is None  # caller's object untouched


class TestAnnotateIntegration:
    """End-to-end through :func:`annotate` with the null resolver."""

    def test_dict_applies_after_resolver(self) -> None:
        ctx = FrontendContext(
            options={
                "pinyin_resolver": "null",
                "user_pinyin_dict": {"重庆": "chong2 qing4"},
            }
        )
        out = annotate([ChineseToken(surface="重庆")], ctx)
        assert out[0].pinyin == "chong2 qing4"

    def test_no_dict_key_is_noop(self) -> None:
        ctx = FrontendContext(options={"pinyin_resolver": "null"})
        out = annotate([ChineseToken(surface="重庆")], ctx)
        # null leaves pinyin unset; nothing fills it.
        assert out[0].pinyin is None

    def test_empty_dict_is_noop(self) -> None:
        ctx = FrontendContext(
            options={"pinyin_resolver": "null", "user_pinyin_dict": {}}
        )
        out = annotate([ChineseToken(surface="重庆")], ctx)
        assert out[0].pinyin is None

    def test_null_resolver_with_dict_does_not_mutate_caller(self) -> None:
        # null returns the caller's own token objects; the dict override
        # must not leak back into the caller's input list.
        original = ChineseToken(surface="重庆", pinyin=None)
        ctx = FrontendContext(
            options={
                "pinyin_resolver": "null",
                "user_pinyin_dict": {"重庆": "chong2 qing4"},
            }
        )
        out = annotate([original], ctx)
        assert out[0].pinyin == "chong2 qing4"
        assert original.pinyin is None


class TestLowConfidenceSuppression:
    """A word the dictionary resolves shouldn't keep nagging with a
    LOW_CONFIDENCE_PINYIN warning — the proofreader already pinned the reading.
    """

    @staticmethod
    def _patch_lowconf_resolver(monkeypatch) -> None:
        """Make resolver lookup return a stub that flags every token as
        low-confidence.

        ``annotate`` re-imports ``resolver_registry`` from its module on
        each call, so swapping the module attribute for a fake registry
        is picked up (the ``Registry.get`` method itself is read-only and
        can't be patched in place).
        """
        from brailix.frontend.zh.pinyin import registry as resolver_mod

        class _LowConf:
            name = "lowconf"

            def resolve(self, tokens, ctx):
                for t in tokens:
                    if ctx is not None:
                        ctx.warnings.warn(
                            code="LOW_CONFIDENCE_PINYIN",
                            message="uncertain",
                            surface=t.surface,
                        )
                return tokens

        class _FakeRegistry:
            def get(self, _name):
                return _LowConf()

        monkeypatch.setattr(
            resolver_mod, "resolver_registry", _FakeRegistry()
        )

    def test_suppressed_for_dict_word_only(self, monkeypatch) -> None:
        self._patch_lowconf_resolver(monkeypatch)
        ctx = FrontendContext(
            options={
                "pinyin_resolver": "lowconf",
                "user_pinyin_dict": {"重庆": "chong2 qing4"},
            }
        )
        annotate(
            [ChineseToken(surface="重庆"), ChineseToken(surface="银行")], ctx
        )
        pairs = [(w.code, w.surface) for w in ctx.warnings.warnings]
        # The dict word's nudge is gone; the non-dict word's stays.
        assert ("LOW_CONFIDENCE_PINYIN", "重庆") not in pairs
        assert ("LOW_CONFIDENCE_PINYIN", "银行") in pairs

    def test_not_suppressed_without_dict(self, monkeypatch) -> None:
        self._patch_lowconf_resolver(monkeypatch)
        ctx = FrontendContext(options={"pinyin_resolver": "lowconf"})
        annotate([ChineseToken(surface="重庆")], ctx)
        assert ctx.warnings.by_code("LOW_CONFIDENCE_PINYIN")
