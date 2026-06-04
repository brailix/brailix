"""Mutable per-formula state for the math backend.

The translator threads one :class:`MathBrailleContext` instance through
a single :class:`MathInline` translation. The dispatcher constructs a
fresh context per math node so state never leaks across formulas.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span


@dataclass(slots=True)
class MathBrailleContext:
    """Mutable per-formula state for the math backend.

    * ``profile`` / ``backend`` — passed through to handlers so they can
      look up cells and emit warnings.
    * ``span`` — the source span of the current ``<math>`` root or the
      narrower span pushed by a ``data-bk-span`` attribute on a child.
      Every cell created during the current handler call inherits this.
    * ``need_number_sign`` — set to True when the next digit run must
      start with a number-sign cell. Reset by digit emission and by
      number-breaking roles (operators, relations, shapes, big-ops).
    * ``chem`` — True while inside a ``data-bk-chem`` subtree (a chemical
      formula from the mhchem ``\\ce`` adapter). Switches the backend to
      chemistry braille rules: subscripts use the lowered digit form with
      no ``script.sub`` marker, and element letters get the chemical-formula
      indicator / capital-sign treatment decided by ``chem_per_element`` (see
      :mod:`brailix.backend.math.chem`). Set/cleared by the dispatcher
      from the ``data-bk-chem`` attribute, the same way ``span`` is
      pushed from ``data-bk-span``.
    * ``chem_per_element`` — set per molecule by :func:`chem.emit_molecule`:
      ``False`` = all-single-letter molecule (one leading ⠸, bare letters);
      ``True`` = molecule has a multi-letter element (per-element capital
      sign, no ⠸).
    """

    profile: BrailleProfile
    backend: BackendContext
    span: Span | None = None
    need_number_sign: bool = True
    chem: bool = False
    chem_per_element: bool = False
