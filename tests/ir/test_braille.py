import pytest

from brailix.core.span import Span
from brailix.ir.braille import (
    BLANK_CELL,
    HANG_CLOSE_CELL,
    HANG_OPEN_CELL,
    LINE_BREAK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
    blank_cell,
    hang_close_cell,
    hang_open_cell,
    line_break_cell,
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

    def test_eight_dot_allowed(self):
        c = BrailleCell(dots=(1, 2, 3, 4, 5, 6, 7, 8))
        assert c.dots == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_frozen_hashable(self):
        c1 = BrailleCell(dots=(1, 2))
        c2 = BrailleCell(dots=(1, 2))
        # Frozen dataclasses with equal fields hash and compare equal.
        assert c1 == c2
        assert hash(c1) == hash(c2)


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


class TestBrailleBlock:
    def test_default(self):
        b = BrailleBlock()
        assert b.block_type == "paragraph"
        assert b.cells == []

    def test_with_heading_level(self):
        b = BrailleBlock(block_type="heading", heading_level=2)
        assert b.heading_level == 2

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


class TestBlankCell:
    def test_blank_cell_constant(self):
        assert BLANK_CELL.is_blank
        assert BLANK_CELL.role == "space"


class TestSpanCarryingFactories:
    """The traceable counterparts of the span-less sentinel constants.

    Every control / spacing cell the backend emits goes through these
    factories, so "every cell maps to a source span" (ARCHITECTURE §3)
    stands on their exact output: zero dots, the exact role the renderers
    key on, the caller's span carried through UNTOUCHED (including a
    zero-width boundary span and the explicit None a hand-built caller may
    pass), and an empty source_text (the cell stands for no source
    character). Pinned directly — the compiled-document suites exercise
    them only incidentally, and a wrong role here silently breaks wrap /
    render logic everywhere. (Flagged by mutation testing: all four
    factories' mutants survived the pre-existing suites.)
    """

    @pytest.mark.parametrize(
        "factory, role",
        [
            (blank_cell, "space"),
            (line_break_cell, "line_break"),
            (hang_open_cell, "hang_open"),
            (hang_close_cell, "hang_close"),
        ],
    )
    @pytest.mark.parametrize(
        "span", [None, Span(3, 3), Span(0, 2)], ids=["none", "boundary", "range"]
    )
    def test_factory_output_contract(self, factory, role, span):
        cell = factory(span)
        assert cell.dots == ()
        assert cell.is_blank
        assert cell.role == role
        assert cell.source_span is span
        assert cell.source_text == ""


class TestSentinelCells:
    """The non-blank zero-width sentinels (forced line break, hang-region
    brackets) are ``is_blank`` True but distinguished by ``role``; wrap /
    render logic keys on ``role``, so it must survive a round-trip."""

    @pytest.mark.parametrize(
        "cell, role",
        [
            (LINE_BREAK_CELL, "line_break"),
            (HANG_OPEN_CELL, "hang_open"),
            (HANG_CLOSE_CELL, "hang_close"),
        ],
    )
    def test_role_and_round_trip(self, cell, role):
        assert cell.is_blank  # zero-width...
        assert cell.role == role  # ...but distinguished by role
        restored = BrailleCell.from_dict(cell.to_dict())
        assert restored == cell
        assert restored.role == role


class TestMalformedSourceSpan:
    def test_from_dict_rejects_malformed_source_span(self):
        # source_span goes through the same canonical Span.from_tuple boundary;
        # a malformed length must raise, not silently truncate to the first two.
        with pytest.raises(ValueError):
            BrailleCell.from_dict({"dots": [1], "source_span": [0, 1, 2]})

    def test_from_dict_absent_source_span_is_none(self):
        c = BrailleCell.from_dict({"dots": [1]})
        assert c.source_span is None


class TestValidateTraceability:
    """:meth:`BrailleDocument.validate_traceability` reports the position of
    every cell missing a ``source_span`` — the reusable, first-class form of
    the "every cell maps to a source span" contract (ARCHITECTURE §3)."""

    def test_empty_document_is_traceable(self):
        assert BrailleDocument().validate_traceability() == []

    def test_all_cells_with_span_is_traceable(self):
        d = BrailleDocument(
            blocks=[
                BrailleBlock(
                    cells=[
                        BrailleCell(dots=(1,), source_span=Span(0, 1)),
                        BrailleCell(dots=(1, 2), source_span=Span(1, 2)),
                    ]
                ),
                BrailleBlock(cells=[BrailleCell(dots=(3,), source_span=Span(2, 3))]),
            ]
        )
        assert d.validate_traceability() == []

    def test_reports_position_of_spanless_cell(self):
        d = BrailleDocument(
            blocks=[
                BrailleBlock(cells=[BrailleCell(dots=(1,), source_span=Span(0, 1))]),
                BrailleBlock(
                    cells=[
                        BrailleCell(dots=(2,), source_span=Span(0, 1)),
                        BrailleCell(dots=(3,)),  # no span
                    ]
                ),
            ]
        )
        assert d.validate_traceability() == [(1, 1)]

    def test_reports_every_spanless_position_in_order(self):
        d = BrailleDocument(
            blocks=[
                BrailleBlock(
                    cells=[
                        BrailleCell(dots=(1,)),  # no span
                        BrailleCell(dots=(2,), source_span=Span(0, 1)),
                    ]
                ),
                BrailleBlock(cells=[BrailleCell(dots=(3,))]),  # no span
            ]
        )
        assert d.validate_traceability() == [(0, 0), (1, 0)]

    def test_span_less_blank_sentinel_is_flagged(self):
        # The shared span-less BLANK_CELL sentinel is exactly what the backend
        # must NOT emit directly (it uses blank_cell(span)); if one leaks into a
        # compiled document the check catches it, no role white-list needed.
        d = BrailleDocument(blocks=[BrailleBlock(cells=[BLANK_CELL])])
        assert d.validate_traceability() == [(0, 0)]

    def test_zero_width_synthetic_span_counts_as_traceable(self):
        # A control cell between two source positions carries a collapsed
        # zero-width span, not None — that still satisfies the contract.
        from brailix.ir.braille import blank_cell

        d = BrailleDocument(blocks=[BrailleBlock(cells=[blank_cell(Span(3, 3))])])
        assert d.validate_traceability() == []
