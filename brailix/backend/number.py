"""Translate number-family IR nodes into braille cells.

Covers :class:`Number`, :class:`Date`, :class:`Percent`,
:class:`Quantity`. Uses the profile's ``digits`` / ``number_sign`` /
``decimal_point`` / ``thousands_sep`` / ``punctuation`` tables.

A number-sign cell is prepended whenever a digit run starts a new
braille "phrase". For now we emit it before every numeric token;
context-aware suppression (e.g. "still inside a number") arrives with
:class:`BackendContext` plumbing.
"""

from __future__ import annotations

from brailix.backend._digits import DigitRoles, emit_digit_run
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import Date, HanziMarker, InlineNode, Number, Percent, Quantity

# Role labels for prose number digit runs (the math backend uses
# "math_digit"); the shared emitter handles the rest.
_NUMBER_ROLES = DigitRoles(digit="digit")

# The one date marker that writes directly against its number with no
# connector: the NCB convention keeps the year marker 年 attached to the
# year digits. Every other marker (月/日/号/时/分/秒) takes the
# number→hanzi joiner the way ``10页`` / ``3个`` do; see
# :func:`translate_date`.
_DATE_CONNECTOR_EXEMPT = "年"

# ---------------------------------------------------------------------------
# Public entry points (one per IR node type)
# ---------------------------------------------------------------------------


def translate_number(node: Number, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Number → [number_sign?, digit_cells...]"""
    return _digits_to_cells(node.surface, node.span, ctx, profile)


def translate_percent(node: Percent, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Percent → digits + percent punctuation."""
    cells = _digits_to_cells(node.surface[:-1], _first_part_span(node), ctx, profile)
    cells.extend(_punct_cells(node.surface[-1], _last_char_span(node), ctx, profile))
    return cells


def translate_quantity(node: Quantity, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Quantity → digits + unit letters.

    Unit characters (``kg``, ``cm``, ``ms``, ``Hz`` ...) are emitted
    through the math-identifier table — the same table that already
    knows the per-script letter prefixes (small/capital latin 56 / 6,
    greek 46 / 456). Characters absent from that table fall back to
    the punctuation table, and only emit an :class:`Unknown` cell
    with a ``UNKNOWN_NUMBER_PART`` warning when both lookups miss.
    """
    digits_part = node.number.surface if node.number else ""
    unit_part = node.unit or ""
    cells = _digits_to_cells(digits_part, node.number.span if node.number else None, ctx, profile)
    base = node.span.start + len(digits_part) if node.span else 0
    for i, ch in enumerate(unit_part):
        sp = Span(base + i, base + i + 1)
        cells.extend(_unit_char_cells(ch, sp, ctx, profile))
    return cells


def _unit_char_cells(
    ch: str, span: Span | None, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Emit one unit character (e.g. ``k`` in ``kg``) as braille cells.

    Looks up the profile's letter table first (latin/greek + caps with
    the right script-class prefix), then the punctuation table, then
    warns and emits a blank unknown cell.
    """
    seq = profile.letter(ch)
    if seq is not None:
        return [
            BrailleCell(dots=dots, role="quantity_unit", source_span=span, source_text=ch)
            for dots in seq
        ]
    punct_cells = profile.punctuation.get(ch)
    if punct_cells:
        return [
            BrailleCell(dots=dots, role="punct", source_span=span, source_text=ch)
            for dots in punct_cells
        ]
    return [_unknown_cell(ch, span, ctx)]


def translate_date(node: Date, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Date → recurse into ``parts``.

    Each :class:`Number` part runs through the number-sign + digit
    pipeline. Each :class:`HanziMarker` (年/月/日/号/时/分/秒/…) is
    translated as a Chinese syllable through :mod:`backend.zh`. The
    backend stays language-agnostic: it relies on whatever
    :attr:`HanziMarker.reading` the frontend already attached. If
    pinyin is missing, backend/zh emits a ``MISSING_PINYIN`` warning
    and an unknown cell — guessing the reading is the frontend's job,
    not ours (the backend "no longer does language detection",
    ARCHITECTURE §12).

    A connector ⠤ is inserted before a marker that directly follows a
    Number — the same number→hanzi joiner the zh frontend applies to
    ``10页`` / ``3个`` (see
    :func:`brailix.frontend.zh.insert_cross_kind_boundary_spaces`) —
    because the digit cells would collide with the marker's leading
    cell (日's is ⠚, the same pattern as the digit 0, so ``17日`` would
    read as "170"). 年 is the lone exception: the NCB convention writes
    a year number directly against 年 with no joiner; 月/日/号/时/分/秒
    all take the connector.
    """
    from brailix.backend import zh as zh_backend  # local import to avoid cycle
    from brailix.ir.inline import HanziChar

    out: list[BrailleCell] = []
    prev: InlineNode | None = None
    for part in node.parts:
        if isinstance(part, Number):
            out.extend(_digits_to_cells(part.surface, part.span, ctx, profile))
        elif isinstance(part, HanziMarker):
            if isinstance(prev, Number) and part.surface != _DATE_CONNECTOR_EXEMPT:
                out.append(_connector_cell(part.span, profile))
            out.extend(
                zh_backend.translate_hanzi_char(
                    HanziChar(
                        surface=part.surface,
                        span=part.span,
                        reading=part.reading,
                    ),
                    ctx,
                    profile,
                )
            )
        prev = part
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _digits_to_cells(
    digits: str,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    cells: list[BrailleCell] = []
    base = span.start if span else 0
    span_at = (
        (lambda i: Span(base + i, base + i + 1)) if span is not None else (lambda _i: None)
    )
    emit_digit_run(
        cells,
        digits,
        profile=profile,
        warnings=ctx.warnings,
        roles=_NUMBER_ROLES,
        want_number_sign=profile.feature("number_sign", True),
        span_at=span_at,
        warn_source="backend.number",
        unknown_code="UNKNOWN_DIGIT",
        missing_code="MISSING_NUMBER_PART",
    )
    return cells


def _punct_cells(
    ch: str, span: Span | None, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Look ``ch`` up in the punctuation table and return one BrailleCell
    per cell in its mapping (may be empty if the char is unmapped)."""
    cells = profile.punctuation.get(ch)
    if not cells:
        return []
    return [
        BrailleCell(dots=dots, role="punct", source_span=span, source_text=ch)
        for dots in cells
    ]


def _connector_cell(span: Span | None, profile: BrailleProfile) -> BrailleCell:
    """One connector cell (⠤) for a number→marker boundary inside a Date.

    Mirrors :func:`brailix.backend.punct.translate_connector`'s cell —
    same ``profile.connector`` dots, ``role="connector"``, empty surface
    — but emitted straight from :func:`translate_date` because a Date
    bundles its Number / HanziMarker parts instead of separating them
    with :class:`~brailix.ir.inline.Connector` IR nodes. The span
    collapses to the boundary point (marker start = number end) so the
    synthetic cell never overlaps real source positions."""
    boundary = Span(span.start, span.start) if span else None
    return BrailleCell(
        dots=profile.connector,
        role="connector",
        source_span=boundary,
        source_text="",
    )


def _unknown_cell(
    ch: str,
    span: Span | None,
    ctx: BackendContext,
    *,
    code: str = "UNKNOWN_NUMBER_PART",
    message: str | None = None,
) -> BrailleCell:
    ctx.warnings.warn(
        code=code,
        message=message or f"no braille mapping for number-family char {ch!r}",
        surface=ch,
        span=span,
        source="backend.number",
    )
    return BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)


def _first_part_span(node) -> Span | None:
    if node.number and node.number.span:
        return node.number.span
    return node.span


def _last_char_span(node) -> Span | None:
    if node.span is None:
        return None
    return Span(node.span.end - 1, node.span.end)
