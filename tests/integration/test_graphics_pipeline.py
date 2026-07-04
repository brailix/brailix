"""End-to-end tests for the tactile-graphics vertical: SVG → raster → BMP.

The thinnest end-to-end slice (T0): a caller hands in an SVG and gets an
embossable ``.bmp`` back, headless. Exercises the full stage chain —
source adapter → normalizer → tactile backend → BMP renderer — through the
public :meth:`brailix.Pipeline.translate_graphic` entry and the
:class:`~brailix.pipeline.GraphicResult` it returns (whose ``render`` goes
through the same ``renderer_registry`` the braille renderers use).
"""

from __future__ import annotations

import json
import struct

import pytest

from brailix import Pipeline, translate_graphic
from brailix.backend.tactile.profile import load_tactile_profile
from brailix.core.errors import WarningCollector
from brailix.ir.braille import BrailleCell
from brailix.pipeline import GraphicResult
from brailix.renderer.bmp import raster_to_bmp
from brailix.renderer.tactile_preview import raster_to_braille

CIRCLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="50mm" height="50mm"><circle cx="50" cy="50" r="40"/></svg>'
)


def _compile(
    svg,
    *,
    tactile_profile="generic",
    source="svg",
    braille_profile=None,
    label_translator=None,
    warnings=None,
) -> GraphicResult:
    """Compile a graphic through the public Pipeline entry.

    The pipeline's braille profile only drives ``<text>`` label translation;
    when no label profile is requested it is an unused placeholder, so we
    default to a builtin standard.
    """
    pipe = Pipeline(profile=braille_profile or "cn_current")
    return pipe.translate_graphic(
        svg,
        source_format=source,
        tactile_profile=tactile_profile,
        braille_profile=braille_profile,
        label_translator=label_translator,
        warnings=warnings,
    )


def _raster(svg, **kw):
    return _compile(svg, **kw).raster


class TestSvgTree:
    def test_returns_normalized_tree(self):
        tree = _compile(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<circle cx="5" cy="5" r="4"/></svg>'
        ).svg_tree
        assert tree.tag == "svg"  # namespace stripped
        assert tree[0].tag == "circle"

    def test_primitives_source(self):
        spec = json.dumps(
            {"width": 10, "height": 10, "shapes": [
                {"type": "rect", "x": 0, "y": 0, "width": 5, "height": 5}
            ]}
        )
        tree = _compile(spec, source="primitives").svg_tree
        assert any(c.tag == "rect" for c in tree)


class TestSvgToRaster:
    def test_dimensions_and_metadata(self):
        r = _raster(CIRCLE)
        assert r.width == 197  # 50 mm @ 100 dpi
        assert r.height == 197
        assert r.page_width_mm == 50.0
        assert r.raised_count() > 0

    def test_accepts_profile_object(self):
        prof = load_tactile_profile("generic")
        r = _raster(CIRCLE, tactile_profile=prof)
        assert r.raised_count() > 0

    def test_warnings_are_captured(self):
        # ``<use>`` is still a deferred element (``<image>`` is now handled).
        svg = (
            '<svg viewBox="0 0 10 10"><use href="#x"/></svg>'
        )
        warn = WarningCollector()
        _raster(svg, warnings=warn)
        assert any(w.code == "GRAPHICS_UNSUPPORTED_ELEMENT" for w in warn)

    def test_vertical_line_lands_in_expected_column(self):
        svg = '<svg viewBox="0 0 100 100"><line x1="50" y1="0" x2="50" y2="100"/></svg>'
        # 100 user units → 100 mm → 100*100/25.4 ≈ 394 px; x=50 maps to ≈197.
        r = _raster(svg)
        mid = round(50 * r.width / 100)
        column_raised = any(r.get(mid, y) for y in range(r.height))
        assert column_raised
        # A column far from the line stays flat.
        assert not any(r.get(5, y) for y in range(r.height))


class TestSvgToBmp:
    def test_produces_valid_bmp(self):
        bmp = _compile(CIRCLE).render("bmp")
        assert bmp[:2] == b"BM"
        assert struct.unpack("<I", bmp[2:6])[0] == len(bmp)
        assert struct.unpack("<H", bmp[28:30])[0] == 8  # 8-bit default
        assert struct.unpack("<i", bmp[18:22])[0] == 197

    def test_default_render_is_bmp(self):
        result = _compile(CIRCLE)
        assert result.render() == result.render("bmp")

    def test_contains_raised_black_pixels(self):
        bmp = _compile(CIRCLE).render("bmp")
        offset = struct.unpack("<I", bmp[10:14])[0]
        pixels = bmp[offset:]
        assert any(b == 0 for b in pixels)  # at least one raised (black) dot

    def test_one_bit_variant(self):
        # A non-default bit depth is a renderer option, taken on the raster.
        bmp = raster_to_bmp(_raster(CIRCLE), bit_depth=1)
        assert bmp[:2] == b"BM"
        assert struct.unpack("<H", bmp[28:30])[0] == 1

    def test_png_sibling(self):
        png = _compile(CIRCLE).render("png")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_pdf_sibling(self):
        pdf = _compile(CIRCLE).render("pdf")
        assert pdf[:5] == b"%PDF-" and pdf.rstrip().endswith(b"%%EOF")

    def test_letter_profile(self):
        # The Letter profile loads and drives a render end to end.
        r = _raster("<svg></svg>", tactile_profile="letter")
        assert r.page_width_mm == 215.9

    def test_soft_fail_still_returns_blank_bmp(self):
        # Malformed SVG must not crash the pipeline — it yields a blank page.
        warn = WarningCollector()
        bmp = _compile("<svg><rect></svg>", warnings=warn).render("bmp")
        assert bmp[:2] == b"BM"
        assert any(w.code == "GRAPHICS_SOFT_FAIL" for w in warn)


class TestSourceSoftFail:
    """A graphic must never crash the whole compile — an unresolvable source
    adapter or non-UTF-8 bytes soft-fail to a blank raster like malformed SVG
    (the pipeline contract: a graphic always rasterises to *something*)."""

    def test_unknown_source_format_soft_fails(self):
        # The frontend's single entry (parse_graphic_tree) owns adapter
        # resolution and warns GRAPHICS_ADAPTER_MISSING — the same shape as
        # math / music's *_ADAPTER_MISSING — then degrades to an error tree
        # the backend surfaces as GRAPHICS_SOFT_FAIL.
        warn = WarningCollector()
        result = _compile("<svg/>", source="does_not_exist", warnings=warn)
        assert result.raster is not None
        codes = {w.code for w in warn}
        assert "GRAPHICS_ADAPTER_MISSING" in codes
        assert "GRAPHICS_SOFT_FAIL" in codes

    def test_non_utf8_bytes_soft_fail(self):
        # A latin-1 SVG with a 0xE9 byte — the source adapters own the byte
        # decode and its soft-failure; it must yield a blank raster, not crash.
        raw = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>'
            "<svg><text>café</text></svg>"
        ).encode("latin-1")
        result = _compile(raw)
        assert result.raster is not None
        assert result.raster.width > 0 and result.raster.height > 0


class TestModuleLevelEntry:
    """The Pipeline-free entry: a graphic's compile needs no braille standard
    (its product is a raster, not cells), so ``brailix.translate_graphic``
    stands alone — ``Pipeline.translate_graphic`` merely delegates to it."""

    def test_compiles_without_any_braille_profile(self):
        result = translate_graphic(CIRCLE)
        assert result.raster.raised_count() > 0
        assert result.render("bmp")[:2] == b"BM"
        assert result.svg_tree is not None and result.svg_tree.tag == "svg"

    def test_label_without_translation_source_warns_and_skips(self):
        labelled = (
            '<svg viewBox="0 0 100 100" width="50mm" height="50mm">'
            '<circle cx="50" cy="50" r="40"/>'
            '<text x="6" y="12">A</text></svg>'
        )
        warn = WarningCollector()
        result = translate_graphic(labelled, warnings=warn)
        assert result.raster.raised_count() > 0  # the circle still draws
        assert any(w.code == "GRAPHICS_LABEL_NO_PROFILE" for w in warn)

    def test_braille_profile_translates_labels(self):
        labelled = (
            '<svg viewBox="0 0 100 100" width="50mm" height="50mm">'
            '<circle cx="50" cy="50" r="40"/>'
            '<text x="6" y="12">A</text></svg>'
        )
        plain = translate_graphic(labelled)
        with_labels = translate_graphic(labelled, braille_profile="cn_current")
        # Stamped label dots only ever add raised cells (set_raise is a max).
        assert (
            with_labels.raster.raised_count() > plain.raster.raised_count()
        )

    def test_pipeline_method_delegates_to_same_result(self):
        via_module = translate_graphic(CIRCLE)
        via_pipeline = _compile(CIRCLE)
        assert via_pipeline.raster.data == via_module.raster.data


class TestBraillePreview:
    def test_preview_of_a_drawing_is_non_blank(self):
        preview = raster_to_braille(_raster(CIRCLE), width_cells=20)
        assert "\n" in preview  # multiple cell rows
        assert any(ch != chr(0x2800) for ch in preview)  # some raised dots
        # Every non-newline character is a braille code point.
        assert all(c == "\n" or 0x2800 <= ord(c) <= 0x28FF for c in preview)

    def test_blank_drawing_previews_blank(self):
        preview = raster_to_braille(
            _raster('<svg viewBox="0 0 100 100"></svg>'), width_cells=10
        )
        assert set(preview) <= {chr(0x2800), "\n"}


class TestPrimitivesPipeline:
    SPEC = json.dumps(
        {
            "width": 100,
            "height": 100,
            "shapes": [
                {"type": "rect", "x": 10, "y": 10, "width": 80, "height": 80},
                {"type": "circle", "cx": 50, "cy": 50, "r": 30},
            ],
        }
    )

    def test_primitives_to_raster(self):
        r = _raster(self.SPEC, source="primitives")
        assert r.raised_count() > 0

    def test_primitives_to_bmp(self):
        bmp = _compile(self.SPEC, source="primitives").render("bmp")
        assert bmp[:2] == b"BM"

    def test_unknown_shape_surfaces_warning(self):
        spec = json.dumps({"width": 10, "height": 10, "shapes": [{"type": "blob"}]})
        warn = WarningCollector()
        _raster(spec, source="primitives", warnings=warn)
        assert any(w.code == "GRAPHICS_UNKNOWN_SHAPE" for w in warn)


class TestFigures:
    BAR = json.dumps(
        {"kind": "bar", "data": [{"label": "A", "value": 3}, {"label": "B", "value": 7}]}
    )

    def test_figure_to_raster(self):
        r = _raster(self.BAR, source="figure")
        assert r.raised_count() > 0

    def test_figure_to_bmp(self):
        bmp = _compile(
            json.dumps({"kind": "number_line", "min": 0, "max": 10, "points": [4]}),
            source="figure",
        ).render("bmp")
        assert bmp[:2] == b"BM"

    def test_labels_render_with_braille_profile(self):
        plain = _raster(self.BAR, source="figure")
        labelled = _raster(self.BAR, source="figure", braille_profile="cn_current")
        assert labelled.raised_count() > plain.raised_count()

    def test_unknown_figure_surfaces_warning(self):
        warn = WarningCollector()
        _raster('{"kind": "pie"}', source="figure", warnings=warn)
        assert any(w.code == "GRAPHICS_UNKNOWN_FIGURE" for w in warn)


class TestTextureFill:
    def test_filled_shape_adds_texture(self):
        outline = _raster(
            '<svg viewBox="0 0 100 100"><rect x="10" y="10" width="80" height="80"/></svg>'
        )
        filled = _raster(
            '<svg viewBox="0 0 100 100">'
            '<rect x="10" y="10" width="80" height="80" fill="hatch"/></svg>'
        )
        assert filled.raised_count() > outline.raised_count()

    def test_primitives_fill(self):
        spec = json.dumps(
            {
                "width": 100,
                "height": 100,
                "shapes": [
                    {
                        "type": "polygon",
                        "points": [[10, 90], [50, 10], [90, 90]],
                        "fill": "cross",
                    }
                ],
            }
        )
        r = _raster(spec, source="primitives")
        assert r.raised_count() > 0


class TestLabels:
    LABEL_SVG = (
        '<svg viewBox="0 0 100 100" width="100mm" height="100mm">'
        '<text x="10" y="10">AB</text></svg>'
    )

    def test_no_braille_profile_warns_and_skips(self):
        warn = WarningCollector()
        r = _raster(self.LABEL_SVG, warnings=warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_LABEL_NO_PROFILE" for w in warn)

    def test_injected_translator_stamps_label(self):
        def translator(_text):
            return [BrailleCell(dots=(1,)), BrailleCell(dots=(1, 2))]

        r = _raster(self.LABEL_SVG, label_translator=translator)
        assert r.raised_count() > 0

    def test_braille_profile_translates_label(self):
        # Real text→braille path via a shipped braille standard.
        r = _raster(self.LABEL_SVG, braille_profile="cn_current")
        assert r.raised_count() > 0

    def test_label_in_bmp(self):
        bmp = _compile(self.LABEL_SVG, braille_profile="cn_current").render("bmp")
        assert bmp[:2] == b"BM"
        offset = struct.unpack("<I", bmp[10:14])[0]
        assert any(b == 0 for b in bmp[offset:])  # raised braille dots present


class TestImageImport:
    """Raster import (T3 bitmap half): a bitmap file → tactile raster → BMP,
    through the public ``image`` source. Needs Pillow (the ``graphics``
    extra), so each method skips when it is absent."""

    def _png(self, tmp_path, *, value: int = 0, size: int = 12):
        image_mod = pytest.importorskip("PIL.Image")
        p = tmp_path / "img.png"
        image_mod.new("L", (size, size), value).save(p, format="PNG")
        return p

    def test_image_path_to_raster(self, tmp_path):
        p = self._png(tmp_path, value=0)  # all black → all raised at threshold
        assert _raster(str(p), source="image").raised_count() > 0

    def test_image_to_bmp(self, tmp_path):
        p = self._png(tmp_path, value=0)
        assert _compile(str(p), source="image").render("bmp")[:2] == b"BM"

    def test_white_image_is_blank(self, tmp_path):
        p = self._png(tmp_path, value=255)  # all white → flat
        assert _raster(str(p), source="image").raised_count() == 0

    def test_svg_image_renders_via_resvg(self, tmp_path):
        # A .svg opened as an image source is rendered full-fidelity by resvg
        # (the faithful raster path, vs the editable 'svg' tag-walk).
        pytest.importorskip("resvg_py")
        svg = tmp_path / "d.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" '
            'viewBox="0 0 40 30"><rect x="5" y="5" width="30" height="20" '
            'fill="black"/></svg>',
            encoding="utf-8",
        )
        assert _raster(str(svg), source="image").raised_count() > 0
