"""Translate Punct / Space / Unknown / CodeInline IR nodes into braille cells.

Punctuation is looked up in the profile's punctuation table.
Spaces become blank cells (``dots=()``, ``role="space"``) carrying the
space's own span — not the span-less :data:`BLANK_CELL` sentinel, whose
use a traceable cell would violate. Unrecognized characters become an
unknown-role cell and emit a warning so a human proofreader can
catch them.

Code-inline rendering is the dumbest possible thing: each character
is looked up in the punctuation table and falls back to unknown.
Proper handling is future work.

Full-width Latin letters (``ＡＢＣ`` / ``ａｂｃ``) are the one prose
character class that folds before lookup: prose renders them through
the letter table via their half-width twins — the same render-time,
per-character fold the digit run gives full-width digits
(:func:`brailix.backend._digits.emit_digit_run` with
``fold_nonascii=True``), and the deliberate opposite of the math
domain, which treats a full-width letter inside a formula as the
writing error it is and warns instead (see
:func:`brailix.core.chars.fold_fullwidth`). Only this prose punct path
folds; code-inline stays literal per its minimal contract above.

LatinWord translation lives in :mod:`brailix.backend.latin`, a layer of
its own rather than a guest here (ARCHITECTURE#arch-layers).
"""

from __future__ import annotations

from brailix.core.chars import fold_fullwidth, is_math_symbol, nonstandard_char_hint
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell, blank_cell
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Punct,
    Space,
    Unknown,
)


def translate_punct(node: Punct, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    # A full-width Latin letter folds to its half-width twin and renders
    # through the letter table (see module docstring) — but an explicit
    # punctuation-table entry for the character, should a profile carry
    # one, always wins over the fold.
    if len(node.surface) == 1 and not profile.punctuation.get(node.surface):
        folded = _folded_fullwidth_latin_cells(node.surface, node.span, ctx, profile)
        if folded is not None:
            return folded
    out: list[BrailleCell] = list(lookup_or_unknown(node.surface, node.span, ctx, profile))
    space_before, space_after = profile.punctuation_spaces(node.surface)
    if space_before:
        # The auto-space belongs to the punctuation that triggers it; trace it
        # to a zero-width span at the punct's leading edge.
        out.insert(
            0, blank_cell(Span(node.span.start, node.span.start) if node.span else None)
        )
    if space_after:
        out.append(
            blank_cell(Span(node.span.end, node.span.end) if node.span else None)
        )
    return out


def _folded_fullwidth_latin_cells(
    ch: str,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell] | None:
    """Braille cells for one full-width Latin letter (``Ａ`` / ``ｂ``),
    rendered through the letter table under its half-width twin — or
    ``None`` when ``ch`` is not one / the profile has no letter for it.

    The folded letter takes :meth:`BrailleProfile.letter` — prefix plus
    bare cell — so case survives the fold (``ＡＢＣ`` keeps its capital
    signs, exactly as typed ``ABC`` would), and a profile without the
    ``letter_prefix.*`` structures warns through the same
    :func:`brailix.backend.latin.warn_letter_prefix_missing` the
    Latin-word path uses (imported locally: latin imports this module at
    load time, so the dependency can only point one way).

    Cells carry the **original** character as ``source_text`` so
    traceability maps them back to what the source actually says, not to
    the folded twin nobody typed.
    """
    half = fold_fullwidth(ch)
    if half is None or not half.isascii() or not half.isalpha():
        return None
    if profile.letter_class(half) is None:
        return None
    # Local import to keep the punct → latin dependency one-way at module
    # load (latin.py imports lookup_or_unknown from here; see docstring).
    from brailix.backend.latin import warn_letter_prefix_missing

    warn_letter_prefix_missing(half, span, ctx, profile)
    cells_for = profile.letter(half)
    if cells_for is None:  # unreachable given the letter_class hit; stay total
        return None
    return [
        BrailleCell(
            dots=dots, role="latin_letter", source_span=span, source_text=ch
        )
        for dots in cells_for
    ]


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
