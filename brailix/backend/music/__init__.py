"""Translate a parsed MusicXML :class:`ET.Element` tree into a sequence of
braille cells.

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
* :func:`translate_tree` — one MusicXML tree → cells

Both are internal, like every path outside the facades and the extension
surface (see the top-level :mod:`brailix` docstring): scoped so the
sub-modules stay private to the package, not offered as a compatibility
promise. There used to be a third name, ``_emit_tree``, a test convenience
that did what :func:`translate_tree` now does — the difference between them
was only that one took a node to unwrap and the other took the tree, and once
the score IR stopped travelling inside a carrier node there was nothing left
to unwrap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

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

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET
    from typing import Literal

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
# can reach the backend without passing it: deserializing a document re-parses
# ``ScoreBlock.tree`` straight from its serialized string, and a caller can
# hand a block a tree directly. (The normalizer's own passes are all
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


def translate_tree(
    tree: ET.Element | None,
    ctx: BackendContext,
    profile: BrailleProfile,
    *,
    surface: str = "",
    span: Span | None = None,
) -> list[BrailleCell]:
    """Translate one normalised MusicXML tree into braille cells.

    The package's one entry point. Music reaches the backend only as a block
    (a :class:`~brailix.ir.document.ScoreBlock` or
    :class:`~brailix.ir.document.MusicBlock`) — there is no inline music in
    prose — so unlike math there is no node-taking wrapper beside this; the
    block backend hands the tree over directly. ``surface`` / ``span`` are the
    passage's source text and extent, for provenance and the fallbacks; a
    caller emitting a subtree it cannot locate passes neither.

    ``tree`` is ``None`` when the music frontend never produced one (missing
    adapter, ``source='plain'`` falling through, a parse that failed): warn
    ``MUSIC_NO_IR`` and fall back to per-char unknown cells over the surface,
    so the document still compiles around the unreadable score.
    """
    if tree is None:
        ctx.warnings.error(
            code="MUSIC_NO_IR",
            message=(
                "music node lacks a parsed MusicXML tree; emitting "
                "raw surface as unknown cells"
            ),
            surface=surface,
            span=span,
            source="backend.music",
        )
        return _unknown_cell_seq(surface, span)

    if tree_depth_exceeds(tree, _MAX_TREE_DEPTH):
        return _too_deep_fallback(surface or None, span, ctx)

    mctx = MusicBrailleContext(
        profile=profile,
        backend=ctx,
        span=span,
        octave_rule=_resolve_octave_rule(profile),
    )
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, tree)
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
    "translate_tree",
)
