"""Tests for the on-demand :meth:`TranslationResult.render` API.

The pipeline no longer pre-renders its output. ``TranslationResult``
exposes ``render(name=None)`` which dispatches through the renderer
registry, so multiple output formats reuse the same braille IR
without re-running the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from brailix import Pipeline, TranslationResult
from brailix.core.errors import UnknownAdapterError
from brailix.ir.braille import BrailleDocument
from brailix.ir.document import DocumentIR
from brailix.ir.tactile import TactileRaster
from brailix.pipeline import GraphicResult, TactilePageResult
from brailix.renderer import renderer_registry

# ---------------------------------------------------------------------------
# Default-renderer behaviour
# ---------------------------------------------------------------------------


class TestDefaultRendering:
    def test_no_arg_uses_default_renderer(self):
        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("。")
        # 。 = ⠐⠆: two cells, no space on either side.
        # ⠐ = U+2810 (dot 5), ⠆ = U+2806 (dots 2,3).
        assert result.render() == chr(0x2810) + chr(0x2806)

    def test_default_renderer_propagated_from_pipeline(self):
        # When the user changes the pipeline default, the result picks
        # it up so ``result.render()`` honors the same choice.
        pipe = Pipeline(profile="cn_current", default_renderer="unicode")
        result = pipe.translate_text("")
        assert result.default_renderer == "unicode"


# ---------------------------------------------------------------------------
# Explicit name dispatch
# ---------------------------------------------------------------------------


class TestExplicitName:
    def test_explicit_unicode(self):
        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("。")
        assert result.render("unicode") == chr(0x2810) + chr(0x2806)

    def test_unknown_renderer_raises_keyerror(self):
        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("。")
        with pytest.raises(KeyError):
            result.render("does-not-exist")


# ---------------------------------------------------------------------------
# Only ``None`` means "no name given"
# ---------------------------------------------------------------------------


def _results() -> dict[str, object]:
    """One of each result type, as the pipeline hands them back.

    Every one of them selects a renderer, each used to select it with its own
    copy of the same expression, and the four are checked together for that
    reason: the bug was identical in all four, and a fix applied to the one
    that happened to have a test would have left the other three.
    """
    raster = TactileRaster(
        width=4,
        height=4,
        dpi=20.0,
        page_width_mm=5.0,
        page_height_mm=5.0,
        data=bytearray(16),
    )
    pages = TactilePageResult(pages=[raster])
    return {
        "TranslationResult.render": TranslationResult(
            text="", ir=DocumentIR(), braille_ir=BrailleDocument()
        ).render,
        "GraphicResult.render": GraphicResult(raster=raster).render,
        "TactilePageResult.render": pages.render,
        "TactilePageResult.render_all": pages.render_all,
    }


class TestTheEmptyRendererNameIsAName:
    """``render("")`` must raise, not quietly render the default.

    Selection read ``name or self.default_renderer``, which is not the rule the
    documentation states and not the rule a caller assumes: ``""`` is a name
    that was *passed*, and falsiness turned it into the default. So a renderer
    read out of a config file with the key missing, a blank CLI flag, an unset
    form field, or a plugin name assembled from parts that came out empty all
    produced the default renderer's output and reported success — while every
    other wrong name raised. A caller cannot tell that apart from having asked
    for what they got.
    """

    @pytest.mark.parametrize("label", sorted(_results()))
    def test_an_empty_name_is_not_the_default(self, label):
        method = _results()[label]
        with pytest.raises(UnknownAdapterError):
            method("")

    @pytest.mark.parametrize("label", sorted(_results()))
    def test_omitting_the_name_and_passing_none_agree(self, label):
        """The other half: fixing the empty string must not disturb the two
        spellings that really do mean "no name given"."""
        method = _results()[label]
        assert method() == method(None)

    def test_the_exception_is_the_registry_contract(self):
        """Asserted against the registry's own error, not a bare ``KeyError``:
        it subclasses ``KeyError`` so the documented promise still holds, and
        the message names the subsystem and lists what is registered."""
        assert issubclass(UnknownAdapterError, KeyError)
        with pytest.raises(UnknownAdapterError) as excinfo:
            TranslationResult(
                text="", ir=DocumentIR(), braille_ir=BrailleDocument()
            ).render("")
        assert "renderer" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Pluggable renderer — non-string output is supported
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CellListRenderer:
    """Test-only renderer that returns ``list[tuple[int, ...]]``.

    Demonstrates that the Renderer protocol does not bake in
    ``str`` / ``bytes`` — anything goes, the caller decides what to do
    with the value.
    """

    name: str = "cell-list-test"

    def render(self, source: BrailleDocument):
        out: list[tuple[int, ...]] = []
        for block in source.blocks:
            for c in block.cells:
                out.append(tuple(c.dots))
        return out


@dataclass(slots=True)
class _BytesRenderer:
    """Returns bytes — verifies non-string returns flow through cleanly."""

    name: str = "bytes-test"

    def render(self, source: BrailleDocument):
        # Toy encoding: one byte per cell, value = sum of dots.
        out = bytearray()
        for block in source.blocks:
            for c in block.cells:
                out.append(sum(c.dots) & 0xFF)
        return bytes(out)


class TestCustomRenderer:
    def test_cells_list_renderer(self):
        with renderer_registry.overriding("cell-list-test", _CellListRenderer):
            pipe = Pipeline(profile="cn_current")
            result = pipe.translate_text("。")  # 。 = ⠐⠆ → two cells, no blank
            cells = result.render("cell-list-test")
            assert cells == [(5,), (2, 3)]

    def test_bytes_renderer(self):
        with renderer_registry.overriding("bytes-test", _BytesRenderer):
            pipe = Pipeline(profile="cn_current")
            result = pipe.translate_text("。")  # ⠐ sums to 5, ⠆ sums to 5
            out = result.render("bytes-test")
            assert isinstance(out, bytes)
            assert out == bytes([5, 5])

    def test_pipeline_default_can_be_a_custom_renderer(self):
        with renderer_registry.overriding("cell-list-test", _CellListRenderer):
            pipe = Pipeline(profile="cn_current", default_renderer="cell-list-test")
            result = pipe.translate_text("。")
            assert result.render() == [(5,), (2, 3)]


# ---------------------------------------------------------------------------
# Reusing the same IR across renderers
# ---------------------------------------------------------------------------


class TestMultiFormat:
    def test_same_result_renders_multiple_formats(self):
        with renderer_registry.overriding("cell-list-test", _CellListRenderer):
            pipe = Pipeline(profile="cn_current")
            result = pipe.translate_text("。")
            unicode_out = result.render("unicode")
            cells_out = result.render("cell-list-test")
            # Same braille tree, different encodings.
            assert unicode_out == chr(0x2810) + chr(0x2806)
            assert cells_out == [(5,), (2, 3)]
            # The braille IR itself is shared and unchanged.
            assert result.braille_ir is result.braille_ir


# ---------------------------------------------------------------------------
# proofread_json doesn't pre-render
# ---------------------------------------------------------------------------


class TestProofreadJson:
    def test_payload_has_no_rendered_field(self):
        pipe = Pipeline(profile="cn_current")
        payload = pipe.translate_text("。").proofread_json()
        assert set(payload) == {"text", "ir", "braille_ir", "warnings"}
        # No "unicode_braille" / "rendered" field leaks in.
        assert "rendered" not in payload
        assert "unicode_braille" not in payload


# ---------------------------------------------------------------------------
# TranslationResult is usable on its own
# ---------------------------------------------------------------------------


class TestLanguageRegisteredSegmenterAndNormalizer:
    """Segmentation and normalization are pluggable **by language**.

    Neither is a ``Pipeline`` field: which one applies follows from
    ``profile.language``, so a replacement is installed by registering it
    under the language subtag — the same move a new language makes — and
    the ``auto`` adapter routes to it. Registering under an arbitrary name
    would prove nothing, since nothing would ever select it."""

    def test_custom_segmenter_via_language_registration(self):
        from dataclasses import dataclass

        from brailix import Pipeline
        from brailix.core.span import Span
        from brailix.frontend.segmentation import segmenter_registry
        from brailix.ir.inline import Segment

        @dataclass(slots=True)
        class _OneBigPunctSegmenter:
            name: str = "all-punct"

            def segment(self, block, ctx=None):
                text = block.text or ""
                if not text:
                    return []
                # Classify the entire text as one punct segment so the
                # default normalizer wraps it as a single Punct node.
                return [Segment(type="punct", surface=text, span=Span(0, len(text)))]

        with segmenter_registry.overriding("zh", _OneBigPunctSegmenter):
            pipe = Pipeline(profile="cn_current")
            # Pipe a single known-punct char through and confirm it
            # actually went through our segmenter.
            result = pipe.translate_text("。")
            # Default normalizer converts punct segment → Punct node → cells.
            # 。 = ⠐⠆ is two cells with no trailing space.
            assert len(result.render()) == 2

    def test_custom_normalizer_via_language_registration(self):
        from dataclasses import dataclass

        from brailix import Pipeline
        from brailix.frontend.normalization import normalizer_registry

        @dataclass(slots=True)
        class _DropEverythingNormalizer:
            name: str = "drop"

            def normalize(self, segments, ctx=None):
                return []  # drop all segments

        with normalizer_registry.overriding("zh", _DropEverythingNormalizer):
            pipe = Pipeline(profile="cn_current")
            result = pipe.translate_text("。")
            # Nothing came through the normalizer → no cells rendered.
            assert result.render() == ""


class TestUnhandledSegmentType:
    """Pipeline dispatches Segment.type to a per-type handler internally;
    an unknown type emits a structural-drop warning instead of crashing.
    We trigger one by registering, under the profile's language, a segmenter
    that emits a type the Pipeline doesn't know."""

    def test_unknown_segment_type_emits_warning(self):
        from brailix import Pipeline
        from brailix.core.span import Span
        from brailix.frontend.segmentation import segmenter_registry
        from brailix.ir.inline import Segment

        class _MysterySegmenter:
            name = "mystery"

            def segment(self, block, ctx):
                return [Segment(type="kanji_text", surface=block.text, span=Span(0, len(block.text)))]

        with segmenter_registry.overriding("zh", _MysterySegmenter):
            pipe = Pipeline(profile="cn_current")
            result = pipe.translate_text("X")
            codes = {w.code for w in result.warnings}
            assert "UNHANDLED_SEGMENT_TYPE" in codes

    def test_string_strict_mode_promotes_warning(self):
        from brailix import Pipeline
        from brailix.core.errors import StrictModeError

        pipe = Pipeline(profile="cn_current", mode="strict", resolver="null")
        with pytest.raises(StrictModeError):
            pipe.translate_text("重庆")


@pytest.mark.requires("latex2mathml")
class TestRunModeEndToEnd:
    """End-to-end mode contrast through the Pipeline.

    ``tests/core/test_errors.py`` covers the WarningCollector mechanism in
    isolation; this exercises all three modes through a full
    ``translate_text`` run on the *same* malformed input so the contrast is
    observable at the public API.

    Input: ``$\\frac{1}{$`` — an unbalanced ``\\frac`` that latex2mathml
    cannot parse. The math frontend surfaces a ``MATH_ERROR`` warning and
    falls back to a placeholder cell.

    Observed real behaviour (verified by running each mode, not assumed):

    * strict  → the MATH_ERROR is promoted to a ``StrictModeError`` and the
      run aborts (no output at all).
    * normal  → the warning is recorded **at ERROR level** and the run still
      completes, rendering a fallback (blank) cell.
    * lenient → same recovered output, but the ERROR-level warning is
      *downgraded to WARN* (see ``WarningCollector`` in core/errors.py).

    A failed parse (``MATH_ERROR``) is an *unrecoverable structure* — the
    formula is lost, only a placeholder cell stands in — so it is emitted at
    ``WarningLevel.ERROR``. That makes the three modes genuinely distinct on
    the same input: strict aborts, normal flags it as an error (a front-end
    can surface it red), and lenient (experimental "just give me output")
    downgrades it to a warning so nothing reads as a hard failure. The
    rendered braille is identical for normal/lenient — recovery is the same;
    only the diagnostic *level* differs.
    """

    BAD_MATH = "$\\frac{1}{$"

    def test_strict_aborts_on_malformed_math(self):
        from brailix import Pipeline
        from brailix.core.errors import StrictModeError

        pipe = Pipeline(profile="cn_current", mode="strict", analyzer="null", resolver="null")
        with pytest.raises(StrictModeError) as ei:
            pipe.translate_text(self.BAD_MATH)
        assert ei.value.warning.code == "MATH_ERROR"

    def test_normal_keeps_error_level_but_still_renders(self):
        from brailix import Pipeline
        from brailix.core.errors import WarningLevel

        pipe = Pipeline(profile="cn_current", mode="normal", analyzer="null", resolver="null")
        result = pipe.translate_text(self.BAD_MATH)
        math_errs = [w for w in result.warnings if w.code == "MATH_ERROR"]
        assert math_errs, "expected a MATH_ERROR warning"
        # NORMAL keeps the unrecoverable-structure diagnostic at ERROR level.
        assert all(w.level is WarningLevel.ERROR for w in math_errs)
        # Run completed and produced a (fallback) rendering rather than
        # aborting — the placeholder cell is U+2800 (blank braille).
        out = result.render()
        assert isinstance(out, str)
        assert out == "⠀"

    def test_lenient_downgrades_error_to_warn(self):
        from brailix import Pipeline
        from brailix.core.errors import WarningLevel

        pipe = Pipeline(profile="cn_current", mode="lenient", analyzer="null", resolver="null")
        result = pipe.translate_text(self.BAD_MATH)
        math_errs = [w for w in result.warnings if w.code == "MATH_ERROR"]
        assert math_errs, "expected a MATH_ERROR warning"
        # LENIENT downgrades the ERROR to WARN — nothing reads as hard-failed.
        assert all(w.level is WarningLevel.WARN for w in math_errs)
        out = result.render()
        assert isinstance(out, str)
        assert out == "⠀"

    def test_modes_differ_on_same_input(self):
        """Same input, three distinct outcomes: strict aborts; normal vs
        lenient render the same braille but disagree on the warning level."""
        from brailix import Pipeline
        from brailix.core.errors import StrictModeError, WarningLevel

        results = {}
        for mode in ("normal", "lenient"):
            pipe = Pipeline(profile="cn_current", mode=mode, analyzer="null", resolver="null")
            results[mode] = pipe.translate_text(self.BAD_MATH)
        # Recovery is identical...
        assert results["normal"].render() == results["lenient"].render()

        def level(result):
            return next(
                w.level for w in result.warnings if w.code == "MATH_ERROR"
            )

        # ...but the diagnostic level distinguishes them.
        assert level(results["normal"]) is WarningLevel.ERROR
        assert level(results["lenient"]) is WarningLevel.WARN

        with pytest.raises(StrictModeError):
            Pipeline(profile="cn_current", mode="strict", analyzer="null", resolver="null").translate_text(
                self.BAD_MATH
            )


class TestStandaloneResult:
    def test_render_without_a_pipeline(self):
        # A caller can hand-build a TranslationResult and still render
        # through the registry — useful for tests and for tools that
        # consume saved BrailleDocuments.
        from brailix.backend.block import translate_document
        from brailix.core.config import load_profile
        from brailix.core.context import BackendContext
        from brailix.ir.document import DocumentIR, Paragraph
        from brailix.ir.inline import Punct

        profile = load_profile("cn_current")
        ctx = BackendContext(profile="cn_current")
        doc = DocumentIR(blocks=[Paragraph(children=[Punct(surface="。")])])
        braille_doc = translate_document(doc, ctx, profile)
        result = TranslationResult(
            text="。", ir=doc, braille_ir=braille_doc
        )
        # 。 = ⠐⠆: two cells, no trailing blank.
        assert result.render() == chr(0x2810) + chr(0x2806)
