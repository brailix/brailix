"""Helpers shared by multiple handler submodules.

Kept tiny on purpose — the rule of thumb is: a helper lives here only
when it's referenced from two or more handler files. Single-use helpers
stay with their owning handler.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET

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
    mctx.warn(
        code=code,
        message=message,
        surface=source_text,
        source="backend.music",
    )
    cells.append(unknown_cell(mctx, role="music_unknown", source_text=source_text))


def warn_feature_unimplemented(
    mctx: MusicBrailleContext,
    feature: str,
    value: object,
    covered: str,
) -> None:
    """A profile feature value the current milestone doesn't implement:
    warn (``MUSIC_UNSUPPORTED_NOTATION``) and let the caller fall back
    to the covered form. ``covered`` names what IS implemented, e.g.
    ``"M3.2 covers 'separate' only"``."""
    mctx.warn(
        code="MUSIC_UNSUPPORTED_NOTATION",
        message=(
            f"{feature}={value!r} not implemented ({covered}); falling back"
        ),
        source="backend.music",
    )


def serialise_short(elem: _ET.Element) -> str:
    """A short XML serialisation for warning messages."""
    s = _ET.tostring(elem, encoding="unicode")
    return s if len(s) < 120 else s[:117] + "..."
