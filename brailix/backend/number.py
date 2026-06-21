"""Translate number-family IR nodes into braille cells.

Covers :class:`Number`, :class:`Date`, :class:`Percent`,
:class:`Quantity`. Uses the profile's ``digits`` / ``number_sign`` /
``decimal_point`` / ``thousands_sep`` / ``punctuation`` tables.

A number-sign cell is prepended whenever a digit run starts a new
braille "phrase". For now we emit it before every numeric token;
context-aware suppression (e.g. "still inside a number") arrives with
:class:`BackendContext` plumbing.

Language scope: every node here is language-agnostic. Number / Percent /
Quantity only touch the profile's digit / punctuation / letter tables.
:func:`translate_date` owns just the language-neutral skeleton (the
numeric components and the blank that separates them) and delegates each
date marker (年/月/日…) to the profile language's
``LanguageBackend.translate_date_marker``, resolved through the registry
rather than a hard import. That backend owns the marker reading and the
connector rule (Chinese exempts the year marker 年), so no per-language
date rule lives in this module (ARCHITECTURE §7.6 / §12).
"""

from __future__ import annotations

from brailix.backend._digits import DigitRoles, emit_digit_run
from brailix.backend._letters import iter_letter_runs, letter_sign_repeats
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import Date, HanziMarker, InlineNode, Number, Percent, Quantity

# Role labels for prose number digit runs (the math backend uses
# "math_digit"); the shared emitter handles the rest.
_NUMBER_ROLES = DigitRoles(digit="digit")

# ---------------------------------------------------------------------------
# Public entry points (one per IR node type)
# ---------------------------------------------------------------------------


def translate_number(node: Number, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Number → [number_sign?, digit_cells...]"""
    return _digits_to_cells(node.surface, node.span, ctx, profile)


def translate_percent(node: Percent, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Percent → digits + percent punctuation."""
    if not node.surface:
        # Empty surface — the frontend never builds one, but a hand-rolled
        # node / IR round-trip could; guard the [-1] index like the other
        # number translators do.
        return []
    cells = _digits_to_cells(node.surface[:-1], _first_part_span(node), ctx, profile)
    last_char = node.surface[-1]
    last_span = _last_char_span(node)
    tail = _punct_cells(last_char, last_span, ctx, profile)
    if not tail:
        # The last char is meant to be the percent sign. If it isn't in the
        # punctuation table (a hand-rolled / round-tripped Percent whose
        # surface doesn't end in '%'), don't drop it silently — warn and emit
        # an unknown cell, the same fail-loud behaviour the digit pipeline
        # gives an unknown digit.
        tail = [_unknown_cell(last_char, last_span, ctx)]
    cells.extend(tail)
    return cells


def translate_quantity(node: Quantity, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Quantity → digits + unit letters.

    Unit letters follow the letter-sign run rule: consecutive letters of
    the same script class
    share one ``letter_prefix.*`` sign — ``47cm`` → ⠼⠙⠛⠰⠉⠍, one ⠰
    for the whole ``cm``. A class change starts a new sign, which is
    what keeps mixed-case units lossless (``mW`` → ⠰⠍⠠⠺); an
    all-capital run of ≥ 2 letters doubles the capital sign (``MW`` →
    ⠠⠠⠍⠺). Characters absent from the letter tables fall back to the
    punctuation table, and only emit an :class:`Unknown` cell with a
    ``UNKNOWN_NUMBER_PART`` warning when both lookups miss.
    """
    digits_part = node.number.surface if node.number else ""
    unit_part = node.unit or ""
    cells = _digits_to_cells(digits_part, node.number.span if node.number else None, ctx, profile)
    # Unit chars start right after the number's *source* span. Deriving the
    # base from ``span.start + len(digits_part)`` would drift whenever the
    # digit surface was normalized (thousands separators stripped, fullwidth
    # folded to half) and so no longer matches the source character count.
    if node.number and node.number.span:
        base = node.number.span.end
    elif node.span:
        base = node.span.start + len(digits_part)
    else:
        base = 0
    pos = 0
    for cls, run in iter_letter_runs(unit_part, profile):
        if cls is None:
            sp = Span(base + pos, base + pos + 1)
            cells.extend(_unit_char_cells(run, sp, ctx, profile))
            pos += len(run)
            continue
        first_sp = Span(base + pos, base + pos + 1)
        prefix = profile.math_structure(f"letter_prefix.{cls}")
        for _ in range(letter_sign_repeats(cls, len(run))):
            cells.extend(
                BrailleCell(dots=dots, role="quantity_unit", source_span=first_sp, source_text=run)
                for dots in prefix
            )
        for ch in run:
            sp = Span(base + pos, base + pos + 1)
            bare = profile.bare_letter(ch)
            if bare is not None:  # always true: letter_class hit this table
                cells.append(
                    BrailleCell(dots=bare, role="quantity_unit", source_span=sp, source_text=ch)
                )
            pos += 1
    return cells


def _unit_char_cells(
    ch: str, span: Span | None, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Emit one non-letter unit character (``°`` in ``°C``, ``/`` in
    ``km/h``) as braille cells.

    Letters are handled by the run-based caller; this fallback looks up
    the punctuation table, then warns and emits a blank unknown cell.
    (The letter lookup stays first for callers/tests that feed a letter
    directly.)
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
    """Date → language-neutral numeric skeleton + delegated markers.

    The Date is the one number-family node with a language-specific part:
    its :class:`HanziMarker` components (年/月/日/号/时/分/秒/…) carry a
    reading and an orthographic connector rule. This function owns only
    the **language-neutral skeleton** — each :class:`Number` component
    runs through the number-sign + digit pipeline, and a word-boundary
    blank separates components (``2026年 5月 17日``, not ``2026年5月17日``) —
    and delegates every marker to the profile language's
    ``LanguageBackend.translate_date_marker`` (resolved through the
    registry, **not** a hard import), which owns the marker reading and
    the connector rule. So no per-language date rule lives in this
    language-neutral module (ARCHITECTURE §7.6 / §12).

    ``follows_number=True`` is passed when the marker directly follows a
    Number, so the language backend can decide whether a connector ⠤
    binds the digits to the marker — e.g. 日's leading cell ⠚ matches the
    digit 0, so ``17日`` needs the joiner to avoid reading as "170"; the
    Chinese backend exempts 年. A missing marker reading degrades to a
    warning + unknown cell inside that backend, never a crash.
    """
    # Local import to avoid the dispatch ↔ number import cycle; the marker
    # translator is resolved by the profile's language, never hard-wired
    # to one language backend.
    from brailix.backend.dispatch import language_backend_registry

    lang = profile.language.split("-")[0]
    backend = (
        language_backend_registry.get(lang)
        if language_backend_registry.has(lang)
        else None
    )

    out: list[BrailleCell] = []
    prev: InlineNode | None = None
    for part in node.parts:
        if isinstance(part, Number):
            if isinstance(prev, HanziMarker):
                # A space separates date components: 年 / 5月 / 17日 are
                # distinct written units, so the number that starts the
                # next component takes a word-boundary blank after the
                # previous marker (年 5月 17日, not 年5月17日).
                out.append(_component_space_cell(part.span))
            out.extend(_digits_to_cells(part.surface, part.span, ctx, profile))
        elif isinstance(part, HanziMarker):
            if backend is None:
                out.append(
                    _unknown_cell(
                        part.surface,
                        part.span,
                        ctx,
                        code="NO_LANGUAGE_BACKEND",
                        message=f"no backend registered for language {lang!r}",
                    )
                )
            else:
                out.extend(
                    backend.translate_date_marker(
                        part, isinstance(prev, Number), ctx, profile
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
        # Full-width digits are routine typography in CJK prose — fold.
        fold_nonascii=True,
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


def _component_space_cell(span: Span | None) -> BrailleCell:
    """One blank cell separating two date components (年 / 5月 / 17日).

    A word-boundary space, emitted straight from :func:`translate_date`
    (a Date bundles its parts rather than separating them with IR
    nodes). The span collapses to the boundary point so the synthetic
    cell never overlaps real source positions."""
    boundary = Span(span.start, span.start) if span else None
    return BrailleCell(dots=(), role="space", source_span=boundary, source_text="")


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
