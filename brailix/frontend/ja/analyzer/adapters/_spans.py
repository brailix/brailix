"""Span recovery shared by the engine-backed ja analyzer adapters.

janome and MeCab (fugashi) both drop inter-token whitespace, so a running
length sum drifts from the real source offsets; each adapter re-locates
its surfaces from a cursor instead. That loop lived in both adapters as
two near-identical copies — and neither carried the clamp: an engine that
returns a surface the text does not contain got ``start = cursor`` and a
span running past the end of the source, which ``_check_analyzer_output``
then refused as a ``FrontendContractError`` — a whole segment's
translation failing because of one invented morpheme. The clamp keeps the
span inside the text; the length mismatch it produces is what the
``TOKEN_SPAN_MISMATCH`` warning is for (the clamped token warns and the
rest of the segment still translates).

Not shared with ``frontend.zh.analyzer.adapters._spans``: zh and ja are
independently replaceable language components (ARCHITECTURE#arch-layers),
and the zh helper carries warnings and a gap-skip walk specific to its
adapters' shapes.
"""

from __future__ import annotations

from brailix.core.span import Span


def recover_span(text: str, surface: str, cursor: int) -> tuple[Span, int]:
    """Locate ``surface`` in ``text`` at/after ``cursor``; return its span
    and the next cursor.

    A surface the text does not contain starts at the cursor (the engine
    dropped or invented something); the span end is clamped to the end of
    the text so a synthetic start can never point past the source.
    """
    start = text.find(surface, cursor)
    if start < 0:
        start = cursor
    end = min(start + len(surface), len(text))
    return Span(start, end), end
