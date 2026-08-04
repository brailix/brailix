"""Script handlers for the math backend.

Covers the sub/superscript family — ``<msub>`` / ``<msup>`` /
``<msubsup>`` — and the under/over family — ``<munder>`` / ``<mover>`` /
``<munderover>``. The dispatch routes each to a regular script, a big-op
script (symbol or function base), the chem charge / subscript paths, or
(for under/over) the accent handler.

Cross-imports :func:`_emit_function_name` from :mod:`.leaves` and the
accent handler / predicates from :mod:`.accents`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.handlers.accents import (
    _emit_accent,
    _emit_accent_char,
    _is_accent_leaf,
    _is_accent_mark_node,
)
from brailix.backend.math.handlers.leaves import _emit_function_name
from brailix.backend.math.utils import (
    _emit_structure,
    _is_atomic,
    _is_digits_mn,
    _unpack_script,
    _unpack_under_over,
)
from brailix.ir.braille import BrailleCell

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET


def _emit_script_dispatch(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Route msub / msup / msubsup based on the base's role.

    big-op symbol base (``<mo>`` role=big_op) → big-op-script handler
    big-op function base (``<mi>`` flagged big_op) → big-op-function-script handler
    everything else → regular script handler

    In chemistry mode an upper script is an ion's charge (``<msup>``, or
    ``<msubsup>`` = subscript + charge) — chemistry has no mathematical
    exponents — and a pure subscript (``<msub>``) is a chemical subscript
    (no ``script.sub`` marker, lowered digit). Both route to the chem emitter.
    """
    base, sub, sup = _unpack_script(elem)
    if mctx.chem:
        from brailix.backend.math import chem as _chem

        if sup is not None:
            _chem.emit_charge(cells, mctx, base, sub, sup)
            return
        if sub is not None:
            _chem.emit_subscript(cells, mctx, base, sub)
            return
    _route_script(cells, mctx, elem, base, sub, sup)


def _emit_under_over_dispatch(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """``<munder>`` / ``<mover>`` / ``<munderover>``.

    Routes to the accent handler when ``accent="true"`` OR the over/under
    script is an accent character — either a single ``role=accent`` leaf
    or an accent-mark node (a vector arrow / bar that keeps ``role=rel``
    elsewhere). latex2mathml's ``accent`` attribute is unreliable
    (``\\bar`` / ``\\dot`` / ``\\tilde`` arrive as ``accent="false"``), so
    we recognise the accent by its character. Otherwise behaves
    structurally like msub/msup/msubsup (big-op limits, ordinary
    under/over scripts)."""
    if mctx.chem:
        # Chemistry: a connector (= / ⇌) carrying over/under reaction
        # conditions. Emit the connector + 46-prefix positioned conditions
        # (or the inline heat symbol). See backend.math.chem.
        from brailix.backend.math import chem as _chem

        _chem.emit_connector_with_conditions(cells, mctx, elem)
        return
    base, sub, sup = _unpack_under_over(elem)
    if (
        elem.get("accent") == "true"
        or _is_accent_leaf(mctx, sub)
        or _is_accent_leaf(mctx, sup)
        or _is_accent_mark_node(mctx, sub)
        or _is_accent_mark_node(mctx, sup)
    ):
        _emit_accent(cells, mctx, elem)
        return
    _route_script(cells, mctx, elem, base, sub, sup)


def _route_script(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    elem: ET.Element,
    base: ET.Element | None,
    sub: ET.Element | None,
    sup: ET.Element | None,
) -> None:
    profile = mctx.profile
    if base is None:
        # Degenerate: no base. Just emit whatever scripts exist with no
        # base, treating them as regular.
        _emit_regular_script(cells, mctx, base, sub, sup)
        return
    base_text = (base.text or "").strip()
    # Single-character big-op symbol (∑, ∫, ∮, ...).
    if (
        base.tag == "mo"
        and len(base_text) == 1
        and profile.math_symbol_role(base_text) == "big_op"
    ):
        _emit_big_op_script(cells, mctx, base, sub, sup)
        return
    # Multi-character function name used as a big-op base. MathML
    # producers vary on whether this is <mi>lim</mi> or <mo>lim</mo>;
    # we accept both (the functions table is keyed by the literal name,
    # and big_op_function_script handles the function-prefix wiring).
    if (
        base.tag in ("mi", "mo")
        and len(base_text) > 1
        and profile.math_function_big_op(base_text)
    ):
        _emit_big_op_function_script(cells, mctx, base, sub, sup)
        return
    _emit_regular_script(cells, mctx, base, sub, sup)


def _emit_regular_script(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    base: ET.Element | None,
    sub: ET.Element | None,
    sup: ET.Element | None,
) -> None:
    """Regular sub/sup: ``base + sub_marker + sub_content [+ close] +
    sup_marker + sup_content [+ close]`` — when both scripts are present
    the subscript is written first (x_1^2 → x ⠡⠂ ⠌⠆). The close marker is
    skipped only when the content is a bare number (``<mn>``) and the
    ``math.simplify_script`` feature is on; a *letter* keeps the close to
    bound the script (``a^n`` → ``a ⠌ n ⠱``), since unlike a digit run it
    carries no self-delimiting number context.

    All-digit script content is written in lower-form digits (no
    number_sign, no close marker) when ``math.atomic_script_lower_digit``
    is on — same rule big-op scripts use for their limits.

    **The script indicator does not break the base's letter run.** A
    structural marker normally does (:func:`_emit_structure` — the
    fraction bar between ``a`` and ``b`` must not let ``a/b`` share one
    sign), but the standard's letter-sign rule is "consecutive letters of
    the same class take one sign, on the first", and a base and its script
    letter are written consecutively: ``a_n`` is ``⠰⠁⠡⠝⠱``, one lowercase
    run, and ``a_{n,1}`` is ``⠰⠁⠡⠝⠐ ⠼⠁⠱`` — the subscript's ``n`` carries
    no sign of its own. So the base's run class is put back right after
    the indicator. It is restored again after the body so a following
    baseline letter still shares it (``a^2b`` is one run, matching
    ``ab^2``).
    """
    profile = mctx.profile
    simplify = profile.feature("math.simplify_script", True)
    if base is not None:
        _emit_element(cells, mctx, base)
        # The base is a baseline atom: when it's a single letter it joins the
        # surrounding letter run, so capture its run class to restore after
        # the (isolated) script body. A non-letter base leaves this ``None``.
        saved_run = mctx.letter_run_class
    else:
        saved_run = None
    if sub is not None:
        _emit_structure(cells, mctx, "script.sub", role="math_subscript")
        mctx.letter_run_class = saved_run
        if not _try_emit_atomic_lower_digit(cells, mctx, sub):
            mctx.need_number_sign = True
            _emit_element(cells, mctx, sub)
            if not (simplify and _is_atomic(sub)):
                _emit_structure(cells, mctx, "script.close", role="math_script_close")
    if sup is not None:
        if _is_accent_leaf(mctx, sup):
            # Postfix mark (prime ′) — NOT an exponent: skip the indicator
            # ⠌ and close; the mark's cells carry their own upper-right mark
            # (⠨⠔). So x' = x + ⠨⠔, not x + ⠌ + ⠨⠔. Order-wise it fills
            # the superscript slot like any sup: subscript first, mark after.
            _emit_accent_char(cells, mctx, (sup.text or "").strip())
        else:
            _emit_structure(cells, mctx, "script.sup", role="math_superscript")
            mctx.letter_run_class = saved_run
            if not _try_emit_atomic_lower_digit(cells, mctx, sup):
                mctx.need_number_sign = True
                _emit_element(cells, mctx, sup)
                if not (simplify and _is_atomic(sup)):
                    _emit_structure(cells, mctx, "script.close", role="math_script_close")
    # Restore the base's letter run across the (now-finished) script body, so
    # a following baseline letter shares the base's sign: ``a^2b`` is one
    # lowercase run, matching ``ab^2``. The sub/sup markers above already
    # cleared the run while the body was being emitted.
    mctx.letter_run_class = saved_run
    mctx.need_number_sign = True


def _emit_big_op_script(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    base: ET.Element,
    sub: ET.Element | None,
    sup: ET.Element | None,
) -> None:
    """big-op symbol base (∑ / ∏ / ∫ / ∮ / ⋃ / ⋂) with sub/sup limits."""
    profile = mctx.profile
    base_text = (base.text or "").strip()
    use_prefix = profile.math_symbol_script_prefix(base_text)
    # The base itself emits the symbol's cells via the regular mo path,
    # honouring its per-entry spacing.
    _emit_element(cells, mctx, base)
    if sub is not None:
        _emit_big_op_side(
            cells, mctx, sub, "script.sub", "math_subscript", use_prefix
        )
    if sup is not None:
        _emit_big_op_side(
            cells, mctx, sup, "script.sup", "math_superscript", use_prefix
        )
    # A big-op (∑ / ∫ / lim …) is a baseline atom: its limits never extend a
    # letter run onto a following baseline letter, so close the run here.
    mctx.break_letter_run()
    mctx.need_number_sign = True


def _emit_big_op_function_script(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    base: ET.Element,
    sub: ET.Element | None,
    sup: ET.Element | None,
) -> None:
    """big-op *function* base (``lim`` / ``max`` / ``min`` / ``sup`` /
    ``inf``) with sub/sup limits."""
    profile = mctx.profile
    name = (base.text or "").strip()
    use_prefix = profile.math_function_script_prefix(name)
    # Emit the function via the standard function path so the
    # function_prefix + name cells land in the stream.
    _emit_function_name(cells, mctx, name)
    if sub is not None:
        _emit_big_op_side(
            cells, mctx, sub, "script.sub", "math_subscript", use_prefix
        )
    if sup is not None:
        _emit_big_op_side(
            cells, mctx, sup, "script.sup", "math_superscript", use_prefix
        )
    # A big-op (∑ / ∫ / lim …) is a baseline atom: its limits never extend a
    # letter run onto a following baseline letter, so close the run here.
    mctx.break_letter_run()
    mctx.need_number_sign = True


def _emit_big_op_side(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    content: ET.Element,
    structure_name: str,
    indicator_role: str,
    use_prefix: bool,
) -> None:
    """One side (sub or sup) of a big-op script.

    Shape: optional ``script.big_op_prefix`` + indicator + content +
    ``script.close``. Single-digit content may use the Antoine
    lower-form digit (no number_sign, no close) when the
    ``math.atomic_script_lower_digit`` feature is on (same rule that
    governs regular scripts in :func:`_emit_regular_script`).
    """
    if use_prefix:
        _emit_structure(
            cells, mctx, "script.big_op_prefix", role="math_big_op_script_prefix"
        )
    _emit_structure(cells, mctx, structure_name, role=indicator_role)
    if _try_emit_atomic_lower_digit(cells, mctx, content):
        return
    mctx.need_number_sign = True
    _emit_element(cells, mctx, content)
    _emit_structure(cells, mctx, "script.close", role="math_script_close")


def _try_emit_atomic_lower_digit(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    content: ET.Element,
) -> bool:
    """If ``content`` is an all-digit ``<mn>`` and the profile opts in,
    emit it in lower-form digits and return True. Used by both regular
    scripts (``x^2``) and big-op scripts (``\\int_0^1``) — same rule: a
    script that is a plain number drops the number sign and the close
    marker, written as "lowered" digit cells instead.

    **Every digit of it**, not just a one-digit script. The standard drops
    the number sign and lowers the digits for a subscript that is a
    non-negative number carrying no comma, and for an exponent that is an
    integer; a determinant's elements are written ``a₁₁`` → ``⠡⠂⠂`` and
    ``a₁₂`` → ``⠡⠂⠆``, two lowered digits each. This used to stop at one
    digit, so ``a₁₁`` came out ``⠡⠼⠁⠁`` — number sign and upper digits —
    while ``a₁`` beside it was correctly ``⠡⠂``.

    Returns False for anything that is not a bare run of digits (``x^{n}``,
    ``x^{n+1}``, a negative or non-integer exponent), which keeps the
    ordinary indicator + content + close path.
    """
    if not mctx.profile.feature("math.atomic_script_lower_digit", False):
        return False
    if not _is_digits_mn(content):
        return False
    text = (content.text or "").strip()
    lowered = [mctx.profile.math_digits_lower.get(d) for d in text]
    if not all(lowered):
        # A profile whose lower-digit table is incomplete: fall back whole
        # rather than emitting half the number in one form and half in the
        # other, which would read as two different numbers.
        return False
    for digit, dots in zip(text, lowered, strict=True):
        assert dots is not None
        cells.append(
            BrailleCell(
                dots=dots,
                role="math_digit_lower",
                source_span=mctx.span,
                source_text=digit,
            )
        )
    mctx.need_number_sign = False
    return True


_DISPATCH_PARTIAL = {
    "msub": _emit_script_dispatch,
    "msup": _emit_script_dispatch,
    "msubsup": _emit_script_dispatch,
    "munder": _emit_under_over_dispatch,
    "mover": _emit_under_over_dispatch,
    "munderover": _emit_under_over_dispatch,
}
