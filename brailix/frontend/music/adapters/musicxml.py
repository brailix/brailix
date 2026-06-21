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

from brailix.core._xml import safe_fromstring, strip_xml_invalid_chars
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
        text = _strip_xml_prolog(text)
        try:
            safe_fromstring(text)
        except ET.ParseError as e:
            return music_error_wrap(text, reason=f"parse error: {e}")
        return text


def _strip_xml_prolog(text: str) -> str:
    """Remove a leading ``<?xml ...?>`` declaration and optional
    ``<!DOCTYPE ...>`` — ElementTree's ``fromstring`` accepts the XML
    declaration but trips on a DOCTYPE that references an external DTD
    (common in MusicXML files exported by older Finale/Sibelius)."""
    out = text
    if out.startswith("<?xml"):
        end = out.find("?>")
        if end != -1:
            out = out[end + 2:].lstrip()
    if out.startswith("<!DOCTYPE"):
        # Scan forward, balancing brackets, to find the matching ``>``.
        depth = 0
        for i, ch in enumerate(out):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            elif ch == ">" and depth == 0:
                out = out[i + 1:].lstrip()
                break
    return out


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
