"""Render a :class:`~brailix.ir.tactile.TactileRaster` as PNG bytes.

PNG is the **sighted-reference** sibling of the ``.bmp`` master: the same
raise grid, the same raised→dark polarity, just a different (compressed)
container. It is handy for a
sighted collaborator's preview, for
embedding in a document, or anywhere a compact lossless image beats a raw
BMP. The encoder is pure standard library (``zlib`` for the IDAT stream,
``struct`` + ``zlib.crc32`` for the chunks) — no third-party dependency.

8-bit grayscale (PNG colour type 0), rows top-to-bottom. A ``pHYs`` chunk
records pixels-per-metre from the raster's physical page size so the image
reproduces at its true millimetre size — the same size the BMP header and
the PDF ``MediaBox`` state (:mod:`brailix.renderer._raster_encoding`).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from brailix.ir.tactile import TactileRaster
from brailix.renderer._raster_encoding import INVERT_LEVELS, pixels_per_metre

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def raster_to_png(raster: TactileRaster) -> bytes:
    """Encode a tactile raster as an 8-bit grayscale PNG."""
    raster.require_renderable()
    w, h = raster.width, raster.height
    data = raster.data
    # Filtered scanlines: each row prefixed with filter byte 0 (None).
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        start = y * w
        raw += data[start:start + w].translate(INVERT_LEVELS)
    idat = zlib.compress(bytes(raw), 9)

    # IHDR: width, height, bit depth 8, colour type 0 (grayscale), default
    # compression / filter / interlace.
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    ppu_x, ppu_y = pixels_per_metre(raster)
    phys = struct.pack(">IIB", ppu_x, ppu_y, 1)  # unit 1 = metre

    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"pHYs", phys)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PngRenderer:
    """Encode a tactile raster as 8-bit grayscale PNG bytes."""

    name: str = "png"
    # Consumes a tactile raster, not a braille IR (see
    # ``brailix.renderer.braille_renderer_names``).
    consumes: str = "tactile_raster"

    def render(self, raster: TactileRaster) -> bytes:
        return raster_to_png(raster)


def _load() -> PngRenderer:
    return PngRenderer()
