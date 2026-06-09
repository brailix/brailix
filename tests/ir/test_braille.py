import pytest

from brailix.core.span import Span
from brailix.ir.braille import (
    BLANK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)


class TestBrailleCellConstruction:
    def test_minimal(self):
        c = BrailleCell(dots=(1, 2, 4))
        assert c.dots == (1, 2, 4)
        assert c.role is None
        assert c.is_blank is False

    def test_blank(self):
        c = BrailleCell()
        assert c.is_blank is True
        assert c.dots == ()

    def test_with_role_and_provenance(self):
        c = BrailleCell(
            dots=(1, 4, 5),
            role="zh_initial",
            source_span=Span(0, 1),
            source_text="d",
        )
        assert c.role == "zh_initial"
        assert c.source_text == "d"
        assert c.source_span == Span(0, 1)

    def test_invalid_dot_rejected(self):
        with pytest.raises(ValueError):
            BrailleCell(dots=(1, 9))
        with pytest.raises(ValueError):
            BrailleCell(dots=(0, 1))

    def test_duplicate_dot_rejected(self):
        with pytest.raises(ValueError):
            BrailleCell(dots=(1, 1, 2))

    def test_eight_dot_allowed(self):
        c = BrailleCell(dots=(1, 2, 3, 4, 5, 6, 7, 8))
        assert c.dots == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_frozen_hashable(self):
        c1 = BrailleCell(dots=(1, 2))
        c2 = BrailleCell(dots=(1, 2))
        # Frozen dataclasses with equal fields hash and compare equal.
        assert c1 == c2
        assert hash(c1) == hash(c2)


class TestBrailleCellSerialization:
    def test_minimal_round_trip(self):
        c = BrailleCell(dots=(1, 2))
        restored = BrailleCell.from_dict(c.to_dict())
        assert restored == c

    def test_full_round_trip(self):
        c = BrailleCell(
            dots=(1, 4, 5),
            role="zh_initial",
            source_span=Span(2, 4),
            source_text="ch",
        )
        restored = BrailleCell.from_dict(c.to_dict())
        assert restored == c

    def test_blank_round_trip(self):
        restored = BrailleCell.from_dict(BLANK_CELL.to_dict())
        assert restored == BLANK_CELL


class TestBrailleSequence:
    def test_empty(self):
        s = BrailleSequence()
        assert len(s) == 0

    def test_append_and_iterate(self):
        s = BrailleSequence()
        s.append(BrailleCell(dots=(1,)))
        s.append(BrailleCell(dots=(2,)))
        assert len(s) == 2
        assert [c.dots for c in s] == [(1,), (2,)]

    def test_extend_with_list(self):
        s = BrailleSequence()
        s.extend([BrailleCell(dots=(1,)), BrailleCell(dots=(2,))])
        assert len(s) == 2

    def test_extend_with_other_sequence(self):
        a = BrailleSequence(cells=[BrailleCell(dots=(1,))])
        b = BrailleSequence(cells=[BrailleCell(dots=(2,)), BrailleCell(dots=(3,))])
        a.extend(b)
        assert len(a) == 3

    def test_round_trip(self):
        s = BrailleSequence(cells=[
            BrailleCell(dots=(1,), role="num"),
            BrailleCell(dots=(1, 2), source_text="a"),
        ])
        restored = BrailleSequence.from_dict(s.to_dict())
        assert restored.cells == s.cells


class TestBrailleBlock:
    def test_default(self):
        b = BrailleBlock()
        assert b.block_type == "paragraph"
        assert b.cells == []

    def test_with_heading_level(self):
        b = BrailleBlock(block_type="heading", heading_level=2)
        assert b.heading_level == 2

    def test_round_trip(self):
        b = BrailleBlock(
            block_type="heading",
            id="h1",
            heading_level=1,
            cells=[BrailleCell(dots=(1,))],
        )
        restored = BrailleBlock.from_dict(b.to_dict())
        assert restored.block_type == "heading"
        assert restored.id == "h1"
        assert restored.heading_level == 1
        assert restored.cells == b.cells

    def test_align_round_trip(self):
        b = BrailleBlock(block_type="paragraph", align="center")
        restored = BrailleBlock.from_dict(b.to_dict())
        assert restored.align == "center"

    def test_align_absent_from_dict_when_none(self):
        # The default carries no ``align`` key — keeps serialized blocks lean
        # and back-compatible with readers written before alignment existed.
        assert "align" not in BrailleBlock(block_type="paragraph").to_dict()


class TestBrailleDocument:
    def test_default(self):
        d = BrailleDocument()
        assert d.metadata == {}
        assert d.blocks == []

    def test_all_cells_flattens(self):
        d = BrailleDocument(
            blocks=[
                BrailleBlock(cells=[BrailleCell(dots=(1,)), BrailleCell(dots=(2,))]),
                BrailleBlock(cells=[BrailleCell(dots=(3,))]),
            ]
        )
        flat = d.all_cells()
        assert [c.dots for c in flat] == [(1,), (2,), (3,)]

    def test_round_trip(self):
        d = BrailleDocument(
            metadata={"profile": "cn_current"},
            blocks=[
                BrailleBlock(
                    block_type="paragraph",
                    cells=[BrailleCell(dots=(1, 2), role="zh_initial")],
                ),
            ],
        )
        restored = BrailleDocument.from_dict(d.to_dict())
        assert restored.metadata == d.metadata
        assert len(restored.blocks) == 1
        assert restored.blocks[0].cells == d.blocks[0].cells


class TestBlankCell:
    def test_blank_cell_constant(self):
        assert BLANK_CELL.is_blank
        assert BLANK_CELL.role == "space"
