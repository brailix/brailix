"""Shared raster-size safety cap for the tactile backend.

A page's pixel dimensions are ``physical_mm × dpi``; ``dpi`` is a user knob
(matched to the embosser), so a pathological DPI × page size could allocate an
enormous buffer. Both the single-graphic rasterizer (:mod:`__init__`) and the
mixed-page compositor (:mod:`page`) allocate that way, so the cap lives here —
a leaf module importing nothing from the package (no import cycle).
"""

from __future__ import annotations

from brailix.core.errors import WarningCollector

# Safety cap so a pathological DPI × page size can't allocate an enormous
# buffer. ~30 MP comfortably covers A4 at ~590 DPI; past it the raster is
# scaled down to fit and a warning is emitted (no silent truncation).
_MAX_RASTER_PIXELS = 30_000_000


def clamp_raster_pixels(
    px_w: int,
    px_h: int,
    warnings: WarningCollector | None = None,
    *,
    max_pixels: int = _MAX_RASTER_PIXELS,
    source: str = "backend.tactile",
) -> tuple[int, int, bool]:
    """Scale ``(px_w, px_h)`` down to at most ``max_pixels`` total, preserving
    aspect ratio.

    Returns ``(width, height, clamped)``. When the input is within the cap it
    is returned unchanged with ``clamped=False``; otherwise it is scaled to fit
    and, if ``warnings`` is given, a ``GRAPHICS_RASTER_CLAMPED`` warning is
    emitted (no silent truncation)."""
    if px_w * px_h <= max_pixels:
        return px_w, px_h, False
    scale = (max_pixels / (px_w * px_h)) ** 0.5
    new_w, new_h = max(1, int(px_w * scale)), max(1, int(px_h * scale))
    if warnings is not None:
        warnings.warn(
            code="GRAPHICS_RASTER_CLAMPED",
            message=f"raster {px_w}x{px_h} exceeds the {max_pixels} "
            f"pixel cap; scaled to {new_w}x{new_h}",
            source=source,
        )
    return new_w, new_h, True
