"""Tag-based dispatcher for MathML trees.

The handler table lives in :mod:`.handlers` to avoid a circular import:
each handler needs :func:`_emit_element` for recursive descent, and the
table needs the handlers. The shared
:class:`~brailix.core.dispatch.LazyTagDispatcher` resolves the cycle by
loading the table lazily on first call.

This module also owns ``data-bk-*`` attribute interpretation, but only
for the two attributes read here in dispatch:
``data-bk-span`` custom span overrides that flow onto every
:class:`BrailleCell` constructed inside the handler's run, and
``data-bk-chem`` which switches :class:`MathBrailleContext` into
chemistry mode for the duration of the subtree (set by the mhchem
``\\ce`` adapter; see ``ARCHITECTURE.md`` and
:mod:`brailix.backend.math.chem`).

A third attribute, ``data-bk-chem-state`` (physical-state labels like
``(aq)``), is *not* read here — it is consumed solely by the ``<mtext>``
leaf handler in :mod:`brailix.backend.math.handlers.leaves`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.utils import _parse_bk_span
from brailix.core.dispatch import Handler, LazyTagDispatcher
from brailix.ir.braille import BrailleCell


def _load() -> tuple[dict[str, Handler], Handler]:
    # Lazy: by the time the first element is dispatched, handlers.py has
    # fully imported, so this breaks the dispatch.py <-> handlers.py
    # module-load cycle.
    from brailix.backend.math import handlers as _handlers

    return _handlers._DISPATCH, _handlers._emit_unsupported


_dispatcher = LazyTagDispatcher(_load)


def _emit_element(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Dispatch one element to its tag-specific handler.

    Wraps the call in two attribute overrides, both restored on exit:

    * ``data-bk-span="start,end"`` pushes that span as the "current" one
      so every :class:`BrailleCell` constructed during the handler's run
      inherits it (see ``ARCHITECTURE.md``);
    * ``data-bk-chem`` turns on :attr:`MathBrailleContext.chem` so the
      handler tree renders with chemistry rules (the mhchem ``\\ce``
      adapter sets it on the ``<math>`` root; see
      ``ARCHITECTURE.md``).
    """
    handler = _dispatcher.resolve(elem.tag)
    override = _parse_bk_span(elem.get("data-bk-span"))
    chem = elem.get("data-bk-chem") is not None
    if override is None and not chem:
        handler(cells, mctx, elem)
        return
    saved_span = mctx.span
    saved_chem = mctx.chem
    if override is not None:
        mctx.span = override
    if chem:
        mctx.chem = True
    try:
        handler(cells, mctx, elem)
    finally:
        mctx.span = saved_span
        mctx.chem = saved_chem
