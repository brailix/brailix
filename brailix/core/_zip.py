"""Shared ZIP-container facts — generic, format-independent.

Two of the formats brailix reads are ZIP archives with an XML payload inside:
Word's ``.docx`` (input layer) and MusicXML's ``.mxl`` (music frontend). Both
have to decide whether an archive is worth opening *before* opening it, and
"does this archive hold more members than we are willing to pay for" is one
fact about the ZIP format, not two — so it lives here, beside the other
cross-layer standard-library plumbing, and each container applies its own limit
and its own reaction to it. Same split as
:data:`~brailix.core.errors.UNREADABLE_ZIP_MEMBER_ERRORS`, which is one shared
fact about :mod:`zipfile` under two different policies.

**Why before, and not the member loop.** Both containers already cap
decompressed bytes per member and in total, and ``.docx`` capped the member
*count* as well — but it did so from ``ZipFile.infolist()``, which is the list
:class:`zipfile.ZipFile` builds by parsing the whole central directory in its
constructor. By the time that count can be compared against anything, every
:class:`zipfile.ZipInfo` it would have rejected has already been allocated. An
archive of a few megabytes holds tens of thousands of central-directory records
— ~46 bytes each, and the file compresses to almost nothing when it is itself
served compressed — and the cost of turning those into Python objects lands
entirely inside the constructor.

**Why the count is not read off the End Of Central Directory record.** It used
to be: the EOCD carries an ``entries_total`` field, it sits at a known offset,
and reading one integer is as cheap as a preflight gets. It is also worth
nothing as a bound. :class:`zipfile.ZipFile` never reads that field when it
builds its list — it walks the central directory by *size*, record after
record, until it has consumed ``size_cd`` bytes — so an archive that keeps a
full directory and writes ``entries_total = 1`` gets every one of those records
turned into a ``ZipInfo`` while the preflight sees a one-entry archive. The
field is a claim by the same party the limit exists to constrain.

So the walk below is the same walk the constructor is about to do, minus the
allocation: locate the central directory the way :mod:`zipfile` locates it,
step over the records by their own length fields, and stop the moment the count
passes the caller's limit. Bounded by ``limit`` rather than by the archive, and
what it counts is what ``ZipFile`` will materialise — which is the only number
a resource limit can be applied to.
"""

from __future__ import annotations

import struct as _struct

# End Of Central Directory record: a fixed 22-byte header ending in a variable
# comment, so it is found by scanning back from the end of the file for its
# signature. The comment length field is 16 bits, so the record starts at most
# 65535 + 22 bytes from the end and the scan is bounded.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_EOCD_SEARCH = 0xFFFF + _EOCD_SIZE

# ZIP64: when a field of the classic EOCD would overflow, it is written as all
# ones and the real value lives in a ZIP64 EOCD record, found through a locator
# that sits immediately before the classic one. :mod:`zipfile` reads both by
# *position* (locator immediately before the EOCD, record immediately before
# the locator) and ignores the offset the locator stores, so this does too —
# the point is to agree with the constructor, not with the spec.
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56

# Central directory file header: a fixed 46-byte record followed by three
# variable-length fields whose sizes it declares (filename at +28, extra at
# +30, comment at +32). Stepping over those three is how both this walk and
# :meth:`zipfile.ZipFile._RealGetContents` find the next record.
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CENTRAL_HEADER_SIZE = 46
_CENTRAL_VARIABLE_LENGTHS_AT = 28


def _classic_eocd_start(data: bytes) -> int | None:
    """Offset of the End Of Central Directory record, or ``None``.

    Mirrors :func:`zipfile._EndRecData`, including its order: the fast path
    tests the final 22 bytes for a comment-less record, and only a miss falls
    back to scanning the last 64 KiB for the signature. The two can disagree —
    a record whose own field bytes happen to spell the signature would be
    "found" a second time inside itself by the scan — and where they disagree,
    what matters is which one the constructor will act on.
    """
    size = len(data)
    if (
        size >= _EOCD_SIZE
        and data[size - _EOCD_SIZE:size - _EOCD_SIZE + 4] == _EOCD_SIGNATURE
        and data[size - 2:] == b"\x00\x00"
    ):
        return size - _EOCD_SIZE
    start = data.rfind(_EOCD_SIGNATURE, max(0, size - _MAX_EOCD_SEARCH))
    if start < 0 or start + _EOCD_SIZE > size:
        return None
    return start


def _zip64_record_positions(data: bytes, eocd_start: int) -> tuple[int, ...]:
    """Where the ZIP64 EOCD record might be, per the locator behind the EOCD.

    Two answers, because :mod:`zipfile` has given two. Every version puts the
    directory's end at the ZIP64 record's position, but they disagree on how
    that position is found: up to 3.13.5 the locator's stored offset is ignored
    and the record is assumed to sit immediately before the locator, while
    3.13.14 reads the stored offset, uses it, and only falls back to adjacency
    when no record is there. The two coincide for every well-formed archive —
    3.13.14 additionally *requires* them to, by checking that the directory ends
    exactly at the stored offset — and a preflight has no business betting on
    which interpreter it is running under. Both are returned, in the order they
    should be tried; the caller counts under each and takes the larger.

    Empty when there is no readable locator or no record at either position, in
    which case the classic record's own 32-bit fields stand — and also when the
    locator says the archive spans disks, which :class:`zipfile.ZipFile` refuses
    outright, so there is nothing to bound.
    """
    locator = eocd_start - _ZIP64_LOCATOR_SIZE
    if locator < 0 or data[locator:locator + 4] != _ZIP64_LOCATOR_SIGNATURE:
        return ()
    # <sig:4> disk:4 zip64_eocd_offset:8 disks:4
    disk, stored, disks = _struct.unpack_from("<LQL", data, locator + 4)
    if disk != 0 or disks > 1:
        return ()
    adjacent = locator - _ZIP64_EOCD_SIZE
    out = [
        at
        for at in dict.fromkeys((int(stored), adjacent))
        if 0 <= at <= len(data) - _ZIP64_EOCD_SIZE
        and data[at:at + 4] == _ZIP64_EOCD_SIGNATURE
    ]
    return tuple(out)


def _central_directory_candidates(data: bytes) -> tuple[tuple[int, int], ...]:
    """Every ``(start, size)`` a :class:`zipfile.ZipFile` might read as the
    central directory.

    The stored offset is relative to the start of the archive, which is not
    necessarily the start of the file: a ZIP appended to something else (a
    self-extracting stub) keeps its internal offsets and :mod:`zipfile`
    recovers the difference by comparing where the EOCD *says* the directory
    ends with where it actually is — the directory ends where the end record
    begins. That correction is reproduced here for the same reason the record
    layout is: a preflight that located a different region than the constructor
    would be counting something else.

    More than one candidate only when a ZIP64 locator disagrees with itself
    (see :func:`_zip64_record_positions`); a well-formed archive yields exactly
    one.

    Empty when there is no readable EOCD, or when the arithmetic lands before
    the start of the file (which :class:`zipfile.ZipFile` rejects as a bad
    offset). Neither is this function's diagnosis to make: both callers already
    have a canonical "this is not a readable archive" error further down
    (python-docx's ``PackageNotFoundError`` for ``.docx``,
    :class:`zipfile.BadZipFile` for ``.mxl``), and a second one raised from a
    preflight would be a different message for the same file depending on which
    check happened to notice first.
    """
    eocd = _classic_eocd_start(data)
    if eocd is None:
        return ()
    ends: list[tuple[int, int]] = []
    for record in _zip64_record_positions(data, eocd):
        # <sig:4> size:8 made_by:2 needed:2 disk:4 cd_disk:4
        # entries_this_disk:8 entries_total:8 dir_size:8 dir_offset:8
        (size64,) = _struct.unpack_from("<Q", data, record + 40)
        ends.append((record, int(size64)))
    if not ends:
        # <sig:4> disk:2 cd_disk:2 entries_this_disk:2 entries_total:2
        # dir_size:4 dir_offset:4 comment_len:2
        (size,) = _struct.unpack_from("<L", data, eocd + 12)
        ends.append((eocd, int(size)))
    return tuple(
        (end - size, size) for end, size in ends if end - size >= 0
    )


def zip_entry_count_exceeds(data: bytes, limit: int) -> bool:
    """Whether ``data``'s central directory holds more than ``limit`` records.

    Walks the directory the way :class:`zipfile.ZipFile` is about to — record
    signature, then the three declared variable-length fields to reach the next
    one — and stops as soon as the count passes ``limit``. So the work is
    bounded by the *limit*, not by the archive, and no ``ZipInfo`` is allocated
    for any of it.

    ``False`` when the archive holds ``limit`` records or fewer, and also when
    the directory cannot be walked at all: no readable EOCD, a record without
    its signature, a header running past the end of the data. Every one of
    those stops :class:`zipfile.ZipFile` at exactly the same record with a
    :class:`zipfile.BadZipFile`, so whatever it had allocated by then is
    already under the limit — there is nothing for a resource check to refuse,
    and the caller's own canonical error for an unreadable archive is the one
    that should speak.

    When a ZIP64 locator leaves two possible directory positions (see
    :func:`_zip64_record_positions`), **both** are walked and either one going
    over is enough. Under-counting is the only failure this check cannot
    afford: it is the whole reason the declared count was abandoned, and
    picking one interpreter's answer would reintroduce it on the other's.
    """
    return any(
        _records_exceed(data, start, size, limit)
        for start, size in _central_directory_candidates(data)
    )


def _records_exceed(data: bytes, start: int, size: int, limit: int) -> bool:
    """Whether the directory at ``start`` holds more than ``limit`` records."""
    # ZipFile reads the directory into a buffer of whatever is actually there
    # and raises "Truncated central directory" when a header runs off the end
    # of it, so a record must fit inside BOTH the declared size and the data.
    end = min(start + size, len(data))
    at = start
    count = 0
    while at + _CENTRAL_HEADER_SIZE <= end:
        if data[at:at + 4] != _CENTRAL_SIGNATURE:
            return False
        count += 1
        if count > limit:
            return True
        filename, extra, comment = _struct.unpack_from(
            "<HHH", data, at + _CENTRAL_VARIABLE_LENGTHS_AT
        )
        at += _CENTRAL_HEADER_SIZE + filename + extra + comment
    return False
