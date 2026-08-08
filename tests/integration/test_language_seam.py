"""The multi-language seam: one registration per language, driving both
halves of its prose.

Locks the infrastructure that lets a new language (Japanese, Korean,
...) plug in without re-architecting the orchestrator — see
ARCHITECTURE#arch-language-slots. The concrete language rules are out of scope
here; these tests exercise the *seam*, using Chinese as the one shipped
implementation plus throwaway registrations.

The seam used to be wider than the languages in it: segmentation and
normalization were plugin families of their own, each with a registry keyed
by the same language subtag as ``language_frontend_registry``, each with an
``auto`` adapter that read ``ctx.options["language"]`` to pick. Adding a
language meant registering the same fact in up to four places and hoping the
segment type names agreed. Now :class:`LanguageFrontend
<brailix.core.protocols.LanguageFrontend>` carries both halves —
``segment`` cuts the language's prose out of the raw text, ``process`` turns
each run into IR — and normalization is a fixed pass nobody selects.
"""

from __future__ import annotations

import pytest

from brailix.core.context import FrontendContext
from brailix.core.segment import Segment
from brailix.core.span import Span
from brailix.frontend import language_frontend_registry
from brailix.frontend.segmentation import segment
from brailix.ir.document import Paragraph
from brailix.pipeline import Pipeline
from brailix.pipeline._helpers import _all_prose_types


def _ctx() -> FrontendContext:
    return FrontendContext(profile="cn_current")


class _RecordingFrontend:
    """Delegates to the real Chinese frontend, recording which half ran."""

    prose_types = frozenset({"hanzi_text"})

    def __init__(self, inner, seen: list[str]) -> None:
        self._inner = inner
        self._seen = seen

    def segment(self, block, ctx=None):  # noqa: ANN001
        self._seen.append("segment")
        return self._inner.segment(block, ctx)

    def process(self, surface, base, ctx):  # noqa: ANN001
        self._seen.append("process")
        return self._inner.process(surface, base, ctx)


class TestTheLanguageOwnsItsSegmentation:
    """``profile.language`` picks one object, and that object cuts the text.

    Registering under the language subtag is the whole mechanism, and it is
    checked end to end rather than by inspecting options: a new language must
    change what actually segments without the orchestrator learning its name.
    """

    def test_both_halves_run_from_the_one_registration(self) -> None:
        seen: list[str] = []
        real = language_frontend_registry.get("zh")

        with language_frontend_registry.overriding(
            "zh", lambda: _RecordingFrontend(real, seen)
        ):
            result = Pipeline(profile="cn_current").translate_text("我在2026年")

        # Segmentation first, then prose routing back into the same object.
        assert seen[0] == "segment"
        assert "process" in seen
        assert result.render()

    def test_a_replacement_segmentation_reaches_a_real_compile(self) -> None:
        class _OneBigPunct:
            prose_types = frozenset({"hanzi_text"})

            def segment(self, block, ctx=None):  # noqa: ANN001
                text = block.text or ""
                if not text:
                    return []
                # One punct segment for the whole text, so normalization
                # wraps it as a single Punct node and the braille is visibly
                # this frontend's doing.
                return [
                    Segment(type="punct", surface=text, span=Span(0, len(text)))
                ]

            def process(self, surface, base, ctx):  # noqa: ANN001
                raise AssertionError("no prose segment should reach process")

        with language_frontend_registry.overriding("zh", _OneBigPunct):
            result = Pipeline(profile="cn_current").translate_text("。")

        # 。 = ⠐⠆, two cells, no trailing space.
        assert len(result.render()) == 2

    def test_a_frontend_missing_a_half_is_rejected_at_resolution(self) -> None:
        # The registry's runtime protocol check covers both members now, so
        # half a language fails when it is resolved rather than by silently
        # segmenting nothing.
        class _ProcessOnly:
            prose_types = frozenset({"hanzi_text"})

            def process(self, surface, base, ctx):  # noqa: ANN001
                return []

        with language_frontend_registry.overriding("zh", _ProcessOnly):
            with pytest.raises(TypeError):
                language_frontend_registry.get("zh")


class TestWithoutAFrontendTheBuiltinChunkingStillRuns:
    """An unconfigured language is a gap in the prose path, not in the pass.

    The built-in chunking is what runs when no language claims the document,
    so its numbers, Latin and punctuation still translate and only the prose
    runs report a gap.
    """

    def test_non_prose_survives_and_prose_warns(self) -> None:
        pipe = Pipeline(profile="cn_current")
        pipe._profile.language = "xx-XX"
        result = pipe.translate_text("2026 CPU 我")
        codes = {w.code for w in result.warnings}
        assert "NO_LANGUAGE_FRONTEND" in codes
        assert "UNHANDLED_SEGMENT_TYPE" not in codes
        # The number and the Latin word came through the built-in chunking.
        assert result.render()

    def test_the_builtin_pass_is_the_module_function(self) -> None:
        # What the driver falls back to is the same public entry a language
        # delegates to, not a private copy of it.
        out = segment(Paragraph(text="我在2026"), _ctx())
        assert [s.type for s in out] == ["hanzi_text", "digit_run"]


class TestProseTypes:
    def test_zh_frontend_declares_hanzi_text(self):
        frontend = language_frontend_registry.get("zh")
        assert "hanzi_text" in frontend.prose_types

    def test_the_declared_types_are_what_its_segmentation_emits(self):
        # The two halves cannot drift: the type a frontend routes on is the
        # type its own segmentation produced. This was the loose joint when
        # the segmenter was a separate registration.
        frontend = language_frontend_registry.get("zh")
        types = {s.type for s in frontend.segment(Paragraph(text="我"), _ctx())}
        assert types <= set(frontend.prose_types) | {
            "digit_run",
            "latin_text",
            "greek_text",
            "punct",
            "space",
            "math_inline",
            "math_op",
            "phonetic_inline",
            "unknown",
        }
        assert types & set(frontend.prose_types)

    def test_all_prose_types_unions_registered_frontends(self):
        assert "hanzi_text" in _all_prose_types()


class TestNoSecondConfigurationAxis:
    """What the driver publishes, and what it no longer does."""

    def test_the_driver_names_the_language_analyzer_only(self) -> None:
        opts = Pipeline(profile="cn_current")._frontend.frontend_options()
        assert opts["zh_analyzer"] == "auto"
        # Neither family exists to be named...
        assert "segmenter" not in opts
        assert "normalizer" not in opts
        # ...and neither does the option they resolved themselves by: the
        # driver picks the language frontend itself, so publishing the subtag
        # would be a second, staler statement of ``profile.language``.
        assert "language" not in opts

    def test_japanese_profile_keys_its_own_analyzer(self) -> None:
        opts = Pipeline(profile="ja_current")._frontend.frontend_options()
        assert "ja_analyzer" in opts
        assert "zh_analyzer" not in opts

    def test_the_language_pick_helper_is_gone(self) -> None:
        # It existed only to answer "is an adapter registered under this
        # document's language?" for the two ``auto`` adapters that are gone.
        # Deleted rather than left resolving: an unused seam reads as a
        # supported one.
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("brailix.frontend._language_pick")


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

    def test_an_unknown_segment_type_still_reports_itself(self):
        # A frontend that emits a type nothing claims: the orchestrator
        # dispatches on Segment.type, and an unknown one is a structural drop
        # rather than a crash.
        class _Mystery:
            prose_types = frozenset({"hanzi_text"})

            def segment(self, block, ctx=None):  # noqa: ANN001
                text = block.text or ""
                return [
                    Segment(
                        type="kanji_text", surface=text, span=Span(0, len(text))
                    )
                ]

            def process(self, surface, base, ctx):  # noqa: ANN001
                return []

        with language_frontend_registry.overriding("zh", _Mystery):
            result = Pipeline(profile="cn_current").translate_text("X")
        assert "UNHANDLED_SEGMENT_TYPE" in {w.code for w in result.warnings}
