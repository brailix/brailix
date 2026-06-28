"""Text segmenter — module-level entry: :func:`segment`.

The :func:`segment` function below is the only public surface; the
underlying registry of pluggable segmenter implementations is an
implementation detail. ``ctx.options["segmenter"]`` selects which
adapter to use (default ``"default"``).

------------------------------------------------------------------

Default text segmenter: character-class chunking with protected regions.

This is the *default* (built-in, dependency-free) implementation of
the :class:`~brailix.core.protocols.Segmenter` protocol. It is good
enough for the basic pipeline and serves as a reference for what
production-grade segmenters (HanLP-based, rule-driven, ML-based) need
to emit.

Strategy:

1. Find "protected" regions — ``$...$`` inline math — that may span
   character categories and should not be split.
2. For the remaining text, group consecutive characters by category
   into Segments: hanzi_text / digit_run / latin_text / punct / space.

Segment types emitted (consumed downstream by Normalizer and
ChineseAnalyzer):

* ``hanzi_text``  — CJK Unified Ideographs run.
* ``digit_run``   — ASCII or fullwidth digit run (Normalizer turns
  this into ``number`` / ``date`` / ``quantity`` / ``percent`` etc.).
* ``latin_text``  — ASCII letters.
* ``greek_text``  — Greek alphabet letters (Α-Ω / α-ω + variants
  ϕ ϵ ϑ ϱ ς). Split from latin_text so each script gets its own
  letter-prefix (Greek upper/lower-case sign ⠸/⠨ vs Latin
  upper/lower-case sign ⠠/⠰) at the head of its run; downstream the
  Normalizer routes them
  through the same ``LatinWord`` / ``LatinAcronym`` path because
  ``profile.letter()`` already picks the right prefix per character.
* ``punct``       — any single punctuation char.
* ``space``       — whitespace run.
* ``math_inline`` — protected ``$...$`` region.
* ``math_op``     — a bare half-width math operator / delimiter
  (one of ``()[]{}+-*/=<>|``); the Normalizer wraps it into a
  degenerate ``MathInline``.
* ``phonetic_inline`` — protected ``/.../`` or ``[...]`` IPA transcription.
* ``unknown``     — anything we don't classify.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from brailix.core.context import FrontendContext
from brailix.core.protocols import Segmenter
from brailix.core.registry import Registry
from brailix.core.span import Span
from brailix.ir.document import Block
from brailix.ir.inline import Segment

# ---------------------------------------------------------------------------
# Protected-region patterns
# ---------------------------------------------------------------------------

# Inline math wrapped in single $...$. Detected by a paired scan
# (:func:`_iter_inline_math_spans`) rather than a regex: the old
# lookaround pattern ``(?<!\$)\$(?!\$)([^$\n]+)\$(?!\$)`` rejected two
# *adjacent* islands like ``$a$$b$`` (each side of the ``$$`` junction
# tripped the other's guard). We still do not treat ``$$...$$`` (display
# math) as inline here; the input layer marks display math as a
# math_block.

# Half-width characters that are universally math operators / delimiters
# in modern technical writing — semantically "half-width = math" per the
# project's profile design (vs. full-width forms, which route through
# Chinese prose punctuation). Recognised here so the Normalizer can wrap
# each one as a degenerate :class:`MathInline` instead of letting it
# fall through to :class:`Punct`, which the prose punctuation table
# (cn_current Current Chinese Braille) doesn't map. ``,`` / ``.`` / ``%``
# are excluded
# because they double as prose punctuation and the punctuation table
# already covers them; ``-`` (U+002D) is included because the prose
# table only has the em dash ``—`` (U+2014), so a bare hyphen-minus
# in prose would otherwise UNKNOWN_PUNCT and surface as a blank cell
# (e.g. ``x=-5`` rendered ``=`` followed by a stray space).
_BARE_MATH_OPERATORS: frozenset[str] = frozenset("()[]{}+-*/=<>|")


# Phonetic-transcription regions. A region opens with a delimiter and
# closes with its partner on the same line: ``/.../`` (slashes, the
# modern phonemic convention) and ``[...]`` (brackets) are both accepted.
# Recognised as a protected region — like ``$...$`` math — but only when
# the content *looks like* IPA (see :func:`_qualifies_as_phonetic`), so a
# plain slashed / bracketed run in prose (``input/output``, ``[注1]``)
# stays untouched.
_PHONETIC_DELIMITERS: dict[str, str] = {"/": "/", "[": "]"}

# Characters that distinctly mark an IPA transcription: the non-ASCII
# phonemes of the English phonetic inventory plus the length mark ``ː``
# and the two stress marks ``ˈ`` / ``ˌ``. Their presence is what tells a
# phonetic ``/.../`` from a file path: a region qualifies as phonetic
# only if at least one of these appears in it. This is a frontend
# character-class fact ("what an IPA region looks like"), kept separate
# from the backend's braille mapping; ``tests`` assert every non-ASCII
# symbol in the phonetic table is covered here so the two can't drift.
_IPA_DISTINCT_CHARS: frozenset[str] = frozenset("ɪʌɜəɑɒɔʊŋθðʃʒɡːæˈˌ")


# ---------------------------------------------------------------------------
# Character categorization
# ---------------------------------------------------------------------------


def _is_hanzi(ch: str) -> bool:
    return (
        "一" <= ch <= "鿿"
        or "㐀" <= ch <= "䶿"  # Extension A
        or "豈" <= ch <= "﫿"  # Compatibility Ideographs
        # U+3007 ideographic number zero (líng): in the CJK Symbols
        # block, outside every ideograph range above, but reads as a
        # numeral in year notation like 二〇二六年. Without this it fell
        # to punct → UNKNOWN_PUNCT + a blank cell, losing the líng
        # syllable and splitting the surrounding hanzi run. The
        # iteration mark 々 (U+3005) and 〆 (U+3006) are left out — they
        # carry no standalone reading and need separate handling.
        or ch == "〇"
        # Supplementary planes: rare given names / dictionary
        # characters live here. Missing them dropped such chars to
        # ``unknown`` and a blank cell instead of routing through the
        # Chinese frontend.
        or "𠀀" <= ch <= "𯨟"  # SIP: Ext B-F + Compat
        or "𰀀" <= ch <= "𲎯"  # TIP: Ext G + H
    )


def _is_digit(ch: str) -> bool:
    # ASCII 0-9 or fullwidth ０-９ only. Deliberately NOT ``str.isdigit()``,
    # which also returns True for superscripts (``²``), circled digits
    # (``①``) and other scripts' decimals — none of which may fold into a
    # number run (the docstring contract is "ASCII or fullwidth digit").
    return ("0" <= ch <= "9") or ("０" <= ch <= "９")


def _is_latin(ch: str) -> bool:
    cp = ord(ch)
    return cp < 0x80 and ch.isalpha()


def _is_greek(ch: str) -> bool:
    # Greek and Coptic block (U+0370-U+03FF) covers Α-Ω / α-ω plus the
    # stylistic variants latex2mathml uses (ϕ ϵ ϑ ϱ ς). isalpha gates
    # out punctuation / diacritics that share the block.
    return 0x0370 <= ord(ch) <= 0x03FF and ch.isalpha()


def _category(ch: str) -> str:
    if _is_hanzi(ch):
        return "hanzi_text"
    if _is_digit(ch):
        return "digit_run"
    if _is_latin(ch):
        return "latin_text"
    if _is_greek(ch):
        return "greek_text"
    if ch.isspace():
        return "space"
    if not ch.isprintable():
        return "unknown"
    if ch in _BARE_MATH_OPERATORS:
        # Half-width math operator/delimiter in prose: half-width = math.
        return "math_op"
    # Treat everything else (CJK punct, ASCII punct, symbols) as punct.
    # Normalizer/Backend will split on specific characters as needed.
    return "punct"


# ---------------------------------------------------------------------------
# Segmenter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DefaultSegmenter:
    """Built-in segmenter with no third-party dependencies."""

    name: str = "default"

    def segment(self, block: Block, ctx: FrontendContext | None = None) -> list[Segment]:
        text = block.text
        if not text:
            return []
        base = block.span.start if block.span is not None else 0
        return _segment_text(text, base_offset=base)


def _segment_text(
    text: str,
    base_offset: int = 0,
    categorize: Callable[[str], str] = _category,
) -> list[Segment]:
    """Public-ish helper that segments a raw string.

    ``categorize`` maps a character to its segment category; a language
    segmenter can pass its own (e.g. the Japanese one adds ``kana_text``)
    to reuse this chunking. Defaults to the built-in Han-aware
    :func:`_category`.
    """
    if not text:
        return []

    protected = _find_protected_regions(text)
    out: list[Segment] = []
    cursor = 0
    for start, end, type_name in protected:
        if start > cursor:
            out.extend(
                _segment_unprotected(text, cursor, start, base_offset, categorize)
            )
        out.append(
            Segment(
                type=type_name,
                surface=text[start:end],
                span=Span(base_offset + start, base_offset + end),
            )
        )
        cursor = end
    if cursor < len(text):
        out.extend(
            _segment_unprotected(text, cursor, len(text), base_offset, categorize)
        )
    return out


def _iter_inline_math_spans(text: str) -> Iterator[tuple[int, int, str]]:
    r"""Yield ``(start, end, "math_inline")`` for each ``$...$`` island.

    A single ``$`` opens an island; the next ``$`` on the same line closes
    it, and the content between must be non-empty and newline-free. Two
    *adjacent* islands (``$a$$b$``) therefore parse as two islands — the
    old lookaround regex rejected the whole run because each side of the
    ``$$`` junction tripped the other's ``(?<!\$)`` / ``(?!\$)`` guard.

    A *doubled* ``$$`` is treated as a display-math delimiter and skipped
    (left as text): the input layer extracts display math as a MathBlock
    upstream, so a ``$$`` reaching the segmenter is not an inline boundary.

    Tagged inline-math islands (:mod:`brailix.core.inline_math`) carry no
    inner ``$`` (it is escaped) and no newline, so each matches here in
    full exactly as a user-typed ``$x^2$`` does.
    """
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "$":
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "$":
            # Doubled ``$$``: display-math delimiter, not an inline island.
            i += 2
            continue
        close = text.find("$", i + 1)
        if close == -1 or "\n" in text[i + 1 : close]:
            i += 1
            continue
        yield (i, close + 1, "math_inline")
        i = close + 1


def _qualifies_as_phonetic(inner: str) -> bool:
    """Whether a delimited region's content looks like an IPA transcription.

    True only when every non-space character is phonetic-class — an ASCII
    letter or an IPA-distinct character (:data:`_IPA_DISTINCT_CHARS`) —
    *and* at least one is IPA-distinct. Requiring an IPA-distinct
    character is what keeps ordinary slashed / bracketed prose out: a file
    path (``input/output``), a ratio (``5/17``), a footnote ref
    (``[注1]``) carries no IPA symbol, so it stays plain text. The cost is
    that a rare all-ASCII transcription (``/pet/``) isn't auto-recognised
    — but almost every English transcription carries a schwa / ɪ / æ / ː /
    ŋ / ʃ, so in practice this captures real phonetics and nothing else.
    """
    if not inner:
        return False
    has_distinct = False
    for ch in inner:
        if ch.isspace():
            continue
        if ch in _IPA_DISTINCT_CHARS:
            has_distinct = True
        elif not _is_latin(ch):
            # A digit, punctuation, CJK char, ``$`` … — not a transcription.
            return False
    return has_distinct


def _iter_phonetic_spans(text: str) -> Iterator[tuple[int, int, str]]:
    r"""Yield ``(start, end, "phonetic_inline")`` for each ``/.../`` or
    ``[...]`` region whose content qualifies as an IPA transcription.

    A region opens with ``/`` or ``[`` and closes with its partner (``/``
    / ``]``) on the same line; the content between must be non-empty,
    newline-free, and pass :func:`_qualifies_as_phonetic`. A delimited run
    that doesn't look like IPA (a path, a footnote ref) is left as plain
    text — the opener just advances by one, so a genuine transcription
    later on the same line is still found.
    """
    i = 0
    n = len(text)
    while i < n:
        close_ch = _PHONETIC_DELIMITERS.get(text[i])
        if close_ch is None:
            i += 1
            continue
        close = text.find(close_ch, i + 1)
        if close == -1 or "\n" in text[i + 1 : close]:
            i += 1
            continue
        if _qualifies_as_phonetic(text[i + 1 : close]):
            yield (i, close + 1, "phonetic_inline")
            i = close + 1
        else:
            i += 1


def _overlaps_any(
    span: tuple[int, int, str], others: list[tuple[int, int, str]]
) -> bool:
    """True if ``span`` shares any character range with one in ``others``
    (half-open intervals)."""
    start, end, _ = span
    return any(start < o_end and o_start < end for o_start, o_end, _ in others)


def _find_protected_regions(text: str) -> list[tuple[int, int, str]]:
    """Return non-overlapping protected regions sorted by start position.

    Two kinds are protected: ``$...$`` inline math (scanned by
    :func:`_iter_inline_math_spans`) and ``/.../`` / ``[...]`` phonetic
    transcriptions (:func:`_iter_phonetic_spans`). Math is scanned first
    and wins every conflict — a phonetic candidate overlapping a math
    island (a stray ``/`` pair inside ``$a/b/c$``) is dropped — so the two
    never overlap. Each scanner yields disjoint, ordered spans on its own;
    the merged list is re-sorted by start so the caller walks it in order.
    """
    math_spans = list(_iter_inline_math_spans(text))
    spans = list(math_spans)
    for span in _iter_phonetic_spans(text):
        if not _overlaps_any(span, math_spans):
            spans.append(span)
    spans.sort(key=lambda s: s[0])
    return spans


def _segment_unprotected(
    text: str,
    start: int,
    end: int,
    base_offset: int,
    categorize: Callable[[str], str] = _category,
) -> list[Segment]:
    """Chunk a run of unprotected text by character category.

    ``categorize`` classifies each character; pass a language-specific
    one (the Japanese segmenter adds ``kana_text``) to reuse this
    chunking. Special case: a decimal point or comma flanked by digits
    stays inside the digit_run so ``3.5`` and ``1,234`` survive as one
    segment. Downstream Normalizer relies on this.
    """
    segments: list[Segment] = []
    i = start
    while i < end:
        cat = categorize(text[i])
        j = i + 1
        if cat == "punct":
            # Emit punctuation one char at a time so each one can be
            # translated independently (e.g. ， → Chinese-comma braille rule).
            segments.append(
                Segment(
                    type="punct",
                    surface=text[i:j],
                    span=Span(base_offset + i, base_offset + j),
                )
            )
            i = j
            continue
        if cat == "math_op":
            # One math operator per segment — each `(` `)` `+` ... is
            # its own tiny inline-math node downstream, never merged
            # into a multi-char run.
            segments.append(
                Segment(
                    type="math_op",
                    surface=text[i:j],
                    span=Span(base_offset + i, base_offset + j),
                )
            )
            i = j
            continue
        if cat == "digit_run":
            while j < end:
                if categorize(text[j]) == "digit_run":
                    j += 1
                elif (
                    text[j] in ".,"
                    and j + 1 < end
                    and categorize(text[j + 1]) == "digit_run"
                ):
                    j += 1  # absorb the punctuation; the loop picks up the next digit
                else:
                    break
            segments.append(
                Segment(
                    type="digit_run",
                    surface=text[i:j],
                    span=Span(base_offset + i, base_offset + j),
                )
            )
            i = j
            continue
        while j < end and categorize(text[j]) == cat:
            j += 1
        segments.append(
            Segment(
                type=cat,
                surface=text[i:j],
                span=Span(base_offset + i, base_offset + j),
            )
        )
        i = j
    return segments


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

segmenter_registry: Registry[Segmenter] = Registry("segmenter", protocol=Segmenter)
segmenter_registry.register("default", DefaultSegmenter)


_DEFAULT_SEGMENTER: str = "default"


def segment(block, ctx: FrontendContext | None = None) -> list[Segment]:
    """Split one :class:`~brailix.ir.document.Block` into Segments.

    The active segmenter is chosen by ``ctx.options["segmenter"]``
    (default ``"default"``). Returns the segmenter's output unchanged.
    """
    name = _DEFAULT_SEGMENTER
    if ctx is not None and ctx.options:
        name = ctx.options.get("segmenter", _DEFAULT_SEGMENTER)
    return segmenter_registry.get(name).segment(block, ctx)
