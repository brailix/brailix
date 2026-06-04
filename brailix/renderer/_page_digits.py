"""Hard-coded 6-dot braille digit cells, used only by page numbering.

Page-number rendering (BANA-aligned 6-dot convention shared by Current
Chinese Braille / National Common Braille / English literary braille):

* number_sign ``⠼`` (dots 3-4-5-6) signals "digits follow"
* digits 1..9, 0 map to letters a..i, j (dots 1, 12, 14, ...)

We hard-code the mapping here rather than reaching into a profile
JSON because :mod:`brailix.renderer.layout` sits *below* the profile
layer (it has no notion of Chinese / English / math), and the digit
mapping is one of the most stable parts of the 6-dot braille standard
— unchanged since Louis Braille's 1829 publication.  The values match
``brailix/resources/numbers.json`` and the equivalent
National Common Braille table; profile authors who need a different
digit shape (very rare — would require a different number_sign too)
should build
their own paginator rather than inject overrides here.

Extracted from ``layout.py`` so the "renderer needs digits but won't
read profile" rationale lives in one file, not woven through 600 lines
of wrap / pagination logic.  If a future UEB / Nemeth output format
ever needs different digit cells for pagination, the surgical answer
is to plug a different ``_page_digits``-shaped module into the
paginator — not to chase profile reads through ``layout.py``.
"""

from __future__ import annotations

from brailix.renderer.brf import dots_to_brf
from brailix.renderer.unicode_braille import dots_to_char

_NUMBER_SIGN_DOTS: tuple[int, ...] = (3, 4, 5, 6)
_DIGIT_DOTS: tuple[tuple[int, ...], ...] = (
    (2, 4, 5),    # 0 — j
    (1,),         # 1 — a
    (1, 2),       # 2 — b
    (1, 4),       # 3 — c
    (1, 4, 5),    # 4 — d
    (1, 5),       # 5 — e
    (1, 2, 4),    # 6 — f
    (1, 2, 4, 5), # 7 — g
    (1, 2, 5),    # 8 — h
    (2, 4),       # 9 — i
)


def page_number_chars(page_num: int) -> str:
    """Unicode-braille string for ``⠼`` + each digit cell."""
    out = [dots_to_char(_NUMBER_SIGN_DOTS)]
    for ch in str(page_num):
        out.append(dots_to_char(_DIGIT_DOTS[int(ch)]))
    return "".join(out)


def page_number_brf(page_num: int) -> bytes:
    """NABCC-ASCII bytes for ``⠼`` + each digit cell."""
    parts = [dots_to_brf(_NUMBER_SIGN_DOTS).encode("ascii")]
    for ch in str(page_num):
        parts.append(dots_to_brf(_DIGIT_DOTS[int(ch)]).encode("ascii"))
    return b"".join(parts)


def page_number_width(page_num: int) -> int:
    """Cells consumed by ``⠼`` + each digit."""
    return 1 + len(str(page_num))
