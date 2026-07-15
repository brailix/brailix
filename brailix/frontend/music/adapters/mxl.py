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
import zlib
from dataclasses import dataclass

from brailix.core._xml import safe_fromstring
from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import (
    MusicXMLSourceAdapter,
    music_error_wrap,
)

# Cap the uncompressed size of any single member we read out of an .mxl
# archive. A small zip can declare a member that inflates to gigabytes (a
# "zip bomb"), exhausting memory before any parse — and the soft-failure
# contract below only catches BadZipFile / corrupt-deflate, not a *valid*
# but enormous member, which OOMs at the read. A real score's MusicXML is a
# few MB; 64 MB is generous headroom while still bounding a malicious file.
# The cap is enforced on the *actual* decompressed bytes (chunked read), not
# ZipInfo.file_size, which a crafted archive can understate.
_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


class _MemberTooLarge(Exception):
    """An .mxl member's decompressed size exceeds :data:`_MAX_MEMBER_BYTES`."""


def _read_member_capped(zf: zipfile.ZipFile, name: str) -> bytes:
    """Read one archive member, aborting if it inflates past the cap.

    Raises :class:`KeyError` if ``name`` is absent (as ``ZipFile.read``
    would) and :class:`_MemberTooLarge` once the decompressed stream
    crosses :data:`_MAX_MEMBER_BYTES`, so a zip bomb is stopped mid-
    inflate instead of after fully materialising in memory.
    """
    chunks: list[bytes] = []
    total = 0
    with zf.open(name) as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_MEMBER_BYTES:
                raise _MemberTooLarge(name)
            chunks.append(chunk)
    return b"".join(chunks)


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
                    inner_bytes = _read_member_capped(zf, inner_name)
                except KeyError:
                    return music_error_wrap(
                        inner_name,
                        reason=f"rootfile {inner_name!r} missing from .mxl",
                    )
                except _MemberTooLarge:
                    return music_error_wrap(
                        "",
                        reason=(
                            f"rootfile {inner_name!r} exceeds the "
                            f"{_MAX_MEMBER_BYTES // (1024 * 1024)} MB "
                            "decompression cap (possible zip bomb)"
                        ),
                    )
        except zipfile.BadZipFile as e:
            return music_error_wrap("", reason=f"not a valid ZIP: {e}")
        except (
            zlib.error,
            NotImplementedError,
            RuntimeError,
            EOFError,
            ValueError,
        ) as e:
            # zipfile raises more than BadZipFile for an *unreadable input*:
            # RuntimeError for an encrypted entry, zlib.error for a corrupt
            # deflate stream, NotImplementedError for an unsupported compression
            # method, EOFError / ValueError for a truncated or malformed stream.
            # Each means "this .mxl can't be read" — degrade like every other
            # adapter instead of crashing the pipeline.
            #
            # Deliberately NOT ``except Exception``: a programming error inside
            # this adapter (an AttributeError / TypeError / KeyError from a code
            # regression) must surface as a real crash, not be disguised as
            # "unreadable .mxl". A green pipeline silently hiding a maintainer's
            # bug behind a soft-failure is worse than a loud, locatable failure —
            # only genuine *input* errors are soft-failed here.
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
        container_bytes = _read_member_capped(zf, "META-INF/container.xml")
    except (KeyError, _MemberTooLarge):
        return _fallback_xml_entry(zf)
    try:
        root = safe_fromstring(container_bytes)
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
