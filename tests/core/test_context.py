import pytest

from brailix.core.context import (
    BackendContext,
    FrontendContext,
    GraphicsContext,
    MathContext,
)
from brailix.core.errors import RunMode, WarningCollector


class TestFrontendContext:
    def test_stores_profile(self):
        # ``profile`` is required — there is no built-in default braille
        # standard; the caller always supplies the chosen one.
        ctx = FrontendContext(profile="cn_current")
        assert ctx.profile == "cn_current"
        assert ctx.mode is RunMode.NORMAL
        assert isinstance(ctx.warnings, WarningCollector)
        assert ctx.options == {}

    def test_profile_is_required(self):
        with pytest.raises(TypeError):
            FrontendContext()  # type: ignore[call-arg]

    def test_mode_synced_to_collector(self):
        ctx = FrontendContext(profile="cn_current", mode=RunMode.STRICT)
        assert ctx.warnings.mode is RunMode.STRICT

    def test_string_mode_is_normalized(self):
        ctx = FrontendContext(profile="cn_current", mode="strict")
        assert ctx.mode is RunMode.STRICT
        assert ctx.warnings.mode is RunMode.STRICT

    def test_supplied_collector_gets_mode_synced(self):
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = FrontendContext(profile="cn_current", mode=RunMode.LENIENT, warnings=wc)
        # The constructor harmonizes the collector to match the context.
        assert ctx.warnings.mode is RunMode.LENIENT
        assert ctx.warnings is wc

    def test_child_shares_warnings_and_overrides_profile(self):
        ctx = FrontendContext(profile="cn_current")
        child = ctx.child(profile="other")
        assert child.warnings is ctx.warnings
        assert child.profile == "other"
        assert child.mode is ctx.mode

    def test_child_options_isolated(self):
        ctx = FrontendContext(profile="cn_current", options={"a": 1})
        child = ctx.child()
        child.options["b"] = 2
        assert "b" not in ctx.options


class TestBackendContext:
    def test_stores_profile(self):
        ctx = BackendContext(profile="cn_current")
        assert ctx.profile == "cn_current"
        assert ctx.block_type == "paragraph"

    def test_profile_is_required(self):
        with pytest.raises(TypeError):
            BackendContext()  # type: ignore[call-arg]

    def test_mode_synced_to_collector(self):
        ctx = BackendContext(profile="cn_current", mode=RunMode.STRICT)
        assert ctx.warnings.mode is RunMode.STRICT

    def test_string_mode_is_normalized(self):
        ctx = BackendContext(profile="cn_current", mode="lenient")
        assert ctx.mode is RunMode.LENIENT
        assert ctx.warnings.mode is RunMode.LENIENT

    def test_inline_text_translator_absent_returns_none(self):
        # Bare backend run — nothing injected (handlers fall back to a
        # warning + marker).
        assert BackendContext(profile="cn_current").inline_text_translator() is None

    def test_inline_text_translator_reads_injected_callable(self):
        from brailix.core.context import INLINE_TEXT_TRANSLATOR_KEY

        sentinel = object()
        ctx = BackendContext(
            profile="cn_current",
            options={INLINE_TEXT_TRANSLATOR_KEY: lambda _t: sentinel},
        )
        fn = ctx.inline_text_translator()
        assert fn is not None
        assert fn("anything") is sentinel


class TestMathContext:
    def test_stores_profile(self):
        ctx = MathContext(profile="cn_current")
        assert ctx.mode == "inline"
        assert ctx.source == "plain"
        assert ctx.profile == "cn_current"
        assert ctx.surrounding_text is None
        assert isinstance(ctx.warnings, WarningCollector)

    def test_profile_is_required(self):
        with pytest.raises(TypeError):
            MathContext(source="latex")  # type: ignore[call-arg]

    def test_with_source_and_surrounding(self):
        ctx = MathContext(
            profile="cn_current",
            mode="display",
            source="latex",
            surrounding_text=("设 ", "，其中 x 为变量。"),
        )
        assert ctx.mode == "display"
        assert ctx.source == "latex"
        assert ctx.surrounding_text == ("设 ", "，其中 x 为变量。")


class TestGraphicsContext:
    def test_defaults(self):
        ctx = GraphicsContext()
        assert ctx.source == "svg"
        assert isinstance(ctx.warnings, WarningCollector)

    def test_asset_resolver_absent_returns_none(self):
        # Bare run — no resolver injected (the image adapter then reads the
        # reference as a filesystem path).
        assert GraphicsContext(source="image").asset_resolver() is None

    def test_asset_resolver_reads_injected_callable(self):
        from brailix.core.context import GRAPHIC_ASSET_RESOLVER_KEY

        ctx = GraphicsContext(
            source="image",
            options={GRAPHIC_ASSET_RESOLVER_KEY: lambda name: b"bytes:" + name.encode()},
        )
        fn = ctx.asset_resolver()
        assert fn is not None
        assert fn("media/x.png") == b"bytes:media/x.png"


class TestSharedWarningCollector:
    def test_frontend_and_backend_can_share(self):
        wc = WarningCollector()
        f = FrontendContext(profile="cn_current", warnings=wc)
        b = BackendContext(profile="cn_current", warnings=wc)
        f.warnings.warn("F", "from frontend")
        b.warnings.warn("B", "from backend")
        assert len(wc) == 2
        assert {w.code for w in wc} == {"F", "B"}
