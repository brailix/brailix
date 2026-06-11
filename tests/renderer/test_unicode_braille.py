import pytest

from brailix.ir.braille import (
    LINE_BREAK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.renderer import renderer_registry
from brailix.renderer.unicode_braille import (
    BRAILLE_BASE,
    UnicodeBrailleRenderer,
    cell_to_char,
    char_to_dots,
    dots_to_char,
)


class TestEncoding:
    def test_blank_cell_is_u2800(self):
        assert cell_to_char(BrailleCell()) == "⠀"

    def test_dot_1(self):
        # dot 1 → bitmask 0x01 → U+2801
        assert cell_to_char(BrailleCell(dots=(1,))) == chr(0x2801)

    def test_dot_8(self):
        # dot 8 → bitmask 0x80 → U+2880
        assert cell_to_char(BrailleCell(dots=(8,))) == chr(0x2880)

    @pytest.mark.parametrize("dots,expected_cp_offset", [
        ((1,),       0x01),
        ((2,),       0x02),
        ((3,),       0x04),
        ((4,),       0x08),
        ((5,),       0x10),
        ((6,),       0x20),
        ((7,),       0x40),
        ((8,),       0x80),
        ((1, 2),     0x03),
        ((1, 2, 3),  0x07),
        ((1, 4),     0x09),  # 'c' / digit 3
        ((1, 2, 3, 4, 5, 6, 7, 8), 0xFF),
    ])
    def test_known_mappings(self, dots, expected_cp_offset):
        assert ord(cell_to_char(BrailleCell(dots=dots))) == BRAILLE_BASE + expected_cp_offset

    def test_dots_order_independent(self):
        a = cell_to_char(BrailleCell(dots=(1, 2, 4)))
        b = cell_to_char(BrailleCell(dots=(4, 2, 1)))
        assert a == b

    def test_dots_to_char_helper(self):
        assert dots_to_char((1, 2)) == chr(0x2803)
        assert dots_to_char([]) == chr(0x2800)


class TestRoundTrip:
    @pytest.mark.parametrize("dots", [
        (),
        (1,), (4,), (8,),
        (1, 2), (3, 4, 5),
        (1, 2, 3, 4, 5, 6, 7, 8),
    ])
    def test_dot_round_trip(self, dots):
        ch = dots_to_char(dots)
        assert char_to_dots(ch) == dots

    def test_char_to_dots_rejects_non_braille(self):
        with pytest.raises(ValueError):
            char_to_dots("A")

    def test_char_to_dots_rejects_multichar(self):
        with pytest.raises(ValueError):
            char_to_dots("ab")


class TestAllSixDotCells:
    """The six-dot braille block has 64 cells (U+2800..U+283F).
    Round-tripping every one keeps the encoder honest."""

    @pytest.mark.parametrize("mask", list(range(0x40)))
    def test_round_trip_every_six_dot_cell(self, mask):
        dots = tuple(i + 1 for i in range(6) if mask & (1 << i))
        ch = dots_to_char(dots)
        assert ord(ch) == BRAILLE_BASE + mask
        assert char_to_dots(ch) == dots


class TestRendererSequence:
    def test_empty(self):
        r = UnicodeBrailleRenderer()
        assert r.render(BrailleSequence()) == ""

    def test_simple_sequence(self):
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,)),
            BrailleCell(dots=(1, 2)),
            BrailleCell(),
        ])
        out = UnicodeBrailleRenderer().render(seq)
        assert out == chr(0x2801) + chr(0x2803) + chr(0x2800)

    def test_line_break_sentinel_renders_as_newline(self):
        # Forced in-block break (matrix / equation-system rows) — a
        # real \n, NOT the U+2800 a blank cell would produce.
        seq = BrailleSequence(cells=[
            BrailleCell(dots=(1,)),
            LINE_BREAK_CELL,
            BrailleCell(dots=(2,)),
        ])
        out = UnicodeBrailleRenderer().render(seq)
        assert out == chr(0x2801) + "\n" + chr(0x2802)

    def test_hang_region_sentinels_print_nothing(self):
        # Zero-width layout metadata — must NOT become U+2800.
        from brailix.ir.braille import HANG_CLOSE_CELL, HANG_OPEN_CELL

        seq = BrailleSequence(cells=[
            HANG_OPEN_CELL,
            BrailleCell(dots=(1,)),
            HANG_CLOSE_CELL,
        ])
        assert UnicodeBrailleRenderer().render(seq) == chr(0x2801)


class TestRendererDocument:
    def test_empty_document(self):
        assert UnicodeBrailleRenderer().render(BrailleDocument()) == ""

    def test_single_block(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,)), BrailleCell(dots=(2,))]),
        ])
        assert UnicodeBrailleRenderer().render(doc) == chr(0x2801) + chr(0x2802)

    def test_multiple_blocks_joined_with_newline(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleBlock(cells=[BrailleCell(dots=(2,))]),
        ])
        out = UnicodeBrailleRenderer().render(doc)
        assert out == chr(0x2801) + "\n" + chr(0x2802)


class TestRegistry:
    def test_unicode_registered(self):
        assert renderer_registry.has("unicode")
        inst = renderer_registry.get("unicode")
        assert inst.name == "unicode"

    def test_registry_lookup_returns_working_renderer(self):
        r = renderer_registry.get("unicode")
        assert r.render(BrailleSequence(cells=[BrailleCell(dots=(1,))])) == chr(0x2801)
