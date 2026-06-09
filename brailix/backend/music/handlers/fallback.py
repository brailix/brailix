"""Soft-failure handlers + the ``no-op`` list of skipped MusicXML tags.

* :func:`_emit_music_error` — surfaces frontend parse failures.
* :func:`_emit_skip` — silently consumes elements that have no braille
  meaning (score metadata, cursor controls, ``<print>`` hints, ...).
* :func:`_emit_unsupported` — catch-all warning for tags not in the
  dispatch table; consumed by
  :mod:`brailix.backend.music.dispatch` directly.

The dispatch contributions split into:

* ``_DISPATCH_PARTIAL`` — known-skip / known-error tags.
* ``_emit_unsupported`` — re-exported by the ``handlers`` package so
  ``dispatch.py`` can use it as the table's default value.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import unknown_cell
from brailix.ir.braille import BrailleCell


def _emit_music_error(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """The frontend wrapped a parse failure here. Surface as a
    warning and produce one unknown cell so the position is visible
    in the output."""
    reason = elem.attrib.get("data-reason", "unknown")
    mctx.backend.warnings.warn(
        code="MUSIC_PARSE_RECOVERY",
        message=f"music adapter soft-failed: {reason}",
        surface=(elem.text or "").strip() or None,
        source="backend.music",
    )
    cells.append(unknown_cell(mctx, role="music_error", source_text="<music-error>"))


def _emit_skip(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Silently skip cursor controls (``<print>`` / ``<backup>`` /
    ``<forward>``) and score-level metadata (``<part-list>`` / ``<work>`` /
    ``<identification>`` / ...) that don't translate into braille cells —
    the exact set is ``_DISPATCH_PARTIAL`` below.  (``<attributes>`` and
    ``<sound>`` are NOT skipped — they have their own handlers.)"""
    return


def _emit_unsupported(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Catch-all for tags the dispatch table doesn't know about.

    Emits one warning per element type so an unfamiliar MusicXML
    feature gets flagged instead of vanishing silently. Caller's
    cell list is untouched — most unknown elements are containers
    whose ignorable children should also be visited; not visiting
    them here keeps the output count stable. M3+ can broaden this.
    """
    mctx.backend.warnings.warn(
        code="MUSIC_UNSUPPORTED_NOTATION",
        message=f"no handler for music element <{elem.tag}>",
        source="backend.music",
    )


_DISPATCH_PARTIAL = {
    "music-error": _emit_music_error,
    # Skipped (no-op) elements until later milestones:
    "print": _emit_skip,
    "backup": _emit_skip,
    "forward": _emit_skip,
    # Score metadata that doesn't translate into braille cells:
    "part-list": _emit_skip,
    "score-part": _emit_skip,
    "part-name": _emit_skip,
    "part-abbreviation": _emit_skip,
    "work": _emit_skip,
    "movement-title": _emit_skip,
    "movement-number": _emit_skip,
    "identification": _emit_skip,
    "defaults": _emit_skip,
    "credit": _emit_skip,
}
