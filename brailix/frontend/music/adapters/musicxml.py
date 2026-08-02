"""Pass-through MusicXML adapter.

Input is already MusicXML, so the adapter only validates that the
string parses as well-formed XML and returns it. Malformed input is
wrapped inside a single ``<music-error>`` document so the normalizer +
backend produce a clean ``MUSIC_PARSE_RECOVERY`` warning rather than
crashing.

The :func:`music_error_wrap` helper is also imported by the
normalizer and used by sibling adapters (``mxl`` / ``plain``) for
soft-failure reporting — exposed at module level for that reason.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from brailix.core._xml import safe_fromstring, strip_xml_invalid_chars, strip_xml_prolog
from brailix.core.context import MusicContext


@dataclass(slots=True)
class MusicXMLSourceAdapter:
    """Trivial adapter: accept MusicXML in, give MusicXML out.

    Strips a leading XML declaration / DOCTYPE so ElementTree (which
    rejects DTD constructs in fragment form) accepts the input. The
    normalizer then drops any remaining namespace prefix.
    """

    source: str = "musicxml"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return music_error_wrap(repr(src), reason="non-utf8 bytes")
        text = src.strip()
        if not text:
            return music_error_wrap("", reason="empty input")
        text = strip_xml_prolog(text)
        try:
            safe_fromstring(text)
        except ET.ParseError as e:
            return music_error_wrap(text, reason=f"parse error: {e}")
        return text
def music_error_wrap(surface: str, *, reason: str) -> str:
    """Build a minimal MusicXML document carrying a single
    ``<music-error>``.

    The root element is ``<score-partwise>`` so the normaliser /
    backend never have to special-case the root tag when an adapter
    soft-fails. ``surface`` is the original input (kept for proofread
    UIs); ``reason`` is a short human-readable string explaining what
    went wrong.

    Shared by every adapter that needs to report a soft failure.
    """
    escaped = escape(strip_xml_invalid_chars(surface))
    escaped_reason = quoteattr(strip_xml_invalid_chars(reason))
    return (
        "<score-partwise>"
        f"<music-error data-reason={escaped_reason}>{escaped}</music-error>"
        "</score-partwise>"
    )


def _load() -> MusicXMLSourceAdapter:
    """Factory — kept symmetric with other adapters even though the
    pass-through doesn't need a third-party library."""
    return MusicXMLSourceAdapter()
