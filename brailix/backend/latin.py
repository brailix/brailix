"""Translate ``LatinWord`` and ``LatinAcronym`` IR nodes into braille cells.

This module is the dedicated home for Latin / English braille
translation. The implementation follows the Current Chinese Braille convention for
**embedded English in Chinese braille text**: only the **first** letter
of a word carries a script-class prefix marker (indicating "Latin word
starts here, first letter is upper / lower"); every subsequent letter
emits its bare letter cell with no per-character prefix. A word written
entirely in capitals (two or more letters) doubles the capital sign —
⠠⠠ — so ``CPU`` and ``Cpu`` stay distinguishable; a single ⠠ means
"only the first letter is capital". Mid-word capitals (proper nouns
like ``McDonald``) emit as plain letter cells — the case info is lost
beyond the first letter, which is the documented trade-off of this
convention.

Examples (cn_current):

* ``hello``  → ``⠰⠓⠑⠇⠇⠕``  (1 latin_lower prefix + 5 bare letters)
* ``Hello``  → ``⠠⠓⠑⠇⠇⠕``  (1 latin_upper prefix + 5 bare letters)
* ``CPU``    → ``⠠⠠⠉⠏⠥``   (doubled latin_upper prefix + 3 bare letters)
* ``McDonald`` → ``⠠⠍⠉⠙⠕⠝⠁⠇⠙`` (upper prefix + bare cells; mid-word
                                   capital ``D`` loses its case info)

This is **not** a UEB / Nemeth implementation — no italic / bold
indicators, no contraction engine. Those land when a real
English-braille profile is added.
"""

from __future__ import annotations

from brailix.backend._letters import letter_sign_repeats
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
    upper vs. lower prefix) — doubled for an all-capitals word — then
    bare letter cells for the rest of the word. Non-letter characters
    mid-word (rare — segmenter usually splits on those) fall through to
    the punctuation table.
    """
    out: list[BrailleCell] = []
    surface = node.surface
    if not surface:
        return out
    base = node.span.start if node.span else 0
    if (
        len(surface) >= 2
        and surface.isascii()
        and surface.isalpha()
        and surface.isupper()
    ):
        _emit_all_caps_word(out, surface, base, node.span is not None, profile)
        return out
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


def _emit_all_caps_word(
    out: list[BrailleCell],
    surface: str,
    base: int,
    has_span: bool,
    profile: BrailleProfile,
) -> None:
    """Whole-word capitals: doubled ⠠ + every letter bare.

    Only called for pure-ASCII all-letter all-upper surfaces of length
    ≥ 2 (``CPU`` / ``NVDA``), so ``bare_letter`` always hits; dotted
    acronyms (``U.S.A.``) and mixed words keep the ordinary path.
    """
    first_sp = Span(base, base + 1) if has_span else None
    prefix = profile.math_structure("letter_prefix.latin_upper")
    for _ in range(letter_sign_repeats("latin_upper", len(surface))):
        out.extend(
            BrailleCell(
                dots=dots,
                role="latin_letter",
                source_span=first_sp,
                source_text=surface[0],
            )
            for dots in prefix
        )
    for i, ch in enumerate(surface):
        sp = Span(base + i, base + i + 1) if has_span else None
        bare = profile.bare_letter(ch)
        if bare is None:  # unreachable for ASCII letters; stay total
            continue
        out.append(
            BrailleCell(
                dots=bare,
                role="latin_letter",
                source_span=sp,
                source_text=ch,
            )
        )


__all__ = ("translate_latin",)
