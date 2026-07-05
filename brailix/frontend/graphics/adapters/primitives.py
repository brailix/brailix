"""Geometry-primitives source adapter: a shape spec → SVG.

This is the non-visual **creation** entry point for tactile graphics
(``ARCHITECTURE.md`` path 2): an author fills in a
structured spec — "a circle at (50, 50) radius 30, a label 'A' at
(20, 20)" — and gets back SVG, with no mouse or canvas involved. The spec
is a plain ``dict`` (or its JSON serialization), which makes it the
natural backing format for a form-driven generator.

Spec shape::

    {
      "width": 100, "height": 100,      # canvas in user units (= mm by default)
      "shapes": [
        {"type": "line",     "x1": 0, "y1": 0, "x2": 100, "y2": 100},
        {"type": "rect",     "x": 10, "y": 10, "width": 80, "height": 80},
        {"type": "circle",   "cx": 50, "cy": 50, "r": 30},
        {"type": "ellipse",  "cx": 50, "cy": 50, "rx": 40, "ry": 20},
        {"type": "polyline", "points": [[10, 90], [50, 10], [90, 90]]},
        {"type": "polygon",  "points": [[10, 10], [90, 10], [50, 90]]},
        {"type": "label",    "x": 20, "y": 20, "text": "A"}
      ]
    }

Any shape may carry ``"stroke_width"`` (user units) to request a thicker
line; the tactile backend floors every stroke to the profile's minimum
touchable width regardless. A closed shape (rect / circle / ellipse /
polygon) may carry ``"fill"`` — a texture name (``"hatch"`` / ``"dots"`` /
``"cross"`` / ...) or any colour — to be filled with a touch-distinguishable
texture rather than left as an outline; distinct fills get distinct
textures. A ``label`` becomes an SVG ``<text>`` — carried
through faithfully and, when a label translator is supplied, rendered to
braille dots by the tactile backend. Unknown shape types are skipped with a
warning.

The SVG is assembled with :mod:`xml.etree.ElementTree` so attribute
escaping and well-formedness come for free.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from brailix.core.context import GraphicsContext
from brailix.core.errors import WarningCollector
from brailix.frontend.graphics.adapters.svg import svg_error_wrap


def _fmt(value: Any) -> str:
    """Compact numeric formatting: drop the decimal point for integers."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else repr(f)


def _points_attr(points: Any) -> str:
    if not isinstance(points, Sequence):
        return ""
    out: list[str] = []
    for p in points:
        if isinstance(p, Sequence) and not isinstance(p, str) and len(p) >= 2:
            out.append(f"{_fmt(p[0])},{_fmt(p[1])}")
    return " ".join(out)


def _style(el: ET.Element, shape: dict[str, Any]) -> None:
    """Carry the optional ``stroke_width`` (thicker line) and ``fill``
    (texture name / alias / colour → tactile texture) onto the element."""
    if "stroke_width" in shape:
        el.set("stroke-width", _fmt(shape["stroke_width"]))
    if "fill" in shape and shape["fill"] is not None:
        el.set("fill", str(shape["fill"]))


def _b_line(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "line")
    for k in ("x1", "y1", "x2", "y2"):
        el.set(k, _fmt(s.get(k, 0)))
    _style(el, s)


def _b_rect(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "rect")
    for k in ("x", "y", "width", "height"):
        el.set(k, _fmt(s.get(k, 0)))
    _style(el, s)


def _b_circle(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "circle")
    for k in ("cx", "cy", "r"):
        el.set(k, _fmt(s.get(k, 0)))
    _style(el, s)


def _b_ellipse(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "ellipse")
    for k in ("cx", "cy", "rx", "ry"):
        el.set(k, _fmt(s.get(k, 0)))
    _style(el, s)


def _b_polyline(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "polyline")
    el.set("points", _points_attr(s.get("points")))
    _style(el, s)


def _b_polygon(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "polygon")
    el.set("points", _points_attr(s.get("points")))
    _style(el, s)


def _b_label(svg: ET.Element, s: dict[str, Any]) -> None:
    el = ET.SubElement(svg, "text")
    el.set("x", _fmt(s.get("x", 0)))
    el.set("y", _fmt(s.get("y", 0)))
    el.text = str(s.get("text", ""))


_SHAPE_BUILDERS: dict[str, Any] = {
    "line": _b_line,
    "rect": _b_rect,
    "circle": _b_circle,
    "ellipse": _b_ellipse,
    "polyline": _b_polyline,
    "polygon": _b_polygon,
    "label": _b_label,
}


def _warn(warnings: WarningCollector | None, message: str) -> None:
    if warnings is not None:
        warnings.warn(
            code="GRAPHICS_UNKNOWN_SHAPE",
            message=message,
            source="frontend.graphics",
        )


def primitives_to_svg(
    spec: Any, warnings: WarningCollector | None = None
) -> str:
    """Build an SVG string from a primitives spec ``dict``.

    The pure builder behind :class:`PrimitivesSourceAdapter` — Python
    callers (and a form-driven generator) can use it directly without
    serializing to JSON first. A non-dict spec soft-fails into an empty
    ``<svg data-bk-error="...">``.
    """
    if not isinstance(spec, dict):
        return svg_error_wrap(
            type(spec).__name__, reason="primitives spec must be an object"
        )
    svg = ET.Element("svg")
    try:
        w, h = float(spec.get("width")), float(spec.get("height"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        w = h = 0.0
    if w > 0 and h > 0:
        svg.set("viewBox", f"0 0 {_fmt(w)} {_fmt(h)}")
        svg.set("width", f"{_fmt(w)}mm")
        svg.set("height", f"{_fmt(h)}mm")
    shapes = spec.get("shapes")
    if isinstance(shapes, list):
        for shape in shapes:
            if not isinstance(shape, dict):
                _warn(warnings, f"primitive shape must be an object, got {shape!r}")
                continue
            stype = shape.get("type")
            builder = _SHAPE_BUILDERS.get(stype) if isinstance(stype, str) else None
            if builder is None:
                _warn(warnings, f"unknown primitive shape type {stype!r}; skipped")
                continue
            builder(svg, shape)
    return ET.tostring(svg, encoding="unicode")


@dataclass(slots=True)
class PrimitivesSourceAdapter:
    """Adapter: a JSON primitives spec → SVG."""

    source: str = "primitives"

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return svg_error_wrap(repr(src), reason="non-utf8 bytes")
        text = src.strip() if isinstance(src, str) else ""
        if not text:
            return svg_error_wrap("", reason="empty primitives spec")
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            return svg_error_wrap(text, reason=f"invalid JSON: {e}")
        warnings = ctx.warnings if ctx is not None else None
        return primitives_to_svg(spec, warnings)


def _load() -> PrimitivesSourceAdapter:
    return PrimitivesSourceAdapter()
