"""Tests for the raster-image source adapter (image path / spec → SVG).

The adapter reads the image's pixel dimensions with Pillow (the ``graphics``
extra), so the module skips when it is absent.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from brailix.core.context import (  # noqa: E402
    GRAPHIC_ASSET_RESOLVER_KEY,
    GraphicsContext,
)
from brailix.frontend.graphics.adapters.image import (  # noqa: E402
    ImageSourceAdapter,
    image_to_svg,
)
from brailix.frontend.graphics.registry import graphic_source_registry  # noqa: E402


def _make_png(tmp_path, w: int, h: int, value: int = 0):
    p = tmp_path / "pic.png"
    im = Image.new("L", (w, h), value)
    im.save(p, format="PNG")
    return p


def _png_bytes(w: int, h: int, value: int = 0) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    Image.new("L", (w, h), value).save(buf, format="PNG")
    return buf.getvalue()


def _parse(svg_str: str) -> ET.Element:
    return ET.fromstring(svg_str)


class TestImageToSvg:
    def test_bare_path_wraps_in_image_element(self, tmp_path):
        p = _make_png(tmp_path, 40, 20)
        root = _parse(image_to_svg(str(p)))
        assert root.tag == "svg"
        assert root.get("viewBox") == "0 0 40 20"
        img = root[0]
        assert img.tag == "image"
        assert img.get("href") == str(p)
        assert img.get("width") == "40" and img.get("height") == "20"
        assert img.get("data-bk-mode") == "threshold"  # default
        assert img.get("data-bk-threshold") == "128"

    def test_default_physical_size_preserves_aspect(self, tmp_path):
        # 40x20 → longest side 160 mm, shorter side 80 mm.
        p = _make_png(tmp_path, 40, 20)
        root = _parse(image_to_svg(str(p)))
        assert root.get("width") == "160mm"
        assert root.get("height") == "80mm"

    def test_json_spec_carries_mode_threshold_invert(self, tmp_path):
        p = _make_png(tmp_path, 10, 10)
        spec = json.dumps(
            {"path": str(p), "mode": "edge", "threshold": 110, "invert": True}
        )
        img = _parse(image_to_svg(spec))[0]
        assert img.get("data-bk-mode") == "edge"
        assert img.get("data-bk-threshold") == "110"
        assert img.get("data-bk-invert") == "1"

    def test_json_spec_width_mm_drives_height_by_aspect(self, tmp_path):
        p = _make_png(tmp_path, 30, 10)  # aspect 3:1
        root = _parse(image_to_svg(json.dumps({"path": str(p), "width_mm": 90})))
        assert root.get("width") == "90mm"
        assert root.get("height") == "30mm"

    def test_threshold_clamped_into_range(self, tmp_path):
        p = _make_png(tmp_path, 4, 4)
        img = _parse(image_to_svg(json.dumps({"path": str(p), "threshold": 999})))[0]
        assert img.get("data-bk-threshold") == "255"

    def test_empty_source_soft_fails(self):
        root = _parse(image_to_svg("   "))
        assert root.get("data-bk-error") is not None

    def test_bad_path_soft_fails(self):
        root = _parse(image_to_svg("Z:/no/such/file_9d2.png"))
        assert root.get("data-bk-error") is not None

    def test_spec_without_path_soft_fails(self):
        root = _parse(image_to_svg(json.dumps({"mode": "edge"})))
        assert root.get("data-bk-error") is not None

    def test_invalid_json_soft_fails(self):
        root = _parse(image_to_svg("{not json"))
        assert root.get("data-bk-error") is not None


class TestAdapter:
    def test_adapter_to_svg(self, tmp_path):
        p = _make_png(tmp_path, 8, 8)
        out = ImageSourceAdapter().to_svg(str(p))
        assert "<image" in out and "data-bk-mode" in out

    def test_non_utf8_bytes_soft_fail(self):
        root = _parse(ImageSourceAdapter().to_svg(b"\xff\xfe\x00bad"))
        assert root.get("data-bk-error") is not None

    def test_registered_with_extra(self):
        # The registry resolves the adapter by name (Pillow present here).
        adapter = graphic_source_registry.get("image")
        assert adapter.source == "image"


class TestSvgSource:
    """An ``.svg`` path is sized from its viewBox (no Pillow), wrapped as an
    ``<image>`` whose href is the SVG — the backend renders it via resvg."""

    def _svg(self, tmp_path, body: str) -> str:
        p = tmp_path / "d.svg"
        p.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" {body}><rect/></svg>',
            encoding="utf-8",
        )
        return str(p)

    def test_sizes_from_viewbox(self, tmp_path):
        path = self._svg(tmp_path, 'viewBox="0 0 60 40" width="60" height="40"')
        root = _parse(image_to_svg(path))
        assert root.get("viewBox") == "0 0 60 40"
        img = root[0]
        assert img.tag == "image" and img.get("href") == path
        assert img.get("width") == "60" and img.get("height") == "40"
        # Longest side defaults to 160 mm, aspect 3:2 preserved.
        assert root.get("width") == "160mm"

    def test_falls_back_to_width_height(self, tmp_path):
        path = self._svg(tmp_path, 'width="80px" height="40px"')  # no viewBox
        root = _parse(image_to_svg(path))
        assert root.get("viewBox") == "0 0 80 40"

    def test_sizeless_svg_soft_fails(self, tmp_path):
        path = self._svg(tmp_path, "")  # no viewBox, no width/height
        root = _parse(image_to_svg(path))
        assert root.get("data-bk-error") is not None

    def test_json_spec_with_svg_and_mode(self, tmp_path):
        import json

        path = self._svg(tmp_path, 'viewBox="0 0 10 10"')
        img = _parse(image_to_svg(json.dumps({"path": path, "mode": "edge"})))[0]
        assert img.get("href") == path
        assert img.get("data-bk-mode") == "edge"


class TestAssetResolver:
    """The injected :class:`GraphicAssetResolver`: an in-document image
    reference resolves to bytes inlined as a ``data:`` URI, so no file is
    read; an unresolved name falls back to the filesystem path (I2)."""

    def test_resolved_reference_becomes_data_uri(self):
        png = _png_bytes(16, 8)
        root = _parse(
            image_to_svg(
                '{"path": "media/image1.png"}',
                lambda name: png if name == "media/image1.png" else None,
            )
        )
        assert root.get("data-bk-error") is None
        # Size is read from the resolved bytes, not any file.
        assert root.get("viewBox") == "0 0 16 8"
        img = root[0]
        assert img.get("href").startswith("data:image/png;base64,")

    def test_bare_path_reference_resolved(self):
        png = _png_bytes(4, 4)
        img = _parse(
            image_to_svg("logo.png", lambda name: png)
        )[0]
        assert img.get("href").startswith("data:image/png;base64,")

    def test_unresolved_name_falls_back_to_path(self, tmp_path):
        # Resolver returns None → the reference is read as a filesystem path,
        # exactly as with no resolver at all.
        p = _make_png(tmp_path, 10, 10)
        img = _parse(image_to_svg(str(p), lambda name: None))[0]
        assert img.get("href") == str(p)

    def test_resolver_spec_keeps_mode_and_size(self):
        png = _png_bytes(30, 10)
        root = _parse(
            image_to_svg(
                json.dumps(
                    {"path": "a.png", "mode": "edge", "width_mm": 90}
                ),
                lambda name: png,
            )
        )
        img = root[0]
        assert img.get("data-bk-mode") == "edge"
        assert root.get("width") == "90mm"  # aspect 3:1 → 30mm tall
        assert root.get("height") == "30mm"

    def test_resolved_svg_bytes_sized_from_viewbox(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" '
            b'viewBox="0 0 50 25"><rect/></svg>'
        )
        root = _parse(image_to_svg("fig.svg", lambda name: svg))
        assert root.get("viewBox") == "0 0 50 25"
        assert root[0].get("href").startswith("data:image/svg+xml;base64,")

    def test_adapter_reads_resolver_from_context(self):
        png = _png_bytes(8, 8)
        ctx = GraphicsContext(
            source="image",
            options={GRAPHIC_ASSET_RESOLVER_KEY: lambda name: png},
        )
        out = ImageSourceAdapter().to_svg('{"path": "x.png"}', ctx)
        assert "data:image/png;base64," in out

    def test_adapter_without_context_uses_no_resolver(self, tmp_path):
        # No ctx → no resolver → path behaviour unchanged.
        p = _make_png(tmp_path, 6, 6)
        img = _parse(ImageSourceAdapter().to_svg(str(p)))[0]
        assert img.get("href") == str(p)


class TestCaptionTitle:
    """A spec ``title`` (I4 — the alt text of a converted picture) becomes the
    SVG's ``<title>``, so the figure keeps a caption a screen-reader announces."""

    def test_title_becomes_svg_title_element(self, tmp_path):
        p = _make_png(tmp_path, 8, 8)
        root = _parse(image_to_svg(json.dumps({"path": str(p), "title": "一张地图"})))
        # <title> is a direct child of the root svg (where the caption reader
        # looks) and precedes the <image>.
        assert root[0].tag == "title"
        assert root[0].text == "一张地图"
        assert root[1].tag == "image"

    def test_desc_is_accepted_as_title_alias(self, tmp_path):
        p = _make_png(tmp_path, 8, 8)
        root = _parse(image_to_svg(json.dumps({"path": str(p), "desc": "说明"})))
        assert root[0].tag == "title" and root[0].text == "说明"

    def test_no_title_when_absent_or_blank(self, tmp_path):
        p = _make_png(tmp_path, 8, 8)
        root = _parse(image_to_svg(json.dumps({"path": str(p), "title": "  "})))
        assert root[0].tag == "image"  # no <title> prepended


class TestRoundTrip:
    def test_adapter_output_renders_through_pipeline(self, tmp_path):
        # A dark image, imported, must rasterize to raised pixels end to end.
        from brailix import Pipeline

        im = Image.new("L", (12, 12), 0)  # all black → all raised at threshold
        p = tmp_path / "black.png"
        im.save(p, format="PNG")
        result = Pipeline(profile="cn_current").translate_graphic(
            str(p), source_format="image"
        )
        assert result.raster.raised_count() > 0
        assert result.render("bmp")[:2] == b"BM"
