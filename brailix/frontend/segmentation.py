"""Text segmentation — character-class chunking with protected regions.

The built-in, language-neutral pass, and the chunker every language shares.
Three names are published:

* :func:`segment` — one :class:`~brailix.ir.document.Block` of raw text into
  typed :class:`~brailix.core.segment.Segment` regions;
* :func:`segment_text` — the same over a raw string, with the character
  classifier as a parameter;
* :func:`char_category` — that classifier's built-in, Han-aware default.

**Who calls which.** Segmentation is a language's own lexical policy, so the
orchestrator asks the active language's frontend for it
(:meth:`brailix.core.protocols.LanguageFrontend.segment`). A language whose
writing system this module's classifier already covers delegates straight to
:func:`segment` — Chinese does. One that adds a script calls
:func:`segment_text` with a classifier of its own, inheriting every
language-neutral rule below: :mod:`brailix.frontend.ja` folds kana *and* kanji
into one ``ja_text`` run that way, in six lines. :func:`segment` also runs
directly when the active language has no frontend at all, so a document in an
unconfigured language still yields its numbers, Latin, punctuation and math
islands rather than nothing.

There is no ``segmenter_registry`` and no ``ctx.options["segmenter"]``. A
second language-keyed registry beside ``language_frontend_registry`` made
adding a language two registrations that had to agree on the segment type
names, for the sake of a freedom — a segmenter chosen independently of the
frontend that consumes its output — naming no combination anyone can use.
:class:`~brailix.core.protocols.LanguageFrontend` records the collapse.

Strategy:

1. Find "protected" regions — ``$...$`` inline math — that may span
   character categories and should not be split.
2. For the remaining text, group consecutive characters by category
   into Segments: hanzi_text / digit_run / latin_text / punct / space.

Segment types emitted (consumed downstream by
:mod:`~brailix.frontend.normalization` and ChineseAnalyzer):

* ``hanzi_text``  — CJK Unified Ideographs run.
* ``digit_run``   — ASCII or fullwidth digit run (normalization turns
  this into ``number`` / ``date``).
* ``latin_text``  — ASCII letters.
* ``greek_text``  — Greek alphabet letters (Α-Ω / α-ω + variants
  ϕ ϵ ϑ ϱ ς). Split from latin_text so each script gets its own
  letter-prefix (Greek upper/lower-case sign ⠸/⠨ vs Latin
  upper/lower-case sign ⠠/⠰) at the head of its run; downstream both
  route through the same ``LatinWord`` path because
  ``profile.letter()`` already picks the right prefix per character.
* ``punct``       — any single punctuation char.
* ``space``       — whitespace run.
* ``math_inline`` — protected ``$...$`` region.
* ``math_op``     — a bare math operator / delimiter: a half-width ASCII
  one (``()[]{}+-*/=<>|``) or a non-ASCII Unicode math symbol
  (category ``Sm``: ``∈`` ``≤`` ``∑`` ``→`` ...); normalization wraps
  each into a degenerate ``MathInline``.
* ``phonetic_inline`` — protected ``/.../`` or ``[...]`` IPA transcription.
* ``unknown``     — anything we don't classify.
"""

from __future__ import annotations

# ``Callable`` is bound at runtime rather than under TYPE_CHECKING: the three
# functions below are on the public surface and their annotations have to be
# readable back by an introspector (``tests/test_public_api.py``). Aliased
# because a foreign name must not answer under a plain one on a brailix
# module. The brailix imports below are runtime-bound for the same reason.
from collections.abc import Callable as _Callable
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.chars import fold_fullwidth, is_math_symbol
from brailix.core.context import FrontendContext
from brailix.core.segment import Segment
from brailix.core.span import Span
from brailix.ir.document import Block

if _TYPE_CHECKING:
    from collections.abc import Iterator

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


def char_category(ch: str) -> str:
    """The built-in, Han-aware segment category of one character.

    Published because a language's own classifier is a *delta* on this one,
    never a replacement: :func:`brailix.frontend.ja._chars._ja_category`
    answers ``ja_text`` for kana and for the characters this one calls
    ``hanzi_text``, and defers everything else — digits, Latin, Greek,
    punctuation, whitespace, bare math operators — to here. Re-deriving those
    per language is how a writing system ends up with math operators falling
    through to punctuation and a formula rendered as blank cells.
    """
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
# Entry points
# ---------------------------------------------------------------------------


def segment(block: Block, ctx: FrontendContext | None = None) -> list[Segment]:
    """Cut one :class:`~brailix.ir.document.Block` into Segments, by the
    built-in language-neutral character classes.

    ``ctx`` is accepted and unused: this pass reads nothing but the text and
    its offset, and the parameter is what lets a
    :class:`~brailix.core.protocols.LanguageFrontend` hand its own context
    straight through when it delegates here (Chinese does).
    """
    text = block.text
    if not text:
        return []
    base = block.span.start if block.span is not None else 0
    return segment_text(text, base_offset=base)


def segment_text(
    text: str,
    base_offset: int = 0,
    categorize: _Callable[[str], str] = char_category,
) -> list[Segment]:
    """Segment a raw string, classifying each character with ``categorize``.

    The reuse seam for a new language: pass a classifier that answers your
    language's own type for its script and defers to :func:`char_category`
    for the rest, and this chunking — protected ``$...$`` and IPA regions,
    digit runs that keep their decimal point, one Segment per punctuation
    character — comes with it. :mod:`brailix.frontend.ja` is the worked
    example.

    ``base_offset`` shifts every emitted :class:`~brailix.core.span.Span`, so
    a block that sits inside a larger document keeps source-accurate
    provenance.
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
    categorize: _Callable[[str], str] = char_category,
) -> list[Segment]:
    """Chunk a run of unprotected text by character category.

    ``categorize`` classifies each character; pass a language-specific
    one (the Japanese frontend's adds ``ja_text``) to reuse this
    chunking. Special case: a decimal point or comma flanked by digits
    stays inside the digit_run so ``3.5`` and ``1,234`` survive as one
    segment. Downstream normalization relies on this.
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


# This module publishes the pass the frontend runs (:func:`segment`, which the
# :mod:`brailix.frontend` facade re-exports) plus the two names a language
# reuses when it writes its own :meth:`LanguageFrontend.segment
# <brailix.core.protocols.LanguageFrontend.segment>` — those two are on the
# **extension surface** (see :mod:`brailix`), which is why they lost their
# leading underscore: the guide cannot in good conscience send an adapter
# author to a private name. The protected-region scanners, the per-script
# predicates and the unprotected-run chunker stay internal; without this list
# ``from ... import *`` offers all of them, along with ``Block``, ``Segment``
# and ``Span``.
__all__ = ("segment", "segment_text", "char_category")
