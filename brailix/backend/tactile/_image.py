"""Raster-image decoding for the tactile backend's ``<image>`` support.

A bitmap (PNG / JPEG / BMP / ...) carries no vector structure, so — unlike
the geometric primitives — it cannot be traced into clean SVG shapes
without guessing. Instead the graphics frontend wraps it as an SVG
``<image>`` element (it stays a positionable, scalable, labelable object in
the SVG-as-IR tree), and this module turns the referenced pixels into
**raise levels** at the on-page device resolution: decode → grayscale →
optional resize (anti-aliased) → a touch-adaptation *mode* → a flat
``0..255`` level buffer the rasterizer samples per device pixel.

Three modes, chosen with the ``data-bk-mode`` attribute:

* ``threshold`` (default) — pixels darker than ``data-bk-threshold`` become
  fully raised, lighter ones flat. Best for line art, diagrams, text, and
  anything already high-contrast; matches the swell-paper mental model
  (dark ink swells).
* ``grayscale`` — the full ``0..255`` height range, dark ink mapped to a
  high raise. The information-richest master for a height-modulating
  embosser (ViewPlus Tiger), per ``ARCHITECTURE.md``
* ``edge`` — edge detection, so a photo's outlines become raised lines
  rather than an unreadable filled mass. Best for photographs.

A complex **external SVG** (gradients / filters / clipPath / ``<use>`` / CSS
— anything the stdlib tag-walk rasterizer can't render faithfully) is also
ingested here: when the source bytes sniff as SVG, they are handed whole to
**resvg** (the ``graphics-svg-raster`` extra) which renders them to a PNG,
and that PNG flows through the very same grayscale → mode pipeline as a
bitmap. So "any external visual" — raster or vector — becomes a tactile
raster through one path (``ARCHITECTURE.md``). The result
is a raster approximation, not a touch-adapted vector (the ``svg`` source's
tag-walk is the editable, touch-adapted path; this is the faithful-render
path), so external text renders as visual ink, not braille.

Pillow is the base third-party requirement (the ``graphics`` extra); resvg
is only needed for the SVG path. Both are imported lazily inside
:func:`load_tactile_image` so a bare install — and the tactile backend's
import — never needs them. A missing Pillow raises the plain
:class:`ImportError`; a missing resvg raises :class:`SvgRasterizerMissing`;
a bad / unreadable source raises :class:`TactileImageError`. The backend
maps each to its own skip warning.
"""

from __future__ import annotations

import base64
import io
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

# Modes the rasterizer accepts on a ``data-bk-mode`` attribute; an
# unrecognized value falls back to ``threshold``.
VALID_MODES: tuple[str, ...] = ("threshold", "grayscale", "edge")


class TactileImageError(Exception):
    """A raster ``<image>`` source could not be read or decoded.

    Distinct from :class:`ImportError` (Pillow missing) so the backend can
    tell "you lack the image extra" apart from "this particular file is
    broken" and warn with the right code."""


class SvgRasterizerMissing(Exception):
    """An ``<image>`` source is an SVG but resvg (the ``graphics-svg-raster``
    extra) is not installed to render it.

    A plain :class:`Exception`, *not* an :class:`ImportError` subclass, so the
    backend's ``except ImportError`` (which means "Pillow missing") doesn't
    swallow it — the fix differs (install ``graphics-svg-raster``, not
    ``graphics``), so it gets its own warning."""


@dataclass(slots=True)
class ImageSampler:
    """A decoded, mode-applied raise-level buffer sampled by normalized
    ``(u, v)`` image coordinates in ``[0, 1)``.

    ``levels`` is a flat row-major ``0..255`` buffer of size
    ``width * height`` — already grayscale-reduced, resized to the on-page
    extent, and passed through the chosen touch-adaptation mode, so the
    rasterizer just maps each device pixel back to ``(u, v)`` and reads a
    level. No Pillow object is retained."""

    width: int
    height: int
    levels: bytes

    def sample(self, u: float, v: float) -> int:
        """Nearest-pixel raise level at normalized ``(u, v)``. Out-of-range
        coordinates clamp to the edge so a caller can probe freely."""
        px = int(u * self.width)
        if px < 0:
            px = 0
        elif px >= self.width:
            px = self.width - 1
        py = int(v * self.height)
        if py < 0:
            py = 0
        elif py >= self.height:
            py = self.height - 1
        return self.levels[py * self.width + px]


def _resolve_href(href: str) -> bytes:
    """Read an ``<image>`` ``href`` into raw bytes.

    Handles both a ``data:`` URI (base64 or percent-encoded inline payload)
    and a filesystem path. Any read / decode problem becomes a
    :class:`TactileImageError` so the backend degrades to a warning instead
    of crashing."""
    if href.startswith("data:"):
        head, sep, payload = href.partition(",")
        if not sep:
            raise TactileImageError("malformed data URI (no comma)")
        try:
            if ";base64" in head.lower():
                # b64decode raises binascii.Error, a ValueError subclass.
                return base64.b64decode(payload)
            return urllib.parse.unquote_to_bytes(payload)
        except ValueError as exc:
            raise TactileImageError(f"bad data URI: {exc}") from exc
    try:
        return Path(href).read_bytes()
    except OSError as exc:
        raise TactileImageError(f"cannot read {href!r}: {exc}") from exc


def _is_svg(raw: bytes) -> bool:
    """Whether ``raw`` looks like SVG (so it needs resvg, not Pillow). The
    ``<svg`` root tag appears within the first bytes of any SVG, after an
    optional ``<?xml …?>`` declaration / comments."""
    return b"<svg" in raw[:2048].lower()


def _render_svg(svg_bytes: bytes, target_w: int) -> bytes:
    """Render SVG bytes to PNG bytes via resvg, at ``target_w`` device pixels
    wide (resvg preserves the SVG aspect; height follows). The PNG then flows
    through the same Pillow decode + grayscale + mode pipeline as a bitmap.

    Raises :class:`SvgRasterizerMissing` if resvg isn't installed, and
    :class:`TactileImageError` if the SVG won't render."""
    try:
        import resvg_py
    except ImportError as exc:
        raise SvgRasterizerMissing(
            "external SVG rendering needs the 'graphics-svg-raster' extra "
            "(resvg-py)"
        ) from exc
    svg_string = svg_bytes.decode("utf-8", errors="replace")
    width = target_w if target_w and target_w > 0 else None
    try:
        png = resvg_py.svg_to_bytes(svg_string=svg_string, width=width)
    except Exception as exc:  # noqa: BLE001 — resvg raises on malformed SVG
        raise TactileImageError(f"SVG render failed: {exc}") from exc
    return bytes(png)


def _level_table(mode: str, threshold: int, invert: bool) -> list[int]:
    """A 256-entry lookup mapping a grayscale value (or, for ``edge``, an
    edge-magnitude value) to a raise level ``0..255``.

    * ``grayscale`` — dark ink (low gray) → high raise (``255 - i``);
      ``invert`` keeps gray as-is (bright → raised).
    * ``threshold`` / ``edge`` — bilevel at ``threshold``. For ``threshold``
      a *dark* pixel (below the cut) is raised; for ``edge`` a *strong*
      edge (at or above the cut) is raised. ``invert`` flips which side
      raises."""
    t = max(0, min(255, threshold))
    if mode == "grayscale":
        return [i if invert else 255 - i for i in range(256)]
    if mode == "edge":
        # Edge magnitude: strong edge (>= t) raised.
        return [(0 if i >= t else 255) if invert else (255 if i >= t else 0)
                for i in range(256)]
    # threshold: dark (< t) raised.
    return [(255 if i >= t else 0) if invert else (0 if i >= t else 255)
            for i in range(256)]


def _flatten_to_gray(im):
    """Convert a decoded image to 8-bit grayscale, compositing any
    transparency onto **white** first.

    Transparent areas (an SVG's empty background, a cut-out PNG) mean "no ink"
    and must read as flat — but a bare ``convert("L")`` ignores alpha and would
    turn transparent-black pixels into a raised mass. Compositing onto white
    makes them flat."""
    from PIL import Image

    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba)
    return im.convert("L")


def load_tactile_image(
    href: str,
    *,
    mode: str = "threshold",
    threshold: int = 128,
    invert: bool = False,
    target_w: int = 0,
    target_h: int = 0,
) -> ImageSampler:
    """Decode the image at ``href`` into an :class:`ImageSampler`.

    ``mode`` is one of :data:`VALID_MODES` (an unknown value falls back to
    ``threshold``). ``threshold`` (``0..255``) is the cut for the bilevel
    modes. ``target_w`` / ``target_h``, when positive, resize the decoded
    image to that on-page extent first — using a high-quality (anti-aliased)
    filter and never upscaling past the source — so a large photo
    downsampled to tactile resolution stays clean and ``edge`` detection
    runs at display scale.

    A source that sniffs as SVG is rendered to a PNG via resvg first (the
    ``graphics-svg-raster`` extra), then decoded like any raster.

    Raises :class:`ImportError` when Pillow is not installed,
    :class:`SvgRasterizerMissing` when an SVG source needs resvg (both mapped
    by the backend to skip warnings) and :class:`TactileImageError` for an
    unreadable / undecodable source.
    """
    from PIL import Image, ImageFilter  # ImportError → backend warns + skips

    raw = _resolve_href(href)
    if _is_svg(raw):
        # Complex external SVG → resvg → PNG, rendered at the on-page width so
        # the vector is sampled at tactile resolution (no "don't upscale"
        # concern — vectors have no native pixel size).
        raw = _render_svg(raw, target_w)
    resample = getattr(Image, "Resampling", Image).LANCZOS
    try:
        with Image.open(io.BytesIO(raw)) as im:
            gray = _flatten_to_gray(im)
    except TactileImageError:
        raise
    except Exception as exc:  # noqa: BLE001 — Pillow raises many decode types
        raise TactileImageError(f"decode failed: {exc}") from exc

    tw = gray.width if target_w <= 0 else min(target_w, gray.width)
    th = gray.height if target_h <= 0 else min(target_h, gray.height)
    tw, th = max(1, tw), max(1, th)
    if (tw, th) != (gray.width, gray.height):
        gray = gray.resize((tw, th), resample)

    norm_mode = mode if mode in VALID_MODES else "threshold"
    if norm_mode == "edge":
        gray = gray.filter(ImageFilter.FIND_EDGES)
    levels = gray.point(_level_table(norm_mode, threshold, invert))
    # ``tobytes()`` gives the row-major 0..255 "L" buffer directly (and
    # without the ``getdata()`` deprecation).
    w, h = levels.width, levels.height
    buf = bytearray(levels.tobytes())
    if norm_mode == "edge" and w > 2 and h > 2:
        # FIND_EDGES is a 3×3 convolution whose response on the outer frame is
        # a border artifact (the kernel runs off the image edge), not a real
        # edge — clear it so a flat image yields nothing and an imported photo
        # isn't ringed by a spurious frame.
        for x in range(w):
            buf[x] = 0
            buf[(h - 1) * w + x] = 0
        for y in range(h):
            buf[y * w] = 0
            buf[y * w + w - 1] = 0
    return ImageSampler(w, h, bytes(buf))
