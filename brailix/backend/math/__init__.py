"""Translate a :class:`MathInline` (carrying a parsed MathML
:class:`ET.Element` tree) into a sequence of braille cells.

The backend dispatches per :attr:`ET.Element.tag` — MathML *is* the
math IR. State threads through a small
:class:`MathBrailleContext` so context-sensitive markers fire at the
right boundaries:

* a number sign is emitted *once* per digit run, then suppressed until
  the run breaks on an operator / identifier / structural boundary;
* fraction / script / sqrt markers fire when the construct cannot be
  simplified (per-profile feature);
* big-operator scripts (``\\sum``, ``\\int``, ``\\lim``...) take an
  optional 46-dot prefix in front of their sub/sup indicators, gated
  by per-symbol / per-function ``script_prefix`` flags.

Soft-failure contract: an unrecognised element / character produces an
unknown cell plus a ``MATH_*`` warning. The pipeline never crashes.

The package is split into focused modules so each one stays scannable:

* :mod:`.context`   — :class:`MathBrailleContext` dataclass
* :mod:`.dispatch`  — :func:`_emit_element`, the tag-dispatch entrypoint
* :mod:`.handlers`  — every ``_emit_<tag>`` handler + ``_DISPATCH`` table
* :mod:`.chem`      — chemistry-specific emit helpers (``\\ce{}`` output)
* :mod:`.utils`     — small pure helpers (shape checks, unpackers, role
  tables, ``_emit_structure``, ``_unknown_cell``, etc.)

This package's own entry points are :class:`MathBrailleContext`,
:func:`translate_tree` (a normalised MathML tree → cells, which is what a
display :class:`~brailix.ir.document.MathBlock` hands over) and
:func:`translate` (the thin node-taking wrapper
:mod:`brailix.backend.dispatch` calls for an inline
:class:`~brailix.ir.inline.MathInline`). Everything else lives in the
sub-modules; callers that need a helper import it from the sub-module that
defines it (e.g. ``from brailix.backend.math.utils import _is_atomic``) so the
package interface stays scoped.

Scoped, not *published*: the whole package is internal, like every path outside
the facades and the extension surface (see the top-level :mod:`brailix`
docstring). Calling these names "stable public API" here would say the opposite
of the policy one level up and leave the question of whether they carry a
compatibility promise answerable two ways.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

# Import handlers so the dispatch table is populated before the first
# translate() call. Handlers is intentionally imported for its side
# effect (registering its functions in ``_DISPATCH``); the explicit
# alias keeps linters from flagging an unused import.
from brailix.backend.math import handlers as _handlers  # noqa: F401
from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.utils import (
    _coalesce_identifier_runs,
    _fallback_surface,
    _unknown_cell,
)
from brailix.core._xml import tree_depth_exceeds
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import MathInline

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET

# A MathML tree deeper than this overflows the recursive descent through
# _emit_element / handlers / _coalesce_identifier_runs (empirically ~470
# levels at the default recursion limit). Real math nests under ~30 levels;
# a corrupt / adversarial tree past the cap degrades to a soft failure
# (one MATH_ERROR warning + a single unknown cell) instead of crashing — the
# package's "pipeline never crashes" contract. The depth probe is iterative,
# so the guard is itself depth-safe. A tree reaching the backend may have
# skipped the frontend normalizer's matching guard (e.g. a .blx round-trip or
# a directly-constructed MathInline), so the backend re-checks rather than
# trusting upstream.
_MAX_TREE_DEPTH = 150


def _too_deep_fallback(
    surface: str | None, span: Span | None, ctx: BackendContext
) -> list[BrailleCell]:
    """Soft-fail a tree nested past :data:`_MAX_TREE_DEPTH`: one MATH_ERROR
    warning plus a single unknown cell, mirroring the ``<merror>`` handler."""
    ctx.warnings.error(
        code="MATH_ERROR",
        message=(
            f"formula nested deeper than {_MAX_TREE_DEPTH} levels; not rendered"
        ),
        surface=surface,
        span=span,
        source="backend.math",
    )
    return [_unknown_cell(surface or "?", span)]

# ---------------------------------------------------------------------------
# Package entry points — the two the docstring names, not published API
# ---------------------------------------------------------------------------


def translate_tree(
    tree: ET.Element | None,
    ctx: BackendContext,
    profile: BrailleProfile,
    *,
    surface: str = "",
    span: Span | None = None,
) -> list[BrailleCell]:
    """Translate one normalised MathML tree into braille cells.

    The package's one entry point, for both of the places a formula comes
    from: an inline ``$...$`` fragment (a :class:`MathInline`, via
    :func:`translate`) and a display block (a
    :class:`~brailix.ir.document.MathBlock`, whose tree the backend reads
    directly). ``surface`` / ``span`` are the formula's source text and extent,
    used for provenance and for the fallbacks — a caller emitting a subtree it
    cannot locate passes neither.

    ``tree`` is ``None`` when the math frontend never produced one (missing
    adapter, ``source='plain'`` falling through, a parse that failed and left
    the block bare): warn ``MATH_NO_IR`` and fall back to per-char unknown
    cells over the surface, so the formula still occupies the real estate it
    would have and every cell still traces to a character.
    """
    if tree is None:
        ctx.warnings.error(
            code="MATH_NO_IR",
            message=(
                "math node lacks a parsed MathML tree; emitting raw "
                "surface as unknown cells"
            ),
            surface=surface,
            span=span,
            source="backend.math",
        )
        return _fallback_surface(surface, span)

    if tree_depth_exceeds(tree, _MAX_TREE_DEPTH):
        return _too_deep_fallback(surface or None, span, ctx)

    # Copy-on-write: never mutate the caller's tree (cached + serialized as IR).
    working_tree = _coalesce_identifier_runs(tree, profile)
    mctx = MathBrailleContext(profile=profile, backend=ctx, span=span)
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, working_tree)
    return cells


def translate(
    node: MathInline, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Translate one :class:`MathInline` node into braille cells.

    The inline dispatcher's entry: unpacks the node and hands its tree to
    :func:`translate_tree`, which is where the work is.
    """
    return translate_tree(
        node.tree, ctx, profile, surface=node.surface, span=node.span
    )


__all__ = (
    "MathBrailleContext",
    "translate",
    "translate_tree",
)
