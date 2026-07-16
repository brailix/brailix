"""Embedded-text diagnostics honour the host compile's mode and collector.

The inline-text seam (music ``<words>`` / lyrics, math ``\\text{...}`` /
``<mtext>``, chem conditions, graphic labels) used to run against a private
NORMAL collector that was thrown away: an untranslatable character inside
embedded text degraded the output *and* left ``TranslationResult.warnings``
empty — and strict mode never fired. That contradicts the mode contract
(strict: fail on anything unrecognized; normal: recover and keep the
warning). Now the nested run's diagnostics are re-emitted into the host
collector, tagged with the embedding construct, and only the explicit
preview API (:meth:`Pipeline.translate_math_inline`) still discards.
"""

from __future__ import annotations

import pytest

from brailix.core.errors import StrictModeError
from brailix.ir.document import GraphicBlock
from brailix.pipeline import Pipeline

# U+E000 (private use area): no zh / latin / punct table knows it, so the
# nested text path reliably degrades with a warning wherever it appears.
BAD = ""

SCORE_WITH_WORDS = f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>P</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <direction><direction-type><words>提示 {BAD} 语</words></direction-type></direction>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>
"""

LABEL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40">'
    f'<text x="2" y="10">甲{BAD}</text></svg>'
)


@pytest.fixture(scope="module")
def normal() -> Pipeline:
    return Pipeline(profile="cn_current")


@pytest.fixture(scope="module")
def strict() -> Pipeline:
    return Pipeline(profile="cn_current", mode="strict")


def _domain_warnings(warnings, domain: str):
    return [
        w
        for w in warnings
        if w.anchor is not None and w.anchor.get("domain") == domain
    ]


class TestMusicWords:
    def test_normal_mode_surfaces_nested_warnings(self, normal: Pipeline) -> None:
        doc = normal.parse_text(SCORE_WITH_WORDS, format="musicxml")
        result = normal.translate_document(doc)
        merged = _domain_warnings(result.warnings, "music_words")
        assert merged, "nested <words> warnings must reach the final result"
        assert BAD in (merged[0].anchor or {}).get("embedded_text", "")

    def test_strict_mode_raises_from_nested_words(self, strict: Pipeline) -> None:
        doc = strict.parse_text(SCORE_WITH_WORDS, format="musicxml")
        with pytest.raises(StrictModeError):
            strict.translate_document(doc)


class TestMathText:
    SRC = f"设 $\\text{{甲{BAD}}}$ 为"

    def test_normal_mode_surfaces_nested_warnings(self, normal: Pipeline) -> None:
        result = normal.translate_text(self.SRC)
        assert _domain_warnings(result.warnings, "math_text")

    def test_strict_mode_raises_from_mtext(self, strict: Pipeline) -> None:
        with pytest.raises(StrictModeError):
            strict.translate_text(self.SRC)

    def test_clean_embedded_text_stays_warning_free(
        self, normal: Pipeline
    ) -> None:
        # The seam must not manufacture noise: fully translatable embedded
        # text merges nothing.
        result = normal.translate_text("设 $\\text{ab}$ 为")
        assert not _domain_warnings(result.warnings, "math_text")


class TestGraphicLabels:
    def test_block_compile_reports_label_warnings(self, normal: Pipeline) -> None:
        compiled = normal.translate_block(GraphicBlock(text=LABEL_SVG, source="svg"))
        assert [
            w
            for w in compiled.warnings
            if w.anchor is not None and w.anchor.get("domain") == "graphic_label"
        ]

    def test_standalone_graphic_reports_label_warnings(
        self, normal: Pipeline
    ) -> None:
        result = normal.translate_graphic(
            LABEL_SVG, braille_profile="cn_current"
        )
        assert _domain_warnings(result.warnings, "graphic_label")


class TestPreviewContractUnchanged:
    def test_translate_math_inline_still_discards(self, normal: Pipeline) -> None:
        # The documented preview contract: parse failures / unknown symbols
        # degrade to soft-failure cells, and NOTHING pollutes the caller's
        # diagnostics — there is no collector to pollute.
        out = normal.translate_math_inline(f"\\text{{甲{BAD}}}", "latex")
        assert isinstance(out, str)

    def test_strict_pipeline_preview_does_not_raise(self) -> None:
        strict = Pipeline(profile="cn_current", mode="strict")
        out = strict.translate_math_inline(f"\\text{{{BAD}}}", "latex")
        assert isinstance(out, str)


class TestWarningShape:
    def test_merged_warning_span_is_hosts_not_nested(
        self, normal: Pipeline
    ) -> None:
        # The nested run's 0-based throwaway spans must not leak: the merged
        # warning is anchored to the embedding node's span (the math inline
        # island inside the paragraph), or carries none — never a span that
        # points at the head of the host document by accident.
        src = f"甲甲甲 $\\text{{乙{BAD}}}$ 尾"
        result = normal.translate_text(src)
        (w,) = _domain_warnings(result.warnings, "math_text")
        assert w.span is None or w.span.start > 0
