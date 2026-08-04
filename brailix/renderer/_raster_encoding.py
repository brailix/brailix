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

# The finest density a container's header can state. BMP's ``biXPelsPerMeter``
# / ``biYPelsPerMeter`` are **signed** 32-bit fields (``struct`` code ``i``);
# PNG's ``pHYs`` pair is unsigned 32-bit (``I``), so it could hold twice as
# much. One ceiling for both — the tighter of the two — because "is this raster
# encodable?" should have one answer: with two ceilings there is a band of
# pages that write a PNG and fail on the BMP of the same drawing, and the BMP
# is the master an embosser actually reads.
MAX_PIXELS_PER_METRE = 2_147_483_647


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

    The other end is **not** clamped — a density past
    :data:`MAX_PIXELS_PER_METRE` raises :class:`ValueError` — and the asymmetry
    is the point. At the floor the true density is a number the field can hold,
    it merely rounds to the one value that means "unknown", and the page is
    over a metre per pixel, so stating 1 is off by a fraction of a pixel on a
    scale where nothing is measured anyway. At the ceiling the true density has
    no representation at all, and clamping would stamp a page size wrong by
    whatever factor the overflow was — a figure that embosses at ten times its
    real size, well-formed in every byte, wrong only on paper under the
    reader's hands. That is the exact failure this module exists to prevent, so
    the raster is refused instead of quietly resized.
    """
    return (
        _axis_density(raster.width, raster.page_width_mm, "page_width_mm"),
        _axis_density(raster.height, raster.page_height_mm, "page_height_mm"),
    )


def _axis_density(pixels: int, page_mm: float, field: str) -> int:
    """One axis of :func:`pixels_per_metre`, refusing what no header can hold.

    A :class:`~brailix.ir.tactile.TactileRaster` is legal with any finite
    positive millimetre pair, and construction is right to allow it: the pair
    is a *measurement*, and the IR has no business knowing which containers a
    caller will write. The density it implies is another matter — it is
    computed here, for a 32-bit integer field, so this is where a page too
    small for its pixel count stops being renderable.

    Without the check the value went straight into ``struct.pack``, and a
    legally-constructed raster died there as ``struct.error: 'i' format
    requires -2147483648 <= number <= 2147483647`` — an error naming a format
    code, from a call the caller never made, about a field they never set. The
    ``ValueError`` below names the field that is wrong instead, which is the
    one they can change.
    """
    density = pixels * _MM_PER_METRE / page_mm
    # ``not <=`` rather than ``>``: it also catches the infinity a denormal
    # page size produces, which would otherwise reach ``round()`` and raise
    # ``OverflowError: cannot convert float infinity to integer``.
    if not density <= MAX_PIXELS_PER_METRE:
        raise ValueError(
            f"{pixels} px across {field}={page_mm!r} needs {density:.6g} "
            f"pixels per metre, past the {MAX_PIXELS_PER_METRE} a container's "
            f"density header can state; give the raster the page size it is "
            f"really drawn at"
        )
    return max(1, round(density))
