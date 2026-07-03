"""G1 of inline tactile graphics — a ``GraphicBlock`` compiles to a raster
through the *one* braille pipeline (ARCHITECTURE.md).

Before G1 the tactile raster only came out of the separate
``Pipeline.translate_graphic`` entry; the braille ``translate`` path skipped a
``GraphicBlock`` (its ``GraphicInline`` child isn't a braille node, so it
warned ``UNHANDLED_NODE_TYPE`` and produced empty cells).  G1 folds
rasterisation into ``Pipeline.translate_block``: a figure block now rides the
same incremental pipeline every text block uses, carrying its
``TactileRaster`` on the ``CompiledBlock``.  This is the compile-layer
collapse of the two code paths — no separate graphic pipeline call.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.input.markdown import parse_markdown
from brailix.ir.document import CodeBlock, DocumentIR, GraphicBlock, Paragraph
from brailix.ir.tactile import TactileRaster
from brailix.pipeline import CompiledBlock

CIRCLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="50mm" height="50mm"><circle cx="50" cy="50" r="40"/></svg>'
)
LABELED = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="50mm" height="50mm"><circle cx="50" cy="50" r="40"/>'
    '<text x="6" y="12">A</text></svg>'
)


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    return Pipeline(profile="cn_current", analyzer="char", resolver="null")


def _raised(raster: TactileRaster) -> int:
    """Count raised (non-flat) cells in the raster."""
    return sum(1 for v in raster.data if v)


class TestGraphicBlockCompilesThroughPipeline:
    def test_translate_block_attaches_raster(self, pipe: Pipeline) -> None:
        compiled = pipe.translate_block(GraphicBlock(text=CIRCLE, source="svg"))
        assert isinstance(compiled, CompiledBlock)
        assert isinstance(compiled.raster, TactileRaster)
        assert compiled.raster.width > 0 and compiled.raster.height > 0
        # A real circle rasterises to actual raised dots.
        assert _raised(compiled.raster) > 0

    def test_no_unhandled_node_warning(self, pipe: Pipeline) -> None:
        # The pre-G1 failure mode: GraphicInline reaching the braille
        # dispatcher and warning UNHANDLED_NODE_TYPE.  It must not appear.
        compiled = pipe.translate_block(GraphicBlock(text=CIRCLE, source="svg"))
        assert not any(
            w.code == "UNHANDLED_NODE_TYPE" for w in compiled.warnings
        )

    def test_graphic_block_expands_to_empty_placeholder(
        self, pipe: Pipeline
    ) -> None:
        # The figure holds its place in the block flow as one empty "graphic"
        # block; its dots live on the raster, not in cells.
        compiled = pipe.translate_block(GraphicBlock(text=CIRCLE, source="svg"))
        assert len(compiled.braille_blocks) == 1
        placeholder = compiled.braille_blocks[0]
        assert placeholder.block_type == "graphic"
        assert placeholder.cells == []

    def test_text_block_has_no_raster(self, pipe: Pipeline) -> None:
        # Only figure blocks carry a raster; a paragraph's is None.
        compiled = pipe.translate_block(Paragraph(text="你好"))
        assert compiled.raster is None
        assert compiled.braille_blocks[0].cells  # real braille cells

    def test_soft_fails_on_bad_svg(self, pipe: Pipeline) -> None:
        # A malformed figure still rasterises to *something* (blank) and never
        # crashes the pipeline — the "always compiles" contract.
        compiled = pipe.translate_block(
            GraphicBlock(text="<not-svg", source="svg")
        )
        assert isinstance(compiled.raster, TactileRaster)


class TestGraphicBlockInWholeDocument:
    def test_translate_document_keeps_figure_placeholder(
        self, pipe: Pipeline
    ) -> None:
        # A hand-built document holding a figure block compiles end-to-end
        # (whole-doc path) without an UNHANDLED warning, and the figure keeps
        # its place as a "graphic" braille block.
        doc = DocumentIR(
            blocks=[
                Paragraph(text="第一章"),
                GraphicBlock(text=CIRCLE, source="svg"),
            ]
        )
        result = pipe.translate_document(doc)
        types = [b.block_type for b in result.braille_ir.blocks]
        assert "graphic" in types
        assert not any(
            d.get("code") == "UNHANDLED_NODE_TYPE"
            for d in result.warnings.to_list()
        )


class TestFigureLabelsUseDocumentStandard:
    def test_labelled_figure_compiles_and_stamps(self, pipe: Pipeline) -> None:
        # A figure with a <text> label compiles through translate_block with no
        # separate braille profile — the label is translated through THIS
        # pipeline's own text path (the document's braille standard), so it
        # stamps onto the raster rather than being skipped.
        plain = pipe.translate_block(GraphicBlock(text=CIRCLE, source="svg"))
        labelled = pipe.translate_block(GraphicBlock(text=LABELED, source="svg"))
        assert labelled.raster is not None and plain.raster is not None
        # The label can only add raised dots (set_raise is a MAX), so a
        # labelled figure never has fewer than the same figure without it.
        assert _raised(labelled.raster) >= _raised(plain.raster)


class TestFencedGraphicSyntax:
    """G2: a ```graphic fence in braille markdown parses to a GraphicBlock, so
    a chapter can carry its figures inline in one portable document."""

    def test_graphic_fence_parses_to_graphic_block(self) -> None:
        doc = parse_markdown(
            f"```graphic\n{CIRCLE}\n```", profile="cn_current", language="zh-CN"
        )
        graphics = [b for b in doc.blocks if isinstance(b, GraphicBlock)]
        assert len(graphics) == 1
        assert graphics[0].source == "svg"
        assert graphics[0].text.strip() == CIRCLE

    def test_format_variants(self) -> None:
        for fence, fmt in (
            ("graphic-svg", "svg"),
            ("graphic-figure", "figure"),
            ("graphic-primitives", "primitives"),
        ):
            doc = parse_markdown(
                f"```{fence}\nbody\n```", profile="cn_current", language="zh-CN"
            )
            blk = doc.blocks[0]
            assert isinstance(blk, GraphicBlock)
            assert blk.source == fmt

    def test_ordinary_code_fence_stays_code(self) -> None:
        # A real code fence must NOT become a figure.
        doc = parse_markdown(
            "```python\nx = 1\n```", profile="cn_current", language="zh-CN"
        )
        assert isinstance(doc.blocks[0], CodeBlock)
        assert not any(isinstance(b, GraphicBlock) for b in doc.blocks)

    def test_figure_interleaves_with_prose(self) -> None:
        src = f"第一章\n\n```graphic\n{CIRCLE}\n```\n\n第二段"
        doc = parse_markdown(src, profile="cn_current", language="zh-CN")
        kinds = [b.type for b in doc.blocks]
        assert kinds == ["paragraph", "graphic", "paragraph"]

    def test_authored_figure_compiles_to_raster(self) -> None:
        # G2 authoring → G1 compile: a fence-authored figure block rasterises
        # through the same pipeline.
        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="null")
        doc = parse_markdown(
            f"正文\n\n```graphic\n{CIRCLE}\n```",
            profile="cn_current",
            language="zh-CN",
        )
        figure = next(b for b in doc.blocks if isinstance(b, GraphicBlock))
        compiled = pipe.translate_block(figure)
        assert isinstance(compiled.raster, TactileRaster)
        assert _raised(compiled.raster) > 0
        # Whole-doc translate also stays clean (empty graphic placeholder).
        result = pipe.translate_document(doc)
        assert "graphic" in [b.block_type for b in result.braille_ir.blocks]
