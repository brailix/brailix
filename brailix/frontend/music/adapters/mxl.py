"""``.mxl`` adapter — MusicXML in a ZIP container.

The .mxl format is a single-entry (or rarely multi-entry) ZIP whose
``META-INF/container.xml`` points at the real MusicXML file inside.
This adapter unzips it with stdlib :mod:`zipfile`, finds the rootfile,
and hands the inner XML to the :class:`MusicXMLSourceAdapter`.

Zero third-party dependencies. See ``ARCHITECTURE.md`` /
§17.2.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import (
    MusicXMLSourceAdapter,
    music_error_wrap,
)


@dataclass(slots=True)
class MxlSourceAdapter:
    """Unzip an ``.mxl`` payload and reuse the MusicXML adapter."""

    source: str = "mxl"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, str):
            # MXL is binary — callers handing a string almost certainly
            # already have the inner XML; route it back through the
            # musicxml adapter rather than failing.
            return MusicXMLSourceAdapter().to_musicxml(src, ctx)
        if not src:
            return music_error_wrap("", reason="empty .mxl payload")
        try:
            with zipfile.ZipFile(io.BytesIO(src)) as zf:
                inner_name = _find_rootfile(zf)
                if inner_name is None:
                    return music_error_wrap(
                        "",
                        reason=(
                            "no META-INF/container.xml or rootfile path "
                            "in .mxl archive"
                        ),
                    )
                try:
                    inner_bytes = zf.read(inner_name)
                except KeyError:
                    return music_error_wrap(
                        inner_name,
                        reason=f"rootfile {inner_name!r} missing from .mxl",
                    )
        except zipfile.BadZipFile as e:
            return music_error_wrap("", reason=f"not a valid ZIP: {e}")
        except Exception as e:  # noqa: BLE001 — soft-failure contract
            # zipfile raises more than BadZipFile: RuntimeError for an
            # encrypted entry, zlib.error for a corrupt deflate stream,
            # NotImplementedError for an unsupported compression
            # method.  Each means "this .mxl can't be read" — degrade
            # like every other adapter instead of crashing the pipeline.
            return music_error_wrap("", reason=f"unreadable .mxl: {e!r}")
        return MusicXMLSourceAdapter().to_musicxml(inner_bytes, ctx)


def _find_rootfile(zf: zipfile.ZipFile) -> str | None:
    """Locate the MusicXML rootfile inside an MXL archive.

    Per the W3C MusicXML container spec, ``META-INF/container.xml``
    holds a ``<rootfiles>`` block with one or more ``<rootfile>``
    entries; the first one is the main score by spec.  We take the first
    ``<rootfile>`` with a ``full-path`` attribute — the ``media-type``
    attribute is not consulted.

    Falls back to scanning for any top-level ``*.xml`` /
    ``*.musicxml`` entry when ``container.xml`` is missing or
    malformed — some tools (older Dorico exports) skip it.
    """
    try:
        container_bytes = zf.read("META-INF/container.xml")
    except KeyError:
        return _fallback_xml_entry(zf)
    try:
        root = ET.fromstring(container_bytes)
    except ET.ParseError:
        return _fallback_xml_entry(zf)
    for rf in root.iter():
        local = rf.tag.split("}", 1)[-1]
        if local == "rootfile":
            path = rf.attrib.get("full-path")
            if path:
                return path
    return _fallback_xml_entry(zf)


def _fallback_xml_entry(zf: zipfile.ZipFile) -> str | None:
    """Scan the archive for a plausible MusicXML entry when
    container.xml is missing or malformed."""
    for info in zf.infolist():
        name = info.filename
        if name.startswith("META-INF/"):
            continue
        lower = name.lower()
        if lower.endswith(".musicxml") or lower.endswith(".xml"):
            return name
    return None


def _load() -> MxlSourceAdapter:
    """Factory. ``.mxl`` handling needs no third-party packages —
    stdlib :mod:`zipfile` + :mod:`xml.etree.ElementTree` cover it."""
    return MxlSourceAdapter()
