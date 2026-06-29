"""Translate Punct / Space / Unknown / CodeInline IR nodes into braille cells.

Punctuation is looked up in the profile's punctuation table.
Spaces become :data:`BLANK_CELL`. Unrecognized characters become an
unknown-role cell and emit a warning so a human proofreader can
catch them.

Code-inline rendering is the dumbest possible thing: each character
is looked up in the punctuation table and falls back to unknown.
Proper handling is future work.

LatinWord / LatinAcronym translation lives in
:mod:`brailix.backend.latin` (it used to share this module but
was moved to its own layer in line with the architecture in
``ARCHITECTURE.md`` §1).
"""

from __future__ import annotations

from brailix.core.chars import is_math_symbol, nonstandard_char_hint
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BLANK_CELL, BrailleCell
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Punct,
    Space,
    Unknown,
)


def translate_punct(node: Punct, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    out: list[BrailleCell] = list(lookup_or_unknown(node.surface, node.span, ctx, profile))
    space_before, space_after = profile.punctuation_spaces(node.surface)
    if space_before:
        out.insert(0, BLANK_CELL)
    if space_after:
        out.append(BLANK_CELL)
    return out


def translate_space(node: Space, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Space → one blank cell per source space char.

    A :class:`Space` with empty surface is a *synthetic* separator the
    frontend inserts between adjacent words (the Chinese-braille inter-word-space rule).
    We still emit one blank cell for it so the spoken / printed
    output gets the word boundary; the source span collapses to the
    boundary point and ``source_text`` is empty to signal the cell
    has no surface character behind it.
    """
    if not node.surface:
        return [
            BrailleCell(
                dots=(),
                role="space",
                source_span=node.span,
                source_text="",
            )
        ]
    return [
        BrailleCell(
            dots=(),
            role="space",
            source_span=Span(node.span.start + i, node.span.start + i + 1) if node.span else None,
            source_text=" ",
        )
        for i in range(len(node.surface))
    ]


def translate_connector(
    node: Connector, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Connector → one connector cell (⠤) from ``profile.connector``.

    A :class:`Connector` is the *synthetic* joiner the zh frontend
    inserts inside a letter+hanzi compound word (``x轴`` / ``T恤`` — x-axis / T-shirt) — the
    counterpart of the word-boundary :class:`Space`, but rendered as the
    profile's connector cell instead of a blank. ``source_text`` is empty
    (no surface character behind it) and the span collapses to the
    boundary point, mirroring :func:`translate_space`.

    A profile that doesn't configure ``connector`` yields ``dots=()`` —
    a blank cell, which degrades to the pre-feature blank-cell behaviour rather
    than crashing. Both shipped zh profiles set it to ⠤ (dots 3-6).
    """
    return [
        BrailleCell(
            dots=profile.connector,
            role="connector",
            source_span=node.span,
            source_text="",
        )
    ]


def _warn_char_no_rule(
    ctx: BackendContext,
    *,
    ch: str,
    span: Span | None,
    unknown_code: str,
    unknown_message: str,
) -> None:
    """Warn that a single prose character has no braille rule.

    Picks the code by what the character *is*:

    * a **math symbol** (``∈`` ``~`` …, :func:`is_math_symbol`) with no
      full-width / invisible hint of its own gets ``PROSE_MATH_SYMBOL`` — an
      actionable "this is mathematics, wrap it in ``$…$``" instead of a bare
      unknown. The common math symbols never arrive here: the segmenter
      auto-routes a non-ASCII one through the math backend, where it either
      translates or raises its own ``MATH_*`` warning. What is left is the
      tail the math path doesn't claim (the ASCII tilde ``~`` is the standing
      example).
    * anything else keeps the caller's ``unknown_code`` /
      ``unknown_message``, with a full-width / invisible hint
      (:func:`nonstandard_char_hint`) appended when one applies — so a
      full-width ``＝`` still reads "use the half-width form" rather than
      being mistaken for a bare math symbol.

    Never rewrites input; the caller still emits an unknown cell so layout
    stays stable.
    """
    hint = nonstandard_char_hint(ch)
    if hint is None and is_math_symbol(ch):
        ctx.warnings.warn(
            code="PROSE_MATH_SYMBOL",
            message=(
                f"math symbol {ch!r} has no rule in Chinese prose; "
                f"wrap it in $...$ if it is part of a formula"
            ),
            surface=ch,
            span=span,
            source="backend.punct",
        )
        return
    message = f"{unknown_message} — {hint}" if hint else unknown_message
    ctx.warnings.warn(
        code=unknown_code,
        message=message,
        surface=ch,
        span=span,
        source="backend.punct",
    )


def translate_unknown(node: Unknown, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    _warn_char_no_rule(
        ctx,
        ch=node.surface,
        span=node.span,
        unknown_code="UNKNOWN_NODE",
        unknown_message=f"no translation for unknown node: {node.surface!r}",
    )
    return [
        BrailleCell(dots=(), role="unknown", source_span=node.span, source_text=node.surface)
    ]


def translate_code_inline(
    node: CodeInline, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    return _char_by_char(node.surface, node.span, ctx, profile)


# ---------------------------------------------------------------------------
# Internal helper: translate ``text`` one character at a time via
# :func:`lookup_or_unknown` (the code-inline path above uses it).
# ---------------------------------------------------------------------------


def _char_by_char(
    text: str, span: Span | None, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    if not text:
        return []
    base = span.start if span else 0
    out: list[BrailleCell] = []
    for i, ch in enumerate(text):
        sp = Span(base + i, base + i + 1) if span else None
        out.extend(lookup_or_unknown(ch, sp, ctx, profile))
    return out


def lookup_or_unknown(
    ch: str, span: Span | None, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Look ``ch`` up in the punctuation table and return one BrailleCell
    *per cell* in its mapping (some punctuation, like the Chinese full stop ⠐⠆, is
    multi-cell). Unknown chars yield a single unknown cell + warning.

    Public helper — also used by :mod:`brailix.backend.latin` for the
    non-letter fall-through path in Latin-word translation.
    """
    cells = profile.punctuation.get(ch)
    if not cells:
        _warn_char_no_rule(
            ctx,
            ch=ch,
            span=span,
            unknown_code="UNKNOWN_PUNCT",
            unknown_message=f"no punctuation mapping for {ch!r}",
        )
        return [BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)]
    return [
        BrailleCell(dots=dots, role="punct", source_span=span, source_text=ch)
        for dots in cells
    ]
