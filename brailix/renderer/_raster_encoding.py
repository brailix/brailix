"""Conventions every tactile-raster encoder has to agree on.

The ``.bmp`` / ``.png`` / ``.pdf`` renderers are three containers around
**one** image: the same raise grid, the same polarity, the same physical
size. Those three properties are not each encoder's own choice — they belong
to the :class:`~brailix.ir.tactile.TactileRaster` contract, and each encoder
merely has to honour them:

* **Polarity — a raised dot is written dark.** The raster stores raise levels
  (0 = flat … 255 = fully raised); a renderer emits ``255 - level``, so a
  fully-raised dot becomes black and a flat area white. Every common tactile
  channel reads it that way — black pixels emboss as dots, swell up on
  capsule paper, or drive Tiger dot height — which is why one master image
  can feed them all.
* **Physical scale — the DPI is stamped, not assumed.** BMP's header field
  and PNG's ``pHYs`` chunk both record pixels *per metre*, so embossing
  software reproduces the drawing at its true millimetre size rather than at
  whatever the viewer's screen resolution implies. (The PDF renderer says
  the same thing in points, via its ``MediaBox``.)

They lived as three private copies per module, which is the shape where a
polarity flip or a DPI-rounding fix silently lands on one renderer and not
the others — and the difference would show up as a *mirrored or mis-sized
embossed page*, i.e. only on paper, in the hands of the reader. Sharing them
costs no replaceability: an alternative encoder (a Pillow-backed PNG, say)
is free to ignore this module, but it is not free to disagree with the
convention, which is what makes it shared rather than duplicated.
"""

from __future__ import annotations

# Raise level (0..255) → grayscale sample, inverted so raised = dark.
# A 256-byte translation table: ``bytes.translate`` applies it to a whole
# scanline in C, which is why the encoders map levels this way rather than
# with a per-pixel expression.
INVERT_LEVELS = bytes(255 - i for i in range(256))

_METRES_PER_INCH = 0.0254


def pixels_per_metre(dpi: float) -> int:
    """Dots-per-inch → pixels-per-metre, for a raster header's density field.

    Returns 0 for a non-positive DPI — both BMP and PNG read 0 as "density
    unknown", which is the honest answer when the raster carries no usable
    resolution, and is what keeps a bad value from being stamped as fact.
    """
    if dpi <= 0:
        return 0
    return int(round(dpi / _METRES_PER_INCH))
