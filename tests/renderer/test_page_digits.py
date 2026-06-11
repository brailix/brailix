"""Tests for :mod:`brailix.renderer._page_digits`.

Two angles:

* golden values — the standard ``⠼`` + a–j shapes asserted literally,
  so an accidental edit to ``resources/numbers.json`` shows up here
  instead of silently re-shaping every exported page number;
* single-authority wiring — the page cells must equal what the profile
  loader parses out of the builtin numbers resource, so the renderer
  can't drift back to a private copy of the digit table.
"""

from brailix.core.config import load_builtin_numbers_table
from brailix.renderer._page_digits import (
    page_number_brf,
    page_number_chars,
    page_number_width,
)
from brailix.renderer.unicode_braille import dots_to_char


class TestGoldenValues:
    def test_unicode_all_digits(self):
        # 1234567890 exercises every digit cell once, in a-j order.
        assert page_number_chars(1234567890) == "⠼⠁⠃⠉⠙⠑⠋⠛⠓⠊⠚"

    def test_unicode_single_page(self):
        assert page_number_chars(1) == "⠼⠁"

    def test_brf_all_digits(self):
        # NABCC: number sign is ``#``, digits 1..0 are letters A..J
        # (this repo's NABCC table emits the uppercase form).
        assert page_number_brf(1234567890) == b"#ABCDEFGHIJ"

    def test_brf_single_page(self):
        assert page_number_brf(1) == b"#A"

    def test_width(self):
        assert page_number_width(7) == 2
        assert page_number_width(42) == 3
        assert page_number_width(100) == 4


class TestSingleAuthority:
    def test_cells_match_builtin_numbers_resource(self):
        """Page-number cells are the loader's parse of
        ``resources/numbers.json`` — not a renderer-private table."""
        table = load_builtin_numbers_table()
        expected = dots_to_char(tuple(table["number_sign"]))
        for ch in "1234567890":
            expected += dots_to_char(tuple(table["digits"][ch]))
        assert page_number_chars(1234567890) == expected
