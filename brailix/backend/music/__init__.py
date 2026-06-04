"""Translate a :class:`MusicInline` (carrying a parsed MusicXML
:class:`ET.Element` tree) into a sequence of braille cells.

The backend dispatches per :attr:`ET.Element.tag` — MusicXML *is* the
music IR (see ``ARCHITECTURE.md``). State threads through a
small :class:`MusicBrailleContext` so context-sensitive markers fire
at the right boundaries:

* **Octave prefix** — per BANA Par. 3.2.2, emitted only when the
  melodic interval to the previous pitch crosses the implicit-octave
  threshold (≤ 3° = omit, ≥ 6° = always mark, 4°/5° = mark only when
  the BANA octave number actually changes).
* **First note of line** always carries an octave prefix
  (Par. 3.2.1).

Soft-failure contract: an unrecognised element / character produces an
unknown cell plus a ``MUSIC_*`` warning. The pipeline never crashes.

Public surface is intentionally tiny:

* :class:`MusicBrailleContext` — per-fragment mutable state
* :func:`translate` — one :class:`MusicInline` → cells
* :func:`emit_tree` — convenience wrapper for tests
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

# Import handlers so the dispatch table is populated before the first
# translate() call. The handlers module is imported for its side effect
# (registering its functions in ``_DISPATCH``); the explicit alias
# keeps linters quiet.
from brailix.backend.music import handlers as _handlers  # noqa: F401
from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.utils import _unknown_cell_seq
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import MusicInline


def translate(
    node: MusicInline, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Translate one :class:`MusicInline` node into braille cells.

    If :attr:`MusicInline.score` was never populated by the music
    frontend (missing adapter, ``source='plain'`` falling through),
    fall back to per-char unknown cells over the raw surface so
    something useful still lands in the output.
    """
    score_tree = node.score
    if score_tree is None:
        ctx.warnings.warn(
            code="MUSIC_NO_IR",
            message=(
                "music node lacks a parsed MusicXML tree; emitting "
                "raw surface as unknown cells"
            ),
            surface=node.surface,
            span=node.span,
            source="backend.music",
        )
        return _unknown_cell_seq(node.surface, node.span)

    mctx = MusicBrailleContext(profile=profile, backend=ctx, span=node.span)
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, score_tree)
    return cells


def emit_tree(
    elem: ET.Element, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Convenience for tests: emit a single MusicXML subtree directly.

    Equivalent to wrapping the element in a fresh :class:`MusicInline`
    and calling :func:`translate`.
    """
    mctx = MusicBrailleContext(profile=profile, backend=ctx)
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, elem)
    return cells


__all__ = (
    "MusicBrailleContext",
    "translate",
    "emit_tree",
)
