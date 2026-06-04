"""Radical handlers for the math backend.

Covers ``<msqrt>`` (square root) and ``<mroot>`` (nth root, base + degree).

This module is a dispatch sink: it imports nothing from sibling handler
submodules.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.utils import _emit_structure
from brailix.ir.braille import BrailleCell


def _emit_msqrt(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    _emit_structure(cells, mctx, "sqrt.open", role="math_sqrt_open")
    _emit_structure(cells, mctx, "sqrt.indicator", role="math_sqrt_indicator")
    mctx.need_number_sign = True
    for child in list(elem):
        _emit_element(cells, mctx, child)
    _emit_structure(cells, mctx, "sqrt.close", role="math_sqrt_close")
    mctx.need_number_sign = True


def _emit_mroot(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """``<mroot>`` order is (base, degree). Output: ``sqrt.open + degree
    + sqrt.indicator + base + sqrt.close``."""
    kids = list(elem)
    base = kids[0] if len(kids) >= 1 else None
    degree = kids[1] if len(kids) >= 2 else None
    _emit_structure(cells, mctx, "sqrt.open", role="math_sqrt_open")
    if degree is not None:
        mctx.need_number_sign = True
        _emit_element(cells, mctx, degree)
    _emit_structure(cells, mctx, "sqrt.indicator", role="math_sqrt_indicator")
    mctx.need_number_sign = True
    if base is not None:
        _emit_element(cells, mctx, base)
    _emit_structure(cells, mctx, "sqrt.close", role="math_sqrt_close")
    mctx.need_number_sign = True


_DISPATCH_PARTIAL = {
    "msqrt": _emit_msqrt,
    "mroot": _emit_mroot,
}
