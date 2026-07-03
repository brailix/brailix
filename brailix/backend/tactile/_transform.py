"""Affine transforms for the tactile backend's SVG ``transform`` support.

A 2x3 affine matrix (SVG's ``transform`` model) maps a point ``(x, y)`` to
``(a*x + c*y + e, b*x + d*y + f)``. This module parses the SVG
``transform`` attribute's function list — ``matrix`` / ``translate`` /
``scale`` / ``rotate`` / ``skewX`` / ``skewY`` — into a single
:class:`Affine` and composes nested transforms, so the rasterizer can
place geometry an authoring tool emitted under a coordinate transform.

Per the SVG spec a list ``"translate(...) rotate(...)"`` applies
right-to-left (the rightmost function is closest to the geometry): the
composed matrix is ``translate · rotate``. A child element's transform
combines with its ancestors as ``parent_ctm · child_transform``
(post-multiplication). Only geometry is transformed — presentational
effects are out of scope for tactile output.
"""

from __future__ import annotations

import math
import re
from typing import NamedTuple


class Affine(NamedTuple):
    """A 2x3 affine transform — SVG's ``matrix(a b c d e f)`` model.

    Maps ``(x, y)`` to ``(a*x + c*y + e, b*x + d*y + f)``.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        """Map a point through this transform."""
        return (
            self.a * x + self.c * y + self.e,
            self.b * x + self.d * y + self.f,
        )

    def then(self, inner: Affine) -> Affine:
        """Compose so ``inner`` is applied first, ``self`` second:
        ``self.then(inner).apply(p) == self.apply(inner.apply(p))`` (the
        matrix product ``self · inner``)."""
        a1, b1, c1, d1, e1, f1 = self
        a2, b2, c2, d2, e2, f2 = inner
        return Affine(
            a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1,
        )

    def scale_factor(self) -> float:
        """Uniform-equivalent scale ``sqrt(|det|)`` — exact for
        translation / rotation / uniform scaling, an area-preserving
        approximation under non-uniform scaling or skew (used to scale
        stroke widths and radii, which lack a single correct value once
        the transform is anisotropic)."""
        return math.sqrt(abs(self.a * self.d - self.b * self.c))

    def inverse(self) -> Affine | None:
        """The inverse transform, or ``None`` when this matrix is singular
        (zero area — e.g. ``scale(0)`` or a degenerate skew).

        ``m.inverse().apply(m.apply(p)) == p`` for every point when it
        exists. Used to map a device pixel back into pre-transform space so
        a placed raster ``<image>`` can be sampled per device pixel under
        any affine (rotation / skew included), the analogue of inverse
        texture mapping."""
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            return None
        return Affine(
            self.d / det,
            -self.b / det,
            -self.c / det,
            self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )


IDENTITY = Affine()

# One transform function call: a name + a parenthesised number list.
_FUNC_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _nums(arg_str: str) -> list[float]:
    out: list[float] = []
    for tok in _NUM_RE.findall(arg_str):
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _function_matrix(name: str, args: list[float]) -> Affine | None:
    """Build the :class:`Affine` for one transform function, or ``None``
    if the name is unknown / the arguments are insufficient (soft-fail)."""
    n = len(args)
    if name == "matrix":
        return Affine(*args[:6]) if n >= 6 else None
    if name == "translate":
        if n == 0:
            return None
        return Affine(1.0, 0.0, 0.0, 1.0, args[0], args[1] if n > 1 else 0.0)
    if name == "scale":
        if n == 0:
            return None
        sx = args[0]
        sy = args[1] if n > 1 else sx
        return Affine(sx, 0.0, 0.0, sy, 0.0, 0.0)
    if name == "rotate":
        if n == 0:
            return None
        th = math.radians(args[0])
        cos, sin = math.cos(th), math.sin(th)
        rot = Affine(cos, sin, -sin, cos, 0.0, 0.0)
        if n >= 3:
            cx, cy = args[1], args[2]
            return (
                Affine(1.0, 0.0, 0.0, 1.0, cx, cy)
                .then(rot)
                .then(Affine(1.0, 0.0, 0.0, 1.0, -cx, -cy))
            )
        return rot
    if name == "skewX":
        if n == 0:
            return None
        return Affine(1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
    if name == "skewY":
        if n == 0:
            return None
        return Affine(1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
    return None


def parse_transform(value: str | None) -> Affine:
    """Parse an SVG ``transform`` attribute into a single :class:`Affine`.

    Composes the function list left-to-right so the result reproduces the
    SVG right-to-left application order. Unknown functions and malformed
    numbers are skipped (soft-fail), returning the transform parsed so far
    (``IDENTITY`` if nothing parsed)."""
    if not value:
        return IDENTITY
    result = IDENTITY
    for name, arg_str in _FUNC_RE.findall(value):
        m = _function_matrix(name, _nums(arg_str))
        if m is not None:
            result = result.then(m)
    return result
