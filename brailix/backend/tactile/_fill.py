"""Texture area fills for the tactile backend.

A BANA touch rule (``ARCHITECTURE.md`` / §7): a filled
region must read as a **texture**, not a solid block — fingers can feel a
hatch or stipple pattern but cannot tell two solid raised areas apart.
Distinct fills therefore map to distinct textures (texture replaces colour
as the way to tell regions apart), and the texture's line gaps respect the
profile's minimum feature spacing so they stay individually touchable.

Each fill scans the shape's device-pixel bounding box, tests membership
(rectangle / ellipse / polygon), and raises the pixels that fall on the
texture pattern. Pure standard library, like the rest of the backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from brailix.ir.tactile import TactileRaster

# Touch-distinguishable textures, ordered clearest/sparsest first so the
# first few distinct fills in a drawing get the easiest-to-read patterns.
TEXTURES: tuple[str, ...] = (
    "hatch_forward",
    "hatch_back",
    "hatch_horizontal",
    "hatch_vertical",
    "stipple",
    "cross_hatch",
)

# Friendly aliases an author can use as an SVG ``fill`` value to pick a
# texture directly instead of relying on the distinct-fill mapping.
_ALIASES: dict[str, str] = {
    "hatch": "hatch_forward",
    "lines": "hatch_horizontal",
    "dots": "stipple",
    "cross": "cross_hatch",
}


def normalize_texture(fill: str) -> str | None:
    """Return the texture a ``fill`` names directly (a texture name or a
    known alias), or ``None`` if it is an arbitrary value (a colour) that
    the caller should map by first-seen order."""
    key = fill.strip().lower()
    if key in TEXTURES:
        return key
    return _ALIASES.get(key)


def _hit(texture: str, x: int, y: int, spacing: int, thickness: int) -> bool:
    """Whether pixel ``(x, y)`` lies on ``texture``'s raised pattern."""
    if spacing <= 0:
        return False
    if texture == "hatch_forward":
        return (x + y) % spacing < thickness
    if texture == "hatch_back":
        return (x - y) % spacing < thickness
    if texture == "hatch_horizontal":
        return y % spacing < thickness
    if texture == "hatch_vertical":
        return x % spacing < thickness
    if texture == "stipple":
        return x % spacing < thickness and y % spacing < thickness
    if texture == "cross_hatch":
        return (x + y) % spacing < thickness or (x - y) % spacing < thickness
    return False


def _point_in_polygon(x: int, y: int, pts: Sequence[tuple[int, int]]) -> bool:
    """Ray-casting point-in-polygon test for integer device coordinates."""
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


@dataclass(frozen=True, slots=True)
class FillStyle:
    """The parameters every texture fill shares: which touch ``texture`` to
    stamp, the pattern's line ``spacing`` and ``thickness`` in device pixels,
    and the raise ``level`` to write. Built once per element by
    ``_State.fill_style`` so one value travels to every ``fill_*`` call."""

    texture: str
    spacing: int
    thickness: int
    level: int


def fill_rect(
    raster: TactileRaster,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    style: FillStyle,
) -> None:
    """Texture-fill the axis-aligned rectangle ``[x0, x1] × [y0, y1]``."""
    lo_x, hi_x = sorted((x0, x1))
    lo_y, hi_y = sorted((y0, y1))
    lo_x, lo_y = max(0, lo_x), max(0, lo_y)
    hi_x = min(raster.width - 1, hi_x)
    hi_y = min(raster.height - 1, hi_y)
    for y in range(lo_y, hi_y + 1):
        for x in range(lo_x, hi_x + 1):
            if _hit(style.texture, x, y, style.spacing, style.thickness):
                raster.set_raise(x, y, style.level)


def fill_ellipse(
    raster: TactileRaster,
    cx: int,
    cy: int,
    rx: int,
    ry: int,
    style: FillStyle,
) -> None:
    """Texture-fill the ellipse centred at ``(cx, cy)`` with radii
    ``rx`` / ``ry`` (a circle when ``rx == ry``)."""
    if rx <= 0 or ry <= 0:
        return
    lo_x, hi_x = max(0, cx - rx), min(raster.width - 1, cx + rx)
    lo_y, hi_y = max(0, cy - ry), min(raster.height - 1, cy + ry)
    rx2, ry2 = rx * rx, ry * ry
    for y in range(lo_y, hi_y + 1):
        dy = y - cy
        for x in range(lo_x, hi_x + 1):
            dx = x - cx
            if dx * dx * ry2 + dy * dy * rx2 <= rx2 * ry2 and _hit(
                style.texture, x, y, style.spacing, style.thickness
            ):
                raster.set_raise(x, y, style.level)


def fill_polygon(
    raster: TactileRaster,
    pts: Sequence[tuple[int, int]],
    style: FillStyle,
) -> None:
    """Texture-fill the polygon through ``pts`` (device coordinates)."""
    if len(pts) < 3:
        return
    lo_x = max(0, min(p[0] for p in pts))
    hi_x = min(raster.width - 1, max(p[0] for p in pts))
    lo_y = max(0, min(p[1] for p in pts))
    hi_y = min(raster.height - 1, max(p[1] for p in pts))
    for y in range(lo_y, hi_y + 1):
        for x in range(lo_x, hi_x + 1):
            if _point_in_polygon(x, y, pts) and _hit(
                style.texture, x, y, style.spacing, style.thickness
            ):
                raster.set_raise(x, y, style.level)


def fill_polygons(
    raster: TactileRaster,
    rings: Sequence[Sequence[tuple[int, int]]],
    style: FillStyle,
) -> None:
    """Texture-fill a multi-ring region via the even-odd rule: a pixel is
    inside when it lies within an odd number of rings, so inner subpaths
    punch holes. Used for ``<path>`` fills made of multiple subpaths."""
    valid = [list(r) for r in rings if len(r) >= 3]
    if not valid:
        return
    all_pts = [p for ring in valid for p in ring]
    lo_x = max(0, min(p[0] for p in all_pts))
    hi_x = min(raster.width - 1, max(p[0] for p in all_pts))
    lo_y = max(0, min(p[1] for p in all_pts))
    hi_y = min(raster.height - 1, max(p[1] for p in all_pts))
    for y in range(lo_y, hi_y + 1):
        for x in range(lo_x, hi_x + 1):
            inside = sum(_point_in_polygon(x, y, ring) for ring in valid) % 2 == 1
            if inside and _hit(style.texture, x, y, style.spacing, style.thickness):
                raster.set_raise(x, y, style.level)
