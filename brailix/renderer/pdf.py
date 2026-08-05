"""Render a :class:`~brailix.ir.tactile.TactileRaster` as a one-page PDF.

The print-ready **sighted-reference** sibling of the ``.png`` / ``.bmp``
master: the same
raise grid with the same raised→dark polarity, embedded as a single
full-page grayscale image so a sighted collaborator can print or drop it
into a document at its true millimetre size. The encoder is pure standard
library (``zlib`` for the image stream, ``struct``-free hand-assembled PDF
objects) — no third-party dependency, like the BMP / PNG renderers.

The page ``MediaBox`` is sized in PostScript points (1 pt = 1/72 inch) from
the raster's physical millimetre dimensions, and the image is drawn to fill
it, so the PDF prints the graphic at the right physical size — the same one
the PNG / BMP renderers stamp as pixels-per-metre, since they derive their
density from those same millimetres
(:mod:`brailix.renderer._raster_encoding`). Writing that size is this
format's own limit, and where its own refusal lives: a page too small to
state in PDF's decimal notation is rejected here (:func:`_positive_pt`), the
way a page too dense for a 32-bit density header is rejected there.
"""

from __future__ import annotations

import zlib as _zlib
from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.ir.tactile import TactileRaster
from brailix.renderer._raster_encoding import INVERT_LEVELS

if _TYPE_CHECKING:
    from collections.abc import Sequence

_MM_PER_INCH = 25.4
_PT_PER_INCH = 72.0


def _mm_to_pt(mm: float) -> float:
    return mm / _MM_PER_INCH * _PT_PER_INCH


# Decimal places a non-integer PDF number is written to. Six, not the two
# this used to use, because two is not enough to *state* every page this
# renderer legally accepts: a raster may carry any finite positive millimetre
# pair (:class:`~brailix.ir.tactile.TactileRaster`), and 0.001 mm is 0.00283
# pt — which ``f"{v:.2f}"`` writes as ``0.00``. The page then had a zero-area
# ``/MediaBox``, the exact thing ``require_renderable`` is called above to
# prevent, produced *after* it passed. (PDF has no exponent notation, so
# precision is the only lever; six places is well inside the five decimal
# digits readers are specified to keep, and costs a few bytes.)
_DECIMALS = 6


def _num(value: float) -> str:
    """Compact PDF number: integers without a decimal point, else up to
    :data:`_DECIMALS` decimals with trailing zeros stripped."""
    if float(value).is_integer():
        return str(int(value))
    text = f"{value:.{_DECIMALS}f}".rstrip("0").rstrip(".")
    # Everything below the last decimal place rounded away. Only reachable
    # for a page under ~4e-7 mm, and the caller is told which field to fix
    # rather than handed a page PDF readers treat as empty.
    return text or "0"


def _positive_pt(mm: float, field: str) -> str:
    """``mm`` as a PDF number in points, refusing one that serializes to zero.

    The physical-size counterpart of
    :func:`~brailix.renderer._raster_encoding._axis_density`, which refuses a
    page whose pixel density no container header can *hold*: this refuses one
    whose size no PDF number can *state*. Both sit at the encoder, because the
    IR is right to accept any finite positive measurement — the limit belongs
    to the format, and each format has its own.
    """
    text = _num(_mm_to_pt(mm))
    if float(text) <= 0:
        raise ValueError(
            f"{field}={mm!r} is {_mm_to_pt(mm):.3g} pt, which rounds to "
            f"{text} at the {_DECIMALS} decimals a PDF number is written to; "
            f"a zero-size /MediaBox is not a page. Give the raster the page "
            f"size it is really drawn at"
        )
    return text


def rasters_to_pdf(rasters: Sequence[TactileRaster]) -> bytes:
    """Encode one or more tactile rasters as a multi-page grayscale PDF.

    Each raster becomes one full page sized to its own physical millimetre
    dimensions (raised→dark polarity), so a whole illustrated document — the
    several tactile pages composed for an embossed braille+figures material —
    prints as a *single* file for a sighted collaborator, instead of one image
    per page. Pure standard library, like :func:`raster_to_pdf`, which is the
    length-1 case and stays byte-for-byte identical to before.
    """
    if not rasters:
        raise ValueError("rasters_to_pdf needs at least one raster")

    # Object layout: 1 = Catalog, 2 = Pages, then three objects per page
    # (Page, Image XObject, Content stream) at 3 + 3i, 4 + 3i, 5 + 3i.
    page_ids = [3 + 3 * i for i in range(len(rasters))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(rasters)} >>".encode("ascii"),
    ]
    for page_id, raster in zip(page_ids, rasters, strict=True):
        raster.require_renderable()  # no zero-pixel image XObject
        image_id, content_id = page_id + 1, page_id + 2
        w, h = raster.width, raster.height
        # The image is mapped onto the unit square with its first row at the
        # top, then the content-stream ``cm`` scales it to the page — so the
        # raster's row-major top-to-bottom data needs no flip, just the
        # raised→dark invert.
        image_data = _zlib.compress(bytes(raster.data).translate(INVERT_LEVELS), 9)
        # Serialized once and reused for the /MediaBox and the content
        # stream's ``cm``: the two describe the same page, and a page whose
        # box and image transform disagree prints the drawing at the wrong
        # size. Both are checked to be positive as they are written, which
        # ``require_renderable`` above cannot do — it sees the pixel grid,
        # not the decimals this file rounds the millimetres to.
        w_pt_text = _positive_pt(raster.page_width_mm, "page_width_mm")
        h_pt_text = _positive_pt(raster.page_height_mm, "page_height_mm")
        content = f"q {w_pt_text} 0 0 {h_pt_text} 0 0 cm /Im0 Do Q".encode("ascii")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt_text} "
                f"{h_pt_text}] /Resources << /XObject << /Im0 {image_id} 0 R "
                f">> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            (
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(image_data)} >>\nstream\n"
            ).encode("ascii") + image_data + b"\nendstream"
        )
        objects.append(
            (
                f"<< /Length {len(content)} >>\nstream\n"
            ).encode("ascii") + content + b"\nendstream"
        )

    return _assemble_pdf(objects)


def _assemble_pdf(objects: list[bytes]) -> bytes:
    """Serialize numbered PDF objects with a byte-correct xref table + trailer."""
    # A leading binary-marker comment tells readers the file carries binary
    # data (so a naive ASCII transfer doesn't mangle it).
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_pos = len(out)
    size = len(objects) + 1  # + the free object 0
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"  # object 0 is always the free-list head
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n"
        f"%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def raster_to_pdf(raster: TactileRaster) -> bytes:
    """Encode a tactile raster as a single-page, full-page grayscale PDF."""
    return rasters_to_pdf([raster])


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
class PdfRenderer:
    """Encode a tactile raster as a one-page grayscale PDF (sighted ref)."""

    name: str = "pdf"
    # Consumes a tactile raster, not a braille IR (see
    # ``brailix.renderer.braille_renderer_names``).
    consumes: str = "tactile_raster"

    def render(self, raster: TactileRaster) -> bytes:
        return raster_to_pdf(raster)


def _load() -> PdfRenderer:
    return PdfRenderer()
