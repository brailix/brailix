"""Shared helper for the ``InlineTextTranslator`` seam.

Embedded prose (a math ``<mtext>`` / ``\\text{...}`` run, a chemistry
reaction condition, a music ``<words>`` direction, inline lyrics) is
translated through the callable the Pipeline injects onto
``BackendContext.options`` — ARCHITECTURE.md §12's one controlled
exception to "backend never calls the frontend".  That translator runs
a private frontend over a throwaway one-paragraph document, so the
cells it returns carry 0-based spans of THAT document.  Those are
meaningless in the host document: a proofread double-click on them
used to jump to the start of the file.

Every call site re-anchors the cells through
:func:`rebase_translated_cells` before splicing them into the host
stream, so the seam has one place that owns the coordinate contract.
"""

from __future__ import annotations

from dataclasses import replace

from brailix.core.span import Span
from brailix.ir.braille import BrailleCell


def rebase_translated_cells(
    cells: list[BrailleCell],
    span: Span | None,
    *,
    role: str | None = None,
) -> list[BrailleCell]:
    """Re-anchor translator output onto the host node's ``span``.

    ``source_text`` is kept — it names the actual character and is what
    proofread UIs display; only the coordinates move.  ``role``
    optionally retags every cell (inline lyrics retag to
    ``music_lyric``; math / direction text keeps the language path's
    own roles).
    """
    out: list[BrailleCell] = []
    for cell in cells:
        if role is None:
            out.append(replace(cell, source_span=span))
        else:
            out.append(replace(cell, source_span=span, role=role))
    return out


__all__ = ("rebase_translated_cells",)
