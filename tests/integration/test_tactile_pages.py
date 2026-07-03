"""G3 of inline tactile graphics — a braille document with embedded figures
lays out onto tactile pages through the one pipeline
(ARCHITECTURE.md).

Where G1 folded a ``GraphicBlock`` into ``translate_block`` (it now carries a
``TactileRaster``) and G2 let a ``` ```graphic ``` fence author one, G3 places
the compiled text (as real braille dots) and figures onto one or more page
rasters via ``Pipeline.translate_document_to_pages`` — output model A, a page
*is* a raster, exported through the existing tactile renderers. No BRF for a
mixed page.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.backend.tactile.profile import load_tactile_profile
from brailix.input.markdown import parse_markdown
from brailix.ir.document import DocumentIR, GraphicBlock, Paragraph
from brailix.ir.tactile import TactileRaster
from brailix.pipeline import TactilePageResult

CIRCLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="50mm" height="50mm"><circle cx="50" cy="50" r="40"/></svg>'
)


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    return Pipeline(profile="cn_current", analyzer="char", resolver="null")


def _raised(raster: TactileRaster) -> int:
    return raster.raised_count()


class TestMixedPageComposition:
    def test_text_and_figure_compose_one_page(self, pipe: Pipeline) -> None:
        # Latin + digits translate to real dots without a pinyin resolver, so
        # this stays headless (like the G1/G2 tests) yet still exercises text
        # stamping — hanzi under ``resolver="null"`` would be blank cells.
        doc = DocumentIR(
            blocks=[
                Paragraph(text="Chapter 1"),
                GraphicBlock(text=CIRCLE, source="svg"),
                Paragraph(text="Note 2"),
            ]
        )
        res = pipe.translate_document_to_pages(doc)
        assert isinstance(res, TactilePageResult)
        assert res.page_count == 1
        page = res.pages[0]
        assert isinstance(page, TactileRaster)
        # The text genuinely contributes: the page carries more ink than the
        # same figure laid out on its own.
        fig_only = pipe.translate_document_to_pages(
            DocumentIR(blocks=[GraphicBlock(text=CIRCLE, source="svg")])
        )
        assert _raised(page) > _raised(fig_only.pages[0]) > 0

    def test_text_only_document_still_makes_a_tactile_page(
        self, pipe: Pipeline
    ) -> None:
        # A figure-free document is still valid mixed output: the braille is
        # stamped as real dots on the page raster.
        res = pipe.translate_document_to_pages(
            DocumentIR(blocks=[Paragraph(text="Chapter 1")])
        )
        assert res.page_count == 1
        assert _raised(res.pages[0]) > 0

    def test_figure_only_document(self, pipe: Pipeline) -> None:
        res = pipe.translate_document_to_pages(
            DocumentIR(blocks=[GraphicBlock(text=CIRCLE, source="svg")])
        )
        assert res.page_count == 1
        assert _raised(res.pages[0]) > 0

    def test_empty_document_no_pages(self, pipe: Pipeline) -> None:
        res = pipe.translate_document_to_pages(DocumentIR(blocks=[]))
        assert res.pages == []
        assert res.page_count == 0

    def test_long_document_paginates(self, pipe: Pipeline) -> None:
        doc = DocumentIR(
            blocks=[Paragraph(text=f"第{i}段落文字内容") for i in range(80)]
        )
        res = pipe.translate_document_to_pages(doc)
        assert res.page_count > 1
        assert all(_raised(p) > 0 for p in res.pages)


class TestExport:
    def test_page_exports_to_bmp(self, pipe: Pipeline) -> None:
        doc = DocumentIR(
            blocks=[
                Paragraph(text="标题"),
                GraphicBlock(text=CIRCLE, source="svg"),
            ]
        )
        res = pipe.translate_document_to_pages(doc)
        data = res.render("bmp", page=0)
        assert isinstance(data, bytes) and data[:2] == b"BM"

    def test_render_all_one_per_page(self, pipe: Pipeline) -> None:
        doc = DocumentIR(
            blocks=[Paragraph(text=f"第{i}段") for i in range(80)]
        )
        res = pipe.translate_document_to_pages(doc)
        outputs = res.render_all("bmp")
        assert len(outputs) == res.page_count > 1
        assert all(o[:2] == b"BM" for o in outputs)

    def test_page_preview_reads_back(self, pipe: Pipeline) -> None:
        # The U+2800 readback renderer accepts a page raster unchanged — a
        # page is just another TactileRaster.
        res = pipe.translate_document_to_pages(
            DocumentIR(blocks=[GraphicBlock(text=CIRCLE, source="svg")])
        )
        preview = res.render("tactile_preview", page=0)
        assert isinstance(preview, str)
        assert any(ch != "⠀" and ch != "\n" for ch in preview)


class TestProfileAndWarnings:
    def test_accepts_loaded_tactile_profile(self, pipe: Pipeline) -> None:
        prof = load_tactile_profile("generic")
        res = pipe.translate_document_to_pages(
            DocumentIR(blocks=[Paragraph(text="Page 1")]), tactile_profile=prof
        )
        assert res.page_count == 1

    def test_bad_figure_soft_fails_and_warns(self, pipe: Pipeline) -> None:
        # A malformed figure never crashes the page build; its soft-failure
        # diagnostics are aggregated onto the result.
        doc = DocumentIR(
            blocks=[
                Paragraph(text="正文"),
                GraphicBlock(text="<not-svg", source="svg"),
            ]
        )
        res = pipe.translate_document_to_pages(doc)
        assert res.page_count == 1
        assert any(
            w.code in {"GRAPHICS_SOFT_FAIL", "GRAPHICS_BLOCK_PARSE_FAILED"}
            for w in res.warnings
        )


class TestAuthoredFigureToPage:
    def test_fenced_graphic_document_lays_out(self, pipe: Pipeline) -> None:
        # G2 authoring (```graphic fence) -> G1 compile -> G3 page: end to end
        # from portable braille markdown to a tactile page.
        doc = parse_markdown(
            f"引言\n\n```graphic\n{CIRCLE}\n```\n\n结语",
            profile="cn_current",
            language="zh-CN",
        )
        res = pipe.translate_document_to_pages(doc)
        assert res.page_count == 1
        assert _raised(res.pages[0]) > 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
