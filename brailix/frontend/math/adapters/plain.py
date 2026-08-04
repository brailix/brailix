"""``plain`` math adapter — last-resort fallback.

``"plain"`` is what a math fragment carries when nothing declared a real
source format: it is the default of :class:`~brailix.core.context.MathContext`
and of :class:`~brailix.ir.inline.MathInline` /
:class:`~brailix.ir.document.MathBlock`, so a hand-built formula node with no
``source=`` lands here. This adapter does **not** guess what dialect the text
might be — a wrong guess produces confident, wrong braille — it wraps the
input in an ``<merror>`` so the backend emits the fallback cells and the
collector records the failure.

Registering it (rather than letting the registry lookup miss) is what makes
the default value honest. Without it, a default-constructed ``MathContext``
could only ever produce ``MATH_ADAPTER_MISSING`` — a diagnostic that names an
adapter the caller never chose and lists the ones it might have meant, which
reads as a broken installation rather than as "this formula never said what it
was". The two-tier contract is now the same as the music subsystem's
(:mod:`brailix.frontend.music.adapters.plain`):

* declared sources (``mathml`` / ``latex`` / ``omml`` / ``mtef`` /
  ``eq_field`` / ``chem`` / ``script_cluster``) parse properly;
* anything undeclared surfaces as an obvious failure for a proofreading UI to
  flag — no guessing.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass

from brailix.core.context import MathContext
from brailix.frontend.math.utils import merror_wrap

_REASON = "plain math source unsupported -- declare a real source"


@_dataclass(slots=True)
class PlainMathSourceAdapter:
    """Surface any undeclared math input as a soft failure."""

    source: str = "plain"

    def to_mathml(
        self, formula: str | bytes, ctx: MathContext | None = None
    ) -> str:
        if isinstance(formula, bytes):
            formula = formula.decode("utf-8", errors="replace")
        return merror_wrap(formula.strip()[:200], reason=_REASON)


def _load() -> PlainMathSourceAdapter:
    return PlainMathSourceAdapter()
