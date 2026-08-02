"""Pass-through SVG adapter.

Input is already SVG, so the adapter only strips a leading XML
declaration / DOCTYPE (which ElementTree rejects in fragment form) and
validates that the remainder parses as well-formed XML, then returns it.
Malformed input is wrapped inside a single empty ``<svg>`` carrying a
``data-bk-error`` attribute so the normalizer + backend produce a clean
soft-failure (a blank raster) rather than crashing — mirroring the math
``<merror>`` / music ``<music-error>`` convention.

The :func:`svg_error_wrap` helper is exposed at module level so sibling
adapters (primitives / image / figure) share one soft-fail shape.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from brailix.core._xml import safe_fromstring, strip_xml_invalid_chars, strip_xml_prolog
from brailix.core.context import GraphicsContext


@dataclass(slots=True)
class SVGSourceAdapter:
    """Trivial adapter: accept SVG in, give SVG out."""

    source: str = "svg"

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return svg_error_wrap(repr(src), reason="non-utf8 bytes")
        text = src.strip()
        if not text:
            return svg_error_wrap("", reason="empty input")
        text = strip_xml_prolog(text)
        try:
            safe_fromstring(text)
        except ET.ParseError as e:
            return svg_error_wrap(text, reason=f"parse error: {e}")
        return text
def svg_error_wrap(surface: str, *, reason: str) -> str:
    """Build a minimal SVG document carrying a soft-failure marker.

    The root is an empty ``<svg>`` with a ``data-bk-error`` attribute; the
    original (sanitised, escaped) source is kept inside a ``<desc>`` for
    proofread UIs. An empty ``<svg>`` rasterizes to a blank page, so the
    pipeline degrades gracefully instead of raising.
    """
    escaped = escape(strip_xml_invalid_chars(surface))
    reason_attr = quoteattr(strip_xml_invalid_chars(reason))
    return f"<svg data-bk-error={reason_attr}><desc>{escaped}</desc></svg>"


def _load() -> SVGSourceAdapter:
    """Factory — kept symmetric with the other adapters even though the
    pass-through doesn't need a third-party library."""
    return SVGSourceAdapter()
