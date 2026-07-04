"""An un-converted image (``ImageAlt``) compiles through the one braille
pipeline: its alt text becomes real braille, and the picture's absence is
surfaced as an ``IMAGE_NOT_CONVERTED`` warning — the reader's running list
of images still awaiting a per-image "convert to tactile graphic" decision
(ARCHITECTURE.md, the I1 step).
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.ir.document import DocumentIR, ImageAlt, Paragraph


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    return Pipeline(profile="cn_current", analyzer="char", resolver="null")


class TestImageAltCompiles:
    def test_alt_text_translates_to_braille(self, pipe: Pipeline) -> None:
        compiled = pipe.translate_block(
            ImageAlt(text="地图", target="media/image1.png")
        )
        (block,) = compiled.braille_blocks
        assert block.block_type == "image_alt"
        # The alt text is real prose braille, not empty — a blind reader
        # still learns what the picture was.
        assert block.cells

    def test_warns_image_not_converted(self, pipe: Pipeline) -> None:
        compiled = pipe.translate_block(
            ImageAlt(text="地图", target="media/image1.png")
        )
        assert any(w.code == "IMAGE_NOT_CONVERTED" for w in compiled.warnings)

    def test_target_less_placeholder_still_warns(self, pipe: Pipeline) -> None:
        compiled = pipe.translate_block(ImageAlt(text="无源图", target=None))
        assert any(w.code == "IMAGE_NOT_CONVERTED" for w in compiled.warnings)

    def test_whole_document_path(self, pipe: Pipeline) -> None:
        doc = DocumentIR(
            blocks=[
                Paragraph(text="正文"),
                ImageAlt(text="示意图", target="media/image1.png"),
            ]
        )
        result = pipe.translate_document(doc)
        types = [b.block_type for b in result.braille_ir.blocks]
        assert "image_alt" in types
        assert any(
            d.get("code") == "IMAGE_NOT_CONVERTED"
            for d in result.warnings.to_list()
        )
