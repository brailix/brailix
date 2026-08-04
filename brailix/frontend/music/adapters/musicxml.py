"""Pass-through MusicXML adapter.

Input is already MusicXML, so the adapter only decodes it (if it arrived as
bytes), validates that it parses as well-formed XML, and returns it.
Malformed input is wrapped inside a single ``<music-error>`` document so the
normalizer + backend produce a clean ``MUSIC_PARSE_RECOVERY`` warning rather
than crashing.

The :func:`music_error_wrap` helper is also imported by the
normalizer and used by sibling adapters (``mxl`` / ``plain``) for
soft-failure reporting — exposed at module level for that reason.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from xml.sax.saxutils import escape as _escape
from xml.sax.saxutils import quoteattr as _quoteattr

from brailix.core._xml import (
    XmlDecodeError,
    decode_xml_bytes,
    safe_fromstring,
    strip_xml_invalid_chars,
    strip_xml_prolog,
)
from brailix.core.context import MusicContext


@_dataclass(slots=True)
class MusicXMLSourceAdapter:
    """Trivial adapter: accept MusicXML in, give MusicXML out.

    Bytes are decoded by XML's own encoding rules
    (:func:`~brailix.core._xml.decode_xml_bytes`), so a UTF-16 score — which
    Finale and several Windows exporters do write, and which the input layer's
    file reader has always accepted — is read rather than refused.

    The prologue is then stripped so that a DOCTYPE carrying an internal
    subset does not run into the ``<!ENTITY`` refusal in
    :func:`~brailix.core._xml.safe_fromstring`; ElementTree itself parses a
    DOCTYPE, external identifier and all. The normalizer afterwards drops any
    remaining namespace prefix.
    """

    source: str = "musicxml"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = decode_xml_bytes(src)
            except XmlDecodeError as e:
                return music_error_wrap(repr(src), reason=f"undecodable bytes: {e}")
        text = src.strip()
        if not text:
            return music_error_wrap("", reason="empty input")
        text = strip_xml_prolog(text)
        try:
            safe_fromstring(text)
        except _ET.ParseError as e:
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
    escaped = _escape(strip_xml_invalid_chars(surface))
    escaped_reason = _quoteattr(strip_xml_invalid_chars(reason))
    return (
        "<score-partwise>"
        f"<music-error data-reason={escaped_reason}>{escaped}</music-error>"
        "</score-partwise>"
    )


def _load() -> MusicXMLSourceAdapter:
    """Factory — kept symmetric with other adapters even though the
    pass-through doesn't need a third-party library."""
    return MusicXMLSourceAdapter()
