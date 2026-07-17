"""Assert-style contracts over the core's pure functions, for CrossHair.

Each ``check_*`` function states a contract in plain ``assert`` form:
leading asserts are the precondition (inputs violating them are discarded),
every later assert — and any unexpected exception — is a claimed invariant.

**Every contract MUST open with an assert** (right after the docstring):
that leading assert is how the ``asserts`` analysis kind *discovers*
contract functions — one that opens with ``try`` or an assignment is
silently skipped, not analyzed. The opening bounds-assert doubles as a
solver budget control. ``test_contracts_smoke.py`` enforces this shape
statically, because the failure mode is invisible otherwise.

Two engines consume this one corpus:

* **CrossHair** (``scripts/run_crosshair.py``, or ``crosshair check
  --analysis_kind asserts tests/crosshair/contracts.py``) executes them
  *symbolically*, asking an SMT solver for a counterexample instead of
  sampling inputs. It needs the ``verify`` dependency group.
* **pytest** (``test_contracts_smoke.py``) executes them *concretely* over
  Hypothesis-generated inputs on every suite run, so the contracts keep
  working — and keep meaning what they say — even in environments without
  the solver stack.

Scope discipline (the reason this file is short): only short, pure,
deterministic functions belong here — the span algebra, BrailleCell dot
canonicalization, the pinyin syllable parser, the page-number line
geometry. Anything touching adapters, ElementTree walks, IO or registries
is out; those are covered by the property and example suites instead.
"""

from __future__ import annotations

import unicodedata

from brailix.backend.zh.pinyin_parser import (
    normalize_syllable_spelling,
    parse_pinyin,
)
from brailix.core.chars import (
    INVISIBLE_CPS,
    fold_fullwidth,
    is_math_symbol,
    nonstandard_char_hint,
)
from brailix.core.inline_math import is_tagged, unwrap, wrap
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.renderer.brf import brf_to_dots, dots_to_brf
from brailix.renderer.layout import _page_number_line
from brailix.renderer.unicode_braille import char_to_dots, dots_to_char

# --- Span algebra -------------------------------------------------------------


def check_span_construction_validity(start: int, end: int) -> None:
    """Accept exactly ``0 <= start <= end``; reject everything else with
    ValueError — and never with anything else."""
    assert -128 <= start <= 128 and -128 <= end <= 128
    try:
        span = Span(start, end)
    except ValueError:
        assert start < 0 or end < start
        return
    assert 0 <= start <= end
    assert span.length == end - start
    assert span.is_empty() == (start == end)


def check_span_merge_is_bounding_box(
    a_start: int, a_len: int, b_start: int, b_len: int
) -> None:
    assert 0 <= a_start and 0 <= a_len
    assert 0 <= b_start and 0 <= b_len
    a = Span(a_start, a_start + a_len)
    b = Span(b_start, b_start + b_len)
    merged = a.merge(b)
    assert merged.start == min(a.start, b.start)
    assert merged.end == max(a.end, b.end)
    assert merged.contains(a) and merged.contains(b)
    assert a.merge(b) == b.merge(a)


def check_span_shift_is_exact_translation(start: int, length: int, offset: int) -> None:
    assert 0 <= start and 0 <= length
    span = Span(start, start + length)
    try:
        shifted = span.shift(offset)
    except ValueError:
        assert start + offset < 0
        return
    assert shifted.start == start + offset
    assert shifted.length == span.length
    assert shifted.shift(-offset) == span


def check_span_relations_match_interval_model(
    a_start: int, a_len: int, b_start: int, b_len: int
) -> None:
    assert 0 <= a_start and 0 <= a_len
    assert 0 <= b_start and 0 <= b_len
    a = Span(a_start, a_start + a_len)
    b = Span(b_start, b_start + b_len)
    assert a.contains(b) == (a.start <= b.start and b.end <= a.end)
    assert a.overlaps(b) == b.overlaps(a)
    if not a.is_empty() and not b.is_empty():
        assert a.overlaps(b) == (max(a.start, b.start) < min(a.end, b.end))
    if a.contains(b) and not b.is_empty():
        assert a.overlaps(b)


# --- BrailleCell dot canonicalization ------------------------------------------


def check_cell_dot_pair_is_canonical_and_order_free(d1: int, d2: int) -> None:
    """A cell is its dot SET: any ordering of the same valid dots builds an
    equal, equally-hashed cell with ascending ``dots``; anything invalid
    (out of 1..8, duplicated) raises ValueError and nothing else."""
    assert -16 <= d1 <= 16 and -16 <= d2 <= 16
    try:
        cell = BrailleCell(dots=(d1, d2))
    except ValueError:
        assert not (1 <= d1 <= 8) or not (1 <= d2 <= 8) or d1 == d2
        return
    assert 1 <= d1 <= 8 and 1 <= d2 <= 8 and d1 != d2
    assert cell.dots == (min(d1, d2), max(d1, d2))
    flipped = BrailleCell(dots=(d2, d1))
    assert cell == flipped
    assert hash(cell) == hash(flipped)


def check_cell_dot_triple_is_canonical_and_order_free(d1: int, d2: int, d3: int) -> None:
    assert -16 <= d1 <= 16 and -16 <= d2 <= 16 and -16 <= d3 <= 16
    try:
        cell = BrailleCell(dots=(d1, d2, d3))
    except ValueError:
        assert (
            not (1 <= d1 <= 8)
            or not (1 <= d2 <= 8)
            or not (1 <= d3 <= 8)
            or d1 == d2
            or d1 == d3
            or d2 == d3
        )
        return
    assert cell.dots == tuple(sorted((d1, d2, d3)))
    assert list(cell.dots) == sorted(set((d1, d2, d3)))
    rotated = BrailleCell(dots=(d3, d1, d2))
    assert cell == rotated
    assert hash(cell) == hash(rotated)


# --- Layout page-number line geometry ------------------------------------------


def check_page_number_line_geometry(pn: str, width: int, align_right: bool) -> None:
    """The page number is never dropped: too-narrow lines overflow with the
    bare number; right alignment pads with blanks to end exactly at the
    right edge; left alignment is the bare number at column 0 (a braille
    line carries no meaning past its last cell, so no trailing blanks)."""
    assert 1 <= len(pn) <= 4
    assert 0 <= width <= 40
    line = _page_number_line(pn, width, align_right=align_right, blank=" ")
    if len(pn) >= width or not align_right:
        assert line == pn
    else:
        assert len(line) == width
        assert line.endswith(pn)
        assert line[: width - len(pn)] == " " * (width - len(pn))


# --- Non-standard character classification ---------------------------------------


def check_fold_fullwidth_mapping(cp: int) -> None:
    """Folding is exactly the documented Unicode relation: a full-width
    ASCII variant (U+FF01..U+FF5E) sits 0xFEE0 above its half-width twin,
    the ideographic space folds to a plain space, everything else has no
    mapping. Folding is terminal (a folded result never folds again), and
    only single characters classify."""
    assert 0 <= cp <= 0xFFFF
    ch = chr(cp)
    half = fold_fullwidth(ch)
    if 0xFF01 <= cp <= 0xFF5E:
        assert half is not None and len(half) == 1
        assert ord(half) == cp - 0xFEE0
        assert half.isascii() and half.isprintable()
        assert fold_fullwidth(half) is None
    elif cp == 0x3000:
        assert half == " "
    else:
        assert half is None
    assert fold_fullwidth(ch + ch) is None
    assert fold_fullwidth("") is None


def check_nonstandard_hint_consistency(cp: int) -> None:
    """A hint fires for exactly the two documented classes — foldable
    full-width forms and invisible zero-width debris — always naming the
    offending code point; ordinary characters and multi-char strings get
    None. Keeps the hint and the classification sets from drifting apart."""
    assert 0 <= cp <= 0xFFFF
    ch = chr(cp)
    hint = nonstandard_char_hint(ch)
    should_fire = fold_fullwidth(ch) is not None or cp in INVISIBLE_CPS
    assert (hint is not None) == should_fire
    if hint is not None:
        assert f"U+{cp:04X}" in hint
    assert nonstandard_char_hint(ch + ch) is None
    assert nonstandard_char_hint("") is None


def check_is_math_symbol_is_exactly_category_sm(cp: int) -> None:
    """Pins the "Unicode category Sm, exactly" line: no hand-kept
    inclusion or exclusion list may creep in. The middle dot · (Po) and
    the degree sign ° (So) stay ordinary by their category — not by
    special-casing — and only single characters qualify."""
    assert 0 <= cp <= 0xFFFF
    ch = chr(cp)
    assert is_math_symbol(ch) == (unicodedata.category(ch) == "Sm")
    assert is_math_symbol(ch + ch) is False


# --- Braille character codecs ---------------------------------------------------


def check_unicode_braille_codec_bijection(mask: int) -> None:
    """The Unicode standard's bit formula, both directions: dot N is bit
    N-1 of the offset from U+2800, and decoding inverts encoding exactly
    over the whole 8-dot space."""
    assert 0 <= mask <= 255
    dots = tuple(i + 1 for i in range(8) if mask & (1 << i))
    ch = dots_to_char(dots)
    assert ord(ch) == 0x2800 + mask
    assert char_to_dots(ch) == dots


def check_char_to_dots_accepts_exactly_the_braille_block(cp: int) -> None:
    assert 0 <= cp <= 0x3000
    ch = chr(cp)
    try:
        dots = char_to_dots(ch)
    except ValueError:
        assert not (0x2800 <= cp <= 0x28FF)
        return
    assert 0x2800 <= cp <= 0x28FF
    assert dots_to_char(dots) == ch


def check_brf_six_dot_codec_round_trips(mask: int) -> None:
    """NABCC is a bijection over the 64 six-dot cells (one printable ASCII
    char each, decode inverts encode), and dots 7 / 8 never change the
    output — the documented best-effort strip for 8-dot input."""
    assert 0 <= mask <= 63
    dots = tuple(i + 1 for i in range(6) if mask & (1 << i))
    ch = dots_to_brf(dots)
    assert len(ch) == 1 and ord(ch) < 128
    assert brf_to_dots(ch) == dots
    assert dots_to_brf(dots + (7,)) == ch
    assert dots_to_brf(dots + (7, 8)) == ch


# --- Inline-math island codec ---------------------------------------------------


def check_inline_math_island_codec(source: str, payload: str) -> None:
    """wrap → unwrap recovers the dialect tag and the (whitespace-
    flattened) payload with every literal ``$`` restored; the island
    itself carries no inner ``$`` and no newline, which is what lets it
    survive the segmenter's protected-region scan; and re-wrapping the
    unwrapped fields reproduces the island byte for byte (fixed point —
    islands don't drift through repeated IR passes). Preconditions mirror
    the documented domain: dialect tags are short alnum names, and the
    delimiter / escape control characters are illegal in XML 1.0, so they
    never occur in a real payload."""
    # Real dialect tags are short identifier-ish names — "omml",
    # "eq_field", "mathml" — so underscores are in the domain.
    assert 1 <= len(source) <= 8
    assert all(c.isalnum() or c == "_" for c in source)
    assert len(payload) <= 8
    assert "\x1d" not in payload and "\x1e" not in payload
    island = wrap(source, payload)
    assert is_tagged(island)
    assert "\n" not in island and "\r" not in island
    assert "$" not in island[1:-1]
    src2, pay2 = unwrap(island)
    assert src2 == source
    assert pay2.count("$") == payload.count("$")
    assert wrap(src2, pay2) == island


# --- Pinyin syllable parser -----------------------------------------------------


def check_parse_pinyin_totality(syllable: str) -> None:
    """For ANY short string: parse_pinyin either returns a well-formed
    ParsedPinyin or raises ValueError — no IndexError / KeyError / slicing
    accident may escape, whatever the input. On success: the tone is one of
    the documented values, something of the syllable survives the split,
    and ``syllabic`` (the deliberate empty-rime marker) implies a bare
    initial with an intentionally empty final."""
    assert len(syllable) <= 8
    try:
        parsed = parse_pinyin(syllable)
    except ValueError:
        return
    assert parsed.tone in ("", "1", "2", "3", "4", "5")
    assert parsed.initial != "" or parsed.final != ""
    if parsed.syllabic:
        assert parsed.initial != ""
        assert parsed.final == ""


def check_normalize_syllable_spelling_idempotent(syllable: str) -> None:
    """Normalizing twice equals normalizing once — the NCB tone-omission
    tables key on this spelling, so a drifting normal form would split one
    syllable across two lookup keys."""
    assert len(syllable) <= 8
    once = normalize_syllable_spelling(syllable)
    assert normalize_syllable_spelling(once) == once
