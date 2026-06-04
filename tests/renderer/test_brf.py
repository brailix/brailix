"""Tests for :mod:`brailix.renderer.brf`.

The BRF renderer's correctness has only two moving parts:

* The 64-entry NABCC lookup table (verified against the published
  spec; ``cell_to_brf`` and ``brf_to_dots`` must round-trip).
* The block joiner (``\\r\\n`` per BRF convention).

8-dot input is downgraded to 6 dots silently — we test that path too
so a future change to "warn on dot 7/8" gets caught."""

import pytest

from brailix.ir.braille import (
    BLANK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.renderer.brf import (
    BrfRenderer,
    brf_to_dots,
    cell_to_brf,
    dots_to_brf,
)

# Canonical reference points the table values everyone agrees on.
# If any of these break, the NABCC table has drifted.
NABCC_SAMPLES = [
    (((), ), " "),
    (((1,), ), "A"),
    (((1, 2), ), "B"),
    (((2,), ), "1"),
    (((2, 3), ), "2"),
    (((3, 4, 5, 6), ), "#"),
    (((1, 2, 3, 4, 5, 6), ), "="),
    (((3,), ), "'"),
    (((4,), ), "@"),
]


class TestNabccTable:
    @pytest.mark.parametrize("dots_tuple, expected", NABCC_SAMPLES)
    def test_known_cell_mappings(self, dots_tuple, expected):
        dots = dots_tuple[0]
        assert cell_to_brf(BrailleCell(dots=dots)) == expected
        assert dots_to_brf(dots) == expected

    def test_round_trip_every_six_dot_combo(self):
        # All 64 possible 6-dot combinations.
        for mask in range(64):
            dots = tuple(i + 1 for i in range(6) if mask & (1 << i))
            ch = dots_to_brf(dots)
            assert brf_to_dots(ch) == dots

    def test_brf_to_dots_rejects_non_brf_char(self):
        with pytest.raises(ValueError):
            brf_to_dots("⠁")  # Unicode braille, not NABCC ASCII

    def test_brf_to_dots_rejects_multi_char(self):
        with pytest.raises(ValueError):
            brf_to_dots("AB")

    def test_eight_dot_cell_strips_dots_7_8(self):
        # Eight-dot cell with dots 1, 7 → mask = just dot 1 → 'A'.
        cell = BrailleCell(dots=(1, 7))
        assert cell_to_brf(cell) == "A"


class TestSequenceRender:
    def test_empty_sequence(self):
        r = BrfRenderer()
        assert r.render(BrailleSequence(cells=[])) == b""

    def test_simple_run(self):
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,)),       # A
            BrailleCell(dots=(1, 2)),     # B
            BrailleCell(dots=(1, 4)),     # C
        ])
        assert BrfRenderer().render(seq) == b"ABC"

    def test_blank_cells_become_spaces(self):
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,)),
            BLANK_CELL,
            BrailleCell(dots=(1, 4)),
        ])
        assert BrfRenderer().render(seq) == b"A C"


class TestDocumentRender:
    def test_blocks_joined_with_crlf(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleBlock(cells=[BrailleCell(dots=(1, 2))]),
        ])
        assert BrfRenderer().render(doc) == b"A\r\nB"

    def test_empty_block_keeps_separator(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleBlock(cells=[]),
            BrailleBlock(cells=[BrailleCell(dots=(1, 2))]),
        ])
        assert BrfRenderer().render(doc) == b"A\r\n\r\nB"

    def test_custom_terminator(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleBlock(cells=[BrailleCell(dots=(1, 2))]),
        ])
        # LF-only — some readers prefer it.
        assert BrfRenderer(line_terminator=b"\n").render(doc) == b"A\nB"


class TestRegistration:
    def test_registered_by_name(self):
        from brailix.renderer import renderer_registry

        assert renderer_registry.has("brf")
        r = renderer_registry.get("brf")
        out = r.render(BrailleSequence(cells=[BrailleCell(dots=(1,))]))
        assert out == b"A"
