"""Translate a :class:`MusicInline` (carrying a parsed MusicXML
:class:`ET.Element` tree) into a sequence of braille cells.

The backend dispatches per :attr:`ET.Element.tag` — MusicXML *is* the
music IR. State threads through a
small :class:`MusicBrailleContext` so context-sensitive markers fire
at the right boundaries:

* **Octave prefix** — per BANA Par. 3.2.2, emitted only when the
  melodic interval to the previous pitch crosses the implicit-octave
  threshold (≤ 3° = omit, ≥ 6° = always mark, 4°/5° = mark only when
  the BANA octave number actually changes).
* **First note of line** always carries an octave prefix
  (Par. 3.2.1).

Soft-failure contract: an unrecognised *element* is a no-op plus a
``MUSIC_*`` warning (it contributes no cells); an unrecognised
*character* or a malformed note produces an unknown cell plus a
warning. The pipeline never crashes — including against tree *depth*,
which the element-level handlers say nothing about: a tree nested past
:data:`_MAX_TREE_DEPTH` soft-fails at the entry points rather than
overflowing the recursive dispatch.

The package's own interface is intentionally tiny:

* :class:`MusicBrailleContext` — per-fragment mutable state
* :func:`translate` — one :class:`MusicInline` → cells

Both are internal, like every path outside the facades and the extension
surface (see the top-level :mod:`brailix` docstring): scoped so the
sub-modules stay private to the package, not offered as a compatibility
promise. ``_emit_tree`` beside them is a convenience wrapper for tests, and
underscore-named so it doesn't read as one either.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Literal

# Import handlers so the dispatch table is populated before the first
# translate() call. The handlers module is imported for its side effect
# (registering its functions in ``_DISPATCH``); the explicit alias
# keeps linters quiet.
from brailix.backend.music import handlers as _handlers  # noqa: F401
from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.utils import _unknown_cell_seq
from brailix.core._xml import tree_depth_exceeds
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import MusicInline

# A MusicXML tree deeper than this overflows the recursive descent through
# _emit_element and the container handlers, which hand every child back to it
# (score → part → measure → note sequence). Real scores nest under ~15 levels;
# a corrupt / adversarial tree past the cap degrades to a soft failure (one
# MUSIC_ERROR warning + a single unknown cell) instead of raising
# ``RecursionError`` — this package's "pipeline never crashes" contract, which
# a 6000-level ``<score-partwise>`` chain did break. The depth probe is
# iterative, so the guard is itself depth-safe.
#
# The check lives here rather than in the frontend normalizer because a tree
# can reach the backend without passing it: a ``.blx`` round-trip re-parses
# ``MusicInline.score`` straight from its serialized string, and a caller can
# construct a ``MusicInline`` directly. (The normalizer's own passes are all
# iterative, so it has nothing to protect on its own behalf.) Same reasoning,
# and the same cap, as ``backend.math``'s guard; both read the depth through
# the shared ``core._xml`` probe rather than sharing a cross-vertical helper.
_MAX_TREE_DEPTH = 150


def _too_deep_fallback(
    surface: str | None, span: Span | None, ctx: BackendContext
) -> list[BrailleCell]:
    """Soft-fail a tree nested past :data:`_MAX_TREE_DEPTH`: one MUSIC_ERROR
    warning plus a single unknown cell.

    One marker cell, not the per-character run ``MUSIC_NO_IR`` emits: there
    *is* a parsed tree here, we are refusing to walk it, so the output marks
    the passage rather than spelling its surface out. Mirrors the
    ``<music-error>`` handler's cell shape.
    """
    ctx.warnings.error(
        code="MUSIC_ERROR",
        message=(
            f"score nested deeper than {_MAX_TREE_DEPTH} levels; not rendered"
        ),
        surface=surface,
        span=span,
        source="backend.music",
    )
    return [
        BrailleCell(
            dots=(),
            role="music_error",
            source_text=surface or "?",
            source_span=span,
        )
    ]


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
        ctx.warnings.error(
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

    if tree_depth_exceeds(score_tree, _MAX_TREE_DEPTH):
        return _too_deep_fallback(node.surface, node.span, ctx)

    mctx = MusicBrailleContext(
        profile=profile,
        backend=ctx,
        span=node.span,
        octave_rule=_resolve_octave_rule(profile),
    )
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, score_tree)
    return cells


def _emit_tree(
    elem: ET.Element, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Convenience for tests: emit a single MusicXML subtree directly.

    Equivalent to wrapping the element in a fresh :class:`MusicInline`
    and calling :func:`translate`. Underscore-named because that is all it is:
    a test convenience with no caller in the compiler.

    Depth-guarded like :func:`translate` — both entry points reach the same
    recursive dispatch, so a check on only one of them leaves the contract
    broken through the other.
    """
    if tree_depth_exceeds(elem, _MAX_TREE_DEPTH):
        return _too_deep_fallback(None, None, ctx)
    mctx = MusicBrailleContext(
        profile=profile,
        backend=ctx,
        octave_rule=_resolve_octave_rule(profile),
    )
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, elem)
    return cells


# Valid ``features.music.octave_rule`` strategies — must stay in sync
# with the ``Literal`` on :attr:`MusicBrailleContext.octave_rule`.
_VALID_OCTAVE_RULES = ("interval16", "every_measure", "always")


def _resolve_octave_rule(
    profile: BrailleProfile,
) -> Literal["interval16", "every_measure", "always"]:
    """Read ``features.music.octave_rule`` and narrow it to a valid
    strategy. An unset / unrecognised value falls back to the BANA
    default ``"interval16"`` (a malformed profile shouldn't crash the
    backend or violate the context's ``Literal`` type)."""
    value = profile.feature("music.octave_rule", "interval16")
    if value in _VALID_OCTAVE_RULES:
        return value
    return "interval16"


__all__ = (
    "MusicBrailleContext",
    "translate",
)
