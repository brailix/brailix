"""Fraction handlers for the math backend.

Covers ``<mfrac>`` (the three Chinese-braille fraction shapes — Antoine /
simplified-bar / compound), the Antoine single-digit detector, and the
typed-slash ``a / b`` path that :mod:`.containers` re-dispatches an
``<mrow>`` into when it looks like a fraction.

This module is a dispatch sink: it imports nothing from sibling handler
submodules.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.utils import (
    _emit_structure,
    _is_single_digit_mn,
    _is_single_structure,
    _last_is_blank,
)
from brailix.ir.braille import BLANK_CELL, BrailleCell


def _emit_mfrac(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Chinese math braille fraction.

    Three shapes, decided here on the fly (no IR-level annotation):

      1. *Antoine* — both operands are atomic single-digit ``<mn>``.
         Output: ``number_sign + upper_digit + lower_digit`` (the
         lower-form digit implies the bar; no open/close).
      2. *Simple* — both operands are single self-fenced structures
         (mi / mn / msqrt / mfrac / msub / msup / ...) and the feature
         ``math.simplify_fraction`` is on. Output: ``numerator +
         fraction.bar + denominator``. The inner structure carries its
         own closing marker (sqrt.close, script.close, Antoine lower
         digit, nested fraction.close), so the bar's position is
         unambiguous without explicit open/close brackets.
      3. *Compound* — anything else (multi-token mrow numerator /
         denominator). Output: ``fraction.open + numerator + blank +
         fraction.bar + denominator + fraction.close``.
    """
    kids = list(elem)
    numerator = kids[0] if len(kids) >= 1 else None
    denominator = kids[1] if len(kids) >= 2 else None
    profile = mctx.profile

    if _try_emit_antoine_fraction(cells, mctx, numerator, denominator):
        mctx.need_number_sign = True
        return

    simplifiable = (
        _is_single_structure(numerator)
        and _is_single_structure(denominator)
        and profile.feature("math.simplify_fraction", True)
    )

    if not simplifiable:
        _emit_structure(cells, mctx, "fraction.open", role="math_fraction_open")
        mctx.need_number_sign = True
    if numerator is not None:
        _emit_element(cells, mctx, numerator)
    if not simplifiable and not _last_is_blank(cells):
        cells.append(BLANK_CELL)
    _emit_structure(cells, mctx, "fraction.bar", role="math_fraction_bar")
    mctx.need_number_sign = True
    if denominator is not None:
        _emit_element(cells, mctx, denominator)
    if not simplifiable:
        _emit_structure(cells, mctx, "fraction.close", role="math_fraction_close")
    mctx.need_number_sign = True


def _try_emit_antoine_fraction(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    numerator: ET.Element | None,
    denominator: ET.Element | None,
) -> bool:
    """If numerator + denominator are both single-digit ``<mn>``, emit
    the Antoine compact form and return True. Else return False.
    """
    if numerator is None or denominator is None:
        return False
    if not _is_single_digit_mn(numerator):
        return False
    if not _is_single_digit_mn(denominator):
        return False
    profile = mctx.profile
    num_text = (numerator.text or "").strip()
    den_text = (denominator.text or "").strip()
    upper = profile.digits.get(num_text)
    lower = profile.math_digits_lower.get(den_text)
    if not upper or not lower:
        return False
    if (
        mctx.need_number_sign
        and profile.feature("math.number_sign", True)
        and profile.number_sign
    ):
        cells.append(
            BrailleCell(
                dots=profile.number_sign,
                role="number_sign",
                source_span=mctx.span,
            )
        )
    cells.append(
        BrailleCell(
            dots=upper,
            role="math_digit",
            source_span=mctx.span,
            source_text=num_text,
        )
    )
    cells.append(
        BrailleCell(
            dots=lower,
            role="math_digit_lower",
            source_span=mctx.span,
            source_text=den_text,
        )
    )
    mctx.need_number_sign = False
    return True


def _emit_typed_slash_fraction(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    numerator: ET.Element,
    denominator: ET.Element,
) -> None:
    """Emit ``a / b`` (literal slash from a typed mrow) as if it were
    ``<mfrac>``. Lets Antoine / simplified-bar encoding fire when both
    operands are single self-fenced structures."""
    profile = mctx.profile
    if _try_emit_antoine_fraction(cells, mctx, numerator, denominator):
        mctx.need_number_sign = True
        return
    simplifiable = (
        _is_single_structure(numerator)
        and _is_single_structure(denominator)
        and profile.feature("math.simplify_fraction", True)
    )
    if not simplifiable:
        _emit_structure(cells, mctx, "fraction.open", role="math_fraction_open")
        mctx.need_number_sign = True
    _emit_element(cells, mctx, numerator)
    if not simplifiable and not _last_is_blank(cells):
        cells.append(BLANK_CELL)
    _emit_structure(cells, mctx, "fraction.bar", role="math_fraction_bar")
    mctx.need_number_sign = True
    _emit_element(cells, mctx, denominator)
    if not simplifiable:
        _emit_structure(cells, mctx, "fraction.close", role="math_fraction_close")
    mctx.need_number_sign = True


_DISPATCH_PARTIAL = {
    "mfrac": _emit_mfrac,
}
