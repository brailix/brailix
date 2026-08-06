"""The member-count preflight (:mod:`brailix.core._zip`).

Both ZIP containers brailix reads cap how many members an archive may hold.
Reading that count from ``ZipFile.infolist()`` — which is what ``.docx`` did,
and what ``.mxl`` did not do at all — measures the cost after paying it: the
constructor has already parsed the whole central directory and allocated a
``ZipInfo`` per entry by the time the number exists.

Reading it off the End Of Central Directory record instead — which is what this
did next — is cheap and worthless: ``ZipFile`` never consults that field, so an
archive that keeps its directory and writes ``entries_total = 1`` walks straight
past the gate. What the preflight counts now is the records themselves, and the
tests that matter are the ones where the two numbers disagree.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from brailix.core._zip import zip_entry_count_exceeds


def build_zip(count: int, *, comment: bytes = b"") -> bytes:
    """A real ZIP holding ``count`` empty members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for i in range(count):
            zf.writestr(f"m{i}.txt", b"")
        zf.comment = comment
    return buffer.getvalue()


def understate_eocd(data: bytes, claim: int = 1) -> bytes:
    """The same archive, with the EOCD entry counts rewritten to ``claim``.

    Byte mutation of a real archive rather than a hand-built lookalike: what is
    under test is that the *directory* still decides, and only a file that keeps
    a genuine directory behind a lying header shows the difference.
    """
    out = bytearray(data)
    start = out.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", out, start + 8, claim, claim)
    return bytes(out)


class TestCountsTheRecordsNotTheClaim:
    @pytest.mark.parametrize("count", [0, 1, 5, 300])
    def test_agrees_with_what_zipfile_materialises(self, count: int) -> None:
        data = build_zip(count)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len(zf.infolist()) == count
        assert not zip_entry_count_exceeds(data, count)
        assert zip_entry_count_exceeds(data, count - 1) is (count > 0)

    def test_an_understated_eocd_does_not_hide_the_members(self) -> None:
        # The whole reason the EOCD count was dropped. A one-entry claim in
        # front of a 40-record directory: ``ZipFile`` allocates all 40 (it
        # walks the directory by size, never reading that field), so a
        # preflight that believed the claim gated on a number nobody acts on.
        data = understate_eocd(build_zip(40))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len(zf.infolist()) == 40
        assert zip_entry_count_exceeds(data, 8)
        assert not zip_entry_count_exceeds(data, 40)

    def test_stops_one_record_past_the_limit(self) -> None:
        # Bounded by the limit, not by the archive — which is the whole reason
        # this is cheaper than the constructor rather than a rehearsal of it.
        # Everything past the fourth record is overwritten with garbage: a
        # limit of 3 is still answered, because the walk never gets there, while
        # a limit of 4 has to step onto a record with no signature and reports
        # nothing.
        data = bytearray(build_zip(400))
        eocd = data.rfind(b"PK\x05\x06")
        fifth = -1
        for _ in range(5):
            fifth = data.find(b"PK\x01\x02", fifth + 1)
        data[fifth:eocd] = b"\xff" * (eocd - fifth)
        blob = bytes(data)
        assert zip_entry_count_exceeds(blob, 3)
        assert not zip_entry_count_exceeds(blob, 4)

    def test_survives_an_archive_comment(self) -> None:
        # The EOCD ends in a variable-length comment, which is why it has to be
        # found by scanning back for its signature rather than read at a fixed
        # offset from the end.
        data = build_zip(3, comment=b"x" * 5000)
        assert zip_entry_count_exceeds(data, 2)
        assert not zip_entry_count_exceeds(data, 3)

    def test_a_planted_eocd_is_read_the_same_way_zipfile_reads_it(self) -> None:
        # A second EOCD signature planted in the archive comment makes "which
        # record is the real one" ambiguous, and the answer that matters is not
        # the spec's — it is ``zipfile``'s, because the preflight exists to
        # predict what ``ZipFile`` is about to allocate. Both scan back and take
        # the last signature, so both read the planted record, whose zero-size
        # directory holds nothing.
        planted = b"PK\x05\x06" + b"\x00" * 18
        data = build_zip(4, comment=planted)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            materialised = len(zf.infolist())
        assert not zip_entry_count_exceeds(data, materialised)

    def test_a_prepended_stub_shifts_the_directory_with_the_archive(self) -> None:
        # A ZIP appended to something else keeps its internal offsets;
        # ``ZipFile`` recovers the difference from where the EOCD says the
        # directory ends. A preflight that read the stored offset literally
        # would land in the stub and count nothing.
        data = b"\x7fELF" + b"\x00" * 4096 + build_zip(12)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len(zf.infolist()) == 12
        assert zip_entry_count_exceeds(data, 4)
        assert not zip_entry_count_exceeds(data, 12)

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
        assert not zip_entry_count_exceeds(data, 0)

    def test_a_corrupt_record_stops_the_walk_where_zipfile_stops(self) -> None:
        # Clobbering the third record's signature: ``ZipFile`` raises there
        # with two entries allocated, so there is nothing over the limit left
        # to refuse and the caller's own "unreadable archive" error speaks.
        out = bytearray(build_zip(20))
        third = -1
        for _ in range(3):
            third = out.find(b"PK\x01\x02", third + 1)
        out[third:third + 4] = b"XXXX"
        data = bytes(out)
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(data))
        assert not zip_entry_count_exceeds(data, 2)
        assert zip_entry_count_exceeds(data, 1)


class TestZip64:
    """Counts stored as all-ones mean "the real values are in the ZIP64 record"."""

    def test_walks_the_directory_the_zip64_record_points_at(self) -> None:
        data = _zip64_archive(entries=6)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len(zf.infolist()) == 6
        assert zip_entry_count_exceeds(data, 4)
        assert not zip_entry_count_exceeds(data, 6)

    def test_both_positions_a_locator_can_mean_are_counted(self) -> None:
        """Two stdlib versions, two readings, and no bet on which is running.

        Every zipfile puts the directory's end at the ZIP64 record. Up to
        3.13.5 that position is *assumed* to be immediately behind the locator
        and the locator's stored offset is ignored; 3.13.14 reads the stored
        offset and only falls back to adjacency when no record is there. An
        archive whose locator points somewhere else therefore has two readings,
        and a preflight that picked one would under-count on the other — the
        one failure this check cannot afford, since under-counting is exactly
        what the abandoned declared count did.

        Here the stored offset points at a *second*, planted ZIP64 record whose
        directory is the big one. Whichever reading the interpreter takes, the
        archive is over the limit, and so is the answer.

        The cost of counting under both is that an archive this malformed can
        be refused where one interpreter would have opened it and found two
        entries — 3.13.14 refuses it outright anyway. Over-refusing something
        no writer produces is the safe direction; under-counting is the one
        that hands the constructor an archive nobody bounded.
        """
        small = _zip64_archive(entries=2)
        big = _zip64_archive(entries=40)
        # Plant the 40-entry archive in front, and point the small archive's
        # locator at the ZIP64 record inside it.
        planted_at = big.rfind(b"PK\x06\x06")
        data = bytearray(big + small)
        locator_at = data.rfind(b"PK\x06\x07")
        struct.pack_into("<Q", data, locator_at + 8, planted_at)
        blob = bytes(data)

        assert zip_entry_count_exceeds(blob, 8)
        assert not zip_entry_count_exceeds(blob, 40)

    @pytest.mark.parametrize("clobber", [b"PK\x06\x07", b"PK\x06\x06"])
    def test_a_damaged_zip64_record_falls_back_the_way_zipfile_does(
        self, clobber: bytes
    ) -> None:
        # Locator or record damaged: :mod:`zipfile` keeps the classic EOCD's
        # own all-ones fields, which put the directory nowhere, and raises. The
        # preflight lands in the same place and leaves the diagnosis to it.
        data = bytearray(_zip64_archive(entries=6))
        at = data.rfind(clobber)
        data[at:at + 4] = b"XXXX"
        with pytest.raises(zipfile.BadZipFile):
            zipfile.ZipFile(io.BytesIO(bytes(data)))
        assert not zip_entry_count_exceeds(bytes(data), 0)


def _zip64_archive(*, entries: int) -> bytes:
    """A small archive whose EOCD is all-ones and whose real central-directory
    size and offset live in a ZIP64 record.

    Built by hand around a genuine directory: :mod:`zipfile` only writes ZIP64
    end records past 65535 entries or 4 GiB, and producing either to exercise a
    record layout costs seconds and gigabytes. The directory itself is real, so
    the walk under test has real records to step over.
    """
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as zf:
        for i in range(entries):
            zf.writestr(f"m{i}.txt", b"")
    plain = body.getvalue()
    eocd = plain.rfind(b"PK\x05\x06")
    size_cd, offset_cd = struct.unpack_from("<LL", plain, eocd + 12)
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
        size_cd,
        offset_cd,
    )
    # The locator's stored offset is the ZIP64 record's real position. Older
    # zipfile ignored it and assumed the record sat immediately behind the
    # locator; 3.13.14 reads it, and refuses the archive unless the directory
    # ends exactly there. Writing it correctly is what a real writer does and
    # what keeps this fixture an archive rather than a shape.
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, eocd, 1)
    all_ones = struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    return plain[:eocd] + zip64_eocd + locator + all_ones
