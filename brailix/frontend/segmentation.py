"""Text segmenter — module-level entry: :func:`segment`.

:func:`segment` is what the orchestrator calls; ``ctx.options["segmenter"]``
selects which adapter runs, defaulting to :data:`AUTO_SEGMENTER` — the
delegating adapter that picks by the profile's language, not the
:class:`DefaultSegmenter` described below.

:data:`segmenter_registry` beside it is **not** an implementation detail: it is
a documented extension point (``tests/test_public_api.py``'s extension manifest
pins it, and adding a language means registering a segmenter under its subtag),
so it carries the same compatibility promise the function does.

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
  this into ``number`` / ``date``).
* ``latin_text``  — ASCII letters.
* ``greek_text``  — Greek alphabet letters (Α-Ω / α-ω + variants
  ϕ ϵ ϑ ϱ ς). Split from latin_text so each script gets its own
  letter-prefix (Greek upper/lower-case sign ⠸/⠨ vs Latin
  upper/lower-case sign ⠠/⠰) at the head of its run; downstream the
  Normalizer routes them
  through the same ``LatinWord`` path because
  ``profile.letter()`` already picks the right prefix per character.
* ``punct``       — any single punctuation char.
* ``space``       — whitespace run.
* ``math_inline`` — protected ``$...$`` region.
* ``math_op``     — a bare math operator / delimiter: a half-width ASCII
  one (``()[]{}+-*/=<>|``) or a non-ASCII Unicode math symbol
  (category ``Sm``: ``∈`` ``≤`` ``∑`` ``→`` ...); the Normalizer wraps
  each into a degenerate ``MathInline``.
* ``phonetic_inline`` — protected ``/.../`` or ``[...]`` IPA transcription.
* ``unknown``     — anything we don't classify.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.chars import fold_fullwidth, is_math_symbol
from brailix.core.context import FrontendContext
from brailix.core.protocols import Segmenter
from brailix.core.registry import Registry
from brailix.core.span import Span
from brailix.frontend._language_pick import pick_by_language
from brailix.ir.document import Block
from brailix.ir.inline import Segment

if _TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ---------------------------------------------------------------------------
# Protected-region patterns
# ---------------------------------------------------------------------------

# Inline math wrapped in single $...$. Detected by a paired scan
# (:func:`_iter_inline_math_spans`) rather than a regex: a lookaround pattern
# like ``(?<!\$)\$(?!\$)([^$\n]+)\$(?!\$)`` rejects two *adjacent* islands
# such as ``$a$$b$``, because each side of the ``$$`` junction trips the
# other's guard. We still do not treat ``$$...$$`` (display
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
    # Non-ASCII math symbols (Unicode category Sm: ∈ ≤ ∀ ∑ → …) are bare
    # math operators too, by the same "half-width = math" logic: route them
    # to the math path so ``x∈A`` translates as mathematics instead of dying
    # as an unknown cell. ASCII Sm chars (``~`` …) are deliberately left to
    # the explicit set above; full-width forms (``＝``) are excluded so they
    # keep their "use the half-width form" diagnostic (fold_fullwidth catches
    # those). The middle dot ``·`` is category Po (not Sm), so the Chinese
    # name separator 间隔号 stays prose punctuation, untouched.
    if ord(ch) >= 0x80 and fold_fullwidth(ch) is None and is_math_symbol(ch):
        return "math_op"
    # Treat everything else (CJK punct, ASCII punct, symbols) as punct.
    # Normalizer/Backend will split on specific characters as needed.
    return "punct"


# ---------------------------------------------------------------------------
# Segmenter
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
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
        if close == -1 or text.find("\n", i + 1, close) != -1:
            i += 1
            continue
        yield (i, close + 1, "math_inline")
        i = close + 1


def _next_occurrence(text: str, ch: str, after: int, cache: dict[str, int]) -> int:
    """First index of ``ch`` at or after ``after``, or ``-1``.

    Remembered per character, because the callers walk a cursor forward and
    would otherwise re-scan the same tail once per step. The cached index is
    still the answer for any later cursor that has not passed it — there is
    nothing between the position it was found from and itself — and a ``-1``
    is permanent, which is what turns a line of unmatched openers from a
    quadratic re-scan into one pass.
    """
    found = cache.get(ch, -2)
    if found != -1 and found < after:
        found = text.find(ch, after)
        cache[ch] = found
    return found


def _qualifies_as_phonetic(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` looks like an IPA transcription.

    Takes offsets rather than the substring: a candidate is tested at every
    delimiter the scanner passes, and slicing out the content to reject it on
    its first character copied the whole region each time.

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
    if start >= end:
        return False
    has_distinct = False
    for pos in range(start, end):
        ch = text[pos]
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

    "Advances by one" is why the closer and newline searches are cached
    (:func:`_next_occurrence`) instead of re-run: this walks every block of
    ordinary prose, the caller's whole-text ceiling is tens of millions of
    characters, and a run of unmatched openers otherwise re-scanned the rest
    of the text once per opener.
    """
    i = 0
    n = len(text)
    seen: dict[str, int] = {}
    while i < n:
        close_ch = _PHONETIC_DELIMITERS.get(text[i])
        if close_ch is None:
            i += 1
            continue
        close = _next_occurrence(text, close_ch, i + 1, seen)
        newline = _next_occurrence(text, "\n", i + 1, seen)
        if close == -1 or (newline != -1 and newline < close):
            i += 1
            continue
        if _qualifies_as_phonetic(text, i + 1, close):
            yield (i, close + 1, "phonetic_inline")
            i = close + 1
        else:
            i += 1


def _drop_overlapping(
    candidates: Iterator[tuple[int, int, str]],
    others: list[tuple[int, int, str]],
) -> Iterator[tuple[int, int, str]]:
    """Yield the candidates that share no character range with ``others``.

    Both sides arrive sorted by start and internally disjoint, so one index
    walks ``others`` forward across the whole stream rather than restarting per
    candidate — which on a text alternating math islands and phonetic
    candidates costs one full scan of every math span per phonetic span.
    """
    at = 0
    total = len(others)
    for span in candidates:
        start, end, _ = span
        while at < total and others[at][1] <= start:
            at += 1
        # ``others[at]`` is the first one that could reach this candidate;
        # everything after it starts later still, so one comparison decides.
        if at < total and others[at][0] < end:
            continue
        yield span


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
    spans.extend(_drop_overlapping(_iter_phonetic_spans(text), math_spans))
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

# What :func:`segment` falls back to when the context names no segmenter —
# the delegating adapter that picks by the document's language.
AUTO_SEGMENTER = "auto"

# The built-in, language-neutral segmenter's registered name, kept distinct
# from the name above: one is what THIS adapter is called, the other is what
# a caller who names nothing gets. Conflating them makes
# ``segmenter="default"`` unselectable — passing it reads as "no preference".
BUILTIN_SEGMENTER = "default"

segmenter_registry.register(BUILTIN_SEGMENTER, DefaultSegmenter)


@_dataclass(slots=True)
class AutoSegmenter:
    """Delegating segmenter: uses the active language's, else the built-in.

    Mirrors the ``auto`` analyzer / resolver adapters so every pluggable
    frontend family is selected the same way — a caller that names nothing
    gets ``auto`` and stops caring which implementations exist. The
    delegation is resolved per call rather than cached: unlike the engine
    chains, what this picks depends on the *document* (its language), not on
    the environment, so there is nothing stable to memoise.
    """

    name: str = "auto"

    def segment(
        self, block: Block, ctx: FrontendContext | None = None
    ) -> list[Segment]:
        picked = pick_by_language(segmenter_registry, ctx, BUILTIN_SEGMENTER)
        return segmenter_registry.get(picked).segment(block, ctx)


segmenter_registry.register(AUTO_SEGMENTER, AutoSegmenter)


def segment(block, ctx: FrontendContext | None = None) -> list[Segment]:
    """Split one :class:`~brailix.ir.document.Block` into Segments.

    The active segmenter is chosen by ``ctx.options["segmenter"]``,
    defaulting to :data:`AUTO_SEGMENTER`.
    Returns the segmenter's output unchanged.
    """
    name = AUTO_SEGMENTER
    if ctx is not None and ctx.options:
        name = ctx.options.get("segmenter", AUTO_SEGMENTER)
    return segmenter_registry.get(name).segment(block, ctx)


# This module publishes its registry — promised on the **extension surface**
# (see :mod:`brailix`), which is where a third-party segmenter registers — and
# the subsystem entry point the orchestrator calls, which the
# :mod:`brailix.frontend` facade re-exports. The concrete adapters, the
# adapter-name constants and the character-class helpers are internal; without
# this list ``from ... import *`` offers all of them, along with ``Block``,
# ``Segment``, ``Span`` and ``Registry``.
__all__ = ("segmenter_registry", "segment")
