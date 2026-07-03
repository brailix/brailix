"""Touch-separability diagnostics for the tactile backend (BANA spacing).

Two raised features a finger cannot tell apart defeat a tactile graphic, so
BANA's *Tactile Graphics Guidelines* require a minimum gap between distinct
features and warn against braille labels printed over the drawing
(``ARCHITECTURE.md``). This module is the geometry behind
those checks.

The backend **detects and warns; it never moves the author's geometry.** An
author — not an algorithm — must redesign a too-cramped figure: silently
nudging coordinates would distort a chart a blind author cannot re-inspect,
and which features are "too close by mistake" versus "intentionally connected"
is the author's call. So these functions only *report* proximity; the caller
turns a report into a warning.

All inputs are device-pixel axis-aligned bounding boxes ``(lo_x, lo_y, hi_x,
hi_y)``.
"""

from __future__ import annotations

BBox = tuple[int, int, int, int]  # (lo_x, lo_y, hi_x, hi_y)


def bbox_gap(a: BBox, b: BBox) -> float:
    """Minimum distance between two axis-aligned boxes (``0.0`` when they touch
    or overlap on both axes)."""
    dx = max(0, a[0] - b[2], b[0] - a[2])
    dy = max(0, a[1] - b[3], b[1] - a[3])
    return (dx * dx + dy * dy) ** 0.5


def find_too_close(
    boxes: list[tuple[str, BBox]], min_gap: float, *, allow_touch: bool
) -> list[tuple[str, str, float]]:
    """Pairs of ``boxes`` separated by less than ``min_gap`` device pixels.

    ``allow_touch`` decides the zero-gap case:

    * distinct **elements** (``allow_touch=True``) — a zero gap means they
      touch / overlap, usually an intentional connection (an axis meeting a
      tick, a bar inside a frame), so only a small *positive* gap
      (close-but-separate) is flagged. This keeps the check low-noise.
    * **labels** (``allow_touch=False``) — any collision is bad (two braille
      cells merging is unreadable), so overlaps are flagged too.

    Returns ``(name_a, name_b, gap)`` triples for each violating pair. O(n²) —
    the caller bounds ``n`` (the boxes come from a single drawing's elements).
    """
    out: list[tuple[str, str, float]] = []
    n = len(boxes)
    for i in range(n):
        name_i, box_i = boxes[i]
        for j in range(i + 1, n):
            gap = bbox_gap(box_i, boxes[j][1])
            if gap < min_gap and (gap > 0.0 or not allow_touch):
                out.append((name_i, boxes[j][0], gap))
    return out


__all__ = ("BBox", "bbox_gap", "find_too_close")
