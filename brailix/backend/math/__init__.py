"""Translate a :class:`MathInline` (carrying a parsed MathML
:class:`ET.Element` tree) into a sequence of braille cells.

The backend dispatches per :attr:`ET.Element.tag` — MathML *is* the
math IR (see ``ARCHITECTURE.md``). State threads through a small
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

The package is split into four modules so each one stays scannable:

* :mod:`.context`   — :class:`MathBrailleContext` dataclass
* :mod:`.dispatch`  — :func:`_emit_element`, the tag-dispatch entrypoint
* :mod:`.handlers`  — every ``_emit_<tag>`` handler + ``_DISPATCH`` table
* :mod:`.utils`     — small pure helpers (shape checks, unpackers, role
  tables, ``_emit_structure``, ``_unknown_cell``, etc.)

Only the three names below — :class:`MathBrailleContext`,
:func:`translate`, :func:`emit_tree` — are stable public API of this
package.  Everything else lives in the sub-modules and is package-
internal; callers that need a helper should import it from its
specific sub-module (e.g. ``from brailix.backend.math.utils import
_is_leaf_like``) so the package interface stays scoped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

# Import handlers so the dispatch table is populated before the first
# translate() call. Handlers is intentionally imported for its side
# effect (registering its functions in ``_DISPATCH``); the explicit
# alias keeps linters from flagging an unused import.
from brailix.backend.math import handlers as _handlers  # noqa: F401
from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.dispatch import _emit_element
from brailix.backend.math.utils import _coalesce_function_names, _fallback_surface
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import MathInline

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def translate(
    node: MathInline, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Translate one :class:`MathInline` node into braille cells.

    If :attr:`MathInline.math` was never populated by the math frontend
    (missing adapter, ``source='plain'`` falling through), we fall back
    to per-char unknown cells over the original surface so something
    useful still lands in the output.
    """
    math_tree = node.math
    if math_tree is None:
        ctx.warnings.error(
            code="MATH_NO_IR",
            message=(
                "math node lacks a parsed MathML tree; emitting raw "
                "surface as unknown cells"
            ),
            surface=node.surface,
            span=node.span,
            source="backend.math",
        )
        return _fallback_surface(node.surface, node.span)

    # Copy-on-write: never mutate node.math (cached + serialized as IR).
    working_tree = _coalesce_function_names(math_tree, profile)
    mctx = MathBrailleContext(profile=profile, backend=ctx, span=node.span)
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, working_tree)
    return cells


def emit_tree(
    elem: ET.Element, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Convenience for tests: emit a single MathML subtree directly.

    Equivalent to wrapping the element in a fresh :class:`MathInline`
    and calling :func:`translate`.
    """
    working_tree = _coalesce_function_names(elem, profile)
    mctx = MathBrailleContext(profile=profile, backend=ctx)
    cells: list[BrailleCell] = []
    _emit_element(cells, mctx, working_tree)
    return cells


__all__ = (
    "MathBrailleContext",
    "translate",
    "emit_tree",
)
