"""End-to-end rasterizer tests for ``<path>`` + ``transform`` support."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.tactile import rasterize
from brailix.backend.tactile.profile import load_tactile_profile
from brailix.ir.tactile import TactileRaster


def _profile():
    return load_tactile_profile("generic")


def _svg(inner: str, view: str = "0 0 100 100") -> ET.Element:
    # Namespace-free SVG so the backend matches plain tags directly
    # (mirrors the existing test_rasterize helper).
    return ET.fromstring(
        f'<svg width="100mm" height="100mm" viewBox="{view}">{inner}</svg>'
    )


def _centroid(r: TactileRaster) -> tuple[float, float] | None:
    sx = sy = n = 0
    w = r.width
    for i, v in enumerate(r.data):
        if v > 0:
            sx += i % w
            sy += i // w
            n += 1
    return (sx / n, sy / n) if n else None


def test_path_lineto_draws_pixels():
    assert rasterize(_svg('<path d="M10 10 L90 90"/>'), _profile()).raised_count() > 0


def test_path_cubic_curve_draws_pixels():
    r = rasterize(_svg('<path d="M10 50 C30 10 70 90 90 50"/>'), _profile())
    assert r.raised_count() > 0


def test_path_closed_fill_adds_interior():
    filled = rasterize(_svg('<path d="M20 20 L80 20 L50 80 Z" fill="hatch"/>'), _profile())
    outline = rasterize(_svg('<path d="M20 20 L80 20 L50 80 Z"/>'), _profile())
    assert filled.raised_count() > outline.raised_count()


def _region_count(r: TactileRaster, lo_u: float, hi_u: float, span_u: float = 100) -> int:
    s = r.width / span_u
    lo, hi = int(lo_u * s), int(hi_u * s)
    return sum(1 for y in range(lo, hi) for x in range(lo, hi) if r.get(x, y) > 0)


def test_path_even_odd_punches_hole():
    holed = rasterize(
        _svg(
            '<path d="M10 10 L90 10 L90 90 L10 90 Z M40 40 L60 40 L60 60 L40 60 Z" '
            'fill="horizontal"/>'
        ),
        _profile(),
    )
    solid = rasterize(
        _svg('<path d="M10 10 L90 10 L90 90 L10 90 Z" fill="horizontal"/>'), _profile()
    )
    # The inner-ring interior (45..55) is filled in the solid square but empty
    # in the holed one (even-odd carves it out).
    assert _region_count(solid, 45, 55) > 0
    assert _region_count(holed, 45, 55) < _region_count(solid, 45, 55)


def test_transform_translate_shifts_geometry():
    base = _centroid(rasterize(_svg('<line x1="0" y1="0" x2="10" y2="0"/>'), _profile()))
    moved = _centroid(
        rasterize(
            _svg('<g transform="translate(40,40)"><line x1="0" y1="0" x2="10" y2="0"/></g>'),
            _profile(),
        )
    )
    assert base is not None and moved is not None
    assert moved[0] > base[0] and moved[1] > base[1]


def test_transform_scale_enlarges_area():
    small = rasterize(
        _svg('<rect x="10" y="10" width="10" height="10" fill="dots"/>'), _profile()
    )
    big = rasterize(
        _svg('<g transform="scale(3)"><rect x="10" y="10" width="10" height="10" fill="dots"/></g>'),
        _profile(),
    )
    assert big.raised_count() > small.raised_count()


def test_transform_rotate_changes_orientation():
    horiz = rasterize(_svg('<line x1="20" y1="50" x2="50" y2="50"/>'), _profile())
    rot = rasterize(
        _svg('<g transform="rotate(90,20,50)"><line x1="20" y1="50" x2="50" y2="50"/></g>'),
        _profile(),
    )
    assert horiz.raised_count() > 0 and rot.raised_count() > 0
    assert _centroid(horiz) != _centroid(rot)


def test_nested_transforms_compose():
    r = rasterize(
        _svg(
            '<g transform="translate(20,20)">'
            '<g transform="scale(2)"><line x1="0" y1="0" x2="5" y2="5"/></g></g>'
        ),
        _profile(),
    )
    c = _centroid(r)
    assert c is not None
    assert c[0] > 20 and c[1] > 20


def test_bezier_density_scale_independent_across_viewboxes():
    # The same physical arch curve (100 mm wide) written in a small vs large
    # viewBox — with physically equal stroke widths — must rasterise nearly
    # identically. Before the fix the small-coordinate curve degraded to 4
    # chords and the two rasters differed by thousands of pixels.
    small = rasterize(
        _svg('<path d="M0 2 C 0 0 4 0 4 2" stroke-width="0.2"/>', view="0 0 4 4"),
        _profile(),
    )
    big = rasterize(
        _svg(
            '<path d="M0 50 C 0 0 100 0 100 50" stroke-width="5"/>',
            view="0 0 100 100",
        ),
        _profile(),
    )
    n = min(len(small.data), len(big.data))
    diff = sum(1 for i in range(n) if (small.data[i] > 0) != (big.data[i] > 0))
    assert diff < 30  # was ~2753 before the device-scale flattening fix
