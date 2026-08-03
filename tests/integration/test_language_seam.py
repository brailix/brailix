"""The multi-language seam: prose routing + language-selected adapters.

Locks the infrastructure that lets a new language (Japanese, Korean,
...) plug in without re-architecting the orchestrator — see
ARCHITECTURE#arch-language-slots. The concrete language rules are out of scope here;
these tests exercise the *seam*, using Chinese as the one shipped
implementation plus throwaway registrations.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.core.registry import Registry
from brailix.frontend import language_frontend_registry
from brailix.frontend._language_pick import LANGUAGE_OPTION, pick_by_language
from brailix.frontend.segmentation import (
    BUILTIN_SEGMENTER,
    segment,
    segmenter_registry,
)
from brailix.ir.document import Paragraph
from brailix.pipeline import Pipeline
from brailix.pipeline._helpers import _all_prose_types


def _ctx(language: str | None = None) -> FrontendContext:
    options = {LANGUAGE_OPTION: language} if language else {}
    return FrontendContext(profile="cn_current", options=options)


class TestPickByLanguage:
    """An adapter registered under the language subtag wins; else built-in."""

    def test_language_registration_is_used(self):
        reg: Registry = Registry("t")
        reg.register("ja", lambda: object())
        assert pick_by_language(reg, _ctx("ja"), "default") == "ja"

    def test_falls_back_when_no_language_adapter(self):
        reg: Registry = Registry("t")
        assert pick_by_language(reg, _ctx("zh"), "default") == "default"

    def test_falls_back_when_context_names_no_language(self):
        # A direct call in a test, or a caller that built its own context:
        # the behaviour every such caller had before languages were pluggable.
        reg: Registry = Registry("t")
        reg.register("ja", lambda: object())
        assert pick_by_language(reg, _ctx(), "default") == "default"
        assert pick_by_language(reg, None, "default") == "default"


class TestAutoSegmenterDelegates:
    """``auto`` is a real registered adapter, not a name the driver resolves."""

    def test_auto_uses_the_language_segmenter(self):
        seen: list[str] = []

        class _Probe:
            name = "probe"

            def segment(self, block, ctx=None):  # noqa: ANN001
                seen.append("probe")
                return []

        with segmenter_registry.overriding("ja", _Probe):
            segmenter_registry.get("auto").segment(
                Paragraph(text="ひらがな"), _ctx("ja")
            )
        assert seen == ["probe"]

    def test_auto_falls_back_to_the_builtin(self):
        out = segmenter_registry.get("auto").segment(
            Paragraph(text="我"), _ctx("zh")
        )
        assert [s.type for s in out] == ["hanzi_text"]

    def test_naming_the_builtin_through_the_context_is_taken_literally(self):
        # ``segment`` still honours an explicit name on the context — the
        # frontend entry points are public, and a caller building their own
        # context can bypass the language routing. What went away is only the
        # Pipeline-level knob, not this.
        seen: list[str] = []

        class _Probe:
            name = "probe"

            def segment(self, block, ctx=None):  # noqa: ANN001
                seen.append("probe")
                return []

        with segmenter_registry.overriding("zh", _Probe):
            ctx = FrontendContext(
                profile="cn_current",
                options={
                    LANGUAGE_OPTION: "zh",
                    "segmenter": BUILTIN_SEGMENTER,
                },
            )
            out = segment(Paragraph(text="我"), ctx)
        assert seen == []  # the language adapter was NOT used
        assert [seg.type for seg in out] == ["hanzi_text"]


class TestProseTypes:
    def test_zh_frontend_declares_hanzi_text(self):
        frontend = language_frontend_registry.get("zh")
        assert "hanzi_text" in frontend.prose_types

    def test_all_prose_types_unions_registered_frontends(self):
        assert "hanzi_text" in _all_prose_types()


class TestLanguageSelectedAdapters:
    """``profile.language`` selects the segmenter / normalizer, with nothing
    left for a caller to configure — neither is a ``Pipeline`` field.

    Registering under the language subtag is the whole mechanism (default
    Chinese registers neither and gets the built-ins)."""

    def test_driver_publishes_the_language(self):
        # The driver states the language and resolves nothing on the adapters'
        # behalf, so "which segmenter runs" is decided in exactly one place —
        # the adapter. It doesn't pass a segmenter / normalizer name at all:
        # there is no such configuration to pass.
        opts = Pipeline(profile="cn_current")._frontend.frontend_options()
        assert opts[LANGUAGE_OPTION] == "zh"
        assert "segmenter" not in opts
        assert "normalizer" not in opts

    def test_japanese_profile_publishes_its_subtag(self):
        opts = Pipeline(profile="ja_current")._frontend.frontend_options()
        assert opts[LANGUAGE_OPTION] == "ja"

    def test_a_language_segmenter_reaches_a_real_compile(self):
        # End to end rather than by inspecting options: registering under the
        # language subtag is what a new language does, and it must change what
        # actually segments without the orchestrator learning the name.
        seen: list[str] = []

        class _Probe:
            name = "probe"

            def segment(self, block, ctx=None):  # noqa: ANN001
                seen.append("probe")
                return []

        with segmenter_registry.overriding("zh", _Probe):
            Pipeline(profile="cn_current").translate_text("我")
        assert seen  # the zh-registered segmenter ran


class TestChineseStillRoutes:
    def test_chinese_prose_routes_without_unhandled_warnings(self):
        result = Pipeline(profile="cn_current").translate_text("我在重庆2026年")
        codes = {w.code for w in result.warnings}
        assert "UNHANDLED_SEGMENT_TYPE" not in codes
        assert "NO_LANGUAGE_FRONTEND" not in codes
        assert result.render()  # non-empty braille

    def test_no_language_frontend_warns_for_unconfigured_language(self):
        # Keep zh registered (so "hanzi_text" is a known prose type), but
        # point the profile at a language with no frontend: a prose segment
        # is then a config gap, not an unknown type.
        pipe = Pipeline(profile="cn_current")
        pipe._profile.language = "xx-XX"
        codes = {w.code for w in pipe.translate_text("我").warnings}
        assert "NO_LANGUAGE_FRONTEND" in codes
        assert "UNHANDLED_SEGMENT_TYPE" not in codes
