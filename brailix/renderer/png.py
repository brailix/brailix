"""Render a :class:`~brailix.ir.tactile.TactileRaster` as PNG bytes.

PNG is the **sighted-reference** sibling of the ``.bmp`` master: the same
raise grid, the same raised→dark polarity, just a different (compressed)
container (``ARCHITECTURE.md`` — "PNG 是同一张栅格的
便宜兄弟,近乎白送"). It is handy for a sighted collaborator's preview, for
embedding in a document, or anywhere a compact lossless image beats a raw
BMP. The encoder is pure standard library (``zlib`` for the IDAT stream,
``struct`` + ``zlib.crc32`` for the chunks) — no third-party dependency.

8-bit grayscale (PNG colour type 0), rows top-to-bottom. A ``pHYs`` chunk
records pixels-per-metre from the raster's DPI so the image reproduces at
its true millimetre size, matching the BMP renderer.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from brailix.ir.tactile import TactileRaster

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Raise level (0..255) → grayscale sample, inverted so raised = dark.
_INVERT = bytes(255 - i for i in range(256))


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _pixels_per_metre(dpi: float) -> int:
    if dpi <= 0:
        return 0
    return int(round(dpi / 0.0254))


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
        raw += data[start:start + w].translate(_INVERT)
    idat = zlib.compress(bytes(raw), 9)

    # IHDR: width, height, bit depth 8, colour type 0 (grayscale), default
    # compression / filter / interlace.
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    ppu = _pixels_per_metre(raster.dpi)
    phys = struct.pack(">IIB", ppu, ppu, 1)  # unit 1 = metre

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
