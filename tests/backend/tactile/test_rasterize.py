"""Tests for the tactile backend rasterizer (SVG tree → TactileRaster)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import brailix.backend.tactile as tactile
from brailix.backend.tactile import rasterize
from brailix.backend.tactile.profile import TactileProfile
from brailix.core.errors import WarningCollector
from brailix.ir.braille import BrailleCell


def _profile(**overrides) -> TactileProfile:
    base = dict(
        name="t",
        dpi=100.0,
        page_width_mm=210.0,
        page_height_mm=297.0,
        min_line_width_mm=0.5,
        min_feature_spacing_mm=2.5,
        braille_dot_radius_mm=0.75,
        braille_dot_spacing_mm=2.5,
        braille_cell_spacing_mm=6.0,
    )
    base.update(overrides)
    return TactileProfile(**base)


def _svg(inner: str = "", **attrs) -> ET.Element:
    attr_str = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return ET.fromstring(f"<svg {attr_str}>{inner}</svg>")


class TestGeometryResolution:
    def test_viewbox_with_mm_size(self):
        root = _svg(viewBox="0 0 100 100", width="50mm", height="50mm")
        r = rasterize(root, _profile())
        # 50 mm * 100 dpi / 25.4 = 196.85 → 197 px
        assert r.width == 197
        assert r.height == 197
        assert r.page_width_mm == 50.0
        assert abs(r.dpi - 100.0) < 1.0

    def test_unitless_size_is_millimetres(self):
        root = _svg(viewBox="0 0 80 40", width="80", height="40")
        r = rasterize(root, _profile())
        # width unitless → 80 mm; 80 * 100 / 25.4 = 314.96 → 315
        assert r.width == 315
        assert r.page_width_mm == 80.0

    def test_viewbox_only_treats_units_as_mm(self):
        root = _svg(viewBox="0 0 100 50")
        r = rasterize(root, _profile())
        assert r.page_width_mm == 100.0
        assert r.page_height_mm == 50.0

    def test_no_geometry_falls_back_to_page_size(self):
        root = _svg()
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert r.page_width_mm == 210.0
        assert r.page_height_mm == 297.0
        assert any(w.code == "GRAPHICS_NO_GEOMETRY" for w in warn)


class TestPrimitiveRendering:
    def test_line_raises_cells(self):
        root = _svg('<line x1="0" y1="0" x2="100" y2="100"/>', viewBox="0 0 100 100")
        r = rasterize(root, _profile())
        assert r.raised_count() > 0

    def test_rect_outline(self):
        root = _svg('<rect x="10" y="10" width="80" height="80"/>', viewBox="0 0 100 100")
        r = rasterize(root, _profile())
        assert r.raised_count() > 0

    def test_circle(self):
        root = _svg('<circle cx="50" cy="50" r="30"/>', viewBox="0 0 100 100")
        r = rasterize(root, _profile())
        assert r.raised_count() > 0

    def test_group_recursion(self):
        root = _svg(
            '<g><line x1="0" y1="0" x2="50" y2="50"/></g>', viewBox="0 0 100 100"
        )
        r = rasterize(root, _profile())
        assert r.raised_count() > 0

    def test_empty_svg_is_blank(self):
        root = _svg(viewBox="0 0 100 100")
        r = rasterize(root, _profile())
        assert r.raised_count() == 0

    def test_zero_size_rect_skipped(self):
        root = _svg('<rect x="0" y="0" width="0" height="0"/>', viewBox="0 0 100 100")
        r = rasterize(root, _profile())
        assert r.raised_count() == 0


class TestNonFiniteGeometry:
    """Non-finite numbers (NaN / ±inf, e.g. a "1e999" overflow or a "nan"
    literal) must not raise — rasterize's "never raises on bad geometry"
    contract. Each degenerate case yields a valid (blank or clamped) raster."""

    @pytest.mark.parametrize(
        "inner,attrs",
        [
            ("<rect x='1' y='1' width='9' height='9'/>", {"viewBox": "nan nan nan nan"}),
            ("<rect x='1' y='1' width='9' height='9'/>", {"viewBox": "0 0 1e999 10"}),
            ("<line x1='0' y1='0' x2='1e999' y2='0'/>", {"viewBox": "0 0 100 100"}),
            ("<path d='M 1e999 0 C 0 0 4 0 4 2'/>", {"viewBox": "0 0 100 100"}),
            ("<circle cx='50' cy='50' r='1e999'/>", {"viewBox": "0 0 100 100"}),
            (
                "<g transform='scale(1e999)'><rect x='1' y='1' width='2' height='2'/></g>",
                {"viewBox": "0 0 100 100"},
            ),
            ("<polygon points='0,0 1e999,1 nan,2'/>", {"viewBox": "0 0 100 100"}),
        ],
    )
    def test_non_finite_does_not_raise(self, inner, attrs):
        root = _svg(inner, **attrs)
        r = rasterize(root, _profile(), WarningCollector())  # must not raise
        assert r.width >= 1 and r.height >= 1


class TestSoftFailures:
    def test_unsupported_element_warns_once(self):
        # ``<use>`` (defs/use flattening) is still a deferred element; two of
        # them warn once. (``<image>`` is now handled — see test_image.py.)
        root = _svg(
            '<use href="#a"/><use href="#b"/>', viewBox="0 0 100 100"
        )
        warn = WarningCollector()
        rasterize(root, _profile(), warn)
        codes = [w.code for w in warn]
        assert codes.count("GRAPHICS_UNSUPPORTED_ELEMENT") == 1

    def test_path_is_drawn(self):
        root = _svg('<path d="M0 0 L9 9"/>', viewBox="0 0 100 100")
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert r.raised_count() > 0
        assert not any(w.code == "GRAPHICS_UNSUPPORTED_ELEMENT" for w in warn)

    def test_transform_applied(self):
        root = _svg(
            '<g transform="translate(5,5)"><line x1="0" y1="0" x2="1" y2="1"/></g>',
            viewBox="0 0 100 100",
        )
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert not any(w.code == "GRAPHICS_UNSUPPORTED_TRANSFORM" for w in warn)
        assert r.raised_count() > 0

    def test_non_drawing_tags_silently_skipped(self):
        root = _svg(
            "<title>t</title><desc>d</desc><defs/>", viewBox="0 0 100 100"
        )
        warn = WarningCollector()
        rasterize(root, _profile(), warn)
        assert len(warn) == 0

    def test_error_marker_emits_soft_fail(self):
        root = ET.Element("svg")
        root.set("data-bk-error", "parse error: boom")
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_SOFT_FAIL" for w in warn)

    def test_malformed_attributes_do_not_crash(self):
        root = _svg(
            '<line x1="oops" y1="" x2="10"/>', viewBox="0 0 100 100"
        )
        # Should not raise; missing coords default to 0.
        rasterize(root, _profile())


class TestStrokeWidth:
    def _line(self, extra: str = "") -> int:
        root = _svg(
            f'<line x1="0" y1="50" x2="100" y2="50" {extra}/>', viewBox="0 0 100 100"
        )
        return rasterize(root, _profile()).raised_count()

    def test_stroke_width_thickens(self):
        assert self._line('stroke-width="5"') > self._line()

    def test_tiny_stroke_floored_to_minimum(self):
        # A sub-minimum stroke is floored to the profile's min line width,
        # so it matches the default (no stroke-width) line.
        assert self._line('stroke-width="0.01"') == self._line()

    def test_zero_stroke_falls_back(self):
        assert self._line('stroke-width="0"') == self._line()

    def test_group_stroke_width_inherited(self):
        grouped = _svg(
            '<g stroke-width="5"><line x1="0" y1="50" x2="100" y2="50"/></g>',
            viewBox="0 0 100 100",
        )
        direct = _svg(
            '<line x1="0" y1="50" x2="100" y2="50" stroke-width="5"/>',
            viewBox="0 0 100 100",
        )
        assert (
            rasterize(grouped, _profile()).raised_count()
            == rasterize(direct, _profile()).raised_count()
        )

    def test_child_overrides_group_stroke_width(self):
        # Child's thin stroke (floored to min) wins over the group's thick one.
        root = _svg(
            '<g stroke-width="9"><line x1="0" y1="50" x2="100" y2="50" '
            'stroke-width="0.01"/></g>',
            viewBox="0 0 100 100",
        )
        thin = self._line()
        assert rasterize(root, _profile()).raised_count() == thin


class TestRasterClamp:
    def test_oversize_raster_is_clamped(self, monkeypatch):
        monkeypatch.setattr(tactile, "_MAX_RASTER_PIXELS", 1000)
        root = _svg(viewBox="0 0 100 100", width="50mm", height="50mm")
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert r.width * r.height <= 1000
        assert any(w.code == "GRAPHICS_RASTER_CLAMPED" for w in warn)


class TestLabels:
    def _fake(self, recorder=None):
        def translate(text):
            if recorder is not None:
                recorder.append(text)
            return [BrailleCell(dots=(1,)) for _ in text.replace(" ", "")]
        return translate

    def test_text_without_translator_warns(self):
        root = _svg('<text x="1" y="1">hi</text>', viewBox="0 0 100 100")
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn)
        assert r.raised_count() == 0
        assert any(w.code == "GRAPHICS_LABEL_NO_PROFILE" for w in warn)

    def test_text_with_translator_stamps(self):
        root = _svg('<text x="10" y="10">hi</text>', viewBox="0 0 100 100")
        r = rasterize(root, _profile(), None, self._fake())
        assert r.raised_count() > 0

    def test_empty_text_skipped(self):
        root = _svg('<text x="1" y="1">   </text>', viewBox="0 0 100 100")
        warn = WarningCollector()
        r = rasterize(root, _profile(), warn, self._fake())
        # Whitespace-only label: nothing translated, no warning.
        assert r.raised_count() == 0
        assert len(warn) == 0

    def test_tspan_text_gathered(self):
        seen: list[str] = []
        root = _svg(
            '<text x="1" y="1">a<tspan>b</tspan>c</text>', viewBox="0 0 100 100"
        )
        rasterize(root, _profile(), None, self._fake(seen))
        assert seen == ["abc"]


class TestFill:
    def _outline(self, fill: str = "") -> int:
        attr = f'fill="{fill}"' if fill else ""
        root = _svg(
            f'<rect x="10" y="10" width="80" height="80" {attr}/>',
            viewBox="0 0 100 100",
        )
        return rasterize(root, _profile()).raised_count()

    def test_fill_adds_interior_texture(self):
        assert self._outline("hatch") > self._outline() * 2

    def test_fill_none_is_outline_only(self):
        assert self._outline("none") == self._outline()
        assert self._outline("transparent") == self._outline()

    def test_fill_is_textured_not_solid(self):
        root = _svg(
            '<rect x="0" y="0" width="100" height="100" fill="hatch"/>',
            viewBox="0 0 100 100",
        )
        r = rasterize(root, _profile())
        # A texture covers far less than a solid fill would.
        assert 0 < r.raised_count() < r.width * r.height * 0.6

    def test_filled_circle(self):
        root = _svg('<circle cx="50" cy="50" r="40" fill="dots"/>', viewBox="0 0 100 100")
        plain = _svg('<circle cx="50" cy="50" r="40"/>', viewBox="0 0 100 100")
        assert (
            rasterize(root, _profile()).raised_count()
            > rasterize(plain, _profile()).raised_count()
        )

    def test_filled_polygon(self):
        root = _svg(
            '<polygon points="10,90 50,10 90,90" fill="cross"/>', viewBox="0 0 100 100"
        )
        assert rasterize(root, _profile()).raised_count() > 0

    def test_polyline_not_filled(self):
        # An open polyline ignores fill (outline only).
        plain = _svg('<polyline points="10,10 90,10 90,90"/>', viewBox="0 0 100 100")
        filled = _svg(
            '<polyline points="10,10 90,10 90,90" fill="hatch"/>', viewBox="0 0 100 100"
        )
        assert (
            rasterize(filled, _profile()).raised_count()
            == rasterize(plain, _profile()).raised_count()
        )

    def test_group_fill_inherited(self):
        grouped = _svg(
            '<g fill="hatch"><rect x="10" y="10" width="80" height="80"/></g>',
            viewBox="0 0 100 100",
        )
        direct = _svg(
            '<rect x="10" y="10" width="80" height="80" fill="hatch"/>',
            viewBox="0 0 100 100",
        )
        assert (
            rasterize(grouped, _profile()).raised_count()
            == rasterize(direct, _profile()).raised_count()
        )


class TestParsingHelpers:
    @pytest.mark.parametrize(
        "value, expected",
        [("10", 10.0), ("10px", 10.0), ("-5.5", -5.5), ("1e3", 1000.0), ("", 0.0)],
    )
    def test_parse_float(self, value, expected):
        assert tactile._parse_float(value, 0.0) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [("10mm", 10.0), ("1cm", 10.0), ("1in", 25.4), ("100", None), ("50%", None)],
    )
    def test_length_to_mm(self, value, expected):
        assert tactile._length_to_mm(value) == expected

    def test_parse_points(self):
        assert tactile._parse_points("1,2 3,4") == [(1.0, 2.0), (3.0, 4.0)]
        assert tactile._parse_points("1 2 3 4") == [(1.0, 2.0), (3.0, 4.0)]
        # Odd trailing coordinate dropped.
        assert tactile._parse_points("1 2 3") == [(1.0, 2.0)]
        assert tactile._parse_points(None) == []


def _full_cell_translator(text):
    # One solid braille cell per non-space char, so a label footprint is dense
    # enough to make overlap unambiguous.
    return [BrailleCell(dots=(1, 2, 3, 4, 5, 6)) for _ in text.replace(" ", "")]


def _codes(warn: WarningCollector) -> list[str]:
    return [w.code for w in warn]


class TestSeparation:
    """BANA touch-separability diagnostics — detection only (never moves the
    author's geometry). 1 user unit = 1 mm via a 100 mm / 100-viewBox SVG."""

    def _render(self, inner: str, *, translator=None) -> WarningCollector:
        root = _svg(inner, width="100mm", height="100mm", viewBox="0 0 100 100")
        warn = WarningCollector()
        rasterize(root, _profile(), warn, translator)
        return warn

    def test_close_elements_warn(self):
        # Two rects 1 mm apart (< 2.5 mm min spacing).
        warn = self._render(
            '<rect x="10" y="10" width="20" height="20"/>'
            '<rect x="31" y="10" width="20" height="20"/>'
        )
        assert "GRAPHICS_FEATURES_TOO_CLOSE" in _codes(warn)

    def test_far_elements_no_warn(self):
        warn = self._render(
            '<rect x="10" y="10" width="20" height="20"/>'
            '<rect x="60" y="10" width="20" height="20"/>'
        )
        assert "GRAPHICS_FEATURES_TOO_CLOSE" not in _codes(warn)

    def test_overlapping_elements_no_warn(self):
        # Touching / overlapping = intentional connection, not flagged.
        warn = self._render(
            '<rect x="10" y="10" width="30" height="30"/>'
            '<rect x="25" y="10" width="30" height="30"/>'
        )
        assert "GRAPHICS_FEATURES_TOO_CLOSE" not in _codes(warn)

    def test_single_element_no_warn(self):
        warn = self._render('<rect x="10" y="10" width="20" height="20"/>')
        assert "GRAPHICS_FEATURES_TOO_CLOSE" not in _codes(warn)

    def test_thick_parallel_lines_surface_too_close_warn(self):
        # Centre lines 6 mm apart (> 2.5 mm min spacing), but a stroke-width of
        # 4 grows each raised surface toward the other so the actual ink gap is
        # < 2.5 mm. The spacing box must include the stroke radius to catch it.
        warn = self._render(
            '<line x1="5" y1="47" x2="95" y2="47" stroke-width="4"/>'
            '<line x1="5" y1="53" x2="95" y2="53" stroke-width="4"/>'
        )
        assert "GRAPHICS_FEATURES_TOO_CLOSE" in _codes(warn)

    def test_thin_parallel_lines_same_centre_gap_no_warn(self):
        # Same 6 mm centre spacing with hairline strokes: surfaces stay well
        # apart, so the warning must come from the radius growth, not the
        # centre distance itself.
        warn = self._render(
            '<line x1="5" y1="47" x2="95" y2="47"/>'
            '<line x1="5" y1="53" x2="95" y2="53"/>'
        )
        assert "GRAPHICS_FEATURES_TOO_CLOSE" not in _codes(warn)

    def test_label_over_figure_warns(self):
        warn = self._render(
            '<line x1="10" y1="40" x2="90" y2="40"/><text x="40" y="40">A</text>',
            translator=_full_cell_translator,
        )
        assert "GRAPHICS_LABEL_OVERLAP" in _codes(warn)

    def test_label_clear_of_figure_no_warn(self):
        warn = self._render(
            '<line x1="10" y1="10" x2="90" y2="10"/><text x="40" y="80">A</text>',
            translator=_full_cell_translator,
        )
        assert "GRAPHICS_LABEL_OVERLAP" not in _codes(warn)

    def test_labels_overlapping_each_other_warn(self):
        warn = self._render(
            '<text x="40" y="40">A</text><text x="40" y="40">B</text>',
            translator=_full_cell_translator,
        )
        assert "GRAPHICS_LABEL_OVERLAP" in _codes(warn)

    def test_deferred_labels_still_stamp(self):
        # Regression: deferring text to the post-walk pass must still paint it.
        root = _svg('<text x="40" y="40">AB</text>', width="100mm",
                    height="100mm", viewBox="0 0 100 100")
        r = rasterize(root, _profile(), None, _full_cell_translator)
        assert r.raised_count() > 0
