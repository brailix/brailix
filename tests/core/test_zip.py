"""The declared-entry-count preflight (:mod:`brailix.core._zip`).

Both ZIP containers brailix reads cap how many members an archive may hold.
Reading that count from ``ZipFile.infolist()`` — which is what ``.docx`` did,
and what ``.mxl`` did not do at all — measures the cost after paying it: the
constructor has already parsed the whole central directory and allocated a
``ZipInfo`` per entry by the time the number exists. The count comes off the
fixed-size End Of Central Directory record instead.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from brailix.core._zip import zip_entry_count, zip_entry_count_exceeds


def build_zip(count: int, *, comment: bytes = b"") -> bytes:
    """A real ZIP holding ``count`` empty members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for i in range(count):
            zf.writestr(f"m{i}.txt", b"")
        zf.comment = comment
    return buffer.getvalue()


class TestEntryCount:
    @pytest.mark.parametrize("count", [0, 1, 5, 300])
    def test_matches_what_zipfile_finds(self, count: int) -> None:
        data = build_zip(count)
        assert zip_entry_count(data) == count
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len(zf.infolist()) == count

    def test_survives_an_archive_comment(self) -> None:
        # The EOCD ends in a variable-length comment, which is why it has to be
        # found by scanning back for its signature rather than read at a fixed
        # offset from the end.
        assert zip_entry_count(build_zip(3, comment=b"x" * 5000)) == 3

    def test_a_planted_eocd_is_read_the_same_way_zipfile_reads_it(self) -> None:
        # A second EOCD signature planted in the archive comment makes "which
        # record is the real one" ambiguous, and the answer that matters is not
        # the spec's — it is ``zipfile``'s, because the count exists to predict
        # what ``ZipFile`` is about to allocate. Both scan back and take the
        # last signature, so both read the planted record (and its zero
        # entries). A preflight that disagreed here would gate on a number
        # nobody was going to act on.
        planted = b"PK\x05\x06" + b"\x00" * 18
        data = build_zip(4, comment=planted)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zip_entry_count(data) == len(zf.infolist())

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"not a zip at all",
            b"PK\x05\x06",  # signature, but truncated before the fields
            b"\x00" * 5000,
        ],
    )
    def test_unreadable_blobs_report_nothing_rather_than_raising(
        self, data: bytes
    ) -> None:
        # "This is not a ZIP" is not this check's diagnosis to make — both
        # callers already have a canonical error for it further down, and a
        # second one raised here would change the message a user sees
        # depending on which check happened to notice first.
        assert zip_entry_count(data) is None
        assert zip_entry_count_exceeds(data, 0) is None


class TestZip64:
    """A classic count of 0xFFFF means "look in the ZIP64 record"."""

    def test_reads_the_zip64_count(self) -> None:
        data = _zip64_archive(entries=70_000)
        assert zip_entry_count(data) == 70_000

    def test_an_all_ones_count_with_no_zip64_record_reads_literally(
        self,
    ) -> None:
        # 0xFFFF is also what a genuine 65535-entry archive writes. Answering
        # "unknown" here would let an archive opt out of the cap by claiming
        # exactly that many.
        data = bytearray(build_zip(2))
        start = data.rfind(b"PK\x05\x06")
        struct.pack_into("<H", data, start + 10, 0xFFFF)
        assert zip_entry_count(bytes(data)) == 0xFFFF

    @pytest.mark.parametrize("clobber", [b"PK\x06\x07", b"PK\x06\x06"])
    def test_an_unreadable_zip64_record_falls_back_rather_than_raising(
        self, clobber: bytes
    ) -> None:
        # Locator or record damaged: the classic 0xFFFF is still there and is
        # still read literally, which keeps the cap applying to a malformed
        # archive instead of the archive escaping it.
        data = bytearray(_zip64_archive(entries=70_000))
        at = data.rfind(clobber)
        data[at:at + 4] = b"XXXX"
        assert zip_entry_count(bytes(data)) == 0xFFFF


def _zip64_archive(*, entries: int) -> bytes:
    """A ZIP64 EOCD record + locator + classic EOCD claiming ``entries``.

    Hand-built rather than written by :mod:`zipfile`: producing a genuine
    70000-entry archive to read one integer back out costs seconds and tens of
    megabytes, and what is under test is the record layout.
    """
    zip64_eocd = struct.pack(
        "<4sQHHIIQQQQ",
        b"PK\x06\x06",
        44,  # size of remaining record
        45,  # version made by
        45,  # version needed
        0,  # this disk
        0,  # disk with central directory
        entries,  # entries on this disk
        entries,  # entries total
        entries * 46,  # central directory size
        0,  # central directory offset
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, 0, 1)
    eocd = struct.pack(
        "<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0, 0, 0
    )
    return zip64_eocd + locator + eocd


class TestEntryCountExceeds:
    def test_returns_the_count_when_over(self) -> None:
        assert zip_entry_count_exceeds(build_zip(10), 4) == 10

    def test_returns_none_at_the_limit(self) -> None:
        assert zip_entry_count_exceeds(build_zip(10), 10) is None

    def test_returns_none_under_the_limit(self) -> None:
        assert zip_entry_count_exceeds(build_zip(3), 10) is None
