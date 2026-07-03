"""Element → pixel provenance (graphic highlight foundation, H1).

The backend records which raster pixels each SVG element (by its stable
``data-bk-gid``) drew, opt-in, for the editor's cross-pane highlight.
"""

from __future__ import annotations

from brailix import Pipeline
from brailix.frontend.graphics.normalizer import normalize

CIRCLE = '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>'
TWO = (
    '<svg viewBox="0 0 100 100">'
    '<circle cx="25" cy="25" r="10"/>'
    '<rect x="60" y="60" width="20" height="20"/></svg>'
)


def svg_to_raster(svg, *, record_provenance=False):
    """Rasterise via the public Pipeline entry (the braille profile is an
    unused placeholder here — these graphics carry no text labels)."""
    return Pipeline(profile="cn_current").translate_graphic(
        svg, record_provenance=record_provenance
    ).raster


def test_normalizer_assigns_unique_gids() -> None:
    tree = normalize(CIRCLE)
    gids = [e.get("data-bk-gid") for e in tree.iter()]
    assert all(g is not None for g in gids)
    assert len(set(gids)) == len(gids)  # stable + unique


def test_no_provenance_by_default() -> None:
    # Export / headless path pays nothing.
    assert svg_to_raster(CIRCLE).provenance is None


def test_provenance_records_element_pixels() -> None:
    raster = svg_to_raster(CIRCLE, record_provenance=True)
    assert raster.provenance is not None
    # The circle drew a non-empty pixel set, keyed by its gid.
    assert any(pixels for pixels in raster.provenance.values())
    # Every recorded pixel index is in range.
    n = raster.width * raster.height
    for pixels in raster.provenance.values():
        assert all(0 <= i < n for i in pixels)


def test_two_elements_have_distinct_keys() -> None:
    raster = svg_to_raster(TWO, record_provenance=True)
    assert raster.provenance is not None
    nonempty = {gid: px for gid, px in raster.provenance.items() if px}
    assert len(nonempty) >= 2  # circle + rect attributed separately
    # The two shapes are far apart → disjoint pixels.
    sets = list(nonempty.values())
    assert sets[0].isdisjoint(sets[1])
