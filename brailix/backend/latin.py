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

**Running English drops the redundant lowercase sign.** The lowercase
sign ⠰ only announces "a Latin letter starts here"; once a stretch of
English is already underway, repeating it on every word is noise. So
inside a running English context (the dispatcher flags this via
``ctx.options['_english_run_active']``) a *lowercase* Latin word emits
its letters bare, with no leading ⠰ — a whole sentence then shows the
sign at most once, on its first word if that word is lowercase. The
**capital** sign keeps emitting per word: it carries case, not merely
"this is a letter", so dropping it would lose information (``World`` vs
``world``). Greek likewise keeps its own script-class sign. What counts
as "running English" — and what breaks it — is decided by
:func:`english_run_role`; the dispatcher (``backend.block``) walks the
inline children and threads the flag.

Examples (cn_current):

* ``hello``        → ``⠰⠓⠑⠇⠇⠕``           (lowercase sign + 5 bare letters)
* ``Hello``        → ``⠠⠓⠑⠇⠇⠕``           (capital sign + 5 bare letters)
* ``hello world``  → ``⠰⠓⠑⠇⠇⠕⠀⠺⠕⠗⠇⠙``    (sign once; ``world`` runs bare)
* ``Hello world``  → ``⠠⠓⠑⠇⠇⠕⠀⠺⠕⠗⠇⠙``    (capital opens it; ``world`` bare)
* ``CPU``          → ``⠠⠠⠉⠏⠥``            (doubled capital sign + 3 bare letters)
* ``McDonald``     → ``⠠⠍⠉⠙⠕⠝⠁⠇⠙``        (capital sign + bare cells; mid-word
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
from brailix.ir.inline import (
    InlineNode,
    LatinAcronym,
    LatinWord,
    Number,
    Punct,
    Space,
)


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
    # Inside a running stretch of embedded English, a lowercase word
    # drops its lowercase sign (see module docstring). Capitals are
    # unaffected — an all-caps word still doubles its capital sign — so
    # this flag only gates the lowercase-first-letter branch below.
    run_active = bool(ctx.options.get("_english_run_active"))
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
            # Running English + lowercase first letter: skip the lowercase
            # sign and emit the bare letter. The English context is already
            # established by an earlier word, so ⠰ would just be noise; the
            # capital and Greek signs (handled by ``profile.letter`` below)
            # still carry case / script and are never skipped.
            if run_active and profile.letter_class(ch) == "latin_lower":
                bare = profile.bare_letter(ch)
                if bare is not None:  # always true for a latin_lower char
                    out.append(
                        BrailleCell(
                            dots=bare,
                            role="latin_letter",
                            source_span=sp,
                            source_text=ch,
                        )
                    )
                    prefix_emitted = True
                    continue
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


# Node types that *carry* a running English context across without being
# letters themselves — the inter-word space, English punctuation (``.`` in
# ``e.g.``, the comma in ``hello, world``) and digits (``ID 42 ok``). They
# neither open nor close the run; the lowercase sign stays suppressed across
# them. Everything not listed here (Chinese prose, math, connectors, …)
# breaks the run, so the next English word re-announces itself.
_RUN_CARRY_TYPES = (Space, Punct, Number)


def english_run_role(node: InlineNode) -> str:
    """Classify ``node``'s effect on a running English context.

    Returns ``"letter"`` for a Latin word/acronym (it *is* English — it
    opens the run and, once open, keeps it open), ``"carry"`` for a node
    that may sit inside an English stretch without ending it (space,
    punctuation, digits), or ``"break"`` for anything that ends it
    (Chinese prose, math, an unknown token …).

    The dispatcher (:func:`brailix.backend.block._translate_children`)
    threads the resulting on/off state into
    ``ctx.options['_english_run_active']`` so :func:`translate_latin` can
    drop the redundant lowercase sign. Keeping this classification here —
    next to the rule it serves — stops the generic block dispatcher from
    hard-coding inline-type knowledge.
    """
    if isinstance(node, (LatinWord, LatinAcronym)):
        return "letter"
    if isinstance(node, _RUN_CARRY_TYPES):
        return "carry"
    return "break"


__all__ = ("english_run_role", "translate_latin")
