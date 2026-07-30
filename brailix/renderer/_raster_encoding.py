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
* **Physical scale — the page's millimetre size is stamped, not assumed.**
  BMP's header fields and PNG's ``pHYs`` chunk both record pixels *per metre*,
  so embossing software reproduces the drawing at its true millimetre size
  rather than at whatever the viewer's screen resolution implies. (The PDF
  renderer says the same thing in points, via its ``MediaBox``.) All three
  read it off the same fact — see :func:`pixels_per_metre`.

They lived as three private copies per module, which is the shape where a
polarity flip or a DPI-rounding fix silently lands on one renderer and not
the others — and the difference would show up as a *mirrored or mis-sized
embossed page*, i.e. only on paper, in the hands of the reader. Sharing them
costs no replaceability: an alternative encoder (a Pillow-backed PNG, say)
is free to ignore this module, but it is not free to disagree with the
convention, which is what makes it shared rather than duplicated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brailix.ir.tactile import TactileRaster

# Raise level (0..255) → grayscale sample, inverted so raised = dark.
# A 256-byte translation table: ``bytes.translate`` applies it to a whole
# scanline in C, which is why the encoders map levels this way rather than
# with a per-pixel expression.
INVERT_LEVELS = bytes(255 - i for i in range(256))

_MM_PER_METRE = 1000.0


def pixels_per_metre(raster: TactileRaster) -> tuple[int, int]:
    """``raster``'s ``(x, y)`` pixel density, for a container's density header.

    Derived from the physical size — ``page_width_mm`` / ``page_height_mm``,
    the raster's one statement of how big the page is — and never from
    ``dpi``. That distinction is the whole point of putting this here: the
    density header and the PDF ``MediaBox`` describe the *same* physical
    page, so they have to be computed from the same fact. Reading ``dpi``
    made it a second, independently-settable source of that fact, and the two
    parted company for any raster whose grid is not exactly ``dpi`` dots per
    inch — a page scaled down by the raster cap, a pixel count that rounded,
    or a hand-built raster whose numbers simply disagree. The BMP and PNG
    then claimed one physical size and the PDF another, for one image.

    Per axis, because a grid can be slightly denser across than down (the two
    pixel counts round independently) and both formats have room to say so:
    BMP's ``biXPelsPerMeter`` / ``biYPelsPerMeter``, PNG's ``pHYs`` x/y pair.

    Rounded to whole pixels per metre (both header fields are integers), and
    floored at 1: a page more than a metre wide per pixel has no density this
    coarse to give, and the nearest representable one is a better answer than
    0, which both formats read as "unknown".
    """
    return (
        max(1, round(raster.width * _MM_PER_METRE / raster.page_width_mm)),
        max(1, round(raster.height * _MM_PER_METRE / raster.page_height_mm)),
    )
