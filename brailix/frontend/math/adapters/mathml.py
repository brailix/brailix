"""Pass-through MathML adapter.

Input is already MathML, so the adapter only validates that the string
parses as well-formed XML and returns it. Malformed input is wrapped
inside a single ``<merror>`` document so the normalizer + backend
produce a clean ``MATH_ERROR`` warning rather than crashing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from brailix.core._xml import safe_fromstring
from brailix.core.context import MathContext
from brailix.frontend.math.utils import _strip_math_delimiters, merror_wrap


@dataclass(slots=True)
class MathMLSourceAdapter:
    """Trivial adapter: accept MathML in, give MathML out."""

    source: str = "mathml"

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        if isinstance(formula, bytes):
            try:
                formula = formula.decode("utf-8")
            except UnicodeDecodeError:
                return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = formula.strip()
        if not text:
            return merror_wrap("", reason="empty input")
        # The segmenter hands math-inline surfaces with the LaTeX-style
        # ``$...$`` (or ``\(...\)`` / ``\[...\]``) delimiters still
        # attached.  MathML callers like the docx adapter share the
        # same delimiter convention (the segmenter would otherwise not
        # recognise the inline region), so we strip here too — the
        # same logic the latex adapter applies.
        text = _strip_math_delimiters(text)
        try:
            safe_fromstring(text)
        except ET.ParseError as e:
            return merror_wrap(text, reason=f"parse error: {e}")
        return text


def _load() -> MathMLSourceAdapter:
    """Factory — kept symmetric with the other adapters even though
    MathML doesn't need a third-party library."""
    return MathMLSourceAdapter()
