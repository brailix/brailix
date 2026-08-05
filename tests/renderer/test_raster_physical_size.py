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
from brailix.renderer._raster_encoding import (
    MAX_PIXELS_PER_METRE,
    pixels_per_metre,
)
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


class TestADensityNoHeaderCanHold:
    """A page too small for its pixel count has no density either header can
    state, and that is where the encoding stops.

    The millimetre pair is a *measurement*: any finite positive value is a
    legal way to spell one, and :class:`TactileRaster` is right to accept a
    very small page rather than guess which containers the caller will write.
    But a 1 px axis across a nanometre is 10^12 pixels per metre, and both
    density headers are 32-bit integers — BMP's ``biXPelsPerMeter`` signed,
    PNG's ``pHYs`` unsigned. So a raster like this constructed cleanly, passed
    :meth:`~brailix.ir.tactile.TactileRaster.require_renderable`, and then died
    inside ``struct.pack`` with ``struct.error: 'i' format requires ...`` —
    which is not even a ``ValueError``, so nothing catching the IR's own error
    type caught it, and the message named a format code rather than the field
    the caller got wrong.
    """

    @staticmethod
    def _square_page(mm: float) -> TactileRaster:
        return TactileRaster.blank(
            1, 1, dpi=100.0, page_width_mm=mm, page_height_mm=mm
        )

    def test_such_a_raster_is_still_legal_ir(self) -> None:
        """Construction and ``require_renderable`` deliberately still accept
        it: the page size is a positive finite measurement and the raster has
        positive dimensions, which is all either of those two check."""
        assert self._square_page(1e-9).require_renderable() is None

    @pytest.mark.parametrize("encode", [raster_to_bmp, raster_to_png])
    def test_both_integer_header_containers_refuse_it(self, encode) -> None:
        with pytest.raises(ValueError) as excinfo:
            encode(self._square_page(1e-9))
        message = str(excinfo.value)
        assert "page_width_mm" in message, message
        assert str(MAX_PIXELS_PER_METRE) in message, message

    def test_the_last_page_both_headers_can_state_still_encodes(self) -> None:
        """The ceiling itself is not off by one: 1 px across
        ``1000 / MAX`` mm is exactly ``MAX`` pixels per metre, and both
        containers write it back unchanged."""
        raster = self._square_page(_MM_PER_METRE / MAX_PIXELS_PER_METRE)
        assert pixels_per_metre(raster) == (
            MAX_PIXELS_PER_METRE,
            MAX_PIXELS_PER_METRE,
        )
        assert struct.unpack_from("<ii", raster_to_bmp(raster), 38) == (
            MAX_PIXELS_PER_METRE,
            MAX_PIXELS_PER_METRE,
        )
        png = raster_to_png(raster)
        assert struct.unpack_from(">II", png, png.index(b"pHYs") + 4) == (
            MAX_PIXELS_PER_METRE,
            MAX_PIXELS_PER_METRE,
        )

    def test_one_step_past_the_ceiling_is_refused(self) -> None:
        raster = self._square_page(_MM_PER_METRE / (MAX_PIXELS_PER_METRE + 1))
        for encode in (raster_to_bmp, raster_to_png):
            with pytest.raises(ValueError):
                encode(raster)

    def test_a_denormal_page_size_does_not_reach_round(self) -> None:
        """The smallest positive float there is: the density is ``inf``, which
        used to reach ``round()`` and raise ``OverflowError`` instead."""
        with pytest.raises(ValueError):
            raster_to_bmp(self._square_page(5e-324))

    def test_the_pdf_encodes_a_page_the_density_headers_refuse(self) -> None:
        """Why the ceiling lives in the shared *encoding* layer and not in
        ``require_renderable()``: the PDF writes a decimal ``MediaBox``, not an
        integer density, so its limit is a different one — and a check on the
        IR would refuse a page this container can still state.

        The page is chosen to sit in the band between the two limits: 1 px
        across 3e-7 mm is 3.3e9 pixels per metre (past what either header can
        hold) and 0.00000085 pt (which a PDF number carries). Both halves are
        asserted, so the case cannot quietly drift to one side.

        The previous version of this test used a 1e-9 mm page and asserted
        only that the bytes began with ``%PDF``. They did — with ``/MediaBox
        [0 0 0.00 0.00]``, because the numbers were written to two decimals.
        The claim it was making ("this container can encode it") was false at
        the one place it mattered, and the assertion could not see it.
        """
        raster = self._square_page(3e-7)
        for encode in (raster_to_bmp, raster_to_png):
            with pytest.raises(ValueError):
                encode(raster)

        pdf = raster_to_pdf(raster)
        match = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf)
        assert match is not None, "no /MediaBox in the PDF"
        assert float(match.group(1)) > 0
        assert float(match.group(2)) > 0

    @pytest.mark.parametrize("raster", _CASES)
    def test_no_real_page_comes_anywhere_near_the_ceiling(self, raster) -> None:
        """The pages the product actually produces are six orders of magnitude
        below it — the check cannot start refusing ordinary output."""
        for density in pixels_per_metre(raster):
            assert density < MAX_PIXELS_PER_METRE // 1000
