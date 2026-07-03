"""Tests for :mod:`brailix.renderer.pdf` — a one-page sighted-reference PDF."""

from __future__ import annotations

import pytest

from brailix.ir.tactile import TactileRaster
from brailix.renderer import braille_renderer_names, renderer_registry
from brailix.renderer.pdf import PdfRenderer, raster_to_pdf, rasters_to_pdf


def _raster(w: int = 8, h: int = 6, *, page_mm: float = 10.0) -> TactileRaster:
    r = TactileRaster.blank(
        w, h, dpi=100.0, page_width_mm=page_mm, page_height_mm=page_mm
    )
    for i in range(min(w, h)):  # a raised diagonal so the image isn't all flat
        r.set_raise(i, i, 255)
    return r


def _xref_entries(pdf: bytes) -> list[tuple[int, int]]:
    """Parse the xref table into ``(object_number, byte_offset)`` pairs,
    reading the fixed-width 20-byte entries from the ``startxref`` pointer."""
    start = int(pdf[pdf.rfind(b"startxref"):].split(b"\n")[1])
    body = pdf[start:]
    assert body[:4] == b"xref"
    nl1 = body.index(b"\n")
    nl2 = body.index(b"\n", nl1 + 1)
    count = int(body[nl1 + 1:nl2].split()[1])
    table = body[nl2 + 1:]
    return [(k, int(table[k * 20:k * 20 + 10])) for k in range(count)]


class TestStructure:
    def test_pdf_header_and_eof(self):
        pdf = raster_to_pdf(_raster())
        assert pdf[:5] == b"%PDF-"
        assert pdf.rstrip().endswith(b"%%EOF")

    def test_required_objects_present(self):
        pdf = raster_to_pdf(_raster())
        for marker in (
            b"/Type /Catalog",
            b"/Type /Pages",
            b"/Type /Page",
            b"/Subtype /Image",
            b"/ColorSpace /DeviceGray",
            b"/Filter /FlateDecode",
        ):
            assert marker in pdf, marker

    def test_xref_offsets_point_at_their_objects(self):
        # The classic PDF corruption is a wrong byte offset; verify each
        # non-free entry lands exactly on "<n> 0 obj".
        pdf = raster_to_pdf(_raster())
        entries = _xref_entries(pdf)
        assert len(entries) == 6  # object 0 (free) + 5 real objects
        for objnum, offset in entries[1:]:
            assert pdf[offset:].startswith(f"{objnum} 0 obj".encode("ascii"))

    def test_image_dimensions_match(self):
        pdf = raster_to_pdf(_raster(8, 6))
        assert b"/Width 8" in pdf and b"/Height 6" in pdf

    def test_mediabox_is_physical_size_in_points(self):
        # 10 mm → 10 / 25.4 * 72 ≈ 28.35 pt.
        pdf = raster_to_pdf(_raster(page_mm=10.0))
        assert b"/MediaBox [0 0 28.35 28.35]" in pdf


class TestMultiPage:
    def test_single_page_is_byte_identical_to_raster_to_pdf(self):
        # ``raster_to_pdf`` is exactly the length-1 case — the refactor must
        # not shift a single byte (structural golden by equality).
        r = _raster()
        assert rasters_to_pdf([r]) == raster_to_pdf(r)

    def test_two_pages_one_pages_node_kids_and_count(self):
        pdf = rasters_to_pdf([_raster(8, 6), _raster(6, 4)])
        assert pdf.count(b"/Type /Page /Parent") == 2  # two Page objects
        assert b"/Type /Pages " in pdf  # exactly one Pages node
        assert b"/Count 2" in pdf
        assert b"/Kids [3 0 R 6 0 R]" in pdf  # page objects at 3, 6

    def test_two_pages_xref_offsets_point_at_their_objects(self):
        pdf = rasters_to_pdf([_raster(8, 6), _raster(6, 4)])
        entries = _xref_entries(pdf)
        # object 0 (free) + 2 base objects + 3 per page × 2 pages = 9.
        assert len(entries) == 9
        for objnum, offset in entries[1:]:
            assert pdf[offset:].startswith(f"{objnum} 0 obj".encode("ascii"))

    def test_each_page_keeps_its_own_dimensions(self):
        pdf = rasters_to_pdf([_raster(8, 6), _raster(6, 4)])
        assert b"/Width 8" in pdf and b"/Height 6" in pdf
        assert b"/Width 6" in pdf and b"/Height 4" in pdf

    def test_empty_sequence_rejected(self):
        with pytest.raises(ValueError):
            rasters_to_pdf([])

    @pytest.mark.parametrize("w,h", [(0, 0), (0, 5), (5, 0)])
    def test_zero_area_page_rejected(self, w, h):
        # A zero-area page (/MediaBox [0 0 0 0] or a zero axis) is invalid PDF;
        # raise instead of writing a degenerate page — single and multi-page.
        with pytest.raises(ValueError):
            raster_to_pdf(_raster(w, h))
        with pytest.raises(ValueError):
            rasters_to_pdf([_raster(8, 6), _raster(w, h)])


class TestRenderer:
    def test_render_matches_function(self):
        r = _raster()
        assert PdfRenderer().render(r) == raster_to_pdf(r)

    def test_registered_and_consumes_tactile(self):
        renderer = renderer_registry.get("pdf")
        assert renderer.render(_raster())[:5] == b"%PDF-"
        assert renderer.consumes == "tactile_raster"

    def test_excluded_from_braille_renderer_names(self):
        # A tactile renderer, so the CLI's braille-only list omits it.
        assert "pdf" not in braille_renderer_names()
