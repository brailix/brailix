"""One raster, one physical size — whichever container it is written into.

The ``.bmp``, ``.png`` and ``.pdf`` renderers are three wrappers around the
same image, and :mod:`brailix.renderer._raster_encoding` says so: same grid,
same polarity, same physical size. Polarity and the grid were pinned by each
encoder's own tests; the physical size was not, and it was the one the three
disagreed about. BMP and PNG stamped a density computed from
:attr:`~brailix.ir.tactile.TactileRaster.dpi` while the PDF sized its
``MediaBox`` from ``page_width_mm`` / ``page_height_mm`` — two independent
statements of one fact, which parted company whenever the raster's grid was
not exactly ``dpi`` dots per inch, and drifted furthest for exactly the pages
that had been through the raster cap.

The failure is invisible in the file: every byte is well-formed, and the
drawing only comes out the wrong size on the embosser, or a millimetre-scale
figure prints at a size a ruler on the page disagrees with. So the check has
to read the headers back and compare them to each other.
"""

from __future__ import annotations

import re
import struct

import pytest

from brailix.ir.tactile import TactileRaster
from brailix.renderer.bmp import raster_to_bmp
from brailix.renderer.pdf import raster_to_pdf
from brailix.renderer.png import raster_to_png

_MM_PER_INCH = 25.4
_PT_PER_INCH = 72.0
_MM_PER_METRE = 1000.0


def _bmp_size_mm(raster: TactileRaster) -> tuple[float, float]:
    """The page size a BMP reader computes: pixels ÷ pixels-per-metre."""
    bmp = raster_to_bmp(raster)
    width, height = struct.unpack_from("<ii", bmp, 18)
    ppm_x, ppm_y = struct.unpack_from("<ii", bmp, 38)
    return width / ppm_x * _MM_PER_METRE, height / ppm_y * _MM_PER_METRE


def _png_size_mm(raster: TactileRaster) -> tuple[float, float]:
    """The same, from the PNG's ``IHDR`` dimensions and ``pHYs`` density."""
    png = raster_to_png(raster)
    ihdr = png.index(b"IHDR") + 4
    width, height = struct.unpack_from(">II", png, ihdr)
    phys = png.index(b"pHYs") + 4
    ppu_x, ppu_y, unit = struct.unpack_from(">IIB", png, phys)
    assert unit == 1, "pHYs unit must be metres for the size to mean anything"
    return width / ppu_x * _MM_PER_METRE, height / ppu_y * _MM_PER_METRE


def _pdf_size_mm(raster: TactileRaster) -> tuple[float, float]:
    """And the PDF's, from the ``MediaBox`` in PostScript points."""
    pdf = raster_to_pdf(raster)
    match = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
    assert match is not None, "no /MediaBox in the PDF"
    w_pt, h_pt = float(match.group(1)), float(match.group(2))
    return (
        w_pt / _PT_PER_INCH * _MM_PER_INCH,
        h_pt / _PT_PER_INCH * _MM_PER_INCH,
    )


# (label, raster) — each a page the three encoders must agree on.
_CASES = [
    pytest.param(
        TactileRaster.blank(
            100, 100, dpi=100.0, page_width_mm=25.4, page_height_mm=25.4
        ),
        id="square-1-inch",
    ),
    pytest.param(
        TactileRaster.blank(
            827, 1169, dpi=100.0, page_width_mm=210.0, page_height_mm=297.0
        ),
        id="a4-at-100-dpi",
    ),
    pytest.param(
        # Two axes at different densities (127 across, 84.7 down): a single
        # dpi cannot describe this page, and nothing says it has to.
        TactileRaster.blank(
            200, 100, dpi=100.0, page_width_mm=40.0, page_height_mm=30.0
        ),
        id="anisotropic-grid",
    ),
    pytest.param(
        # The raster cap's shape: a page whose pixels were scaled down, so the
        # grid is no longer profile.dpi and only the millimetres still are.
        TactileRaster.blank(
            413, 584, dpi=50.0, page_width_mm=210.0, page_height_mm=297.0
        ),
        id="clamped-page",
    ),
    pytest.param(
        # The reported construction: 100 px declared as both 100 dpi (which
        # would be 25.4 mm) and a 10 mm page. The nominal dpi is simply not
        # what an encoder reads any more, so the three still agree.
        TactileRaster.blank(
            100, 100, dpi=100.0, page_width_mm=10.0, page_height_mm=10.0
        ),
        id="dpi-contradicts-the-page",
    ),
]


@pytest.mark.parametrize("raster", _CASES)
def test_every_container_states_the_rasters_page_size(raster) -> None:
    declared = (raster.page_width_mm, raster.page_height_mm)
    for name, size in (
        ("bmp", _bmp_size_mm(raster)),
        ("png", _png_size_mm(raster)),
        ("pdf", _pdf_size_mm(raster)),
    ):
        assert size == pytest.approx(declared, rel=1e-3, abs=0.01), (
            f"{name} reproduces {size} mm for a {declared} mm page"
        )


@pytest.mark.parametrize("raster", _CASES)
def test_the_three_containers_agree_with_each_other(raster) -> None:
    """The property that actually reaches the reader: two files of one figure
    emboss and print at the same size, whatever that size is."""
    bmp, png, pdf = (
        _bmp_size_mm(raster),
        _png_size_mm(raster),
        _pdf_size_mm(raster),
    )
    assert bmp == pytest.approx(png, rel=1e-3, abs=0.01)
    assert bmp == pytest.approx(pdf, rel=1e-3, abs=0.01)


def test_a_denser_grid_on_the_same_page_keeps_the_page_size() -> None:
    """Doubling the resolution is a resolution change, not a size change: the
    density doubles and the physical page stays put in all three."""
    coarse = TactileRaster.blank(
        100, 100, dpi=100.0, page_width_mm=25.4, page_height_mm=25.4
    )
    fine = TactileRaster.blank(
        200, 200, dpi=200.0, page_width_mm=25.4, page_height_mm=25.4
    )
    for size_of in (_bmp_size_mm, _png_size_mm, _pdf_size_mm):
        assert size_of(coarse) == pytest.approx(size_of(fine), rel=1e-3)

    fine_bmp = raster_to_bmp(fine)
    coarse_bmp = raster_to_bmp(coarse)
    assert struct.unpack_from("<i", fine_bmp, 38)[0] == pytest.approx(
        2 * struct.unpack_from("<i", coarse_bmp, 38)[0], rel=1e-3
    )
