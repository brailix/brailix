"""Shared cursor-based span recovery for analyzers that return no offsets.

HanLP and THULAC both hand back a flat word list with no source spans, so
each recovers spans by linear ``text.find`` from a moving cursor: a word
not found in the remaining text falls back to a synthetic span plus an
``<ENGINE>_WORD_NOT_IN_TEXT`` warning; characters skipped before a match
emit ``<ENGINE>_SKIPPED_CHARS``.  This is that one recovery loop, extracted
so a fix lands once instead of in two near-identical copies.  (jieba and
char carry their own offsets and don't use this.)

The warning codes are declared below as module constants whose *name equals
their value* (``HANLP_WORD_NOT_IN_TEXT = "HANLP_WORD_NOT_IN_TEXT"``) and
emitted by reference — deliberately NOT by f-string assembly.  The i18n
guard (the warning-code/locale parity test) scans source text for
literal ``code=`` sites and for this constant form; a dynamically assembled
code is invisible to it, which is exactly how these four shipped with no
locale entries and no guard.  ``code_prefix`` remains the per-engine knob
for callers; an unregistered prefix is a programming error and raises
rather than silently inventing an unscannable code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.zh.tokens import ChineseToken

if _TYPE_CHECKING:
    from collections.abc import Iterable

# name == value so the i18n scanner can enumerate them statically.
HANLP_WORD_NOT_IN_TEXT = "HANLP_WORD_NOT_IN_TEXT"
HANLP_SKIPPED_CHARS = "HANLP_SKIPPED_CHARS"
THULAC_WORD_NOT_IN_TEXT = "THULAC_WORD_NOT_IN_TEXT"
THULAC_SKIPPED_CHARS = "THULAC_SKIPPED_CHARS"

#: prefix → (word-not-in-text code, skipped-chars code); keep in sync with
#: the adapters that call :func:`recover_spans_by_cursor`.
_CODES_BY_PREFIX: dict[str, tuple[str, str]] = {
    "HANLP": (HANLP_WORD_NOT_IN_TEXT, HANLP_SKIPPED_CHARS),
    "THULAC": (THULAC_WORD_NOT_IN_TEXT, THULAC_SKIPPED_CHARS),
}


def _warning_codes(code_prefix: str) -> tuple[str, str]:
    """(word-not-in-text code, skipped-chars code) for a registered prefix.

    Resolved lazily, only on a warn path with a live context: an
    unregistered prefix reaching a real emission is a programming error and
    raises instead of inventing a dynamic code the i18n scanner can't see.
    """
    try:
        return _CODES_BY_PREFIX[code_prefix]
    except KeyError:
        raise ValueError(
            f"unknown code_prefix {code_prefix!r}: register its warning codes "
            f"in _CODES_BY_PREFIX so the i18n scanner can see them"
        ) from None


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
    resolve to the right occurrence.  ``code_prefix`` selects this engine's
    warning codes from ``_CODES_BY_PREFIX`` (``"THULAC"`` →
    ``THULAC_WORD_NOT_IN_TEXT`` / ``THULAC_SKIPPED_CHARS``) and must be
    registered there once a warning is actually emitted; ``source`` is the
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
                    code=_warning_codes(code_prefix)[0],
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
                code=_warning_codes(code_prefix)[1],
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
