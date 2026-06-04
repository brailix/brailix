from brailix.core.context import BackendContext, FrontendContext, MathContext
from brailix.core.errors import RunMode, WarningCollector


class TestFrontendContext:
    def test_defaults(self):
        ctx = FrontendContext()
        # Default profile is sourced from brailix.core.defaults, not
        # hard-coded here. The actual current value lives in that module.
        from brailix.core.defaults import DEFAULT_PROFILE

        assert ctx.profile == DEFAULT_PROFILE
        assert ctx.mode is RunMode.NORMAL
        assert isinstance(ctx.warnings, WarningCollector)
        assert ctx.options == {}

    def test_mode_synced_to_collector(self):
        ctx = FrontendContext(mode=RunMode.STRICT)
        assert ctx.warnings.mode is RunMode.STRICT

    def test_string_mode_is_normalized(self):
        ctx = FrontendContext(mode="strict")
        assert ctx.mode is RunMode.STRICT
        assert ctx.warnings.mode is RunMode.STRICT

    def test_supplied_collector_gets_mode_synced(self):
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = FrontendContext(mode=RunMode.LENIENT, warnings=wc)
        # The constructor harmonizes the collector to match the context.
        assert ctx.warnings.mode is RunMode.LENIENT
        assert ctx.warnings is wc

    def test_child_shares_warnings_and_overrides_profile(self):
        ctx = FrontendContext()
        child = ctx.child(profile="other")
        assert child.warnings is ctx.warnings
        assert child.profile == "other"
        assert child.mode is ctx.mode

    def test_child_options_isolated(self):
        ctx = FrontendContext(options={"a": 1})
        child = ctx.child()
        child.options["b"] = 2
        assert "b" not in ctx.options


class TestBackendContext:
    def test_defaults(self):
        from brailix.core.defaults import DEFAULT_PROFILE

        ctx = BackendContext()
        assert ctx.profile == DEFAULT_PROFILE
        assert ctx.block_type == "paragraph"

    def test_mode_synced_to_collector(self):
        ctx = BackendContext(mode=RunMode.STRICT)
        assert ctx.warnings.mode is RunMode.STRICT

    def test_string_mode_is_normalized(self):
        ctx = BackendContext(mode="lenient")
        assert ctx.mode is RunMode.LENIENT
        assert ctx.warnings.mode is RunMode.LENIENT

    def test_inline_text_translator_absent_returns_none(self):
        # Bare backend run — nothing injected (handlers fall back to a
        # warning + marker).
        assert BackendContext().inline_text_translator() is None

    def test_inline_text_translator_reads_injected_callable(self):
        from brailix.core.context import INLINE_TEXT_TRANSLATOR_KEY

        sentinel = object()
        ctx = BackendContext(
            options={INLINE_TEXT_TRANSLATOR_KEY: lambda _t: sentinel}
        )
        fn = ctx.inline_text_translator()
        assert fn is not None
        assert fn("anything") is sentinel


class TestMathContext:
    def test_defaults(self):
        from brailix.core.defaults import DEFAULT_PROFILE

        ctx = MathContext()
        assert ctx.mode == "inline"
        assert ctx.source == "plain"
        assert ctx.profile == DEFAULT_PROFILE
        assert ctx.surrounding_text is None
        assert isinstance(ctx.warnings, WarningCollector)

    def test_with_source_and_surrounding(self):
        ctx = MathContext(
            mode="display",
            source="latex",
            surrounding_text=("设 ", "，其中 x 为变量。"),
        )
        assert ctx.mode == "display"
        assert ctx.source == "latex"
        assert ctx.surrounding_text == ("设 ", "，其中 x 为变量。")


class TestSharedWarningCollector:
    def test_frontend_and_backend_can_share(self):
        wc = WarningCollector()
        f = FrontendContext(warnings=wc)
        b = BackendContext(warnings=wc)
        f.warnings.warn("F", "from frontend")
        b.warnings.warn("B", "from backend")
        assert len(wc) == 2
        assert {w.code for w in wc} == {"F", "B"}
