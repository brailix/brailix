"""SVG ``<path>`` data parsing + flattening for the tactile backend.

Parses a path's ``d`` attribute into a list of :class:`Subpath`s, each a
polyline of points in **user-space** coordinates. The backend then maps
those points through the current transform + viewBox to device pixels and
strokes / fills them with the existing polyline primitives — so ``<path>``
reuses the same drawing path as ``<polyline>``/``<polygon>``.

Curves are flattened to line segments: cubic (``C``/``S``) and quadratic
(``Q``/``T``) Béziers by uniform subdivision sized to the curve's extent,
and elliptical arcs (``A``) via the SVG endpoint→center parameterization
(W3C SVG implementation notes, "elliptical arc implementation"). Tactile
output needs touchable geometry, not sub-pixel fidelity, so a modest
flattening density is used.

Malformed path data soft-fails: an unparseable / truncated command stops
parsing and the subpaths collected so far are returned, mirroring the
backend's "never crash" contract.

Note: arc flag values (large-arc / sweep) are read as ordinary numbers,
so the rare unseparated-flag shorthand (e.g. ``a5 5 0 11 10 10``) is not
disambiguated — tool-exported paths separate them and parse correctly.
"""

from __future__ import annotations

import math
import re
from typing import NamedTuple


class Subpath(NamedTuple):
    """A flattened subpath: a run of user-space points + whether it closes."""

    points: list[tuple[float, float]]
    closed: bool


_TOKEN_RE = re.compile(
    r"([MmLlHhVvCcSsQqTtAaZz])|"
    r"([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)"
)

_ARGC = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}


def _tokenize(d: str) -> list[tuple[str, str | float]]:
    out: list[tuple[str, str | float]] = []
    for cmd, num in _TOKEN_RE.findall(d):
        if cmd:
            out.append(("cmd", cmd))
        elif num:
            try:
                out.append(("num", float(num)))
            except ValueError:
                continue
    return out


def _seg_count(span: float) -> int:
    """Segment count for a curve whose extent is ``span`` **device pixels**.

    Callers scale the user-space extent by the user-unit→device-pixel factor
    first, so the subdivision density tracks the on-page size of the curve, not
    the magnitude of the author's coordinates (a curve drawn in a tiny viewBox
    or under a large ``scale`` transform gets the same density as its
    equivalent drawn at full size)."""
    if not math.isfinite(span):
        # A NaN / inf span (a "1e999" control point) would raise in int();
        # fall back to the minimum subdivision instead of crashing.
        return 4
    return max(4, min(64, int(span / 2.0) + 1))


def _flatten_cubic(
    out: list[tuple[float, float]],
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    scale: float = 1.0,
) -> None:
    xs = (p0[0], p1[0], p2[0], p3[0])
    ys = (p0[1], p1[1], p2[1], p3[1])
    n = _seg_count(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) * scale)
    for i in range(1, n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        out.append((x, y))


def _flatten_quad(
    out: list[tuple[float, float]],
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    scale: float = 1.0,
) -> None:
    xs = (p0[0], p1[0], p2[0])
    ys = (p0[1], p1[1], p2[1])
    n = _seg_count(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) * scale)
    for i in range(1, n + 1):
        t = i / n
        mt = 1.0 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        out.append((x, y))


def _arc_angle(ux: float, uy: float, vx: float, vy: float) -> float:
    """Signed angle from vector u to vector v (radians)."""
    n1 = math.hypot(ux, uy)
    n2 = math.hypot(vx, vy)
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    cosv = max(-1.0, min(1.0, (ux * vx + uy * vy) / (n1 * n2)))
    a = math.acos(cosv)
    return -a if (ux * vy - uy * vx) < 0 else a


def _flatten_arc(
    out: list[tuple[float, float]],
    p0: tuple[float, float],
    rx: float,
    ry: float,
    rot_deg: float,
    large_arc: float,
    sweep: float,
    p1: tuple[float, float],
) -> None:
    """Flatten an elliptical arc (endpoint->center parameterization, W3C)."""
    x1, y1 = p0
    x2, y2 = p1
    if x1 == x2 and y1 == y2:
        return  # identical endpoints: arc is omitted
    rx, ry = abs(rx), abs(ry)
    if rx == 0.0 or ry == 0.0:
        out.append((x2, y2))  # zero radius: straight line
        return
    phi = math.radians(rot_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    rx2, ry2 = rx * rx, ry * ry
    x1p2, y1p2 = x1p * x1p, y1p * y1p
    denom = rx2 * y1p2 + ry2 * x1p2
    num = rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2
    coef = math.sqrt(max(0.0, num / denom)) if denom != 0.0 else 0.0
    if (large_arc != 0.0) == (sweep != 0.0):
        coef = -coef
    cxp = coef * (rx * y1p / ry)
    cyp = -coef * (ry * x1p / rx)
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0
    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = _arc_angle(1.0, 0.0, ux, uy)
    dtheta = _arc_angle(ux, uy, vx, vy)
    if sweep == 0.0 and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep != 0.0 and dtheta < 0.0:
        dtheta += 2.0 * math.pi
    n = max(2, min(64, int(abs(dtheta) / (math.pi / 16.0)) + 1))
    for i in range(1, n + 1):
        th = theta1 + dtheta * i / n
        ex, ey = math.cos(th) * rx, math.sin(th) * ry
        out.append((cos_p * ex - sin_p * ey + cx, sin_p * ex + cos_p * ey + cy))


def parse_path_data(d: str, scale: float = 1.0) -> list[Subpath]:
    """Parse an SVG path ``d`` string into flattened :class:`Subpath`s.

    ``scale`` is the user-unit→device-pixel factor. It only affects the
    Bézier subdivision density (so a curve keeps a consistent smoothness
    regardless of its coordinate magnitude); the returned points stay in user
    space. The default ``1.0`` reproduces the old user-space density."""
    if not d:
        return []
    toks = _tokenize(d)
    ntok = len(toks)
    subpaths: list[Subpath] = []
    pts: list[tuple[float, float]] = []
    cx = cy = sx = sy = 0.0
    prev_c: tuple[float, float] | None = None
    prev_q: tuple[float, float] | None = None
    cmd = ""
    pos = 0

    while pos < ntok:
        kind, val = toks[pos]
        if kind == "cmd":
            cmd = str(val)
            pos += 1
            if cmd in ("Z", "z"):
                if pts:
                    subpaths.append(Subpath(pts, True))
                    pts = []
                cx, cy = sx, sy
                prev_c = prev_q = None
                continue
        if not cmd:
            pos += 1
            continue
        up = cmd.upper()
        rel = cmd.islower()
        argc = _ARGC.get(up, 0)
        if pos + argc > ntok:
            break
        args: list[float] = []
        ok = True
        for j in range(argc):
            k2, v2 = toks[pos + j]
            if k2 != "num":
                ok = False
                break
            args.append(float(v2))
        if not ok:
            break
        pos += argc

        if up != "M" and not pts:
            pts.append((cx, cy))  # start a subpath (e.g. drawing right after Z)

        if up == "M":
            x, y = args
            if rel:
                x += cx
                y += cy
            if pts:
                subpaths.append(Subpath(pts, False))
            pts = [(x, y)]
            cx = sx = x
            cy = sy = y
            cmd = "l" if rel else "L"  # subsequent coordinate pairs are implicit L
            prev_c = prev_q = None
        elif up == "L":
            x, y = args
            if rel:
                x += cx
                y += cy
            pts.append((x, y))
            cx, cy = x, y
            prev_c = prev_q = None
        elif up == "H":
            x = args[0] + (cx if rel else 0.0)
            pts.append((x, cy))
            cx = x
            prev_c = prev_q = None
        elif up == "V":
            y = args[0] + (cy if rel else 0.0)
            pts.append((cx, y))
            cy = y
            prev_c = prev_q = None
        elif up == "C":
            x1, y1, x2, y2, x, y = args
            if rel:
                x1, y1, x2, y2, x, y = x1 + cx, y1 + cy, x2 + cx, y2 + cy, x + cx, y + cy
            _flatten_cubic(pts, (cx, cy), (x1, y1), (x2, y2), (x, y), scale)
            cx, cy = x, y
            prev_c, prev_q = (x2, y2), None
        elif up == "S":
            x2, y2, x, y = args
            if rel:
                x2, y2, x, y = x2 + cx, y2 + cy, x + cx, y + cy
            if prev_c is not None:
                x1, y1 = 2 * cx - prev_c[0], 2 * cy - prev_c[1]
            else:
                x1, y1 = cx, cy
            _flatten_cubic(pts, (cx, cy), (x1, y1), (x2, y2), (x, y), scale)
            cx, cy = x, y
            prev_c, prev_q = (x2, y2), None
        elif up == "Q":
            x1, y1, x, y = args
            if rel:
                x1, y1, x, y = x1 + cx, y1 + cy, x + cx, y + cy
            _flatten_quad(pts, (cx, cy), (x1, y1), (x, y), scale)
            cx, cy = x, y
            prev_q, prev_c = (x1, y1), None
        elif up == "T":
            x, y = args
            if rel:
                x += cx
                y += cy
            if prev_q is not None:
                x1, y1 = 2 * cx - prev_q[0], 2 * cy - prev_q[1]
            else:
                x1, y1 = cx, cy
            _flatten_quad(pts, (cx, cy), (x1, y1), (x, y), scale)
            cx, cy = x, y
            prev_q, prev_c = (x1, y1), None
        elif up == "A":
            rx, ry, rot, laf, sf, x, y = args
            if rel:
                x += cx
                y += cy
            _flatten_arc(pts, (cx, cy), rx, ry, rot, laf, sf, (x, y))
            cx, cy = x, y
            prev_c = prev_q = None

    if pts:
        subpaths.append(Subpath(pts, False))
    return subpaths
