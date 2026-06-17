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
from brailix.ir.braille import BrailleDocument
from brailix.renderer import renderer_registry

# ---------------------------------------------------------------------------
# Default-renderer behaviour
# ---------------------------------------------------------------------------


class TestDefaultRendering:
    def test_no_arg_uses_default_renderer(self):
        pipe = Pipeline()
        result = pipe.translate_text("。")
        # 。 = ⠐⠆: two cells, no space on either side.
        # ⠐ = U+2810 (dot 5), ⠆ = U+2806 (dots 2,3).
        assert result.render() == chr(0x2810) + chr(0x2806)

    def test_default_renderer_propagated_from_pipeline(self):
        # When the user changes the pipeline default, the result picks
        # it up so ``result.render()`` honors the same choice.
        pipe = Pipeline(default_renderer="unicode")
        result = pipe.translate_text("")
        assert result.default_renderer == "unicode"


# ---------------------------------------------------------------------------
# Explicit name dispatch
# ---------------------------------------------------------------------------


class TestExplicitName:
    def test_explicit_unicode(self):
        pipe = Pipeline()
        result = pipe.translate_text("。")
        assert result.render("unicode") == chr(0x2810) + chr(0x2806)

    def test_unknown_renderer_raises_keyerror(self):
        pipe = Pipeline()
        result = pipe.translate_text("。")
        with pytest.raises(KeyError):
            result.render("does-not-exist")


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
        renderer_registry.register("cell-list-test", _CellListRenderer)
        try:
            pipe = Pipeline()
            result = pipe.translate_text("。")  # 。 = ⠐⠆ → two cells, no blank
            cells = result.render("cell-list-test")
            assert cells == [(5,), (2, 3)]
        finally:
            renderer_registry.unregister("cell-list-test")

    def test_bytes_renderer(self):
        renderer_registry.register("bytes-test", _BytesRenderer)
        try:
            pipe = Pipeline()
            result = pipe.translate_text("。")  # ⠐ sums to 5, ⠆ sums to 5
            out = result.render("bytes-test")
            assert isinstance(out, bytes)
            assert out == bytes([5, 5])
        finally:
            renderer_registry.unregister("bytes-test")

    def test_pipeline_default_can_be_a_custom_renderer(self):
        renderer_registry.register("cell-list-test", _CellListRenderer)
        try:
            pipe = Pipeline(default_renderer="cell-list-test")
            result = pipe.translate_text("。")
            assert result.render() == [(5,), (2, 3)]
        finally:
            renderer_registry.unregister("cell-list-test")


# ---------------------------------------------------------------------------
# Reusing the same IR across renderers
# ---------------------------------------------------------------------------


class TestMultiFormat:
    def test_same_result_renders_multiple_formats(self):
        renderer_registry.register("cell-list-test", _CellListRenderer)
        try:
            pipe = Pipeline()
            result = pipe.translate_text("。")
            unicode_out = result.render("unicode")
            cells_out = result.render("cell-list-test")
            # Same braille tree, different encodings.
            assert unicode_out == chr(0x2810) + chr(0x2806)
            assert cells_out == [(5,), (2, 3)]
            # The braille IR itself is shared and unchanged.
            assert result.braille_ir is result.braille_ir
        finally:
            renderer_registry.unregister("cell-list-test")


# ---------------------------------------------------------------------------
# proofread_json doesn't pre-render
# ---------------------------------------------------------------------------


class TestProofreadJson:
    def test_payload_has_no_rendered_field(self):
        pipe = Pipeline()
        payload = pipe.translate_text("。").proofread_json()
        assert set(payload) == {"text", "ir", "braille_ir", "warnings"}
        # No "unicode_braille" / "rendered" field leaks in.
        assert "rendered" not in payload
        assert "unicode_braille" not in payload


# ---------------------------------------------------------------------------
# TranslationResult is usable on its own
# ---------------------------------------------------------------------------


class TestSegmenterAndNormalizerFields:
    """``segmenter`` and ``normalizer`` are now Pipeline-level fields
    that look up adapters by name in their respective registries — no
    longer hard-coded to ``DefaultSegmenter`` / ``DefaultNormalizer``."""

    def test_custom_segmenter_via_field(self):
        from dataclasses import dataclass

        from brailix import Pipeline
        from brailix.core.span import Span
        from brailix.frontend.segment import segmenter_registry
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

        segmenter_registry.register("all-punct-test", _OneBigPunctSegmenter)
        try:
            pipe = Pipeline(segmenter="all-punct-test")
            # Pipe a single known-punct char through and confirm it
            # actually went through our segmenter.
            result = pipe.translate_text("。")
            # Default normalizer converts punct segment → Punct node → cells.
            # 。 = ⠐⠆ is two cells with no trailing space.
            assert len(result.render()) == 2
        finally:
            segmenter_registry.unregister("all-punct-test")

    def test_custom_normalizer_via_field(self):
        from dataclasses import dataclass

        from brailix import Pipeline
        from brailix.frontend.normalize import normalizer_registry

        @dataclass(slots=True)
        class _DropEverythingNormalizer:
            name: str = "drop"

            def normalize(self, segments, ctx=None):
                return []  # drop all segments

        normalizer_registry.register("drop-test", _DropEverythingNormalizer)
        try:
            pipe = Pipeline(normalizer="drop-test")
            result = pipe.translate_text("。")
            # Nothing came through the normalizer → no cells rendered.
            assert result.render() == ""
        finally:
            normalizer_registry.unregister("drop-test")


class TestUnhandledSegmentType:
    """Pipeline dispatches Segment.type to a per-type handler internally;
    an unknown type emits a structural-drop warning instead of crashing.
    We trigger one by injecting a custom segmenter that emits a type the
    Pipeline doesn't know."""

    def test_unknown_segment_type_emits_warning(self):
        from brailix import Pipeline
        from brailix.core.span import Span
        from brailix.frontend.segment import segmenter_registry
        from brailix.ir.inline import Segment

        class _MysterySegmenter:
            name = "mystery"

            def segment(self, block, ctx):
                return [Segment(type="kanji_text", surface=block.text, span=Span(0, len(block.text)))]

        segmenter_registry.register("mystery", _MysterySegmenter)
        try:
            pipe = Pipeline(segmenter="mystery")
            result = pipe.translate_text("X")
            codes = {w.code for w in result.warnings}
            assert "UNHANDLED_SEGMENT_TYPE" in codes
        finally:
            segmenter_registry.unregister("mystery")

    def test_string_strict_mode_promotes_warning(self):
        from brailix import Pipeline
        from brailix.core.errors import StrictModeError

        pipe = Pipeline(mode="strict", resolver="null")
        with pytest.raises(StrictModeError):
            pipe.translate_text("重庆")


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

        pipe = Pipeline(mode="strict", analyzer="null", resolver="null")
        with pytest.raises(StrictModeError) as ei:
            pipe.translate_text(self.BAD_MATH)
        assert ei.value.warning.code == "MATH_ERROR"

    def test_normal_keeps_error_level_but_still_renders(self):
        from brailix import Pipeline
        from brailix.core.errors import WarningLevel

        pipe = Pipeline(mode="normal", analyzer="null", resolver="null")
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

        pipe = Pipeline(mode="lenient", analyzer="null", resolver="null")
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
            pipe = Pipeline(mode=mode, analyzer="null", resolver="null")
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
            Pipeline(mode="strict", analyzer="null", resolver="null").translate_text(
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
        ctx = BackendContext()
        doc = DocumentIR(blocks=[Paragraph(children=[Punct(surface="。")])])
        braille_doc = translate_document(doc, ctx, profile)
        result = TranslationResult(
            text="。", ir=doc, braille_ir=braille_doc
        )
        # 。 = ⠐⠆: two cells, no trailing blank.
        assert result.render() == chr(0x2810) + chr(0x2806)
