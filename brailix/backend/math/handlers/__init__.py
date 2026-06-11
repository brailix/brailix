"""Per-tag handlers for the math backend.

The handler set is organised by MathML element family, one submodule per
family. Each submodule that owns top-level tags contributes a
``_DISPATCH_PARTIAL`` mapping that is merged here into the full ``_DISPATCH``
table; submodules that own no top-level tag (``accents``) expose only the
helpers their siblings call.

Every handler signature is ``(cells, mctx, elem) -> None`` so they can live
behind one dispatch table. :func:`brailix.backend.math.dispatch._emit_element`
imports this package lazily (via ``_DISPATCH`` + ``_emit_unsupported``) to
avoid a dispatch ↔ handlers cycle.

Subpackage map (per MathML element family):

* :mod:`.containers` — ``<math>`` root / ``<mrow>`` (+ typed-slash fractions)
* :mod:`.leaves`     — ``<mi>`` / ``<mn>`` / ``<mo>`` / ``<mspace>`` /
  ``<mtext>`` (+ the ``_emit_as_mo`` shim)
* :mod:`.fractions`  — ``<mfrac>`` (+ Antoine / typed-slash fraction paths)
* :mod:`.roots`      — ``<msqrt>`` / ``<mroot>``
* :mod:`.matrices`   — ``<mtable>`` linear notation (fenced + bare +
  equation-system ``{``-only form)
* :mod:`.scripts`    — ``<msub>`` / ``<msup>`` / ``<msubsup>`` +
  ``<munder>`` / ``<mover>`` / ``<munderover>`` dispatch
* :mod:`.accents`    — ``accent="true"`` under/over variants (no top-level tag)
* :mod:`.fallback`   — ``<merror>`` + unsupported catch-all (``<mtr>`` / ``<mtd>``)
"""

from __future__ import annotations

from brailix.backend.math.handlers.containers import (
    _DISPATCH_PARTIAL as _containers,
)
from brailix.backend.math.handlers.fallback import (
    _DISPATCH_PARTIAL as _fallback,
)
from brailix.backend.math.handlers.fallback import _emit_unsupported
from brailix.backend.math.handlers.fractions import (
    _DISPATCH_PARTIAL as _fractions,
)
from brailix.backend.math.handlers.leaves import (
    _DISPATCH_PARTIAL as _leaves,
)
from brailix.backend.math.handlers.matrices import (
    _DISPATCH_PARTIAL as _matrices,
)
from brailix.backend.math.handlers.roots import (
    _DISPATCH_PARTIAL as _roots,
)
from brailix.backend.math.handlers.scripts import (
    _DISPATCH_PARTIAL as _scripts,
)

_DISPATCH: dict = {}
for _partial in (
    _containers, _leaves, _fractions, _roots,
    _matrices, _scripts, _fallback,
):
    _DISPATCH.update(_partial)

__all__ = ("_DISPATCH", "_emit_unsupported")
