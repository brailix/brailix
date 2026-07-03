"""Tests for :mod:`brailix.renderer.bmp`.

The BMP encoder's correctness has a few moving parts worth pinning:

* header fields (magic, sizes, offsets, bit count, physical DPI);
* the grayscale palette;
* bottom-up row order, 4-byte row padding, and the *raised → dark*
  polarity that makes one master image emboss correctly everywhere.
"""

from __future__ import annotations

import struct

import pytest

from brailix.ir.tactile import TactileRaster
from brailix.renderer.bmp import BmpRenderer, raster_to_bmp


def _raster(w: int, h: int, dpi: float = 100.0) -> TactileRaster:
    return TactileRaster.blank(
        w, h, dpi=dpi, page_width_mm=10.0, page_height_mm=10.0
    )


def _u32(b: bytes, off: int) -> int:
    return struct.unpack("<I", b[off:off + 4])[0]


def _i32(b: bytes, off: int) -> int:
    return struct.unpack("<i", b[off:off + 4])[0]


def _u16(b: bytes, off: int) -> int:
    return struct.unpack("<H", b[off:off + 2])[0]


def _decode8(bmp: bytes):
    offset = _u32(bmp, 10)
    w, h = _i32(bmp, 18), _i32(bmp, 22)
    row_stride = (w + 3) & ~3

    def px(x: int, y: int) -> int:
        return bmp[offset + (h - 1 - y) * row_stride + x]

    return w, h, px


def _decode1(bmp: bytes):
    offset = _u32(bmp, 10)
    w, h = _i32(bmp, 18), _i32(bmp, 22)
    row_stride = ((w + 31) // 32) * 4

    def bit(x: int, y: int) -> int:
        byte = bmp[offset + (h - 1 - y) * row_stride + (x >> 3)]
        return (byte >> (7 - (x & 7))) & 1

    return w, h, bit


class TestZeroSizeRejected:
    @pytest.mark.parametrize("bit_depth", [8, 1])
    @pytest.mark.parametrize("w,h", [(0, 0), (0, 5), (5, 0)])
    def test_zero_size_raises(self, w, h, bit_depth):
        with pytest.raises(ValueError):
            raster_to_bmp(_raster(w, h), bit_depth=bit_depth)


class TestEightBitHeader:
    def test_magic_and_sizes(self):
        bmp = raster_to_bmp(_raster(4, 3))
        assert bmp[:2] == b"BM"
        assert _u32(bmp, 2) == len(bmp)  # bifh file size
        assert _u32(bmp, 10) == 14 + 40 + 256 * 4  # pixel data offset = 1078

    def test_info_header(self):
        bmp = raster_to_bmp(_raster(4, 3))
        assert _u32(bmp, 14) == 40  # header size
        assert _i32(bmp, 18) == 4  # width
        assert _i32(bmp, 22) == 3  # height (positive = bottom-up)
        assert _u16(bmp, 28) == 8  # bit count
        assert _u32(bmp, 30) == 0  # BI_RGB

    def test_physical_dpi_in_header(self):
        bmp = raster_to_bmp(_raster(4, 3, dpi=100.0))
        ppm = _i32(bmp, 38)
        assert ppm == round(100.0 / 0.0254)  # 3937

    def test_grayscale_palette(self):
        bmp = raster_to_bmp(_raster(2, 2))
        palette = bmp[54:54 + 256 * 4]
        for i in (0, 1, 128, 255):
            assert palette[4 * i:4 * i + 3] == bytes((i, i, i))


class TestEightBitPixels:
    def test_polarity_and_position(self):
        r = _raster(3, 2)
        r.set_raise(0, 0, 255)  # top-left fully raised
        r.set_raise(2, 1, 128)  # bottom-right half raised
        bmp = raster_to_bmp(r)
        _, _, px = _decode8(bmp)
        assert px(0, 0) == 0  # raised → black
        assert px(2, 1) == 255 - 128
        assert px(1, 0) == 255  # flat → white

    def test_row_padding(self):
        # width 5 → row stride padded to 8 bytes.
        bmp = raster_to_bmp(_raster(5, 1))
        offset = _u32(bmp, 10)
        assert len(bmp) - offset == 8  # one padded row


class TestOneBit:
    def test_header(self):
        bmp = raster_to_bmp(_raster(4, 3), bit_depth=1)
        assert bmp[:2] == b"BM"
        assert _u16(bmp, 28) == 1  # bit count
        assert _u32(bmp, 46) == 2  # colors used
        assert _u32(bmp, 10) == 14 + 40 + 2 * 4  # offset = 62

    def test_threshold_polarity(self):
        r = _raster(3, 1)
        r.set_raise(0, 0, 200)  # >= threshold → raised
        r.set_raise(1, 0, 50)   # < threshold → flat
        bmp = raster_to_bmp(r, bit_depth=1, threshold=128)
        _, _, bit = _decode1(bmp)
        assert bit(0, 0) == 0  # raised → black → bit 0
        assert bit(1, 0) == 1  # flat → white → bit 1
        assert bit(2, 0) == 1  # untouched → flat


class TestRenderer:
    def test_renderer_matches_function(self):
        r = _raster(4, 4)
        r.set_raise(1, 1, 255)
        assert BmpRenderer().render(r) == raster_to_bmp(r, bit_depth=8)

    def test_renderer_one_bit(self):
        r = _raster(4, 4)
        assert BmpRenderer(bit_depth=1).render(r) == raster_to_bmp(r, bit_depth=1)

    def test_invalid_bit_depth(self):
        with pytest.raises(ValueError):
            raster_to_bmp(_raster(2, 2), bit_depth=4)
