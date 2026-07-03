"""Tests for :mod:`brailix.renderer.png`."""

from __future__ import annotations

import struct
import zlib

import pytest

from brailix.ir.tactile import TactileRaster
from brailix.renderer.png import PngRenderer, raster_to_png

_SIG = b"\x89PNG\r\n\x1a\n"


def _raster(w: int, h: int, dpi: float = 100.0) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=dpi, page_width_mm=10.0, page_height_mm=10.0
    )


def _chunks(png: bytes) -> dict[bytes, bytes]:
    """Parse PNG chunks, verifying each CRC."""
    assert png[:8] == _SIG
    out: dict[bytes, bytes] = {}
    pos = 8
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, tag
        out[tag] = data
        pos += 12 + length
    return out


class TestZeroSizeRejected:
    @pytest.mark.parametrize("w,h", [(0, 0), (0, 5), (5, 0)])
    def test_zero_size_raises_instead_of_corrupt_png(self, w, h):
        # A 0-dim IHDR is an invalid PNG that every reader rejects; raise up
        # front rather than return silently-corrupt bytes.
        with pytest.raises(ValueError):
            raster_to_png(_raster(w, h))


class TestStructure:
    def test_signature_and_chunks_present(self):
        chunks = _chunks(raster_to_png(_raster(4, 3)))
        assert set(chunks) == {b"IHDR", b"pHYs", b"IDAT", b"IEND"}

    def test_ihdr_grayscale_8bit(self):
        chunks = _chunks(raster_to_png(_raster(4, 3)))
        w, h, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
        assert (w, h, depth, colour) == (4, 3, 8, 0)

    def test_phys_records_dpi(self):
        chunks = _chunks(raster_to_png(_raster(4, 3, dpi=100.0)))
        x_ppu, y_ppu, unit = struct.unpack(">IIB", chunks[b"pHYs"])
        assert x_ppu == round(100.0 / 0.0254)
        assert unit == 1  # metre


class TestPixels:
    def test_polarity_and_scanlines(self):
        r = _raster(3, 2)
        r.set_raise(0, 0, 255)  # top-left fully raised
        r.set_raise(2, 1, 100)
        raw = zlib.decompress(_chunks(raster_to_png(r))[b"IDAT"])
        # Each row: 1 filter byte (0 = None) + width sample bytes.
        assert len(raw) == 2 * (1 + 3)
        assert raw[0] == 0 and raw[4] == 0  # filter bytes
        row0 = raw[1:4]
        assert row0[0] == 0  # raised → black
        assert row0[1] == 255  # flat → white
        row1 = raw[5:8]
        assert row1[2] == 255 - 100


class TestRenderer:
    def test_renderer_matches_function(self):
        r = _raster(4, 4)
        r.set_raise(1, 1, 200)
        assert PngRenderer().render(r) == raster_to_png(r)

    def test_name(self):
        assert PngRenderer().name == "png"
