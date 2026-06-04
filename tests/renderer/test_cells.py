"""Tests for :mod:`brailix.renderer.cells`.

The cells renderer is the structural counterpart to unicode/brf — its
output is a JSON-friendly list (or dict) that downstream tooling can
reason about by role / source span. Tests here lock in the shape so
proofread tools have a stable contract."""

from brailix.core.span import Span
from brailix.ir.braille import (
    BLANK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.renderer.cells import CellsRenderer


class TestSequence:
    def test_empty_sequence_yields_empty_list(self):
        out = CellsRenderer().render(BrailleSequence(cells=[]))
        assert out == []

    def test_single_cell_payload(self):
        cell = BrailleCell(
            dots=(1, 2),
            role="zh_initial",
            source_span=Span(0, 1),
            source_text="b",
        )
        out = CellsRenderer().render(BrailleSequence(cells=[cell]))
        assert out == [
            {
                "dots": [1, 2],
                "role": "zh_initial",
                "source_span": [0, 1],
                "source_text": "b",
            }
        ]

    def test_blanks_kept_by_default(self):
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,), role="a"),
            BLANK_CELL,
            BrailleCell(dots=(1, 2), role="b"),
        ])
        out = CellsRenderer().render(seq)
        assert len(out) == 3
        # The middle entry is the blank.
        assert out[1]["dots"] == []
        assert out[1]["role"] == "space"

    def test_blanks_dropped_when_disabled(self):
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,), role="a"),
            BLANK_CELL,
            BrailleCell(dots=(1, 2), role="b"),
        ])
        out = CellsRenderer(include_blanks=False).render(seq)
        assert len(out) == 2
        assert [c["role"] for c in out] == ["a", "b"]


class TestDocument:
    def test_document_round_trip_shape(self):
        doc = BrailleDocument(
            metadata={"profile": "cn_current"},
            blocks=[
                BrailleBlock(
                    block_type="heading",
                    heading_level=1,
                    cells=[BrailleCell(dots=(1,), role="zh_final")],
                ),
                BrailleBlock(
                    block_type="paragraph",
                    cells=[BrailleCell(dots=(1, 2), role="zh_final")],
                ),
            ],
        )
        out = CellsRenderer().render(doc)
        assert out["type"] == "braille_document"
        assert out["metadata"] == {"profile": "cn_current"}
        assert len(out["blocks"]) == 2
        assert out["blocks"][0]["block_type"] == "heading"
        assert out["blocks"][0]["heading_level"] == 1
        assert out["blocks"][0]["cells"][0]["dots"] == [1]
        assert out["blocks"][1]["block_type"] == "paragraph"

    def test_document_with_empty_block(self):
        doc = BrailleDocument(blocks=[BrailleBlock(block_type="paragraph", cells=[])])
        out = CellsRenderer().render(doc)
        assert out["blocks"][0]["cells"] == []


class TestRegistration:
    def test_registered_by_name(self):
        from brailix.renderer import renderer_registry

        assert renderer_registry.has("cells")
        r = renderer_registry.get("cells")
        # Sequence input → list output.
        assert r.render(BrailleSequence(cells=[])) == []
