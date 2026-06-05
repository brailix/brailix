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
* ``latin_text``  — ASCII letters (possibly with ``-`` or ``'``).
* ``greek_text``  — Greek alphabet letters (Α-Ω / α-ω + variants
  ϕ ϵ ϑ ϱ ς). Split from latin_text so each script gets its own
  letter-prefix (Greek upper/lower-case sign ⠸/⠨ vs Latin
  upper/lower-case sign ⠠/⠰) at the head of its run; downstream the
  Normalizer routes them
  through the same ``LatinWord`` / ``LatinAcronym`` path because
  ``profile.letter()`` already picks the right prefix per character.
* ``punct``       — any single punctuation char.
* ``space``       — whitespace run.
* ``math_inline`` — protected region.
* ``unknown``     — anything we don't classify.
"""

from __future__ import annotations

import re
from collections.abc import Callable
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

# Inline math wrapped in single $...$. We deliberately do not match
# $$...$$ (display math) or \(...\) here; the input layer should mark
# display math as a math_block.
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)([^$\n]+)\$(?!\$)")

_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("math_inline", _INLINE_MATH_RE),
)

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


# ---------------------------------------------------------------------------
# Character categorization
# ---------------------------------------------------------------------------


def _is_hanzi(ch: str) -> bool:
    return (
        "一" <= ch <= "鿿"
        or "㐀" <= ch <= "䶿"  # Extension A
        or "豈" <= ch <= "﫿"  # Compatibility Ideographs
    )


def _is_digit(ch: str) -> bool:
    return ch.isdigit()  # accepts fullwidth digits too


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


def _find_protected_regions(text: str) -> list[tuple[int, int, str]]:
    """Return non-overlapping protected regions sorted by start position.

    When two patterns match overlapping spans, the earlier-starting one
    wins; ties go to longer matches.
    """
    candidates: list[tuple[int, int, str]] = []
    for type_name, pattern in _PROTECTED_PATTERNS:
        for m in pattern.finditer(text):
            candidates.append((m.start(), m.end(), type_name))
    if not candidates:
        return []
    # Sort by (start, -length) so longer earlier-starting matches win.
    candidates.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    result: list[tuple[int, int, str]] = []
    last_end = 0
    for start, end, type_name in candidates:
        if start < last_end:
            continue  # overlaps with an accepted region; drop
        result.append((start, end, type_name))
        last_end = end
    return result


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
