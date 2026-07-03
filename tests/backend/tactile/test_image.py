"""Tests for raster ``<image>`` support: decode/mode helper + the
rasterizer handler that stamps a bitmap as raise levels.

The decode path needs Pillow (the ``graphics`` extra), so the whole module
skips when it is absent — the feature genuinely requires it. The
soft-failure branches that need no decoder (no href / no box / Pillow
missing) are asserted by direct rasterizer calls and a monkeypatch.
"""

from __future__ import annotations

import base64
import io
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

import brailix.backend.tactile as tactile  # noqa: E402
from brailix.backend.tactile import rasterize  # noqa: E402
from brailix.backend.tactile._image import (  # noqa: E402
    ImageSampler,
    TactileImageError,
    _resolve_href,
    load_tactile_image,
)
from brailix.backend.tactile.profile import load_tactile_profile  # noqa: E402
from brailix.core.errors import WarningCollector  # noqa: E402
from brailix.ir.tactile import TactileRaster  # noqa: E402


def _profile():
    return load_tactile_profile("generic")


def _png_bytes(pixels: list[list[int]]) -> bytes:
    """Encode a 2-D grayscale (0..255) pixel grid as PNG bytes."""
    h = len(pixels)
    w = len(pixels[0])
    im = Image.new("L", (w, h))
    im.putdata([v for row in pixels for v in row])
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _data_uri(pixels: list[list[int]]) -> str:
    return "data:image/png;base64," + base64.b64encode(_png_bytes(pixels)).decode("ascii")


def _solid(value: int, size: int = 16) -> list[list[int]]:
    return [[value] * size for _ in range(size)]


def _left_dark(size: int = 16) -> list[list[int]]:
    # Left half black (0), right half white (255).
    return [[0 if x < size // 2 else 255 for x in range(size)] for _ in range(size)]


def _svg_image(
    href: str,
    *,
    mode: str | None = None,
    threshold: int | None = None,
    invert: bool = False,
    box: tuple[float, float, float, float] = (0, 0, 100, 100),
    viewbox: str = "0 0 100 100",
    page_mm: float = 100.0,
) -> ET.Element:
    """A namespace-free ``<svg>`` wrapping one ``<image>`` placed at ``box``
    ``(x, y, w, h)`` in user units. Built with ElementTree so a data-URI
    href is attribute-escaped correctly."""
    svg = ET.Element(
        "svg",
        {"width": f"{page_mm}mm", "height": f"{page_mm}mm", "viewBox": viewbox},
    )
    x, y, w, h = box
    img = ET.SubElement(
        svg,
        "image",
        {"href": href, "x": str(x), "y": str(y), "width": str(w), "height": str(h)},
    )
    if mode:
        img.set("data-bk-mode", mode)
    if threshold is not None:
        img.set("data-bk-threshold", str(threshold))
    if invert:
        img.set("data-bk-invert", "1")
    return svg


def _centroid(r: TactileRaster) -> tuple[float, float] | None:
    sx = sy = n = 0
    for i, v in enumerate(r.data):
        if v > 0:
            sx += i % r.width
            sy += i // r.width
            n += 1
    return (sx / n, sy / n) if n else None


# --------------------------------------------------------------------------
# load_tactile_image / ImageSampler / _resolve_href
# --------------------------------------------------------------------------


class TestLoad:
    def test_threshold_dark_raised_white_flat(self):
        dark = load_tactile_image(_data_uri(_solid(0)))
        light = load_tactile_image(_data_uri(_solid(255)))
        assert all(v == 255 for v in dark.levels)
        assert all(v == 0 for v in light.levels)

    def test_threshold_cut_respected(self):
        gray = _data_uri(_solid(100))
        below = load_tactile_image(gray, threshold=128)  # 100 < 128 → raised
        above = load_tactile_image(gray, threshold=50)   # 100 >= 50 → flat
        assert all(v == 255 for v in below.levels)
        assert all(v == 0 for v in above.levels)

    def test_grayscale_mode_uses_full_height_range(self):
        s = load_tactile_image(_data_uri(_solid(100)), mode="grayscale")
        assert all(v == 155 for v in s.levels)  # 255 - 100

    def test_invert_flips_threshold(self):
        s = load_tactile_image(_data_uri(_solid(0)), invert=True)
        assert all(v == 0 for v in s.levels)  # dark normally raised; inverted → flat

    def test_edge_mode_flat_image_has_no_edges(self):
        s = load_tactile_image(_data_uri(_solid(128)), mode="edge")
        assert all(v == 0 for v in s.levels)

    def test_edge_mode_boundary_raises_something(self):
        s = load_tactile_image(_data_uri(_left_dark()), mode="edge")
        assert any(v > 0 for v in s.levels)

    def test_unknown_mode_falls_back_to_threshold(self):
        s = load_tactile_image(_data_uri(_solid(0)), mode="bogus")
        assert all(v == 255 for v in s.levels)

    def test_resize_never_upscales_past_source(self):
        s = load_tactile_image(_data_uri(_solid(0, size=8)), target_w=200, target_h=200)
        assert s.width == 8 and s.height == 8

    def test_downsamples_to_target(self):
        s = load_tactile_image(_data_uri(_solid(0, size=64)), target_w=16, target_h=16)
        assert s.width == 16 and s.height == 16

    def test_bad_path_raises_image_error(self):
        with pytest.raises(TactileImageError):
            load_tactile_image("Z:/does/not/exist_4f9.png")

    def test_malformed_data_uri_raises_image_error(self):
        with pytest.raises(TactileImageError):
            _resolve_href("data:image/png;base64,@@@not-base64@@@")

    def test_resolve_href_reads_file(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(_png_bytes(_solid(0, size=4)))
        assert _resolve_href(str(p)) == p.read_bytes()


class TestSampler:
    def test_sample_clamps_out_of_range(self):
        s = ImageSampler(2, 2, bytes([10, 20, 30, 40]))
        assert s.sample(0.0, 0.0) == 10
        assert s.sample(0.99, 0.99) == 40
        assert s.sample(-1.0, -1.0) == 10   # clamps low
        assert s.sample(2.0, 2.0) == 40     # clamps high


# --------------------------------------------------------------------------
# Rasterizer handler (_h_image)
# --------------------------------------------------------------------------


class TestRasterize:
    def test_solid_dark_image_fills_with_raise(self):
        r = rasterize(_svg_image(_data_uri(_solid(0))), _profile())
        assert r.raised_count() > 0

    def test_white_image_stays_flat(self):
        r = rasterize(_svg_image(_data_uri(_solid(255))), _profile())
        assert r.raised_count() == 0

    def test_left_dark_lands_on_the_left(self):
        r = rasterize(_svg_image(_data_uri(_left_dark())), _profile())
        c = _centroid(r)
        assert c is not None
        assert c[0] < r.width / 2  # raised mass on the dark (left) half

    def test_threshold_attribute_changes_result(self):
        gray = _data_uri(_solid(100))
        raised = rasterize(_svg_image(gray, threshold=128), _profile())
        flat = rasterize(_svg_image(gray, threshold=50), _profile())
        assert raised.raised_count() > 0
        assert flat.raised_count() == 0

    def test_placement_box_offsets_the_image(self):
        # A small image placed in the bottom-right quadrant only raises there.
        r = rasterize(
            _svg_image(_data_uri(_solid(0)), box=(50, 50, 50, 50)), _profile()
        )
        c = _centroid(r)
        assert c is not None
        assert c[0] > r.width / 2 and c[1] > r.height / 2

    def test_off_page_placement_draws_nothing(self):
        r = rasterize(
            _svg_image(_data_uri(_solid(0)), box=(200, 200, 50, 50)), _profile()
        )
        assert r.raised_count() == 0

    def test_no_href_warns_and_skips(self):
        svg = ET.fromstring(
            '<svg width="50mm" height="50mm" viewBox="0 0 100 100">'
            '<image x="0" y="0" width="100" height="100"/></svg>'
        )
        warn = WarningCollector()
        r = rasterize(svg, _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_IMAGE_LOAD_FAILED" for w in warn)

    def test_no_box_warns_and_skips(self):
        svg = ET.fromstring(
            '<svg width="50mm" height="50mm" viewBox="0 0 100 100">'
            '<image href="x.png"/></svg>'
        )
        warn = WarningCollector()
        r = rasterize(svg, _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_IMAGE_LOAD_FAILED" for w in warn)

    def test_unreadable_image_warns_and_skips(self):
        warn = WarningCollector()
        r = rasterize(_svg_image("Z:/missing_a83.png"), _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_IMAGE_LOAD_FAILED" for w in warn)

    def test_missing_decoder_warns_no_decoder(self, monkeypatch):
        def _raise_import(*a, **k):
            raise ImportError("No module named 'PIL'")

        # Patch the name as the rasterizer module looked it up.
        monkeypatch.setattr(tactile, "load_tactile_image", _raise_import)
        warn = WarningCollector()
        r = rasterize(_svg_image(_data_uri(_solid(0))), _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_IMAGE_NO_DECODER" for w in warn)

    def test_provenance_attributes_image_pixels(self):
        # The <image> goes through the same _walk begin_element wiring as
        # every other element, so element→pixel provenance (editor highlight,
        # ARCHITECTURE.md) comes for free.
        svg = _svg_image(_data_uri(_solid(0)))
        svg[0].set("data-bk-gid", "img1")
        r = rasterize(svg, _profile(), record_provenance=True)
        assert r.provenance is not None
        assert r.provenance.get("img1")  # the image element touched pixels
