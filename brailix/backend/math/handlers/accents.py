"""Accent handlers for the math backend.

Covers the ``accent="true"`` variants of ``<munder>`` / ``<mover>`` /
``<munderover>`` — overbars, dots, tildes, primes, and vector marks —
reached from :mod:`.scripts` once the under/over dispatch recognises an
accent. Also exposes the accent-recognition predicates (``_is_accent_*``)
that the dispatch consults.

This module is a dispatch sink: it imports nothing from sibling handler
submodules and contributes no top-level tags.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.utils import (
    _emit_structure,
    _unknown_cell,
    _unpack_under_over,
)
from brailix.ir.braille import BrailleCell


def _is_accent_leaf(mctx: MathBrailleContext, node: ET.Element | None) -> bool:
    """True if ``node`` is a single-character ``<mo>`` / ``<mi>`` whose
    char is a ``role=accent`` symbol (prime ′, bar ¯, dot ˙, tilde ˜, ...).

    Lets the backend recognise an accent independent of latex2mathml's
    unreliable ``accent="true"`` attribute (``\\bar`` / ``\\dot`` /
    ``\\tilde`` come through as ``accent="false"``), and treat a
    prime-as-superscript as a postfix mark rather than an exponent.
    """
    if node is None or node.tag not in ("mo", "mi") or list(node):
        return False
    text = (node.text or "").strip()
    if len(text) != 1:
        return False
    return mctx.profile.math_symbol_role(text) == "accent"


def _is_accent_mark_node(
    mctx: MathBrailleContext, node: ET.Element | None
) -> bool:
    """True if ``node`` is a single-character ``<mo>`` / ``<mi>`` carrying
    an ``accent_mark`` tag (→ / ← for ``\\vec`` / ``\\overrightarrow``, ¯ /
    ― / ‾ for ``\\bar`` / ``\\overline``).

    Lets the under/over dispatch route a vector arrow to the accent
    handler even though → keeps its global ``role=rel`` — an ordinary
    relation arrow outside an accent position is unaffected."""
    if node is None or node.tag not in ("mo", "mi") or list(node):
        return False
    text = (node.text or "").strip()
    if len(text) != 1:
        return False
    return mctx.profile.math_accent_mark_kind(text) is not None


def _accent_base_is_multi(base: ET.Element | None) -> bool:
    """True when the accent base spans ≥2 letters — the docx two-letter form
    (short bar ⠒⠒ / arrow sign ⠒⠆) vs the single-letter form (⠒ / ⠒⠂). Counts
    alphabetic characters in the base text so ``\\vec{v}`` is single and
    ``\\overrightarrow{AB}`` is double."""
    if base is None:
        return False
    letters = sum(
        1 for ch in "".join(t or "" for t in base.itertext()) if ch.isalpha()
    )
    return letters >= 2


def _emit_accent(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Accent handler for ``<mover>`` / ``<munder>`` / ``<munderover>``
    (reached via :func:`_emit_under_over_dispatch` on ``accent="true"`` or
    a recognised ``role=accent`` over/under char).

    Shape per the annotation section of 《盲文常用数学符号》: ``base`` +
    direction marker + mark symbol. The direction marker is emitted by the
    backend — ``accent.over`` (directly above ⠘) for an over-script,
    ``accent.under`` (directly below ⠰) for an under-script — and the accent
    symbol's own cells come from its ``role=accent`` entry in ``symbols.json``
    (horizontal bar ⠒ / tilde ⠢ / dot ⠂ ...). Unknown accent chars fall back
    to an ``unknown`` cell with a ``MATH_UNKNOWN_SYMBOL`` warning.
    """
    base, under, over = _unpack_under_over(elem)
    if base is not None:
        _emit_element(cells, mctx, base)
    # single-letter vs two-letter form is a property of the base width,
    # shared by both sides (docx §15).
    multi = _accent_base_is_multi(base)
    _emit_accent_side(cells, mctx, under, "accent.under", multi)
    _emit_accent_side(cells, mctx, over, "accent.over", multi)
    mctx.need_number_sign = True


def _emit_accent_side(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    node: ET.Element | None,
    marker: str,
    multi: bool = False,
) -> None:
    """Emit one accent side: direction marker (``accent.over`` /
    ``accent.under``) followed by the accent character(s). No-op when the
    node is absent or carries no text — an empty accent emits nothing
    (not even the marker). ``multi`` selects the two-letter vector-mark form."""
    if node is None:
        return
    text = (node.text or "").strip()
    if not text:
        text = "".join(t or "" for t in node.itertext()).strip()
    if not text:
        return
    _emit_structure(cells, mctx, marker, role="math_accent_prefix")
    for ch in text:
        _emit_accent_char(cells, mctx, ch, multi)


def _emit_accent_char(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    ch: str,
    multi: bool = False,
) -> None:
    """Emit one accent character through the standard lookup chain.

    A char tagged with an ``accent_mark`` kind (→ / ← / ¯ / ― / ‾) is a
    vector mark: its cells come from ``accent.mark.<kind>.{single,double}``
    in the structures table — the double form when the base spans ≥2
    letters (``multi``). Everything else falls through to the ordinary
    symbol / letter / punctuation chain (prime ′, dot ˙, tilde ~, ...)."""
    profile = mctx.profile
    # Vector marks (→/←/¯/―/‾): structure-backed single/double form,
    # consulted before the global symbol table so → renders as the arrow sign
    # ⠒⠂ here rather than the relation arrow ⠒⠕ it is everywhere else.
    kind = profile.math_accent_mark_kind(ch)
    if kind is not None:
        form = "double" if multi else "single"
        for dots in profile.math_structure(f"accent.mark.{kind}.{form}"):
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_accent",
                    source_span=mctx.span,
                    source_text=ch,
                )
            )
        return
    # symbol table first (role=accent / op / etc.)
    cells_for = profile.math_symbol(ch)
    if cells_for is not None:
        for dots in cells_for:
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_accent",
                    source_span=mctx.span,
                    source_text=ch,
                )
            )
        return
    # letter table (in case the accent char happens to be a letter,
    # e.g. some standards encode ̂ as a letter-prefix variant).
    letter_seq = profile.letter(ch)
    if letter_seq is not None:
        for dots in letter_seq:
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_accent",
                    source_span=mctx.span,
                    source_text=ch,
                )
            )
        return
    # punctuation fallback.
    punct_seq = profile.punctuation.get(ch)
    if punct_seq:
        for dots in punct_seq:
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_accent",
                    source_span=mctx.span,
                    source_text=ch,
                )
            )
        return
    # Unknown: warn + unknown cell. Use the same warning code as other
    # unknown math symbols so consumers don't have to special-case.
    mctx.backend.warnings.warn(
        code="MATH_UNKNOWN_SYMBOL",
        message=f"no braille mapping for accent character {ch!r}",
        surface=ch,
        span=mctx.span,
        source="backend.math",
    )
    cells.append(_unknown_cell(ch, mctx.span))
