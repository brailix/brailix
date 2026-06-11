"""Page-number braille cells, read from ``resources/numbers.json``.

Page-number rendering (BANA-aligned 6-dot convention shared by Current
Chinese Braille / National Common Braille / English literary braille):

* number_sign ``⠼`` (dots 3-4-5-6) signals "digits follow"
* digits 1..9, 0 map to letters a..i, j (dots 1, 12, 14, ...)

The cells come from
:func:`brailix.core.config.load_builtin_numbers_table` — the same
``resources/numbers.json`` the number and math backends consume through
the profile loader — so the digit shapes have exactly one authority.
That resource is *universal* (it lives at the resources root beside
``cells.json``, shared across braille systems), not a per-language
profile table: reading it keeps :mod:`brailix.renderer.layout`
language-blind — the paginator still has no notion of Chinese / English
/ math and takes no profile.

A profile that swaps in different digit shapes (very rare — it would
need a different number_sign too) still paginates with the builtin
cells; the surgical answer for such a format is to plug a different
``_page_digits``-shaped module into the paginator, not to chase profile
reads through ``layout.py``.
"""

from __future__ import annotations

from functools import lru_cache

from brailix.core.config import load_builtin_numbers_table
from brailix.renderer.brf import dots_to_brf
from brailix.renderer.unicode_braille import dots_to_char


@lru_cache(maxsize=1)
def _page_cells() -> tuple[tuple[int, ...], dict[str, tuple[int, ...]]]:
    """``(number_sign_dots, digit_dots_by_char)`` from the builtin
    numbers resource, loaded once."""
    table = load_builtin_numbers_table()
    sign: tuple[int, ...] = tuple(table["number_sign"])
    digits: dict[str, tuple[int, ...]] = {
        ch: tuple(dots) for ch, dots in table["digits"].items()
    }
    return sign, digits


def page_number_chars(page_num: int) -> str:
    """Unicode-braille string for ``⠼`` + each digit cell."""
    sign, digits = _page_cells()
    out = [dots_to_char(sign)]
    for ch in str(page_num):
        out.append(dots_to_char(digits[ch]))
    return "".join(out)


def page_number_brf(page_num: int) -> bytes:
    """NABCC-ASCII bytes for ``⠼`` + each digit cell."""
    sign, digits = _page_cells()
    parts = [dots_to_brf(sign).encode("ascii")]
    for ch in str(page_num):
        parts.append(dots_to_brf(digits[ch]).encode("ascii"))
    return b"".join(parts)


def page_number_width(page_num: int) -> int:
    """Cells consumed by ``⠼`` + each digit."""
    return len(page_number_chars(page_num))
