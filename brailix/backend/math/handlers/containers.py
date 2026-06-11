"""Container handlers for the math backend.

Covers the ``<math>`` root and ``<mrow>`` grouping element, plus the
``_emit_chem_children`` shim that hands a container's children to the chem
child emitter in chemistry mode. ``<mrow>`` also recognises the typed-slash
``a / b`` fraction shape and re-dispatches it through the fraction handler.

Cross-imports :func:`_emit_children_with_matrix` from :mod:`.matrices` and
:func:`_emit_typed_slash_fraction` from :mod:`.fractions`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.handlers.fractions import _emit_typed_slash_fraction
from brailix.backend.math.handlers.matrices import _emit_children_with_matrix
from brailix.backend.math.utils import _is_typed_slash_mrow
from brailix.ir.braille import BrailleCell


def _emit_chem_children(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """In chemistry mode, hand a container's children to the chem child
    emitter (per-molecule casing + leading chemical-formula indicator;
    coefficients / operators fall back to the maths paths)."""
    from brailix.backend.math import chem as _chem

    _chem.emit_children(cells, mctx, list(elem))


def _emit_math_root(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    if mctx.chem:
        _emit_chem_children(cells, mctx, elem)
        return
    _emit_children_with_matrix(cells, mctx, list(elem))


def _emit_mrow(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    if mctx.chem:
        _emit_chem_children(cells, mctx, elem)
        return
    kids = list(elem)
    # Typed-slash fraction: ``a / b`` — exactly three children with the
    # middle one a ``/`` operator. Treat as an mfrac-equivalent so the
    # backend renders it with the same compact (Antoine / slash) encoding
    # as ``\frac{a}{b}`` would receive. This routing happens in-place
    # (we don't rewrite the tree) by re-dispatching the two flanking
    # children through the fraction handler.
    if _is_typed_slash_mrow(elem):
        _emit_typed_slash_fraction(cells, mctx, kids[0], kids[2])
        return
    _emit_children_with_matrix(cells, mctx, kids)


_DISPATCH_PARTIAL = {
    "math": _emit_math_root,
    "mrow": _emit_mrow,
}
