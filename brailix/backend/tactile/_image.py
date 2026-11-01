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
  embosser (ViewPlus Tiger).
* ``edge`` — edge detection, so a photo's outlines become raised lines
  rather than an unreadable filled mass. Best for photographs.

A complex **external SVG** (gradients / filters / clipPath / ``<use>`` / CSS
— anything the stdlib tag-walk rasterizer can't render faithfully) is also
ingested here: when the source bytes sniff as SVG, they are handed whole to
**resvg** (the ``graphics-svg-raster`` extra) which renders them to a PNG,
and that PNG flows through the very same grayscale → mode pipeline as a
bitmap. So "any external visual" — raster or vector — becomes a tactile
raster through one path. The result
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

Every byte an href delivers is read under a
:class:`~brailix.backend.tactile._asset_limits.GraphicAssetLimits` budget
(byte ceiling on file reads and ``data:`` payloads, pixel ceiling on the
decoded image, an ``allow_filesystem`` switch for bare paths) — see
:mod:`brailix.backend.tactile._asset_limits` for why. The exception
messages below stay specific (they name the file, the size, the decode
error) because they are diagnostic detail for the caller who has it; what
must NOT carry them is user-visible warning text, where the difference
between "cannot read" and "cannot decode" is a file-existence probe. The
backend's image handler keeps that text to one fixed sentence.
"""

from __future__ import annotations

import base64 as _base64
import io as _io
import re as _re
import urllib.parse as _urllib_parse
from dataclasses import dataclass as _dataclass

from brailix.backend.tactile._asset_limits import (
    DEFAULT_GRAPHIC_ASSET_LIMITS,
    GraphicAssetLimits,
    GraphicAssetTooLargeError,
)

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


@_dataclass(slots=True)
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


def _resolve_href(
    href: str, limits: GraphicAssetLimits = DEFAULT_GRAPHIC_ASSET_LIMITS
) -> bytes:
    """Read an ``<image>`` ``href`` into raw bytes, under ``limits``.

    Handles both a ``data:`` URI (base64 or percent-encoded inline payload)
    and a filesystem path; a bare path is only read at all when the policy
    allows touching the filesystem. Every route is bounded: the file read
    goes through :meth:`GraphicAssetLimits.read_bounded`, the inline payload
    through a length gate before decoding and a byte gate after. Any read /
    decode problem becomes a :class:`TactileImageError` so the backend
    degrades to a warning instead of crashing."""
    if href.startswith("data:"):
        head, sep, payload = href.partition(",")
        if not sep:
            raise TactileImageError("malformed data URI (no comma)")
        # Refuse-by-arithmetic before the decode materializes the payload:
        # a payload longer than 4/3 of the byte budget cannot fit under it
        # in either encoding (base64 inflates by exactly 4/3, percent-
        # encoding only shrinks).
        try:
            limits.check_uri_payload(len(payload))
            if ";base64" in head.lower():
                # b64decode raises binascii.Error, a ValueError subclass.
                data = _base64.b64decode(payload)
            else:
                data = _urllib_parse.unquote_to_bytes(payload)
        except GraphicAssetTooLargeError as exc:
            raise TactileImageError(str(exc)) from exc
        except ValueError as exc:
            raise TactileImageError(f"bad data URI: {exc}") from exc
        try:
            limits.check_bytes(len(data), detail="data URI payload")
        except GraphicAssetTooLargeError as exc:
            raise TactileImageError(str(exc)) from exc
        return data
    if not limits.allow_filesystem:
        # Refused before anything is read — a policy outcome, not a load
        # failure, and one that reveals nothing about the filesystem.
        raise TactileImageError(
            f"image href is a filesystem path ({href!r}), but reading "
            "local files is disabled by the caller's graphic asset limits"
        )
    try:
        return limits.read_bounded(href)
    except GraphicAssetTooLargeError as exc:
        raise TactileImageError(str(exc)) from exc
    except OSError as exc:
        raise TactileImageError(f"cannot read {href!r}: {exc}") from exc


def _is_svg(raw: bytes) -> bool:
    """Whether ``raw`` looks like SVG (so it needs resvg, not Pillow). The
    ``<svg`` root tag appears within the first bytes of any SVG, after an
    optional ``<?xml …?>`` declaration / comments."""
    return b"<svg" in raw[:2048].lower()


# An ``<image`` opening tag (case-insensitive; ``\b`` so ``<imagex>`` doesn't
# match) whose region we then scan for href attributes.
_IMAGE_TAG = _re.compile(r"<image\b", _re.IGNORECASE)

# ``href="…"`` / ``xlink:href='…'`` in either quote style — the value is the
# first quoted string after the ``=``.
_HREF_ATTR = _re.compile(r"(?:xlink:)?href\s*=\s*([\"'])(.*?)\1", _re.IGNORECASE)

# A bare ``href =`` the value pattern cannot parse out of the region —
# malformed on its face, which the caller treats as external (refuse).
_HREF_BARE = _re.compile(r"(?:xlink:)?href\s*=", _re.IGNORECASE)


def _external_image_ref(svg: str) -> bool:
    """Whether the SVG carries an ``<image>`` reference resvg would fetch.

    resvg resolves a **bare absolute filesystem path** in an ``<image href>``
    and reads the file itself — measured, not assumed: a ``C:\\…`` href
    renders, while ``file:///`` URIs and bare relative names do not (no
    ``resources_dir`` is ever passed). That fetch happens in the rasterizer,
    behind the back of :class:`GraphicAssetLimits`, so it is refused here
    instead: every ``<image>`` href that is not a ``data:`` URI or a
    same-document fragment (``#id``) makes the whole render decline.

    The scan is deliberately crude and errs toward refusing. Each
    ``<image`` match is read to the next ``<`` — a raw ``<`` is illegal
    inside an attribute value, so that region can hold only the one tag's
    text — which is what defeats the "hide the href behind a ``>`` inside an
    earlier attribute" trick (``<image alt=">" href="C:\\secret.png">``).
    What the region scan can misread, it misreads in the safe direction: a
    comment mentioning an image href also refuses, an href it cannot parse
    refuses, and losing a render is the cheap failure — the direct
    ``<image>`` element (the documented external-asset channel, read under
    the budget) is how a file-referencing image is spelled.
    """
    for m in _IMAGE_TAG.finditer(svg):
        end = svg.find("<", m.end())
        region = svg[m.end():] if end < 0 else svg[m.end():end]
        found_value = False
        for attr in _HREF_ATTR.finditer(region):
            found_value = True
            value = attr.group(2).lstrip().lower()
            if not value.startswith(("data:", "#")):
                return True
        # An href we could not extract a quoted value for: refuse rather
        # than guess what resvg would make of it.
        if not found_value and _HREF_BARE.search(region):
            return True
    return False


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
    if _external_image_ref(svg_string):
        # Refused before the rasterizer can fetch: resvg reads absolute-path
        # image hrefs itself, uncapped and outside every policy in
        # GraphicAssetLimits — the second entry to the hole the budget
        # closed on the direct path (adversarial review 2026-10-31).
        raise TactileImageError(
            "external SVG carries an <image> reference to a file; inline it "
            "(a data: URI) or reference it with a direct <image> element"
        )
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
    limits: GraphicAssetLimits = DEFAULT_GRAPHIC_ASSET_LIMITS,
) -> ImageSampler:
    """Decode the image at ``href`` into an :class:`ImageSampler`.

    ``mode`` is one of :data:`VALID_MODES` (an unknown value falls back to
    ``threshold``). ``threshold`` (``0..255``) is the cut for the bilevel
    modes. ``target_w`` / ``target_h``, when positive, resize the decoded
    image to that on-page extent first — using a high-quality (anti-aliased)
    filter and never upscaling past the source — so a large photo
    downsampled to tactile resolution stays clean and ``edge`` detection
    runs at display scale.

    ``limits`` is the read budget (see
    :class:`~brailix.backend.tactile._asset_limits.GraphicAssetLimits`):
    the href's bytes are read under its byte ceiling and its
    ``allow_filesystem`` switch, and the decoded image under its pixel
    ceiling — checked from the header, because the decode is the moment the
    full pixel buffer is allocated.

    A source that sniffs as SVG is rendered to a PNG via resvg first (the
    ``graphics-svg-raster`` extra), then decoded like any raster.

    Raises :class:`ImportError` when Pillow is not installed,
    :class:`SvgRasterizerMissing` when an SVG source needs resvg (both mapped
    by the backend to skip warnings) and :class:`TactileImageError` for an
    unreadable / undecodable / over-budget source.
    """
    from PIL import Image, ImageFilter  # ImportError → backend warns + skips

    raw = _resolve_href(href, limits)
    if _is_svg(raw):
        # Complex external SVG → resvg → PNG, rendered at the on-page width so
        # the vector is sampled at tactile resolution (no "don't upscale"
        # concern — vectors have no native pixel size).
        raw = _render_svg(raw, target_w)
    resample = getattr(Image, "Resampling", Image).LANCZOS
    try:
        with Image.open(_io.BytesIO(raw)) as im:
            # The pixel gate reads header dimensions — for the standard
            # formats that costs no decode — and must run before
            # _flatten_to_gray touches pixel data, because that is the
            # moment Pillow allocates the buffer a huge source would cost.
            limits.check_pixels(im.width, im.height)
            gray = _flatten_to_gray(im)
    except TactileImageError:
        raise
    except GraphicAssetTooLargeError as exc:
        raise TactileImageError(str(exc)) from exc
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
