"""Translate ``LatinWord`` and ``LatinAcronym`` IR nodes into braille cells.

This module is the dedicated home for Latin / English braille
translation. The implementation follows the Current Chinese Braille convention for
**embedded English in Chinese braille text**: only the **first** letter
of a word carries a script-class prefix marker (indicating "Latin word
starts here, first letter is upper / lower"); every subsequent letter
emits its bare letter cell with no per-character prefix. Mid-word
capitals (proper nouns like ``McDonald``) emit as plain letter cells —
the case info is lost beyond the first letter, which is the documented
trade-off of this convention.

Examples (cn_current):

* ``hello``  → ``⠰⠓⠑⠇⠇⠕``  (1 latin_lower prefix + 5 bare letters)
* ``Hello``  → ``⠠⠓⠑⠇⠇⠕``  (1 latin_upper prefix + 5 bare letters)
* ``CPU``    → ``⠠⠉⠏⠥``    (1 latin_upper prefix + 3 bare letters)
* ``McDonald`` → ``⠠⠍⠉⠙⠕⠝⠁⠇⠙`` (upper prefix + bare cells; mid-word
                                   capital ``D`` loses its case info)

This is **not** a UEB / Nemeth implementation — no word-level double
capital, no italic / bold indicators, no contraction engine. Those
land when a real English-braille profile is added.
"""

from __future__ import annotations

from brailix.backend.punct import lookup_or_unknown
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import LatinAcronym, LatinWord


def translate_latin(
    node: LatinWord | LatinAcronym,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    """Translate one Latin / English token.

    Emits one prefix marker on the first letter (its case determines
    upper vs. lower prefix), then bare letter cells for the rest of
    the word. Non-letter characters mid-word (rare — segmenter usually
    splits on those) fall through to the punctuation table.
    """
    out: list[BrailleCell] = []
    surface = node.surface
    if not surface:
        return out
    base = node.span.start if node.span else 0
    prefix_emitted = False
    for i, ch in enumerate(surface):
        sp = Span(base + i, base + i + 1) if node.span else None
        if not prefix_emitted:
            # First letter of the word: emit prefix + letter (full
            # composed sequence from ``profile.letter``). The prefix
            # itself carries the case info — latin_lower vs latin_upper.
            cells_for = profile.letter(ch)
            if cells_for is not None:
                out.extend(
                    BrailleCell(
                        dots=dots,
                        role="latin_letter",
                        source_span=sp,
                        source_text=ch,
                    )
                    for dots in cells_for
                )
                prefix_emitted = True
                continue
            # First character isn't a letter (very rare for a LatinWord
            # given the segmenter's classification rules). Fall through
            # to punct lookup and leave ``prefix_emitted`` false so the
            # next letter takes the prefix path.
            out.extend(lookup_or_unknown(ch, sp, ctx, profile))
            continue
        # Subsequent character: bare letter cell (no prefix). If it's
        # not a letter (mid-word punct etc.), fall back to punct lookup
        # but keep ``prefix_emitted`` true — we're still inside the
        # same Latin run.
        bare = profile.bare_letter(ch)
        if bare is not None:
            out.append(
                BrailleCell(
                    dots=bare,
                    role="latin_letter",
                    source_span=sp,
                    source_text=ch,
                )
            )
            continue
        out.extend(lookup_or_unknown(ch, sp, ctx, profile))
    return out


__all__ = ("translate_latin",)
