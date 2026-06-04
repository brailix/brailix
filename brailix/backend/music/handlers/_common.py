"""Helpers shared by multiple handler submodules.

Kept tiny on purpose — the rule of thumb is: a helper lives here only
when it's referenced from two or more handler files. Single-use helpers
stay with their owning handler.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import unknown_cell
from brailix.ir.braille import BrailleCell


def warn_and_fallback(
    mctx: MusicBrailleContext,
    cells: list[BrailleCell],
    *,
    code: str,
    message: str,
    source_text: str | None,
) -> None:
    """Common pattern: warn, then emit one unknown cell as a marker."""
    mctx.backend.warnings.warn(
        code=code,
        message=message,
        surface=source_text,
        source="backend.music",
    )
    cells.append(unknown_cell(mctx, role="music_unknown", source_text=source_text))


def serialise_short(elem: ET.Element) -> str:
    """A short XML serialisation for warning messages."""
    s = ET.tostring(elem, encoding="unicode")
    return s if len(s) < 120 else s[:117] + "..."
