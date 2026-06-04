"""Tag-based dispatcher for MusicXML trees.

The handler table lives in :mod:`.handlers`; this dispatcher owns the
recursive entry point. The shared
:class:`~brailix.core.dispatch.LazyTagDispatcher` resolves the cycle —
handlers need :func:`_emit_element` for descent into measure / part
children — by loading the table lazily on first call.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.core.dispatch import Handler, LazyTagDispatcher
from brailix.ir.braille import BrailleCell


def _load() -> tuple[dict[str, Handler], Handler]:
    from brailix.backend.music import handlers as _handlers

    return _handlers._DISPATCH, _handlers._emit_unsupported


_dispatcher = LazyTagDispatcher(_load)


def _emit_element(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Dispatch one element to its tag-specific handler.

    Unknown tags fall back to ``_emit_unsupported`` (a no-op + warning)
    so the backend stays soft-failure: an unrecognised element never
    crashes, it just doesn't contribute cells.
    """
    _dispatcher.resolve(elem.tag)(cells, mctx, elem)
