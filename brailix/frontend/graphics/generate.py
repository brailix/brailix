"""Parametric figure generators: a data/figure spec → a primitives spec.

The non-visual **creation** high point (``ARCHITECTURE.md`` path 2 / T4): an author describes a figure by data — "a bar chart of
these values", "a number line 0..10 with 3 and 7 marked" — and gets a
drawing, no canvas or mouse involved. Each generator expands a high-level
spec into a **primitives spec** (the same ``{width, height, shapes}`` dict
:mod:`brailix.frontend.graphics.adapters.primitives` already renders), so
generators reuse the whole tactile pipeline: the figure's axis / data
labels become braille labels (T2) and its filled regions become touch
textures (T2) for free, and the intermediate primitives spec stays
inspectable / editable.

Generators are looked up by ``kind`` through a small registry (no
``if/else`` dispatch — see ``ARCHITECTURE.md`` §7); adding a figure type
is one function plus one ``register_generator`` call. The
:class:`~brailix.frontend.graphics.adapters.figure.FigureSourceAdapter`
drives the lookup and chains the result through ``primitives_to_svg``.

Coordinates are in millimetres (the tactile default: 1 user unit = 1 mm).
Charts map data values into a plot area inset by a margin, flipping the y
axis so larger values sit higher on the page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# A generator turns a figure spec into a primitives spec.
FigureGenerator = Callable[[dict[str, Any]], dict[str, Any]]

_GENERATORS: dict[str, FigureGenerator] = {}

# Default canvas + margin (mm). The margin leaves room for axis / data
# labels, which render at physical braille size.
_W, _H, _MARGIN = 120.0, 90.0, 18.0


def register_generator(kind: str, fn: FigureGenerator) -> None:
    """Register (or replace) a figure generator under ``kind``."""
    _GENERATORS[kind] = fn


def get_generator(kind: str) -> FigureGenerator | None:
    return _GENERATORS.get(kind)


def generator_kinds() -> tuple[str, ...]:
    """Registered figure kinds — what a generate menu can offer."""
    return tuple(sorted(_GENERATORS))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _canvas(spec: dict[str, Any]) -> tuple[float, float, float]:
    w = _num(spec.get("width"), _W) or _W
    h = _num(spec.get("height"), _H) or _H
    m = _num(spec.get("margin"), _MARGIN) or _MARGIN
    return w, h, m


def _line(x1: float, y1: float, x2: float, y2: float) -> dict[str, Any]:
    return {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _rect(x: float, y: float, w: float, h: float, **kw: Any) -> dict[str, Any]:
    return {"type": "rect", "x": x, "y": y, "width": w, "height": h, **kw}


def _circle(cx: float, cy: float, r: float, **kw: Any) -> dict[str, Any]:
    return {"type": "circle", "cx": cx, "cy": cy, "r": r, **kw}


def _polyline(points: list[list[float]]) -> dict[str, Any]:
    return {"type": "polyline", "points": points}


def _label(x: float, y: float, text: Any) -> dict[str, Any]:
    return {"type": "label", "x": x, "y": y, "text": str(text)}


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    """Tick values from ``lo`` to ``hi`` inclusive, spaced by ``step``."""
    if step <= 0 or hi <= lo:
        return []
    n = int(round((hi - lo) / step))
    return [lo + i * step for i in range(n + 1)]


def _title(shapes: list[dict[str, Any]], spec: dict[str, Any], m: float) -> None:
    title = spec.get("title")
    if title:
        shapes.insert(0, _label(m, max(0.0, m - 8.0), title))


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _gen_bar(spec: dict[str, Any]) -> dict[str, Any]:
    """Bar chart from ``data: [{"label", "value"}, ...]`` (values ≥ 0)."""
    w, h, m = _canvas(spec)
    items = [
        (str(d.get("label", "")), _num(d.get("value")))
        for d in (spec.get("data") or [])
        if isinstance(d, dict)
    ]
    shapes: list[dict[str, Any]] = [
        _line(m, h - m, w - m, h - m),  # x axis
        _line(m, m, m, h - m),  # y axis
    ]
    if items:
        vmax = max((v for _, v in items), default=0.0)
        vmax = vmax if vmax > 0 else 1.0
        plot_w, plot_h = w - 2 * m, h - 2 * m
        slot = plot_w / len(items)
        bw = slot * 0.6
        for i, (label, value) in enumerate(items):
            cx = m + slot * (i + 0.5)
            bh = max(0.0, value) / vmax * plot_h
            shapes.append(_rect(cx - bw / 2, (h - m) - bh, bw, bh))
            shapes.append(_label(cx - bw / 2, h - m + 5, label))
    _title(shapes, spec, m)
    return {"width": w, "height": h, "shapes": shapes}


def _gen_line(spec: dict[str, Any]) -> dict[str, Any]:
    """Line chart from ``points: [[x, y], ...]`` or evenly-spaced
    ``values: [y, ...]``."""
    w, h, m = _canvas(spec)
    raw = spec.get("points")
    if not raw:
        raw = [[i, v] for i, v in enumerate(spec.get("values") or [])]
    pts = [
        (_num(p[0]), _num(p[1]))
        for p in raw
        if isinstance(p, (list, tuple)) and len(p) >= 2
    ]
    shapes: list[dict[str, Any]] = [
        _line(m, h - m, w - m, h - m),
        _line(m, m, m, h - m),
    ]
    if pts:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = xmax - xmin or 1.0
        yspan = ymax - ymin or 1.0
        mapped = [
            [
                m + (x - xmin) / xspan * (w - 2 * m),
                (h - m) - (y - ymin) / yspan * (h - 2 * m),
            ]
            for x, y in pts
        ]
        shapes.append(_polyline(mapped))
        shapes.extend(_circle(px, py, 1.5) for px, py in mapped)
    _title(shapes, spec, m)
    return {"width": w, "height": h, "shapes": shapes}


def _gen_number_line(spec: dict[str, Any]) -> dict[str, Any]:
    """Number line ``min``..``max`` by ``step``, with optional marked
    ``points``."""
    w, h, m = _canvas(spec)
    lo = _num(spec.get("min"), 0.0)
    hi = _num(spec.get("max"), 10.0)
    if hi <= lo:
        hi = lo + 1.0
    step = _num(spec.get("step"), 1.0) or 1.0
    y = h / 2.0
    span = hi - lo

    def x_of(v: float) -> float:
        return m + (v - lo) / span * (w - 2 * m)

    a = 3.0
    shapes: list[dict[str, Any]] = [
        _line(m, y, w - m, y),
        _line(m, y, m + a, y - a),  # left arrowhead
        _line(m, y, m + a, y + a),
        _line(w - m, y, w - m - a, y - a),  # right arrowhead
        _line(w - m, y, w - m - a, y + a),
    ]
    for v in _ticks(lo, hi, step):
        x = x_of(v)
        shapes.append(_line(x, y - 2, x, y + 2))
        shapes.append(_label(x, y + 5, _fmt(v)))
    for p in spec.get("points") or []:
        shapes.append(_circle(x_of(_num(p)), y, 2.0, fill="dots"))
    _title(shapes, spec, m)
    return {"width": w, "height": h, "shapes": shapes}


def _gen_axes(spec: dict[str, Any]) -> dict[str, Any]:
    """Coordinate axes / grid over ``xmin..xmax`` × ``ymin..ymax``."""
    w, h, m = _canvas(spec)
    xmin, xmax = _num(spec.get("xmin"), -5.0), _num(spec.get("xmax"), 5.0)
    ymin, ymax = _num(spec.get("ymin"), -5.0), _num(spec.get("ymax"), 5.0)
    if xmax <= xmin:
        xmax = xmin + 1.0
    if ymax <= ymin:
        ymax = ymin + 1.0
    xstep = _num(spec.get("xstep"), 1.0) or 1.0
    ystep = _num(spec.get("ystep"), 1.0) or 1.0

    def x_of(x: float) -> float:
        return m + (x - xmin) / (xmax - xmin) * (w - 2 * m)

    def y_of(y: float) -> float:
        return (h - m) - (y - ymin) / (ymax - ymin) * (h - 2 * m)

    shapes: list[dict[str, Any]] = []
    if spec.get("grid"):
        for xv in _ticks(xmin, xmax, xstep):
            shapes.append(_line(x_of(xv), m, x_of(xv), h - m))
        for yv in _ticks(ymin, ymax, ystep):
            shapes.append(_line(m, y_of(yv), w - m, y_of(yv)))
    # Axes through the origin when it's in range, else along the low edge.
    axis_y = y_of(0.0) if ymin <= 0 <= ymax else (h - m)
    axis_x = x_of(0.0) if xmin <= 0 <= xmax else m
    shapes.append(_line(m, axis_y, w - m, axis_y))  # x axis
    shapes.append(_line(axis_x, m, axis_x, h - m))  # y axis
    for xv in _ticks(xmin, xmax, xstep):
        if xv == 0:
            continue
        shapes.append(_line(x_of(xv), axis_y - 2, x_of(xv), axis_y + 2))
        shapes.append(_label(x_of(xv), axis_y + 5, _fmt(xv)))
    for yv in _ticks(ymin, ymax, ystep):
        if yv == 0:
            continue
        shapes.append(_line(axis_x - 2, y_of(yv), axis_x + 2, y_of(yv)))
        shapes.append(_label(axis_x + 3, y_of(yv), _fmt(yv)))
    _title(shapes, spec, m)
    return {"width": w, "height": h, "shapes": shapes}


def _gen_table(spec: dict[str, Any]) -> dict[str, Any]:
    """Table grid from ``rows: [[cell, ...], ...]`` with cell labels."""
    w, h, m = _canvas(spec)
    rows = [r for r in (spec.get("rows") or []) if isinstance(r, (list, tuple))]
    nrows = len(rows)
    ncols = max((len(r) for r in rows), default=0)
    if nrows == 0 or ncols == 0:
        return {"width": w, "height": h, "shapes": []}
    x0, y0, x1, y1 = m, m, w - m, h - m
    cw, ch = (x1 - x0) / ncols, (y1 - y0) / nrows
    shapes: list[dict[str, Any]] = []
    for c in range(ncols + 1):
        shapes.append(_line(x0 + c * cw, y0, x0 + c * cw, y1))
    for r in range(nrows + 1):
        shapes.append(_line(x0, y0 + r * ch, x1, y0 + r * ch))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            shapes.append(_label(x0 + c * cw + 1.5, y0 + r * ch + ch * 0.4, cell))
    _title(shapes, spec, m)
    return {"width": w, "height": h, "shapes": shapes}


register_generator("bar", _gen_bar)
register_generator("line", _gen_line)
register_generator("number_line", _gen_number_line)
register_generator("axes", _gen_axes)
register_generator("table", _gen_table)
