"""Shared char-aligned reading assembly for the pinyin resolvers.

Every pinyin resolver (pypinyin / g2pM / g2pW) produces one numeric-tone
syllable per *character* of the joined sentence.  This module owns the one
piece they all repeat: walk the token list with a char cursor, join each
token's slice into a space-separated reading, and rebuild the token —
bailing out with a ``PINYIN_LENGTH_MISMATCH`` warning if the per-character
count diverges (which would smear every later token's reading one slot
over).  g2pW additionally threads per-character confidences through,
attaching each token's min and a ``LOW_CONFIDENCE_PINYIN`` nudge below
threshold.

Extracted so a fix to the alignment / bail-out logic lands once instead of
in three near-identical copies (the only per-resolver differences are the
warning ``source`` tag, the engine name in the message, and whether
confidences are present).
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.ir.inline import ChineseToken


def resolve_by_char_alignment(
    tokens: list[ChineseToken],
    syllables: list[str],
    ctx: FrontendContext | None,
    *,
    source: str,
    engine: str,
    confidences: list[float] | None = None,
    low_confidence_threshold: float | None = None,
) -> list[ChineseToken]:
    """Slice per-character ``syllables`` back onto ``tokens``.

    ``source`` is the warning ``source`` tag (e.g. ``"pinyin.g2pw"``);
    ``engine`` names the resolver in the mismatch message.  ``confidences``
    (g2pW only) attaches a per-token min confidence and, below
    ``low_confidence_threshold``, a ``LOW_CONFIDENCE_PINYIN`` warning.

    A length divergence (engine merged / dropped a position) returns the
    tokens with pinyin / confidence cleared after warning, since slicing
    past the divergence would misalign every later token.
    """
    if not tokens:
        return []
    sentence = "".join(t.surface for t in tokens)

    if len(syllables) != len(sentence) or (
        confidences is not None and len(confidences) != len(sentence)
    ):
        if ctx is not None:
            ctx.warnings.warn(
                code="PINYIN_LENGTH_MISMATCH",
                message=(
                    f"{engine} returned {len(syllables)} syllables for "
                    f"{len(sentence)}-char input; dropping pinyin"
                ),
                surface=sentence,
                source=source,
            )
        return [
            ChineseToken(
                surface=tok.surface,
                pos=tok.pos,
                span=tok.span,
                pinyin=None,
                confidence=None,
            )
            for tok in tokens
        ]

    out: list[ChineseToken] = []
    char_cursor = 0
    for tok in tokens:
        length = len(tok.surface)
        chunk = syllables[char_cursor : char_cursor + length]
        pinyin_str = " ".join(s for s in chunk if s) or None
        token_confs = (
            confidences[char_cursor : char_cursor + length]
            if confidences is not None
            else None
        )
        out.append(
            ChineseToken(
                surface=tok.surface,
                pos=tok.pos,
                span=tok.span,
                pinyin=pinyin_str,
                confidence=min(token_confs) if token_confs else None,
            )
        )
        if (
            ctx is not None
            and token_confs
            and low_confidence_threshold is not None
            and min(token_confs) < low_confidence_threshold
        ):
            ctx.warnings.warn(
                code="LOW_CONFIDENCE_PINYIN",
                message=f"polyphone reading has low confidence: {tok.surface}",
                surface=tok.surface,
                span=tok.span,
                source=source,
            )
        char_cursor += length
    return out
