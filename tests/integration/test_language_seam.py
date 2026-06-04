"""The multi-language seam: prose routing + language-selected adapters.

Locks the infrastructure that lets a new language (Japanese, Korean,
...) plug in without re-architecting the orchestrator — see
ARCHITECTURE §7.6. The concrete language rules are out of scope here;
these tests exercise the *seam*, using Chinese as the one shipped
implementation plus throwaway registrations.
"""

from __future__ import annotations

from brailix.core.registry import Registry
from brailix.frontend import language_frontend_registry
from brailix.frontend.segment import DefaultSegmenter, segmenter_registry
from brailix.pipeline import Pipeline, _all_prose_types, _resolve_language_adapter


class TestResolveLanguageAdapter:
    """Precedence: explicit override > language registration > default."""

    def test_explicit_non_default_override_wins(self):
        reg: Registry = Registry("t")
        reg.register("ja", lambda: object())
        # User passed a specific adapter; it wins even if a language one exists.
        assert _resolve_language_adapter(reg, "hanlp", "default", "ja") == "hanlp"

    def test_language_registration_used_when_configured_is_default(self):
        reg: Registry = Registry("t")
        reg.register("ja", lambda: object())
        assert _resolve_language_adapter(reg, "default", "default", "ja") == "ja"

    def test_falls_back_to_default_when_no_language_adapter(self):
        reg: Registry = Registry("t")
        assert _resolve_language_adapter(reg, "default", "default", "zh") == "default"


class TestProseTypes:
    def test_zh_frontend_declares_hanzi_text(self):
        frontend = language_frontend_registry.get("zh")
        assert "hanzi_text" in frontend.prose_types

    def test_all_prose_types_unions_registered_frontends(self):
        assert "hanzi_text" in _all_prose_types()


class TestLanguageSelectedAdapters:
    """``profile.language`` selects the segmenter / normalizer when one is
    registered under the language subtag (default Chinese has none)."""

    def test_segmenter_defaults_when_no_language_specific(self):
        opts = Pipeline()._frontend_options()
        assert opts["segmenter"] == "default"
        assert opts["normalizer"] == "default"

    def test_language_registered_segmenter_is_auto_selected(self):
        # cn_current is language zh-CN → subtag "zh".
        segmenter_registry.register("zh", DefaultSegmenter)
        try:
            assert Pipeline()._frontend_options()["segmenter"] == "zh"
        finally:
            segmenter_registry.unregister("zh")

    def test_explicit_segmenter_overrides_language_selection(self):
        segmenter_registry.register("zh", DefaultSegmenter)
        try:
            opts = Pipeline(segmenter="default")._frontend_options()
            # Passing the default name reads as "auto", so the language one
            # still wins; an explicit *non-default* name would override.
            assert opts["segmenter"] == "zh"
            segmenter_registry.register("custom", DefaultSegmenter)
            opts2 = Pipeline(segmenter="custom")._frontend_options()
            assert opts2["segmenter"] == "custom"
        finally:
            segmenter_registry.unregister("zh")
            segmenter_registry.unregister("custom")


class TestChineseStillRoutes:
    def test_chinese_prose_routes_without_unhandled_warnings(self):
        result = Pipeline().translate_text("我在重庆2026年")
        codes = {w.code for w in result.warnings}
        assert "UNHANDLED_SEGMENT_TYPE" not in codes
        assert "NO_LANGUAGE_FRONTEND" not in codes
        assert result.render()  # non-empty braille

    def test_no_language_frontend_warns_for_unconfigured_language(self):
        # Keep zh registered (so "hanzi_text" is a known prose type), but
        # point the profile at a language with no frontend: a prose segment
        # is then a config gap, not an unknown type.
        pipe = Pipeline()
        pipe._profile.language = "xx-XX"
        codes = {w.code for w in pipe.translate_text("我").warnings}
        assert "NO_LANGUAGE_FRONTEND" in codes
        assert "UNHANDLED_SEGMENT_TYPE" not in codes
