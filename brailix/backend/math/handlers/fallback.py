"""Error / unsupported fall-through handlers for the math backend.

* :func:`_emit_merror` — surfaces a frontend ``<merror>`` as a warning plus
  one unknown cell so the failure is visible in the output.
* :func:`_emit_unsupported` — catch-all warning for tags the dispatch table
  doesn't know about; also serves as the table's default value, re-exported
  by the ``handlers`` package so :mod:`brailix.backend.math.dispatch` can use
  it directly.

This module is a dispatch sink: it imports nothing from sibling handler
submodules.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.utils import _describe_nonstandard_char, _unknown_cell
from brailix.ir.braille import BrailleCell

# Cap on how much of an unsupported element's serialized subtree is copied
# into the warning surface. A bare <mtable> can serialize to many kilobytes;
# the surface only needs to identify *what* was dropped, not reproduce it.
_SURFACE_MAX = 200


def _emit_merror(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    if list(elem):
        # Concatenate text from any direct children (mtext, etc.).
        surface = "".join(t or "" for t in elem.itertext()).strip()
    else:
        surface = (elem.text or "").strip()
    if elem.get("data-bk-soft") == "1":
        # Soft, in-place degrade (e.g. the chem parser flagging one stray
        # full-width / zero-width / unsupported character): warn rather than
        # error — the surrounding formula translated fine, only this cell is
        # blank. The message names the writing problem so it's actionable.
        mctx.backend.warnings.warn(
            code="MATH_NONSTANDARD_CHAR",
            message=_describe_nonstandard_char(surface),
            surface=surface or None,
            span=mctx.span,
            source="backend.math",
        )
    else:
        reason = elem.get("data-reason", "merror")
        mctx.backend.warnings.error(
            code="MATH_ERROR",
            message=f"<merror>: {reason}",
            surface=surface,
            span=mctx.span,
            source="backend.math",
        )
    cells.append(_unknown_cell(surface or "?", mctx.span))
    mctx.need_number_sign = True


def _emit_unsupported(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    serialized = ET.tostring(elem, encoding="unicode")
    if len(serialized) > _SURFACE_MAX:
        # Truncate large subtrees (e.g. a whole <mtable>) so the warning
        # surface stays bounded; the tag is already in the message.
        surface = serialized[:_SURFACE_MAX] + "…"
    else:
        surface = serialized
    mctx.backend.warnings.error(
        code="MATH_UNSUPPORTED_ELEMENT",
        message=f"unsupported math element <{elem.tag}>",
        surface=surface,
        span=mctx.span,
        source="backend.math",
    )
    cells.append(_unknown_cell(elem.tag, mctx.span))
    mctx.need_number_sign = True


_DISPATCH_PARTIAL = {
    "mtr": _emit_unsupported,
    "mtd": _emit_unsupported,
    "merror": _emit_merror,
}
