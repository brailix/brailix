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
    _antoine_applies,
    _emit_structure,
    _fraction_simplifiable,
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
         (mi / mn / msqrt / msub / msup / Antoine-or-compound mfrac / ...)
         and the feature ``math.simplify_fraction`` is on. Output:
         ``numerator + fraction.bar + denominator``. The inner structure
         carries its own closing marker (sqrt.close, script.close, Antoine
         lower digit, compound fraction.close), so the bar's position is
         unambiguous without explicit open/close brackets. **A nested
         *simple* fraction is NOT self-fenced** (it's just a bare bar), so
         an operand like that forces the compound form below — otherwise
         ``\\frac{\\frac{a}{b}}{c}`` and ``\\frac{a}{\\frac{b}{c}}`` would
         both flatten to the same ambiguous ``a/b/c`` chain.
      3. *Compound* — anything else (multi-token mrow operand, or a nested
         simple fraction operand). Output: ``fraction.open + numerator +
         blank + fraction.bar + denominator + fraction.close``.

    Two function-name interactions refine the choice between 2 and 3:

    * An operand that is a *function application* (``cos α``) still
      counts as a single term, so ``\\frac{\\cos α}{a}`` keeps the simple
      bar form (see ``_is_function_application``).
    * A fraction in *function-argument* position (``\\cos \\frac{α}{a}``
      — the sibling walker raised ``mctx.fraction_is_function_arg``)
      must take the compound form even when its operands are atomic;
      without the ⠆…⠰ brackets it would collapse into the same cells as
      the simple form of (cos α)/a. The Antoine shape stays exempt: its
      lower-form digit makes ⠼⠁⠆ one self-delimiting number token, so
      ``cos`` of 1/2 is unambiguous without brackets.
    """
    kids = list(elem)
    numerator = kids[0] if len(kids) >= 1 else None
    denominator = kids[1] if len(kids) >= 2 else None
    profile = mctx.profile
    # One-shot context flag: read and clear at entry so it never leaks
    # into operand sub-emission (a nested fraction inside the numerator
    # must not inherit it).
    force_compound = mctx.fraction_is_function_arg
    mctx.fraction_is_function_arg = False

    if _try_emit_antoine_fraction(cells, mctx, numerator, denominator):
        mctx.need_number_sign = True
        return

    simplifiable = not force_compound and _fraction_simplifiable(
        numerator, denominator, profile
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
    profile = mctx.profile
    if not _antoine_applies(numerator, denominator, profile):
        return False
    assert numerator is not None and denominator is not None  # _antoine_applies
    num_text = (numerator.text or "").strip()
    den_text = (denominator.text or "").strip()
    upper = profile.digits.get(num_text)
    lower = profile.math_digits_lower.get(den_text)
    assert upper is not None and lower is not None  # guaranteed by _antoine_applies
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
    operands are single self-fenced structures. Honours the same
    function-argument flag as ``_emit_mfrac`` — ``cos`` directly before
    a typed ``a / b`` forces the compound form."""
    profile = mctx.profile
    force_compound = mctx.fraction_is_function_arg
    mctx.fraction_is_function_arg = False
    if _try_emit_antoine_fraction(cells, mctx, numerator, denominator):
        mctx.need_number_sign = True
        return
    simplifiable = not force_compound and _fraction_simplifiable(
        numerator, denominator, profile
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
