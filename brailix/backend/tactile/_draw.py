"""Zero-dependency raster drawing primitives for the tactile backend.

The built-in rasterizer paints geometry into a
:class:`~brailix.ir.tactile.TactileRaster` using nothing but integer
Bresenham line walking and disk stamping — pure standard library, so the
tactile vertical keeps core's "zero third-party parser dependency"
guarantee even with no optional rasterization library installed. (Full
external-SVG rasterization lives in ``_image`` behind the
``graphics-svg-raster`` extra, for ``<image>`` references; this primitive
set covers the inline shapes the graphics frontend emits.)

Every stroke is drawn by stamping a small filled disk at each point along
its centre line. Disk stamping gives round caps and joins for free and
guarantees a continuous, gap-free raised line — exactly what a finger
needs to trace. All writes go through
:meth:`TactileRaster.set_raise`, which takes the maximum, so overlapping
strokes only ever add height.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence

from brailix.ir.tactile import TactileRaster


def stamp_disk(
    raster: TactileRaster, cx: int, cy: int, radius: int, level: int
) -> None:
    """Raise a filled disk of ``radius`` px centred at ``(cx, cy)``.

    ``radius == 0`` raises the single centre pixel — the thinnest
    possible stroke. Out-of-bounds pixels are clipped by
    :meth:`TactileRaster.set_raise`.
    """
    if radius <= 0:
        raster.set_raise(cx, cy, level)
        return
    r2 = radius * radius
    # Clamp the disk's bounding box to the raster before iterating, exactly
    # like ``_fill.fill_ellipse``. Pixels outside the page are dropped by
    # ``set_raise`` anyway, so on-page output is byte-for-byte identical, but
    # a pathologically large ``radius`` no longer costs O(radius²).
    lo_y = max(0, cy - radius)
    hi_y = min(raster.height - 1, cy + radius)
    lo_x = max(0, cx - radius)
    hi_x = min(raster.width - 1, cx + radius)
    for yy in range(lo_y, hi_y + 1):
        dy = yy - cy
        dy2 = dy * dy
        for xx in range(lo_x, hi_x + 1):
            dx = xx - cx
            if dx * dx + dy2 <= r2:
                raster.set_raise(xx, yy, level)


def _bresenham(
    x0: int, y0: int, x1: int, y1: int
) -> Iterator[tuple[int, int]]:
    """Yield every integer point on the line from ``(x0, y0)`` to
    ``(x1, y1)`` inclusive (classic integer Bresenham, all octants)."""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _clip_segment(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
) -> tuple[float, float, float, float] | None:
    """Liang-Barsky clip of the segment to the rectangle
    ``[xmin, xmax] × [ymin, ymax]``.

    Returns the clipped ``(x0, y0, x1, y1)`` endpoints, or ``None`` when the
    segment lies entirely outside the rectangle. The clip is exact: the part
    of the segment inside the rectangle is unchanged.
    """
    dx = x1 - x0
    dy = y1 - y0
    p = (-dx, dx, -dy, dy)
    q = (x0 - xmin, xmax - x0, y0 - ymin, ymax - y0)
    u0, u1 = 0.0, 1.0
    for pi, qi in zip(p, q, strict=True):
        if pi == 0:
            if qi < 0:  # parallel to this edge and on its outside
                return None
        else:
            r = qi / pi
            if pi < 0:
                if r > u1:
                    return None
                if r > u0:
                    u0 = r
            else:
                if r < u0:
                    return None
                if r < u1:
                    u1 = r
    return (x0 + u0 * dx, y0 + u0 * dy, x0 + u1 * dx, y0 + u1 * dy)


def draw_line(
    raster: TactileRaster,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    radius: int,
    level: int,
) -> None:
    """Draw a straight stroke of half-width ``radius`` px between two
    integer points.

    The segment is first clipped to the page (expanded by ``radius`` so a
    stroke whose centre line just misses the page but whose disk still
    reaches it is not dropped), so the Bresenham walk only ever iterates
    over the on-page portion. A fully on-page segment is clipped to itself
    and draws byte-for-byte as before; an out-of-page or huge-coordinate
    segment no longer walks millions of off-page points.
    """
    m = max(0, radius)
    clip = _clip_segment(
        x0,
        y0,
        x1,
        y1,
        -m,
        -m,
        raster.width - 1 + m,
        raster.height - 1 + m,
    )
    if clip is None:
        return
    cx0, cy0, cx1, cy1 = clip
    for px, py in _bresenham(
        round(cx0), round(cy0), round(cx1), round(cy1)
    ):
        stamp_disk(raster, px, py, radius, level)


def draw_polyline(
    raster: TactileRaster,
    points: Sequence[tuple[int, int]],
    radius: int,
    level: int,
    *,
    closed: bool,
) -> None:
    """Draw a connected run of segments through ``points``.

    With ``closed=True`` the last point is joined back to the first
    (polygon outline). A single point is stamped as a dot; an empty
    sequence draws nothing.
    """
    if len(points) < 2:
        if points:
            stamp_disk(raster, points[0][0], points[0][1], radius, level)
        return
    pts = list(points)
    if closed:
        pts.append(pts[0])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        draw_line(raster, x0, y0, x1, y1, radius, level)


def draw_ellipse(
    raster: TactileRaster,
    cx: int,
    cy: int,
    rx: int,
    ry: int,
    radius: int,
    level: int,
) -> None:
    """Draw an ellipse outline by sampling its perimeter densely enough
    that consecutive stamped disks always touch.

    The sample count is proportional to the larger semi-axis so the arc
    step never exceeds ~1 px — the disk stamps then overlap into a
    continuous outline regardless of ``radius`` (a circle is just
    ``rx == ry``).
    """
    rx = abs(rx)
    ry = abs(ry)
    if rx == 0 and ry == 0:
        stamp_disk(raster, cx, cy, radius, level)
        return
    m = max(0, radius)
    lo_x, hi_x = -m, raster.width - 1 + m
    lo_y, hi_y = -m, raster.height - 1 + m
    # Fast path: the whole outline fits the (radius-expanded) page. Keep the
    # historical uniform sampling exactly, so every on-page ellipse renders
    # byte-for-byte as before and the goldens hold.
    if (
        cx - rx >= lo_x
        and cx + rx <= hi_x
        and cy - ry >= lo_y
        and cy + ry <= hi_y
    ):
        steps = max(16, int(2 * math.pi * max(rx, ry)) + 1)
        for i in range(steps):
            t = 2 * math.pi * i / steps
            x = round(cx + rx * math.cos(t))
            y = round(cy + ry * math.sin(t))
            stamp_disk(raster, x, y, radius, level)
        return
    # Windowed path: the outline leaves the page. A point at angle ``t`` is
    # on the (expanded) page only when ``cos t`` lies in ``[u0, u1]`` and
    # ``sin t`` in ``[v0, v1]``. Sample just those arc spans, at <=1 px
    # spacing so the disks still overlap into a gap-free outline. Work is
    # bounded by the on-page arc length, never by the radius.
    if rx == 0:
        if not lo_x <= cx <= hi_x:
            return
        u0, u1 = -1.0, 1.0
    else:
        u0 = max(-1.0, (lo_x - cx) / rx)
        u1 = min(1.0, (hi_x - cx) / rx)
        if u0 > u1:
            return
    if ry == 0:
        if not lo_y <= cy <= hi_y:
            return
        v0, v1 = -1.0, 1.0
    else:
        v0 = max(-1.0, (lo_y - cy) / ry)
        v1 = min(1.0, (hi_y - cy) / ry)
        if v0 > v1:
            return
    two_pi = 2 * math.pi
    events = [0.0, two_pi]
    for c in (u0, u1):
        a = math.acos(c)
        events.append(a)
        events.append(two_pi - a)
    for s in (v0, v1):
        a = math.asin(s)
        events.append(a % two_pi)
        events.append((math.pi - a) % two_pi)
    events = sorted(set(events))
    max_r = max(rx, ry)
    for a, b in zip(events, events[1:], strict=False):
        if b <= a:
            continue
        mid = 0.5 * (a + b)
        if u0 <= math.cos(mid) <= u1 and v0 <= math.sin(mid) <= v1:
            n = max(1, int((b - a) * max_r) + 1)
            for k in range(n + 1):
                t = a + (b - a) * k / n
                x = round(cx + rx * math.cos(t))
                y = round(cy + ry * math.sin(t))
                stamp_disk(raster, x, y, radius, level)


def draw_circle(
    raster: TactileRaster,
    cx: int,
    cy: int,
    r: int,
    radius: int,
    level: int,
) -> None:
    """Draw a circle outline — :func:`draw_ellipse` with equal axes."""
    draw_ellipse(raster, cx, cy, r, r, radius, level)
