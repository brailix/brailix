"""``.mxl`` adapter — MusicXML in a ZIP container.

The .mxl format is a single-entry (or rarely multi-entry) ZIP whose
``META-INF/container.xml`` points at the real MusicXML file inside.
This adapter unzips it with stdlib :mod:`zipfile`, finds the rootfile,
and hands the inner XML to the :class:`MusicXMLSourceAdapter`.

Zero third-party dependencies.
"""

from __future__ import annotations

import io as _io
import xml.etree.ElementTree as _ET
import zipfile as _zipfile
from dataclasses import dataclass as _dataclass

from brailix.core._xml import safe_fromstring
from brailix.core._zip import zip_entry_count_exceeds
from brailix.core.context import MusicContext
from brailix.core.errors import UNREADABLE_ZIP_MEMBER_ERRORS
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

# ...and cap what the whole archive can cost, which the per-member cap alone
# does not. Two budgets, both of which ``.docx`` has had and this did not:
#
# * the member COUNT, read off the End Of Central Directory record before
#   ``ZipFile`` is constructed (:func:`~brailix.core._zip.zip_entry_count_exceeds`).
#   A few hundred KB of archive can declare millions of zero-length entries,
#   and the whole cost of turning those into ``ZipInfo`` objects lands inside
#   the constructor — so a count taken from ``infolist()`` afterwards is a
#   measurement, not a limit. A ``.mxl`` holds a score, a container manifest
#   and perhaps a handful of images; 1024 is already far past generous.
# * the CUMULATIVE decompressed bytes across every member this adapter reads.
#   Only two are read on the happy path (the manifest and the rootfile), so
#   this is the belt to the per-member cap's braces — but "how many members
#   does the code read" is not something a resource bound should have to rest
#   on, since a container manifest is attacker-controlled and names its own
#   rootfile.
_MAX_MEMBERS = 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024


class _MemberTooLarge(Exception):
    """An .mxl read exceeded :data:`_MAX_MEMBER_BYTES` for one member or
    :data:`_MAX_TOTAL_BYTES` across the archive."""


class _Budget:
    """The decompressed bytes this adapter may still read from one archive.

    Per-archive rather than module state: two concurrent conversions must not
    share (or exhaust) one another's allowance.
    """

    __slots__ = ("remaining",)

    def __init__(self) -> None:
        self.remaining = _MAX_TOTAL_BYTES

    def spend(self, count: int) -> None:
        self.remaining -= count
        if self.remaining < 0:
            raise _MemberTooLarge("archive total")


def _read_member_capped(
    zf: _zipfile.ZipFile, name: str, budget: _Budget
) -> bytes:
    """Read one archive member, aborting if it inflates past a cap.

    Raises :class:`KeyError` if ``name`` is absent (as ``ZipFile.read``
    would) and :class:`_MemberTooLarge` once the decompressed stream
    crosses :data:`_MAX_MEMBER_BYTES` or exhausts ``budget``, so a zip bomb is
    stopped mid-inflate instead of after fully materialising in memory.
    """
    chunks: list[bytes] = []
    total = 0
    with zf.open(name) as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            budget.spend(len(chunk))
            if total > _MAX_MEMBER_BYTES:
                raise _MemberTooLarge(name)
            chunks.append(chunk)
    return b"".join(chunks)


@_dataclass(slots=True)
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
        declared = zip_entry_count_exceeds(src, _MAX_MEMBERS)
        if declared is not None:
            # Before ``ZipFile``, deliberately: this is the one check whose
            # whole value is being cheaper than parsing the central directory.
            return music_error_wrap(
                "",
                reason=(
                    f".mxl declares {declared} members, over the "
                    f"{_MAX_MEMBERS} limit (possible zip bomb)"
                ),
            )
        budget = _Budget()
        try:
            with _zipfile.ZipFile(_io.BytesIO(src)) as zf:
                inner_name = _find_rootfile(zf, budget)
                if inner_name is None:
                    return music_error_wrap(
                        "",
                        reason=(
                            "no META-INF/container.xml or rootfile path "
                            "in .mxl archive"
                        ),
                    )
                try:
                    inner_bytes = _read_member_capped(zf, inner_name, budget)
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
        except _zipfile.BadZipFile as e:
            return music_error_wrap("", reason=f"not a valid ZIP: {e}")
        except UNREADABLE_ZIP_MEMBER_ERRORS as e:
            # zipfile raises more than BadZipFile for an *unreadable input* —
            # an encrypted entry, a corrupt deflate stream, an unsupported
            # compression method, a truncated stream. Each means "this .mxl
            # can't be read", so degrade like every other adapter instead of
            # crashing the pipeline. The list of which exceptions those are is
            # one fact about zipfile, shared with the ``.docx`` preflight that
            # reads members the same way (and that once had only half of it);
            # the *policy* stays here — this vertical soft-fails where the
            # input layer raises. Deliberately NOT ``except Exception``: see
            # :data:`~brailix.core.errors.UNREADABLE_ZIP_MEMBER_ERRORS`.
            return music_error_wrap("", reason=f"unreadable .mxl: {e!r}")
        return MusicXMLSourceAdapter().to_musicxml(inner_bytes, ctx)


def _find_rootfile(zf: _zipfile.ZipFile, budget: _Budget) -> str | None:
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
        container_bytes = _read_member_capped(
            zf, "META-INF/container.xml", budget
        )
    except (KeyError, _MemberTooLarge):
        return _fallback_xml_entry(zf)
    try:
        root = safe_fromstring(container_bytes)
    except _ET.ParseError:
        return _fallback_xml_entry(zf)
    for rf in root.iter():
        local = rf.tag.split("}", 1)[-1]
        if local == "rootfile":
            path = rf.attrib.get("full-path")
            if path:
                return path
    return _fallback_xml_entry(zf)


def _fallback_xml_entry(zf: _zipfile.ZipFile) -> str | None:
    """Scan the archive for a plausible MusicXML entry when
    container.xml is missing or malformed.

    Bounded by :data:`_MAX_MEMBERS`, like the declared count checked before the
    archive was opened. The entries really present can be fewer than the count
    the End Of Central Directory claimed (that is a claim, not a guarantee) but
    they can also be more, and this walk is the one place that would otherwise
    touch all of them.
    """
    for index, info in enumerate(zf.infolist()):
        if index >= _MAX_MEMBERS:
            return None
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
