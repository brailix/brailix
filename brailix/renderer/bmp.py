"""Render a :class:`~brailix.ir.tactile.TactileRaster` as BMP bytes.

BMP is the de-facto raster interchange for tactile output: dot-matrix
embossers (graphics mode), swell / capsule paper, and height-modulating
embossers (ViewPlus Tiger) all accept it (see
``ARCHITECTURE.md``). The default is an **8-bit
grayscale** master — the most information-rich common form, where
grayscale encodes dot height — with a **1-bit** black/white degradation
available behind a flag (the same pipeline, one toggle).

Polarity: a *raised* dot is written **dark**. The raster stores raise
levels (0 = flat … 255 = fully raised); this renderer emits pixel value
``255 - level`` so a fully-raised dot becomes black (0) and a flat area
white (255). That matches every common tactile channel — black pixels
emboss as dots / swell up / drive Tiger dot height — so one master image
feeds them all.

Physical scale: the BMP header records pixels-per-metre from the raster's
``dpi``, so embossing software reproduces the drawing at its true
millimetre size. The encoder is pure standard library (``struct``); only
*reading* external bitmaps (a later phase) needs a third-party library.

The output type is ``bytes`` — BMP files are written in binary mode.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from brailix.ir.tactile import TactileRaster

# Raise level (0..255) → grayscale pixel value, inverted so raised = dark.
_INVERT = bytes(255 - i for i in range(256))


def _pixels_per_metre(dpi: float) -> int:
    """Dots-per-inch → pixels-per-metre for the BMP header (1 inch =
    0.0254 m)."""
    if dpi <= 0:
        return 0
    return int(round(dpi / 0.0254))


def raster_to_bmp(
    raster: TactileRaster, *, bit_depth: int = 8, threshold: int = 128
) -> bytes:
    """Encode a tactile raster as BMP bytes.

    ``bit_depth=8`` (default) produces a grayscale master; ``bit_depth=1``
    thresholds each cell (raised when its level ``>= threshold``) into a
    black/white bitmap. Any other value raises :class:`ValueError`.
    """
    raster.require_renderable()
    if bit_depth == 8:
        return _bmp_8bit(raster)
    if bit_depth == 1:
        return _bmp_1bit(raster, threshold)
    raise ValueError(f"unsupported bit_depth {bit_depth}; use 8 or 1")


def _file_header(file_size: int, pixel_offset: int) -> bytes:
    # "BM" + file size + 2 reserved words + offset to pixel data.
    return b"BM" + struct.pack("<IHHI", file_size, 0, 0, pixel_offset)


def _info_header(
    width: int, height: int, bit_count: int, image_size: int, ppm: int, colors: int
) -> bytes:
    # BITMAPINFOHEADER (40 bytes). Positive height = bottom-up rows.
    return struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        width,       # biWidth
        height,      # biHeight (positive → bottom-up)
        1,           # biPlanes
        bit_count,   # biBitCount
        0,           # biCompression = BI_RGB
        image_size,  # biSizeImage
        ppm,         # biXPelsPerMeter
        ppm,         # biYPelsPerMeter
        colors,      # biClrUsed
        0,           # biClrImportant
    )


def _bmp_8bit(raster: TactileRaster) -> bytes:
    w, h = raster.width, raster.height
    row_stride = (w + 3) & ~3  # rows padded to a 4-byte boundary
    pad = b"\x00" * (row_stride - w)
    palette = bytearray()
    for i in range(256):  # grayscale palette: index i → (i, i, i)
        palette += bytes((i, i, i, 0))
    pixel_size = row_stride * h
    pixel_offset = 14 + 40 + len(palette)
    out = bytearray()
    out += _file_header(pixel_offset + pixel_size, pixel_offset)
    out += _info_header(w, h, 8, pixel_size, _pixels_per_metre(raster.dpi), 256)
    out += palette
    data = raster.data
    for y in range(h - 1, -1, -1):  # bottom-up
        start = y * w
        out += data[start:start + w].translate(_INVERT)
        out += pad
    return bytes(out)


def _bmp_1bit(raster: TactileRaster, threshold: int) -> bytes:
    w, h = raster.width, raster.height
    row_stride = ((w + 31) // 32) * 4  # 1 bit/px, rows padded to 32 bits
    # Palette: index 0 = black (raised), index 1 = white (flat).
    palette = bytes((0, 0, 0, 0)) + bytes((255, 255, 255, 0))
    pixel_size = row_stride * h
    pixel_offset = 14 + 40 + len(palette)
    out = bytearray()
    out += _file_header(pixel_offset + pixel_size, pixel_offset)
    out += _info_header(w, h, 1, pixel_size, _pixels_per_metre(raster.dpi), 2)
    out += palette
    data = raster.data
    for y in range(h - 1, -1, -1):  # bottom-up
        row = bytearray(row_stride)
        base = y * w
        for x in range(w):
            # Raised → black → palette index 0 → bit 0 (leave clear).
            # Flat → white → palette index 1 → set the bit (MSB-first).
            if data[base + x] < threshold:
                row[x >> 3] |= 0x80 >> (x & 7)
        out += row
    return bytes(out)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BmpRenderer:
    """Encode a tactile raster as BMP bytes.

    Defaults to the 8-bit grayscale master; construct with ``bit_depth=1``
    for a black/white bitmap.
    """

    name: str = "bmp"
    # The IR this renderer consumes — a tactile raster, not a braille IR. Lets
    # a braille-only front-end (the CLI) filter it out; see
    # ``brailix.renderer.braille_renderer_names``.
    consumes: str = "tactile_raster"
    bit_depth: int = 8
    threshold: int = 128

    def render(self, raster: TactileRaster) -> bytes:
        return raster_to_bmp(
            raster, bit_depth=self.bit_depth, threshold=self.threshold
        )


def _load() -> BmpRenderer:
    return BmpRenderer()
