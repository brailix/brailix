"""Shared ZIP-container facts — generic, format-independent.

Two of the formats brailix reads are ZIP archives with an XML payload inside:
Word's ``.docx`` (input layer) and MusicXML's ``.mxl`` (music frontend). Both
have to decide whether an archive is worth opening *before* opening it, and
"how many entries does this archive claim to hold" is one fact about the ZIP
format, not two — so it lives here, beside the other cross-layer standard-
library plumbing, and each container applies its own limit and its own reaction
to it. Same split as :data:`~brailix.core.errors.UNREADABLE_ZIP_MEMBER_ERRORS`,
which is one shared fact about :mod:`zipfile` under two different policies.

**Why before, and not the member loop.** Both containers already cap decompressed
bytes per member and in total, and ``.docx`` capped the member *count* as well —
but it did so from ``ZipFile.infolist()``, which is the list
:class:`zipfile.ZipFile` builds by parsing the whole central directory in its
constructor. By the time that count can be compared against anything, every
:class:`zipfile.ZipInfo` it would have rejected has already been allocated. An
archive of a few hundred kilobytes can declare millions of zero-length entries
— the central directory is ~46 bytes per record and compresses to almost
nothing when the file is itself served compressed — and the cost of turning
those into Python objects lands entirely inside the constructor. The count read
here comes off the fixed-size End Of Central Directory record instead, which is
a bounded read at a known offset and is what the ZIP format put it there for.
"""

from __future__ import annotations

import struct

# End Of Central Directory record: a fixed 22-byte header ending in a variable
# comment, so it is found by scanning back from the end of the file for its
# signature. The comment length field is 16 bits, so the record starts at most
# 65535 + 22 bytes from the end and the scan is bounded.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_EOCD_SEARCH = 0xFFFF + _EOCD_SIZE

# ZIP64: when a field of the classic EOCD would overflow, it is written as all
# ones and the real value lives in a ZIP64 EOCD record, found through a locator
# that sits immediately before the classic one.
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_UINT16_MAX = 0xFFFF


def zip_entry_count(data: bytes) -> int | None:
    """How many entries ``data``'s central directory claims to hold.

    Reads the count out of the End Of Central Directory record (and the ZIP64
    one behind it, when the classic field has overflowed) without parsing the
    directory itself — the point being to answer *before* something else pays
    to parse it.

    ``None`` when there is no readable EOCD: a blob that is not a ZIP at all, a
    truncated one, a header that does not decode. That is deliberately not an
    error here. Neither caller wants to own "this is not a ZIP" — both already
    have a canonical diagnosis for it further down (python-docx's
    ``PackageNotFoundError`` for ``.docx``, ``BadZipFile`` for ``.mxl``), and a
    second one raised from a preflight would be a different message for the
    same file depending on which check happened to notice first.

    The count is what the archive *claims*, which is exactly the right thing to
    gate on: it is also what :class:`zipfile.ZipFile` will believe and allocate
    for. An archive that lies low still gets its real entries counted by the
    member loop that follows.
    """
    start = data.rfind(_EOCD_SIGNATURE, max(0, len(data) - _MAX_EOCD_SEARCH))
    if start < 0 or start + _EOCD_SIZE > len(data):
        return None
    try:
        # <sig:4> disk:2 cd_disk:2 entries_this_disk:2 entries_total:2 ...
        entries = struct.unpack_from("<H", data, start + 10)[0]
    except struct.error:  # pragma: no cover — bounds already checked above
        return None
    if entries != _UINT16_MAX:
        return entries
    return _zip64_entry_count(data, start)


def _zip64_entry_count(data: bytes, eocd_start: int) -> int | None:
    """The entry count from the ZIP64 EOCD record behind the classic one.

    An all-ones classic count means "look in ZIP64" — but it is also what a
    genuine 65535-entry archive writes, so a missing or unreadable ZIP64 record
    falls back to that literal reading rather than to "unknown". Refusing to
    answer would let an archive opt out of the cap by writing 0xFFFF.
    """
    locator = eocd_start - _ZIP64_LOCATOR_SIZE
    if locator < 0 or data[locator:locator + 4] != _ZIP64_LOCATOR_SIGNATURE:
        return _UINT16_MAX
    try:
        # <sig:4> disk:4 zip64_eocd_offset:8 disks:4
        offset = struct.unpack_from("<Q", data, locator + 8)[0]
    except struct.error:  # pragma: no cover — 20 bytes verified above
        return _UINT16_MAX
    if offset + 40 > len(data) or data[offset:offset + 4] != _ZIP64_EOCD_SIGNATURE:
        return _UINT16_MAX
    try:
        # <sig:4> size:8 made_by:2 needed:2 disk:4 cd_disk:4
        # entries_this_disk:8 entries_total:8 ...
        return int(struct.unpack_from("<Q", data, offset + 32)[0])
    except struct.error:  # pragma: no cover — 40 bytes verified above
        return _UINT16_MAX


def zip_entry_count_exceeds(data: bytes, limit: int) -> int | None:
    """The declared entry count when it is over ``limit``, else ``None``.

    The shape both callers want: a truthy answer carrying the number to put in
    their own error message, and nothing to react to otherwise — including when
    the blob has no readable EOCD, which is not this check's business to
    diagnose (see :func:`zip_entry_count`).
    """
    entries = zip_entry_count(data)
    if entries is not None and entries > limit:
        return entries
    return None
