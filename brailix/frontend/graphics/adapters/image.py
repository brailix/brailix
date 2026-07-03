"""Raster-image source adapter: an image path / spec → SVG.

The **import / curate** entry point for tactile graphics
(``ARCHITECTURE.md`` path 1): point at an existing
picture (PNG / JPEG / BMP / ...) and get a touchable version, instead of
drawing from scratch. A bitmap has no vector structure to trace cleanly,
so rather than guess contours this adapter wraps it as a single SVG
``<image>`` — which keeps it a positionable, scalable, labelable object in
the SVG-as-IR tree — and the tactile backend turns its pixels into raise
levels (see :mod:`brailix.backend.tactile._image`). The image is *raster*,
not editable as vectors; everything around it (other primitives, braille
labels, the page) stays first-class SVG.

The source is either a bare path string or a JSON spec::

    {
      "path": "C:/photos/map.png",   # required (alias: "href")
      "mode": "edge",                # threshold (default) / grayscale / edge
      "threshold": 110,              # 0..255 cut for the bilevel modes
      "invert": false,               # swap which side raises
      "width_mm": 180,               # physical size; aspect filled from the
      "height_mm": 120               # other / the image if omitted
    }

Reading the image's pixel dimensions (to set the SVG ``viewBox`` and a
sensible physical size) needs Pillow — the ``graphics`` extra — so the
adapter registers with ``extra="graphics"`` and a missing install surfaces
as a friendly :class:`~brailix.core.errors.MissingExtraError`. A bad path
or spec soft-fails into an empty ``<svg data-bk-error="...">``, mirroring
the other source adapters.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brailix.core.context import GraphicsContext
from brailix.frontend.graphics.adapters.svg import svg_error_wrap

# The opening ``<svg …>`` tag, for reading an external SVG's intrinsic size
# from its viewBox / width / height without a full parse (avoids DOCTYPE /
# prolog pitfalls) and without Pillow (which can't read SVG).
_SVG_OPEN_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)

# Physical size (mm) for the image's longest side when the caller gives no
# explicit ``width_mm`` / ``height_mm`` — a sub-A4 default that stays
# device-independent (the backend turns mm into pixels via the profile DPI,
# the single device dial). Not "no default"; just no device binding.
_DEFAULT_LONGEST_MM = 160.0


def _fmt(value: float) -> str:
    """Compact numeric formatting: drop the decimal point for integers."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _as_pos_float(value: Any) -> float | None:
    """``value`` as a positive float, or ``None`` for missing / non-positive
    / unparseable — so a malformed size field falls back to the default."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _clamp_threshold(value: Any) -> int:
    """``value`` as an int clamped to ``0..255`` (the bilevel-mode cut),
    defaulting to 128 for a missing / unparseable field."""
    try:
        t = round(float(value))
    except (TypeError, ValueError):
        return 128
    return max(0, min(255, t))


def _length_px(value: str | None) -> int:
    """An SVG ``width`` / ``height`` length as an integer pixel count, or 0 —
    strips a trailing unit (``px`` / ``mm`` / ...); a percentage is unusable
    (relative) and returns 0 so the caller falls back to the viewBox."""
    if not value or "%" in value:
        return 0
    s = value.strip()
    i = len(s)
    while i > 0 and not (s[i - 1].isdigit() or s[i - 1] == "."):
        i -= 1
    try:
        return round(float(s[:i])) if i else 0
    except ValueError:
        return 0


def _svg_dimensions(path: str) -> tuple[int, int]:
    """Intrinsic ``(width, height)`` of the SVG file at ``path`` from its
    viewBox (preferred) or width/height, or ``(0, 0)`` if unreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (0, 0)
    m = _SVG_OPEN_TAG.search(text)
    if not m:
        return (0, 0)
    tag = m.group(0)
    vb = _svg_attr(tag, "viewBox")
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                w, h = float(parts[2]), float(parts[3])
                if w > 0 and h > 0:
                    return (round(w), round(h))
            except ValueError:
                pass
    return (_length_px(_svg_attr(tag, "width")), _length_px(_svg_attr(tag, "height")))


def _svg_attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
    return m.group(1) if m else None


def _looks_like_svg(path: str) -> bool:
    """Whether ``path`` is an SVG (so its size comes from XML, not Pillow):
    sniff the file head for ``<svg``, falling back to the ``.svg`` suffix."""
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return path.lower().endswith(".svg")
    return b"<svg" in head.lower()


def _read_dimensions(path: str) -> tuple[int, int] | None:
    """``(px_w, px_h)`` for an image / SVG ``path``, or ``None`` if unreadable.

    An SVG's size is read from its viewBox / width-height (stdlib, no Pillow —
    Pillow can't read SVG); a raster's from Pillow. Either way the result only
    sets the ``<image>`` box aspect; the actual render resolution comes from
    the device box at rasterize time."""
    if _looks_like_svg(path):
        w, h = _svg_dimensions(path)
        return (w, h) if w > 0 and h > 0 else None
    from PIL import Image  # surfaced via the registry's extra= when missing

    try:
        with Image.open(path) as im:
            if im.width > 0 and im.height > 0:
                return (im.width, im.height)
    except Exception:  # noqa: BLE001 — Pillow raises many open / decode types
        return None
    return None


def _physical_size(
    px_w: int, px_h: int, width_mm: Any, height_mm: Any
) -> tuple[float, float]:
    """Resolve the image's physical (mm) size, preserving the pixel aspect
    ratio when only one dimension (or neither) is given."""
    wmm = _as_pos_float(width_mm)
    hmm = _as_pos_float(height_mm)
    if wmm and hmm:
        return wmm, hmm
    aspect = px_w / px_h
    if wmm:
        return wmm, wmm / aspect
    if hmm:
        return hmm * aspect, hmm
    if px_w >= px_h:
        return _DEFAULT_LONGEST_MM, _DEFAULT_LONGEST_MM / aspect
    return _DEFAULT_LONGEST_MM * aspect, _DEFAULT_LONGEST_MM


def image_to_svg(src: str) -> str:
    """Build an SVG ``<image>`` wrapper from an image path / JSON spec.

    The pure builder behind :class:`ImageSourceAdapter`; a Python caller
    (or a form-driven import dialog) can use it directly. Accepts a raster
    image *or* an external ``.svg`` (rendered full-fidelity via resvg at
    rasterize time — the faithful, non-editable path, vs the ``svg`` source's
    editable tag-walk). Soft-fails into an empty ``<svg data-bk-error="...">``
    for an empty / malformed source or an unreadable image. A raster needs
    Pillow (the ``graphics`` extra) for its pixel size; an SVG's size is read
    from its viewBox (stdlib).
    """
    text = src.strip()
    if not text:
        return svg_error_wrap("", reason="empty image source")

    mode: Any = "threshold"
    threshold: Any = 128
    invert = False
    width_mm: Any = None
    height_mm: Any = None
    if text.startswith("{"):
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            return svg_error_wrap(text, reason=f"invalid JSON: {e}")
        if not isinstance(spec, dict):
            return svg_error_wrap(
                type(spec).__name__, reason="image spec must be an object"
            )
        path = spec.get("path") or spec.get("href")
        if not isinstance(path, str) or not path.strip():
            return svg_error_wrap(text, reason="image spec missing 'path'")
        path = path.strip()
        mode = spec.get("mode", "threshold")
        threshold = spec.get("threshold", 128)
        invert = bool(spec.get("invert", False))
        width_mm = spec.get("width_mm")
        height_mm = spec.get("height_mm")
    else:
        path = text

    dims = _read_dimensions(path)
    if dims is None:
        return svg_error_wrap(path, reason="cannot read image / SVG size")
    px_w, px_h = dims

    pw_mm, ph_mm = _physical_size(px_w, px_h, width_mm, height_mm)

    svg = ET.Element("svg")
    # viewBox in source pixels (1 user unit = 1 source pixel); physical size
    # in mm so the backend rasterizes at the right touch scale.
    svg.set("viewBox", f"0 0 {px_w} {px_h}")
    svg.set("width", f"{_fmt(pw_mm)}mm")
    svg.set("height", f"{_fmt(ph_mm)}mm")
    img = ET.SubElement(svg, "image")
    img.set("href", path)
    img.set("x", "0")
    img.set("y", "0")
    img.set("width", str(px_w))
    img.set("height", str(px_h))
    # ``data-bk-mode`` is a backend-interpreted hint forwarded as-is; the
    # tactile backend owns the mode vocabulary and falls back to "threshold"
    # for an unrecognized value, so the frontend needn't know the valid set.
    img.set("data-bk-mode", str(mode))
    img.set("data-bk-threshold", str(_clamp_threshold(threshold)))
    if invert:
        img.set("data-bk-invert", "1")
    return ET.tostring(svg, encoding="unicode")


@dataclass(slots=True)
class ImageSourceAdapter:
    """Adapter: a raster image path / JSON spec → an SVG ``<image>``."""

    source: str = "image"

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return svg_error_wrap(
                    repr(src),
                    reason="image source must be a path / spec string, "
                    "not raw image bytes",
                )
        return image_to_svg(src if isinstance(src, str) else "")


def _load() -> ImageSourceAdapter:
    """Factory. Probes the Pillow import so a missing install surfaces as a
    :class:`~brailix.core.errors.MissingExtraError` at ``registry.get`` time
    (the registry wraps a loader ``ImportError`` when ``extra=`` is set),
    rather than only failing later inside :meth:`ImageSourceAdapter.to_svg`."""
    import PIL  # noqa: F401 — load-time probe for the friendly missing-extra error

    return ImageSourceAdapter()
