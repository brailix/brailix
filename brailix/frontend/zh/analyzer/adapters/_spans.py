"""Shared cursor-based span recovery for analyzers that return no offsets.

HanLP and THULAC both hand back a flat word list with no source spans, so
each recovers spans by linear ``text.find`` from a moving cursor: a word
not found in the remaining text falls back to a synthetic span plus an
``<ENGINE>_WORD_NOT_IN_TEXT`` warning; characters skipped before a match
emit ``<ENGINE>_SKIPPED_CHARS``.  This is that one recovery loop, extracted
so a fix lands once instead of in two near-identical copies.  (jieba and
char carry their own offsets and don't use this.)
"""

from __future__ import annotations

from collections.abc import Iterable

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken


def recover_spans_by_cursor(
    words: Iterable[tuple[str, str | None]],
    text: str,
    ctx: FrontendContext | None,
    *,
    code_prefix: str,
    source: str,
    engine: str,
    skip_blank: bool = False,
) -> list[ChineseToken]:
    """Build :class:`ChineseToken`s with spans recovered by cursor search.

    ``words`` yields ``(surface, pos)`` pairs (``pos`` may be ``None``).
    The cursor advances past each match so repeated words (``很好，很好``)
    resolve to the right occurrence.  ``code_prefix`` / ``engine`` name the
    warnings (``"THULAC"`` → ``THULAC_WORD_NOT_IN_TEXT``); ``source`` is the
    warning source tag.  ``skip_blank`` drops empty / whitespace-only
    surfaces (THULAC emits per-line markers, and an empty surface would
    make ``find`` match at the cursor and stall).
    """
    tokens: list[ChineseToken] = []
    cursor = 0
    for word, pos in words:
        if skip_blank and (not word or word.isspace()):
            continue
        start = text.find(word, cursor)
        if start < 0:
            # Engine normalised the word (e.g. full-width → half-width) or
            # invented a token; use the cursor as a synthetic span so we
            # don't crash, but warn — the surface↔source mapping is
            # unreliable for this word.
            start = cursor
            if ctx is not None:
                ctx.warnings.warn(
                    code=f"{code_prefix}_WORD_NOT_IN_TEXT",
                    message=(
                        f"{engine} returned word {word!r} not found in "
                        f"source at cursor {cursor}"
                    ),
                    surface=word,
                    span=Span(start, start + len(word)),
                    source=source,
                )
        elif start > cursor and ctx is not None:
            # Characters between cursor and start aren't claimed by any
            # token (the engine's input cleaning dropped them) — proofread
            # accuracy needs to know about the gap.
            ctx.warnings.warn(
                code=f"{code_prefix}_SKIPPED_CHARS",
                message=(
                    f"{engine} skipped {start - cursor} char(s) before "
                    f"word {word!r}; gap text: {text[cursor:start]!r}"
                ),
                surface=text[cursor:start],
                span=Span(cursor, start),
                source=source,
            )
        # Clamp: a not-found synthetic span (start=cursor) could otherwise
        # run past the end of the source.
        end = min(start + len(word), len(text))
        tokens.append(ChineseToken(surface=word, pos=pos, span=Span(start, end)))
        cursor = end
    return tokens
