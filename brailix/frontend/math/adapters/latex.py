"""LaTeX adapter backed by the ``latex2mathml`` package.

The adapter calls :func:`latex2mathml.converter.convert` and returns
the resulting MathML string. Errors from the converter are reported
as a soft failure (``<merror>`` wrapped MathML) rather than re-raised,
so a malformed snippet never breaks a whole document.

**Source-span granularity**: ``latex2mathml`` does not emit token
positions, so the MathML this adapter produces never carries
``data-bk-span`` attributes. Every braille cell emitted downstream
therefore inherits the formula-level :class:`~brailix.ir.inline.MathInline`
span. Future LaTeX adapters (or wrappers around this one) can opt
into finer-grained spans by post-processing the MathML tree and
filling ``data-bk-span="start,end"`` on tokens; the backend will
honour those per-element. See ``ARCHITECTURE.md``
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from brailix.core.context import MathContext
from brailix.frontend.math.adapters import chem
from brailix.frontend.math.utils import (
    _strip_math_delimiters as _shared_strip,
)
from brailix.frontend.math.utils import merror_wrap


@dataclass(slots=True)
class LatexMathSourceAdapter:
    """Wraps a ``latex → MathML`` converter callable.

    ``converter`` is exposed as a constructor argument so tests can
    inject a fake without installing latex2mathml. The real one is
    plugged in by :func:`_load`.
    """

    source: str = "latex"
    converter: Callable[[str], str] = field(default=None)  # type: ignore[assignment]

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        if isinstance(formula, bytes):
            try:
                formula = formula.decode("utf-8")
            except UnicodeDecodeError:
                return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = formula.strip()
        if not text:
            return merror_wrap("", reason="empty input")
        # ``$...$`` / ``\(...\)`` wrappers leak into segment surfaces;
        # strip them so latex2mathml sees just the inner expression.
        text = _strip_math_delimiters(text)
        # mhchem ``\ce{...}`` is LaTeX syntax latex2mathml can't parse —
        # hand a top-level chemical formula to the chemistry converter,
        # which emits ``data-bk-chem`` MathML for the backend's chem rules.
        ce_inner = chem.extract_ce_inner(text)
        if ce_inner is not None:
            return chem.convert_ce(ce_inner)
        try:
            return self.converter(text)
        except Exception as e:  # noqa: BLE001 — third-party failures vary
            return merror_wrap(text, reason=f"latex2mathml error: {e}")


def _strip_math_delimiters(text: str) -> str:
    """Peel the inline-math delimiters that the segmenter leaves attached.

    Delegates to the shared helper in :mod:`.mathml` so the LaTeX and
    MathML adapters stay in sync on delimiter rules; re-exported under
    the local name to preserve the historical import path for tests.
    """
    return _shared_strip(text)


def _load() -> LatexMathSourceAdapter:
    """Lazy-import the converter and return a configured adapter."""
    from latex2mathml.converter import convert  # noqa: WPS433 — lazy by design

    return LatexMathSourceAdapter(converter=convert)
