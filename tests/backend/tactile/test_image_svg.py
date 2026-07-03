"""External SVG → resvg → tactile raster (T3 vector half).

A complex external SVG (one the stdlib tag-walk can't render faithfully) is
handed whole to resvg → PNG → the same bitmap grayscale/threshold path. Gated
on resvg_py + Pillow (the ``graphics-svg-raster`` + ``graphics`` extras).
"""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("resvg_py")
pytest.importorskip("PIL")

import brailix.backend.tactile as tactile  # noqa: E402
from brailix.backend.tactile import rasterize  # noqa: E402
from brailix.backend.tactile._image import (  # noqa: E402
    SvgRasterizerMissing,
    load_tactile_image,
)
from brailix.backend.tactile.profile import load_tactile_profile  # noqa: E402
from brailix.core.errors import WarningCollector  # noqa: E402

_NS = 'xmlns="http://www.w3.org/2000/svg"'
SVG_BLACK = (
    f'<svg {_NS} width="40" height="30" viewBox="0 0 40 30">'
    '<rect x="5" y="5" width="30" height="20" fill="black"/></svg>'
)
SVG_CIRCLE = (
    f'<svg {_NS} width="40" height="40" viewBox="0 0 40 40">'
    '<circle cx="20" cy="20" r="6" fill="black"/></svg>'
)
# A gradient — exactly the kind of element the stdlib tag-walk skips, so resvg
# is the only way to render it.
SVG_GRADIENT = (
    f'<svg {_NS} width="40" height="40" viewBox="0 0 40 40">'
    '<defs><linearGradient id="g"><stop offset="0" stop-color="black"/>'
    '<stop offset="1" stop-color="white"/></linearGradient></defs>'
    '<rect width="40" height="40" fill="url(#g)"/></svg>'
)


def _profile():
    return load_tactile_profile("generic")


def _svg_file(tmp_path, svg: str) -> str:
    p = tmp_path / "diagram.svg"
    p.write_text(svg, encoding="utf-8")
    return str(p)


def _image_svg(href: str) -> ET.Element:
    svg = ET.Element(
        "svg", {"width": "100mm", "height": "75mm", "viewBox": "0 0 40 30"}
    )
    ET.SubElement(
        svg, "image",
        {"href": href, "x": "0", "y": "0", "width": "40", "height": "30"},
    )
    return svg


class TestLoadSvg:
    def test_renders_black_rect_raised(self, tmp_path):
        s = load_tactile_image(_svg_file(tmp_path, SVG_BLACK), target_w=80, target_h=60)
        assert any(v > 0 for v in s.levels)  # the black rect → raised

    def test_transparent_background_is_flat(self, tmp_path):
        # Circle on a transparent canvas: shape raised, background flat (the
        # transparent area is composited onto white, so it reads as "no ink").
        s = load_tactile_image(_svg_file(tmp_path, SVG_CIRCLE), target_w=80, target_h=80)
        raised = sum(1 for v in s.levels if v > 0)
        assert 0 < raised < len(s.levels)

    def test_gradient_renders(self, tmp_path):
        # The dark half of the gradient crosses the threshold → some raised.
        s = load_tactile_image(_svg_file(tmp_path, SVG_GRADIENT), target_w=80, target_h=80)
        assert any(v > 0 for v in s.levels)

    def test_data_uri_svg(self):
        href = "data:image/svg+xml," + urllib.parse.quote(SVG_BLACK)
        s = load_tactile_image(href, target_w=40, target_h=30)
        assert any(v > 0 for v in s.levels)


class TestRasterizeSvg:
    def test_image_svg_rasterizes(self, tmp_path):
        r = rasterize(_image_svg(_svg_file(tmp_path, SVG_BLACK)), _profile())
        assert r.raised_count() > 0

    def test_missing_resvg_warns_distinct_code(self, tmp_path, monkeypatch):
        def _raise(*a, **k):
            raise SvgRasterizerMissing("no resvg")

        monkeypatch.setattr(tactile, "load_tactile_image", _raise)
        warn = WarningCollector()
        r = rasterize(_image_svg(_svg_file(tmp_path, SVG_BLACK)), _profile(), warn)
        assert r.raised_count() == 0
        codes = [w.code for w in warn]
        assert "GRAPHICS_SVG_NO_RASTERIZER" in codes
        # Not mistaken for the missing-Pillow code.
        assert "GRAPHICS_IMAGE_NO_DECODER" not in codes
