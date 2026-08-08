"""Translate number-family IR nodes into braille cells.

Covers :class:`Number` and :class:`Date`. Uses the profile's ``digits`` /
``number_sign`` / ``decimal_point`` / ``thousands_sep`` tables.

A number-sign cell is prepended whenever a digit run starts a new
braille "phrase". For now we emit it before every numeric token;
context-aware suppression (e.g. "still inside a number") is future
work.

Language scope: every node here is language-agnostic. Number
only touches the profile's digit table.
:func:`translate_date` owns just the language-neutral skeleton (the
numeric components and the blank that separates them) and delegates each
date marker (年/月/日…) to the profile language's
``LanguageBackend.translate_date_marker``, resolved through the registry
rather than a hard import. That backend owns the marker reading and the
connector rule (Chinese exempts the year marker 年), so no per-language
date rule lives in this module (ARCHITECTURE#arch-language-slots / #arch-boundaries).
"""

from __future__ import annotations

from brailix.backend._digits import (
    DigitRoles,
    DigitRunPolicy,
    emit_digit_run,
)
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import Date, Number

# Role labels for prose number digit runs (the math backend uses
# "math_digit"); the shared emitter handles the rest.
_NUMBER_ROLES = DigitRoles(digit="digit")
_NUMBER_DIGIT_POLICY = DigitRunPolicy(
    roles=_NUMBER_ROLES,
    # Full-width digits are routine typography in CJK prose — fold.
    fold_nonascii=True,
    warn_source="backend.number",
    unknown_code="UNKNOWN_DIGIT",
    missing_code="MISSING_NUMBER_PART",
)

# ---------------------------------------------------------------------------
# Module entry points (one per IR node type)
# ---------------------------------------------------------------------------


def translate_number(node: Number, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Number → [number_sign?, digit_cells...]"""
    return _digits_to_cells(node.surface, node.span, ctx, profile)


def translate_date(node: Date, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]:
    """Date → language-neutral numeric skeleton + delegated markers.

    The Date is the one number-family node with a language-specific part:
    each :class:`~brailix.ir.inline.DateComponent`'s marker (年/月/日/号/时/分/秒/…)
    carries a reading and an orthographic connector rule. This function owns
    only the **language-neutral skeleton** — each component's digits run
    through the number-sign + digit pipeline, and a word-boundary
    blank separates components (``2026年 5月 17日``, not ``2026年5月17日``) —
    and delegates every marker to the profile language's
    ``LanguageBackend.translate_date_marker`` (resolved through the
    registry, **not** a hard import), which owns the marker reading and
    the connector rule. So no per-language date rule lives in this
    language-neutral module (ARCHITECTURE#arch-language-slots / #arch-boundaries).

    Whether a connector ⠤ binds the digits to the marker is that backend's
    call, read off the component's own digits — e.g. 日's leading cell ⠚
    matches the digit 0, so ``17日`` needs the joiner to avoid reading as
    "170"; the Chinese backend exempts 年. A missing marker reading degrades to
    a warning + unknown cell inside that backend, never a crash.
    """
    # Local import to avoid the dispatch ↔ number import cycle; the marker
    # translator is resolved by the profile's language, never hard-wired
    # to one language backend. The traceability post-condition comes from
    # there too, rather than being re-implemented here: this is the second
    # boundary a third-party LanguageBackend is called across, and it has to
    # be held to the same contract as the first.
    from brailix.backend.dispatch import (
        _enforce_source_spans,
        language_backend_registry,
    )

    lang = profile.language.split("-")[0]
    backend = (
        language_backend_registry.get(lang)
        if language_backend_registry.has(lang)
        else None
    )

    out: list[BrailleCell] = []
    for i, component in enumerate(node.components):
        if i:
            # A space separates date components: 2026年 / 5月 / 17日 are
            # distinct written units, so every component after the first
            # opens with a word-boundary blank (年 5月 17日, not 年5月17日).
            out.append(_component_space_cell(component.digits_span))
        out.extend(
            _digits_to_cells(
                component.digits, component.digits_span, ctx, profile
            )
        )
        if component.marker is None:
            continue
        if backend is None:
            out.append(
                _unknown_cell(
                    component.marker,
                    component.marker_span,
                    ctx,
                    code="NO_LANGUAGE_BACKEND",
                    message=f"no backend registered for language {lang!r}",
                )
            )
        else:
            out.extend(
                _enforce_source_spans(
                    backend.translate_date_marker(component, ctx, profile),
                    component,
                    f"language backend {lang!r} (translate_date_marker)",
                )
            )
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
        policy=_NUMBER_DIGIT_POLICY,
        want_number_sign=profile.feature("zh.number_sign", True),
        span_at=span_at,
        # The number sign has no surface char; anchor it to the run's leading
        # edge (a zero-width span) so it traces back to source without landing
        # in the compact source-text override map. Without any span it was the
        # one number cell with no provenance at all.
        number_sign_span=Span(span.start, span.start) if span else None,
    )
    return cells


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
    code: str,
    message: str,
) -> BrailleCell:
    ctx.warnings.warn(
        code=code,
        message=message,
        surface=ch,
        span=span,
        source="backend.number",
    )
    return BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)
