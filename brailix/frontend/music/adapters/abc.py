"""ABC notation source adapter — converts ABC text to MusicXML.

Uses ``abc-xml-converter`` (a packaging of Wim Vree's classic
``abc2xml`` script). Pure Python, no external binaries. The adapter
is registered with ``extra="abc"``; missing dependency surfaces as
:class:`~brailix.core.errors.MissingExtraError` pointing at
``pip install brailix[abc]``.

ABC is a text format, so input is ``str`` — bytes get utf-8 decoded
first. Conversion errors fall through to a ``<music-error>`` MusicXML
placeholder per the music subsystem's soft-failure contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import music_error_wrap


@dataclass(slots=True)
class AbcSourceAdapter:
    """ABC text → MusicXML via ``abc-xml-converter``.

    Soft-failure: any conversion exception comes back as a
    ``<music-error>`` MusicXML doc.
    """

    source: str = "abc"

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
            return music_error_wrap("", reason="empty abc payload")
        try:
            # abc_xml_converter's public API has shifted across versions;
            # try the documented entry points in order. The Wim Vree
            # original exposes ``convert_string`` / ``do_convert``;
            # newer wrappers expose ``abc2xml``.
            from abc_xml_converter import abc2xml  # noqa: WPS433
        except ImportError:
            return music_error_wrap(
                text,
                reason=(
                    "abc-xml-converter not installed "
                    "(pip install brailix[abc])"
                ),
            )
        try:
            # The packaging exposes a function or module-level
            # ``convert(text) -> str``; fall back to attribute probing
            # so we don't pin a specific entry point.
            convert = getattr(abc2xml, "convert", None) \
                or getattr(abc2xml, "abc_to_xml", None)
            if convert is None:
                return music_error_wrap(
                    text,
                    reason=(
                        "abc-xml-converter lacks expected ``convert`` API"
                    ),
                )
            return convert(text)
        except Exception as e:  # noqa: BLE001 — third-party failures vary
            return music_error_wrap(text, reason=f"abc-xml-converter error: {e}")


def _load() -> AbcSourceAdapter:
    """Lazy-load the adapter. Imports the converter here so the
    registry's MissingExtraError fires at registration-touch time."""
    import abc_xml_converter  # noqa: F401, WPS433 — registration-time gate

    return AbcSourceAdapter()
