"""The library's defaults live on :class:`brailix.Pipeline`'s field
declarations, and every other statement of them agrees.

There used to be a ``brailix.core.defaults`` module holding one constant per
family — an indirection whose whole job was to keep the copies in step, which
it did not actually do: each subsystem kept a same-valued private copy anyway,
so changing a default there left the subsystem applying the old one to any
caller who omitted the option. Equal values made that invisible.

The defaults are now written where they are read: as the dataclass field
defaults a caller sees in the signature. Two kinds of statement remain, and
this file pins both:

* **Derived** — the CLI's ``--help`` and a front-end's preferences read the
  fields off the dataclass, so they cannot drift by construction. Checked
  here anyway, cheaply, because "reads it off the class" is a claim the code
  could stop honouring in a refactor.
* **Independent** — the frontend subsystems name ``"auto"`` themselves
  (they sit BELOW the orchestrator and must not import it), as does
  ``TranslationResult``. Those are genuine second statements of the same
  fact, and equality is what these tests exist to enforce.
"""

from __future__ import annotations

import dataclasses

import pytest

from brailix import Pipeline
from brailix.core.context import FrontendContext
from brailix.core.registry import Registry
from brailix.frontend.zh import tokenize
from brailix.frontend.zh.analyzer import registry as zh_analyzer_registry_mod
from brailix.frontend.zh.pinyin import annotate
from brailix.frontend.zh.pinyin import registry as pinyin_registry_mod
from brailix.frontend.zh.tokens import ChineseToken
from brailix.pipeline import TranslationResult

PIPELINE_DEFAULTS = {f.name: f.default for f in dataclasses.fields(Pipeline)}


def _ctx() -> FrontendContext:
    return FrontendContext(profile="cn_current")


class TestPipelineDeclaresTheDefaults:
    def test_every_engine_family_defaults_to_auto(self) -> None:
        for field_name in ("analyzer", "resolver"):
            assert PIPELINE_DEFAULTS[field_name] == "auto", field_name

    def test_segmenter_and_normalizer_are_not_fields(self) -> None:
        """Neither names an adapter family at all any more: segmentation
        belongs to the language frontend ``profile.language`` selects, and
        normalization is a fixed pass.

        Pinned because re-adding either would look like a harmless
        convenience while actually creating a second place for a fact settled
        elsewhere — and a knob whose only correct value is "whatever the
        language says" is one a caller can only get wrong.
        """
        assert "segmenter" not in PIPELINE_DEFAULTS
        assert "normalizer" not in PIPELINE_DEFAULTS

    def test_the_renderer_deliberately_has_no_auto(self) -> None:
        """A renderer choice is an output FORMAT, not a capability.

        No amount of probing can tell whether the caller wanted BRF or a PDF,
        so this family is the one that names a concrete adapter — pinned so
        an "everything should be auto" tidy-up has to argue with it first.
        """
        assert PIPELINE_DEFAULTS["default_renderer"] == "unicode"

    @pytest.mark.parametrize(
        ("field_name", "registry"),
        [
            ("analyzer", zh_analyzer_registry_mod.analyzer_registry),
            ("resolver", pinyin_registry_mod.resolver_registry),
        ],
    )
    def test_the_default_names_a_registered_adapter(
        self, field_name: str, registry: Registry
    ) -> None:
        # A default naming an adapter nobody registered fails every bare call.
        assert registry.has(PIPELINE_DEFAULTS[field_name])

    def test_default_renderer_is_registered(self) -> None:
        from brailix.renderer import renderer_registry

        assert renderer_registry.has(PIPELINE_DEFAULTS["default_renderer"])


class TestIndependentStatementsAgree:
    """The copies that genuinely cannot import the Pipeline."""

    def test_zh_analyzer_matches(self) -> None:
        import brailix.frontend.zh.analyzer as zh_analyzer_mod

        assert zh_analyzer_mod._AUTO == PIPELINE_DEFAULTS["analyzer"]

    def test_zh_pinyin_matches(self) -> None:
        import brailix.frontend.zh.pinyin as pinyin_mod

        assert pinyin_mod._AUTO == PIPELINE_DEFAULTS["resolver"]

    def test_translation_result_renderer_matches(self) -> None:
        result_default = {
            f.name: f.default for f in dataclasses.fields(TranslationResult)
        }["default_renderer"]
        assert result_default == PIPELINE_DEFAULTS["default_renderer"]


class TestDerivedStatementsReadRatherThanCopy:
    def test_cli_reads_the_pipeline_fields(self) -> None:
        from brailix import cli

        assert cli._PIPELINE_DEFAULTS == PIPELINE_DEFAULTS

    # A front-end's preferences make the same claim, and check it on their
    # own side: this file ships in the library's public test subset, which
    # cannot reach a front-end package.


class TestSubsystemsActuallyApplyTheirDefault:
    """Moving a subsystem's default moves what an option-less call selects.

    Not "the constants are equal" — that is exactly what hid the old bug.

    Segmentation and normalization are absent by construction: they name no
    adapter and read no option, so there is no default of theirs to apply
    (``tests/frontend/test_segment.py`` and ``test_normalize.py`` pin that
    directly).
    """

    def test_zh_analyzer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        class _Probe:
            name = "probe"

            def analyze(self, text, ctx=None):  # noqa: ANN001
                seen.append("probe")
                return []

        import brailix.frontend.zh.analyzer as zh_analyzer_mod

        with zh_analyzer_registry_mod.analyzer_registry.overriding(
            "probe", _Probe
        ):
            monkeypatch.setattr(zh_analyzer_mod, "_AUTO", "probe")
            tokenize("我", _ctx())
        assert seen == ["probe"]

    def test_pinyin_resolver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        class _Probe:
            name = "probe"

            def resolve(self, tokens, ctx=None):  # noqa: ANN001
                seen.append("probe")
                return list(tokens)

        import brailix.frontend.zh.pinyin as pinyin_mod

        with pinyin_registry_mod.resolver_registry.overriding("probe", _Probe):
            monkeypatch.setattr(pinyin_mod, "_AUTO", "probe")
            annotate([ChineseToken(surface="我")], _ctx())
        assert seen == ["probe"]


class TestJapaneseKeepsItsOwn:
    def test_ja_states_its_default_independently(self) -> None:
        """The deliberate non-sharing, pinned so a tidy-up doesn't weld them.

        zh and ja are independently replaceable language components; their
        defaults are separate facts that happen to be equal. One constant for
        both would mean neither could change without touching the other.
        """
        import brailix.frontend.ja.analyzer as ja_analyzer_mod

        assert ja_analyzer_mod._DEFAULT_ANALYZER == "auto"


class TestTheOldIndirectionIsGone:
    def test_core_defaults_module_no_longer_exists(self) -> None:
        with pytest.raises(ImportError):
            import brailix.core.defaults  # noqa: F401
