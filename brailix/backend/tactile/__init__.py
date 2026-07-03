"""Tactile backend: rasterize a normalized SVG tree into a
:class:`~brailix.ir.tactile.TactileRaster`.

SVG **is** the graphics IR (see :mod:`brailix.frontend.graphics`), so the
backend dispatches per :attr:`xml.etree.ElementTree.Element.tag` — the
exact analogue of how the math / music backends walk MathML / MusicXML
element trees. Each supported shape is mapped from logical SVG user units
into device pixels (via the profile's DPI and page size) and painted with
the zero-dependency primitives in :mod:`._draw`.

Coordinate mapping
------------------

* The drawing's **logical** coordinate space comes from the SVG
  ``viewBox`` (or ``width`` / ``height`` user units if no viewBox).
* Its **physical** size in millimetres comes from ``width`` / ``height``
  when they carry real units (``mm`` / ``cm`` / ``in`` / ``pt`` / ``px``);
  otherwise one logical user unit is taken as one millimetre — a clean,
  predictable default that makes our own generated primitives
  millimetre-addressable. With nothing at all, the profile page size is
  used (with a warning).
* **Device pixels** = physical millimetres × DPI ÷ 25.4. The single
  device-dependent dial is the profile's ``dpi``; everything else is
  device independent (``ARCHITECTURE.md``).

This first increment covers the primitive subset the graphics frontend
emits — ``line`` / ``rect`` / ``circle`` / ``ellipse`` / ``polyline`` /
``polygon`` plus ``<g>`` grouping. ``<path>`` data, ``transform``
matrices, and ``<text>`` labels (which become braille) arrive in later
phases; until then they are skipped with a one-time warning rather than
guessed at. Soft-failure contract: an unsupported element or malformed
attribute never crashes — it is warned and skipped, mirroring the
"pipeline never crashes" rule of the math / music backends.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace

from brailix.backend.tactile._draw import (
    draw_circle,
    draw_ellipse,
    draw_line,
    draw_polyline,
)
from brailix.backend.tactile._fill import (
    TEXTURES,
    fill_ellipse,
    fill_polygon,
    fill_polygons,
    normalize_texture,
)
from brailix.backend.tactile._image import (
    SvgRasterizerMissing,
    TactileImageError,
    load_tactile_image,
)
from brailix.backend.tactile._labels import LabelStamper, LabelTranslator
from brailix.backend.tactile._path import parse_path_data
from brailix.backend.tactile._separation import find_too_close
from brailix.backend.tactile._transform import IDENTITY, Affine, parse_transform

# ``page`` reuses this package's LabelStamper / profile but nothing here imports
# back from it, so the package-init line forms no cycle.
from brailix.backend.tactile.page import (
    PageFigure,
    PageItem,
    PageText,
    compose_pages,
)
from brailix.backend.tactile.profile import TactileProfile
from brailix.core.errors import WarningCollector
from brailix.ir.braille import BrailleCell
from brailix.ir.tactile import TactileRaster

__all__ = (
    "rasterize",
    "compose_pages",
    "PageText",
    "PageFigure",
    "PageItem",
)

# Raster-size safety cap — defined in the leaf module so the page compositor
# shares it without an import cycle. Re-exported here so existing
# ``monkeypatch.setattr(tactile, "_MAX_RASTER_PIXELS", ...)`` tests still work
# (the value is read at each rasterize call, below).
from brailix.backend.tactile._raster_cap import (  # noqa: E402
    _MAX_RASTER_PIXELS,
    clamp_raster_pixels,
)

# Absolute-unit → millimetre factors for SVG length values. CSS px is
# 1/96 inch; pt is 1/72 inch; pc is 1/6 inch; Q is a quarter-millimetre.
_UNIT_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0,
    "q": 0.25,
}

_MM_PER_INCH = 25.4


def _round_finite(v: float) -> int:
    """``round(v)`` that yields 0 for a non-finite ``v`` instead of raising.

    A NaN / ±inf coordinate (from a ``"1e999"`` overflow, a degenerate
    transform, or a ``"nan"`` literal — all of which parse to a non-finite
    float) would make ``round()`` raise ``ValueError`` / ``OverflowError``,
    breaking rasterize's "never raises on bad geometry" contract. Off-page /
    zero pixels are dropped downstream anyway, so 0 is a safe degenerate."""
    return round(v) if math.isfinite(v) else 0


# Container tags whose children are walked in place (grouping only — their
# own ``transform`` / nested-viewBox semantics are a later phase).
_CONTAINERS = frozenset({"g", "a", "svg", "switch"})

# Non-drawing tags skipped silently (no geometry to render).
_NON_DRAWING = frozenset({"desc", "title", "defs", "style", "metadata"})

# Known tags deferred to later phases — warned with a clearer message.
# ``<text>`` is handled (translated to braille) when a label translator is
# supplied; ``<tspan>`` content is gathered from within ``<text>``;
# ``<image>`` is handled (raster → raise levels) when the ``graphics`` extra
# (Pillow) is installed.
_DEFERRED: dict[str, str] = {
    "use": "defs/use flattening",
}


# ---------------------------------------------------------------------------
# Attribute parsing helpers
# ---------------------------------------------------------------------------


def _parse_float(value: str | None, default: float) -> float:
    """Parse an SVG coordinate / length to a float in user units.

    A trailing unit suffix (``px`` / ``mm`` / ...) is stripped and ignored
    — geometry attributes are in user-unit (viewBox) space. Returns
    ``default`` for missing / unparseable input so a malformed attribute
    degrades instead of raising.
    """
    if value is None:
        return default
    s = value.strip()
    if not s:
        return default
    i = len(s)
    while i > 0 and s[i - 1].isalpha():
        i -= 1
    head = s[:i]
    if not head:
        return default
    try:
        val = float(head)
    except ValueError:
        return default
    # Reject NaN / ±inf (e.g. "1e999" overflows to inf): they would raise in
    # the round()/int() downstream, defeating the soft-failure contract.
    return val if math.isfinite(val) else default


def _length_to_mm(value: str | None) -> float | None:
    """Convert an SVG root ``width`` / ``height`` to millimetres.

    Returns ``None`` when the length is unitless, a percentage, or carries
    an unknown unit — the caller then treats the logical size as
    millimetres (1 user unit = 1 mm).
    """
    if value is None:
        return None
    s = value.strip().lower()
    if not s or s.endswith("%"):
        return None
    i = len(s)
    while i > 0 and s[i - 1].isalpha():
        i -= 1
    head, unit = s[:i], s[i:]
    if not unit:
        return None
    factor = _UNIT_MM.get(unit)
    if factor is None:
        return None
    try:
        val = float(head) * factor
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def _parse_view_box(root: ET.Element) -> tuple[float, float, float, float] | None:
    """Parse ``viewBox="minx miny width height"`` into floats, or ``None``
    if absent / malformed / non-positive."""
    vb = root.get("viewBox")
    if not vb:
        return None
    parts = vb.replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        minx, miny, w, h = (float(p) for p in parts)
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in (minx, miny, w, h)):
        return None  # "nan" / "1e999" would raise downstream
    if w <= 0 or h <= 0:
        return None
    return (minx, miny, w, h)


def _parse_points(value: str | None) -> list[tuple[float, float]]:
    """Parse a ``points`` attribute (``"x1,y1 x2,y2 ..."``) into (x, y)
    pairs, tolerating comma or whitespace separators and dropping a stray
    trailing odd coordinate."""
    if not value:
        return []
    nums: list[float] = []
    for tok in value.replace(",", " ").split():
        try:
            f = float(tok)
        except ValueError:
            continue
        if math.isfinite(f):
            nums.append(f)
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


# ---------------------------------------------------------------------------
# Render state + element handlers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _State:
    """Per-rasterization transform + style carried through the walk.

    ``min_radius`` is the floor stroke half-width (from the profile's
    minimum touchable line width); ``scale`` converts an SVG
    ``stroke-width`` (user units) into device pixels so an element can ask
    for a *thicker* line — never thinner than the floor.
    """

    minx: float
    miny: float
    sx: float
    sy: float
    min_radius: int
    scale: float
    level: int
    warnings: WarningCollector | None
    warned: set[str]
    labeler: LabelStamper | None
    tex_spacing: int
    tex_thickness: int
    fill_map: dict[str, str]
    # Accumulators for the post-walk separability pass, shared across every
    # ``replace``-d copy (the field holds the same list reference): ``labels``
    # collects ``(text element, its effective state)`` so labels draw last
    # (order-independent overlap), ``boxes`` collects ``(name, device bbox)``
    # for every drawn non-text element so spacing can be checked pairwise.
    labels: list[tuple[ET.Element, _State]] = field(default_factory=list)
    boxes: list[tuple[str, tuple[int, int, int, int]]] = field(
        default_factory=list
    )
    ctm: Affine = IDENTITY

    def tx(self, x: float) -> int:
        return _round_finite((x - self.minx) * self.sx)

    def ty(self, y: float) -> int:
        return _round_finite((y - self.miny) * self.sy)

    def dev(self, x: float, y: float) -> tuple[int, int]:
        """Map a user-space point through the current transform, then the
        viewBox->device mapping (paired so a rotating / skewing transform
        that mixes x and y is applied correctly)."""
        ux, uy = self.ctm.apply(x, y)
        return (
            _round_finite((ux - self.minx) * self.sx),
            _round_finite((uy - self.miny) * self.sy),
        )

    def len_scale(self) -> float:
        """Extra length / radius scaling contributed by the transform."""
        return self.ctm.scale_factor()

    def path_scale(self) -> float:
        """User-unit→device-pixel factor for path-flattening density.

        ``max(sx, sy)`` (a conservative upper bound so an anisotropic / skewed
        mapping never under-subdivides) times the transform's scale factor, so
        Bézier curves keep a consistent on-page smoothness regardless of the
        author's coordinate magnitude."""
        return max(self.sx, self.sy) * self.ctm.scale_factor()

    def lx(self, v: float) -> int:
        return _round_finite(v * self.sx)

    def ly(self, v: float) -> int:
        return _round_finite(v * self.sy)

    def texture_for(self, fill: str) -> str:
        """Texture for a ``fill`` value: a directly-named texture / alias,
        else one assigned by first-seen order so distinct fills read as
        distinct textures."""
        direct = normalize_texture(fill)
        if direct is not None:
            return direct
        tex = self.fill_map.get(fill)
        if tex is None:
            tex = TEXTURES[len(self.fill_map) % len(TEXTURES)]
            self.fill_map[fill] = tex
        return tex

    def warn_once(self, *, key: str, code: str, message: str) -> None:
        # ``code`` is passed as a ``code="..."`` literal at each call site
        # (not threaded through a variable) so a static scan of the source
        # can enumerate every warning code this module emits.
        if self.warnings is None or key in self.warned:
            return
        self.warned.add(key)
        self.warnings.warn(code=code, message=message, source="backend.tactile")


def _h_line(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    x1, y1 = _parse_float(elem.get("x1"), 0.0), _parse_float(elem.get("y1"), 0.0)
    x2, y2 = _parse_float(elem.get("x2"), 0.0), _parse_float(elem.get("y2"), 0.0)
    draw_line(raster, *st.dev(x1, y1), *st.dev(x2, y2), radius, st.level)


def _h_rect(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    x, y = _parse_float(elem.get("x"), 0.0), _parse_float(elem.get("y"), 0.0)
    w, h = _parse_float(elem.get("width"), 0.0), _parse_float(elem.get("height"), 0.0)
    if w <= 0 or h <= 0:
        return
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts = [st.dev(cx, cy) for cx, cy in corners]
    if fill is not None:
        fill_polygon(
            raster, pts, st.texture_for(fill), st.tex_spacing,
            st.tex_thickness, st.level,
        )
    draw_polyline(raster, pts, radius, st.level, closed=True)


def _h_circle(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    cx, cy = _parse_float(elem.get("cx"), 0.0), _parse_float(elem.get("cy"), 0.0)
    r = _parse_float(elem.get("r"), 0.0)
    if r <= 0:
        return
    # Use the x-scale for the radius; under a non-uniform viewBox→page
    # mapping a circle is drawn as an ellipse via the x-scale (a rare edge
    # this first increment accepts).
    dcx, dcy = st.dev(cx, cy)
    dr = st.lx(r * st.len_scale())
    if fill is not None:
        fill_ellipse(
            raster, dcx, dcy, dr, dr,
            st.texture_for(fill), st.tex_spacing, st.tex_thickness, st.level,
        )
    draw_circle(raster, dcx, dcy, dr, radius, st.level)


def _h_ellipse(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    cx, cy = _parse_float(elem.get("cx"), 0.0), _parse_float(elem.get("cy"), 0.0)
    rx, ry = _parse_float(elem.get("rx"), 0.0), _parse_float(elem.get("ry"), 0.0)
    if rx <= 0 or ry <= 0:
        return
    dcx, dcy = st.dev(cx, cy)
    drx, dry = st.lx(rx * st.len_scale()), st.ly(ry * st.len_scale())
    if fill is not None:
        fill_ellipse(
            raster, dcx, dcy, drx, dry,
            st.texture_for(fill), st.tex_spacing, st.tex_thickness, st.level,
        )
    draw_ellipse(raster, dcx, dcy, drx, dry, radius, st.level)


def _h_polyline(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    pts = [st.dev(x, y) for x, y in _parse_points(elem.get("points"))]
    draw_polyline(raster, pts, radius, st.level, closed=False)


def _h_polygon(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    pts = [st.dev(x, y) for x, y in _parse_points(elem.get("points"))]
    if fill is not None and len(pts) >= 3:
        fill_polygon(
            raster, pts, st.texture_for(fill), st.tex_spacing,
            st.tex_thickness, st.level,
        )
    draw_polyline(raster, pts, radius, st.level, closed=True)


def _h_path(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    subpaths = parse_path_data(elem.get("d", ""), scale=st.path_scale())
    dev_subs = []
    for sp in subpaths:
        pts = [st.dev(px, py) for px, py in sp.points]
        if pts:
            dev_subs.append((pts, sp.closed))
    if fill is not None:
        rings = [p for p, closed in dev_subs if closed and len(p) >= 3]
        if rings:
            fill_polygons(
                raster, rings, st.texture_for(fill), st.tex_spacing,
                st.tex_thickness, st.level,
            )
    for pts, closed in dev_subs:
        draw_polyline(raster, pts, radius, st.level, closed=closed)


def _h_image(
    elem: ET.Element, raster: TactileRaster, st: _State, radius: int, fill: str | None
) -> None:
    # A bitmap has no vector structure, so it stays a raster: decode the
    # referenced pixels and stamp them as raise levels over the element's
    # placement box. ``href`` may be SVG2 bare or xlink (post-normalize the
    # latter keeps its Clark-notation namespace, which strip_namespace only
    # removes from *tags*, not attributes).
    href = elem.get("href") or elem.get(
        "{http://www.w3.org/1999/xlink}href"
    )
    if not href:
        st.warn_once(
            key="image-nohref",
            code="GRAPHICS_IMAGE_LOAD_FAILED",
            message="<image> has no href; skipped",
        )
        return
    x, y = _parse_float(elem.get("x"), 0.0), _parse_float(elem.get("y"), 0.0)
    w, h = _parse_float(elem.get("width"), 0.0), _parse_float(elem.get("height"), 0.0)
    if w <= 0 or h <= 0:
        st.warn_once(
            key="image-nobox",
            code="GRAPHICS_IMAGE_LOAD_FAILED",
            message="<image> has no positive width/height; skipped",
        )
        return
    # Affine mapping the image's normalized (u, v) ∈ [0, 1]² space onto
    # device pixels: place into the [x, x+w]×[y, y+h] box, through the
    # current transform, through the viewBox→device mapping. Invert it to
    # sample one source texel per device pixel (correct under rotation /
    # skew, the inverse-texture-mapping idiom the fills also use).
    box = Affine(w, 0.0, 0.0, h, x, y)
    viewbox = Affine(st.sx, 0.0, 0.0, st.sy, -st.minx * st.sx, -st.miny * st.sy)
    full = viewbox.then(st.ctm).then(box)
    inv = full.inverse()
    if inv is None:
        return  # degenerate placement (zero-area transform) — nothing to draw
    corners = [full.apply(0.0, 0.0), full.apply(1.0, 0.0),
               full.apply(1.0, 1.0), full.apply(0.0, 1.0)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    lo_x = max(0, math.floor(min(xs)))
    hi_x = min(raster.width - 1, math.ceil(max(xs)))
    lo_y = max(0, math.floor(min(ys)))
    hi_y = min(raster.height - 1, math.ceil(max(ys)))
    if lo_x > hi_x or lo_y > hi_y:
        return  # placement box is entirely off the page
    mode = (elem.get("data-bk-mode") or "threshold").strip().lower()
    threshold = round(_parse_float(elem.get("data-bk-threshold"), 128.0))
    invert = (elem.get("data-bk-invert") or "").strip().lower() in ("1", "true", "yes")
    try:
        sampler = load_tactile_image(
            href,
            mode=mode,
            threshold=threshold,
            invert=invert,
            target_w=hi_x - lo_x + 1,
            target_h=hi_y - lo_y + 1,
        )
    except SvgRasterizerMissing:
        st.warn_once(
            key="image-svg-rasterizer",
            code="GRAPHICS_SVG_NO_RASTERIZER",
            message="external SVG rendering needs the 'graphics-svg-raster' "
            "extra (resvg-py); <image> skipped",
        )
        return
    except ImportError:
        st.warn_once(
            key="image-decoder",
            code="GRAPHICS_IMAGE_NO_DECODER",
            message="raster image import needs the 'graphics' extra "
            "(Pillow); <image> skipped",
        )
        return
    except TactileImageError as exc:
        st.warn_once(
            key=f"image:{href}",
            code="GRAPHICS_IMAGE_LOAD_FAILED",
            message=f"could not load image {href!r}: {exc}",
        )
        return
    for dy in range(lo_y, hi_y + 1):
        for dx in range(lo_x, hi_x + 1):
            u, v = inv.apply(dx + 0.5, dy + 0.5)
            if 0.0 <= u < 1.0 and 0.0 <= v < 1.0:
                level = sampler.sample(u, v)
                if level > 0:
                    raster.set_raise(dx, dy, level)


_DISPATCH: dict[str, object] = {
    "line": _h_line,
    "rect": _h_rect,
    "circle": _h_circle,
    "ellipse": _h_ellipse,
    "polyline": _h_polyline,
    "polygon": _h_polygon,
    "path": _h_path,
    "image": _h_image,
}
# ``<text>`` is intentionally absent from _DISPATCH: labels are deferred to a
# second pass (see _walk / _place_labels) so their overlap with the figure is
# order-independent, and they render on top.


def _device_bbox(
    elem: ET.Element, tag: str, st: _State, radius: int
) -> tuple[int, int, int, int] | None:
    """Device-pixel bounding box of a drawn element's *raised ink*, for the
    spacing check.

    Mirrors each shape handler's coordinate math but keeps only the extent
    (not the pixels), so the separability pass can measure element-to-element
    gaps without recording per-pixel provenance. The centre-line extent is
    expanded by the stroke ``radius`` (each stroke stamps a disk of ``radius``
    px around its centre line — see :func:`stamp_disk`), so the measured gap is
    the real raised-surface clearance, not the wider centre-line gap. This
    mirrors how :func:`_place_labels` grows a label box by ``dot_radius``.
    Returns ``None`` for a degenerate / unsupported element (it then takes no
    part in the check)."""
    box: tuple[int, int, int, int] | None = None
    pts: list[tuple[int, int]] = []
    if tag == "line":
        pts = [
            st.dev(_parse_float(elem.get("x1"), 0.0), _parse_float(elem.get("y1"), 0.0)),
            st.dev(_parse_float(elem.get("x2"), 0.0), _parse_float(elem.get("y2"), 0.0)),
        ]
    elif tag in ("rect", "image"):
        x, y = _parse_float(elem.get("x"), 0.0), _parse_float(elem.get("y"), 0.0)
        w = _parse_float(elem.get("width"), 0.0)
        h = _parse_float(elem.get("height"), 0.0)
        if w <= 0 or h <= 0:
            return None
        pts = [st.dev(x, y), st.dev(x + w, y), st.dev(x + w, y + h), st.dev(x, y + h)]
    elif tag == "circle":
        r = _parse_float(elem.get("r"), 0.0)
        if r <= 0:
            return None
        dcx, dcy = st.dev(_parse_float(elem.get("cx"), 0.0), _parse_float(elem.get("cy"), 0.0))
        dr = st.lx(r * st.len_scale())
        box = (dcx - dr, dcy - dr, dcx + dr, dcy + dr)
    elif tag == "ellipse":
        rx, ry = _parse_float(elem.get("rx"), 0.0), _parse_float(elem.get("ry"), 0.0)
        if rx <= 0 or ry <= 0:
            return None
        dcx, dcy = st.dev(_parse_float(elem.get("cx"), 0.0), _parse_float(elem.get("cy"), 0.0))
        drx, dry = st.lx(rx * st.len_scale()), st.ly(ry * st.len_scale())
        box = (dcx - drx, dcy - dry, dcx + drx, dcy + dry)
    elif tag in ("polyline", "polygon"):
        pts = [st.dev(x, y) for x, y in _parse_points(elem.get("points"))]
    elif tag == "path":
        for sp in parse_path_data(elem.get("d", ""), scale=st.path_scale()):
            pts.extend(st.dev(px, py) for px, py in sp.points)
    if box is None:
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        box = (min(xs), min(ys), max(xs), max(ys))
    # Expand the centre-line box by the stroke radius so the gap reflects the
    # actual raised surface, not the centre line.
    return (box[0] - radius, box[1] - radius, box[2] + radius, box[3] + radius)


def _resolve_radius(elem: ET.Element, st: _State, inherited: int) -> int:
    """Stroke half-width (px) for ``elem``: its own ``stroke-width`` if
    present, else the inherited value — always floored to the profile's
    minimum touchable width so no line is ever too thin to feel."""
    sw = elem.get("stroke-width")
    if sw is None:
        return inherited
    val = _parse_float(sw, -1.0)
    if val <= 0:
        return inherited
    return max(st.min_radius, round(val * st.scale * st.ctm.scale_factor() / 2.0))


def _resolve_fill(elem: ET.Element, inherited: str | None) -> str | None:
    """Fill value for ``elem``: its own ``fill`` if present, else the
    inherited one. ``none`` / ``transparent`` / empty resolve to ``None``
    (outline only — the tactile default), so a region is textured only on
    an explicit non-empty fill."""
    f = elem.get("fill")
    if f is None:
        return inherited
    key = f.strip().lower()
    if key in ("none", "transparent", ""):
        return None
    return key


def _walk(
    elem: ET.Element,
    raster: TactileRaster,
    st: _State,
    inherited_radius: int,
    inherited_fill: str | None,
) -> None:
    for child in elem:
        tag = child.tag
        if not isinstance(tag, str):
            # ET.Comment / ProcessingInstruction carry a callable tag.
            continue
        tf = child.get("transform")
        st_eff = replace(st, ctm=st.ctm.then(parse_transform(tf))) if tf else st
        radius = _resolve_radius(child, st_eff, inherited_radius)
        fill = _resolve_fill(child, inherited_fill)
        if tag in _CONTAINERS:
            # A group's stroke-width / fill / transform inherit into its subtree.
            _walk(child, raster, st_eff, radius, fill)
        elif tag == "text":
            # Defer to the post-walk label pass: drawn after the whole figure
            # so label-vs-figure overlap is order-independent (generators
            # interleave shapes and labels), and labels land on top.
            st_eff.labels.append((child, st_eff))
        elif tag in _DISPATCH:
            # Attribute every pixel this element draws to its stable gid
            # (no-op unless provenance recording is on).
            raster.begin_element(child.get("data-bk-gid"))
            _DISPATCH[tag](child, raster, st_eff, radius, fill)  # type: ignore[operator]
            raster.begin_element(None)
            # Record its raised-ink extent (centre line grown by the stroke
            # radius) for the element-spacing check.
            bbox = _device_bbox(child, tag, st_eff, radius)
            if bbox is not None:
                st_eff.boxes.append((tag, bbox))
        elif tag in _NON_DRAWING:
            continue
        else:
            phase = _DEFERRED.get(tag)
            message = (
                f"<{tag}> not yet supported ({phase}); skipped"
                if phase
                else f"<{tag}> not supported; skipped"
            )
            st.warn_once(
                key=f"tag:{tag}",
                code="GRAPHICS_UNSUPPORTED_ELEMENT",
                message=message,
            )


# ---------------------------------------------------------------------------
# Geometry resolution + public entry point
# ---------------------------------------------------------------------------


def _resolve_geometry(
    root: ET.Element, profile: TactileProfile, warnings: WarningCollector | None
) -> tuple[float, float, float, float, float, float]:
    """Return ``(minx, miny, logical_w, logical_h, phys_w_mm, phys_h_mm)``
    for the SVG root, applying the viewBox / width-height / page-size
    fallback chain described in the module docstring."""
    vb = _parse_view_box(root)
    w_mm = _length_to_mm(root.get("width"))
    h_mm = _length_to_mm(root.get("height"))
    if vb:
        minx, miny, logw, logh = vb
    else:
        wu = _parse_float(root.get("width"), 0.0)
        hu = _parse_float(root.get("height"), 0.0)
        if wu > 0 and hu > 0:
            minx, miny, logw, logh = 0.0, 0.0, wu, hu
        else:
            if warnings is not None:
                warnings.warn(
                    code="GRAPHICS_NO_GEOMETRY",
                    message="SVG has no viewBox or width/height; "
                    "using profile page size",
                    source="backend.tactile",
                )
            minx, miny = 0.0, 0.0
            logw, logh = profile.page_width_mm, profile.page_height_mm
    phys_w_mm = w_mm if w_mm is not None else logw
    phys_h_mm = h_mm if h_mm is not None else logh
    # Defensive guards so a degenerate value can never divide by zero.
    if logw <= 0:
        logw = phys_w_mm if phys_w_mm > 0 else 1.0
    if logh <= 0:
        logh = phys_h_mm if phys_h_mm > 0 else 1.0
    if phys_w_mm <= 0:
        phys_w_mm = logw
    if phys_h_mm <= 0:
        phys_h_mm = logh
    return minx, miny, logw, logh, phys_w_mm, phys_h_mm


# Cap on the O(n²) pairwise separability scan: beyond this many elements /
# labels the check is skipped (a hand-made tactile figure has far fewer; a
# pathological tree shouldn't let a diagnostic dominate compile time).
_SEP_CAP = 2000


def _place_labels(
    labels: list[tuple[ET.Element, _State]],
    raster: TactileRaster,
    min_gap_px: float,
    warnings: WarningCollector | None,
) -> None:
    """Draw the deferred ``<text>`` labels, warning about collisions first.

    Two passes so the checks see a clean state: (1) translate + locate each
    label, flag any whose dots land on already-raised *figure* pixels (the
    raster carries no labels yet) and collect footprints for the
    label-to-label check; (2) stamp them. A missing braille profile is warned
    once and the label skipped, mirroring the old inline behaviour."""
    placed: list[tuple[LabelStamper, str | None, list[BrailleCell], int, int]] = []
    label_boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    figure_overlaps = 0
    no_profile_warned = False
    for elem, st in labels:
        text = "".join(elem.itertext()).strip()
        if not text:
            continue
        if st.labeler is None:
            if warnings is not None and not no_profile_warned:
                warnings.warn(
                    code="GRAPHICS_LABEL_NO_PROFILE",
                    message="<text> label skipped: no braille profile given "
                    "to translate it",
                    source="backend.tactile",
                )
                no_profile_warned = True
            continue
        dx, dy = st.dev(
            _parse_float(elem.get("x"), 0.0), _parse_float(elem.get("y"), 0.0)
        )
        # Translate once; probe + paint reuse the same cells (so a label's
        # translation runs exactly once, like the old inline path).
        cells = st.labeler.translate(text)
        centers = st.labeler.dot_centers_from_cells(cells, dx, dy)
        if not centers:
            continue
        # The raster has no labels stamped yet, so a raised pixel under any
        # dot's disk is the figure → the label sits on top of a drawn feature.
        # Scan the whole dot disk, not just its centre: a stroke crossing the
        # disk but missing the centre still fuses with the dot.
        if st.labeler.figure_under_dots(raster, centers):
            figure_overlaps += 1
        r = st.labeler.dot_radius
        xs = [c[0] for c in centers]
        ys = [c[1] for c in centers]
        label_boxes.append(
            (text, (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r))
        )
        placed.append((st.labeler, elem.get("data-bk-gid"), cells, dx, dy))

    label_collisions = (
        find_too_close(label_boxes, min_gap_px, allow_touch=False)
        if len(label_boxes) <= _SEP_CAP
        else []
    )
    if warnings is not None and (figure_overlaps or label_collisions):
        warnings.warn(
            code="GRAPHICS_LABEL_OVERLAP",
            message=f"{figure_overlaps} label(s) overlap the figure; "
            f"{len(label_collisions)} label pair(s) overlap each other — "
            f"braille dots may be unreadable",
            source="backend.tactile",
        )

    for labeler, gid, cells, dx, dy in placed:
        raster.begin_element(gid)
        labeler.stamp_cells(raster, cells, dx, dy)
        raster.begin_element(None)


def _check_spacing(
    boxes: list[tuple[str, tuple[int, int, int, int]]],
    min_gap_px: float,
    min_gap_mm: float,
    ppm: float,
    warnings: WarningCollector | None,
) -> None:
    """Warn when two distinct elements are closer than the minimum touchable
    spacing — but not touching, since a zero gap is usually an intentional
    connection (see :func:`._separation.find_too_close`)."""
    if warnings is None or len(boxes) > _SEP_CAP:
        return
    close = find_too_close(boxes, min_gap_px, allow_touch=True)
    if not close:
        return
    min_gap_mm_found = (min(gap for _a, _b, gap in close) / ppm) if ppm else 0.0
    warnings.warn(
        code="GRAPHICS_FEATURES_TOO_CLOSE",
        message=f"{len(close)} element pair(s) are closer than {min_gap_mm} mm "
        f"(min {min_gap_mm_found:.2f} mm); features may not be "
        f"distinguishable by touch",
        source="backend.tactile",
    )


def rasterize(
    svg_root: ET.Element,
    profile: TactileProfile,
    warnings: WarningCollector | None = None,
    label_translator: LabelTranslator | None = None,
    record_provenance: bool = False,
) -> TactileRaster:
    """Rasterize a normalized SVG tree into a :class:`TactileRaster`.

    ``svg_root`` is an SVG element tree as produced by
    :func:`brailix.frontend.graphics.normalizer.normalize`. ``profile``
    supplies the DPI, default page size, millimetre line width, and braille
    label metrics. ``label_translator`` (a ``text → cells`` callable,
    injected so the backend never imports the text frontend) turns
    ``<text>`` labels into braille dots; without it, labels are warned and
    skipped. Diagnostics go to ``warnings`` when provided; the function
    never raises on bad geometry.
    """
    if svg_root.get("data-bk-error") is not None and warnings is not None:
        warnings.error(
            code="GRAPHICS_SOFT_FAIL",
            message=f"graphic could not be parsed: {svg_root.get('data-bk-error')}",
            source="backend.tactile",
        )

    minx, miny, logw, logh, phys_w_mm, phys_h_mm = _resolve_geometry(
        svg_root, profile, warnings
    )

    px_w = max(1, _round_finite(phys_w_mm * profile.dpi / _MM_PER_INCH))
    px_h = max(1, _round_finite(phys_h_mm * profile.dpi / _MM_PER_INCH))
    px_w, px_h, _clamped = clamp_raster_pixels(
        px_w, px_h, warnings, max_pixels=_MAX_RASTER_PIXELS
    )

    # Effective DPI after any clamp, so the raster's physical metadata
    # (and the BMP pixels-per-metre the renderer stamps) stay accurate.
    eff_dpi = (
        px_w * _MM_PER_INCH / phys_w_mm + px_h * _MM_PER_INCH / phys_h_mm
    ) / 2.0
    raster = TactileRaster.blank(
        px_w,
        px_h,
        dpi=eff_dpi,
        page_width_mm=phys_w_mm,
        page_height_mm=phys_h_mm,
    )
    if record_provenance:
        # Editor highlight: record which pixels each SVG element drew
        # (ARCHITECTURE.md). Opt-in — off for export / headless.
        raster.enable_provenance()

    sx, sy = px_w / logw, px_h / logh
    min_radius = max(
        0, round(profile.min_line_width_mm * eff_dpi / _MM_PER_INCH / 2.0)
    )
    ppm = eff_dpi / _MM_PER_INCH  # pixels per millimetre
    labeler: LabelStamper | None = None
    if label_translator is not None:
        labeler = LabelStamper(
            translate=label_translator,
            dot_radius=max(0, round(profile.braille_dot_radius_mm * ppm)),
            dot_dx=profile.braille_dot_spacing_mm * ppm,
            dot_dy=profile.braille_dot_spacing_mm * ppm,
            cell_dx=profile.braille_cell_spacing_mm * ppm,
        )
    # Texture lines are min-line-width thick; their gaps respect the
    # minimum feature spacing so the pattern stays individually touchable.
    tex_thickness = max(1, round(profile.min_line_width_mm * ppm))
    tex_spacing = tex_thickness + max(1, round(profile.min_feature_spacing_mm * ppm))
    state = _State(
        minx=minx,
        miny=miny,
        sx=sx,
        sy=sy,
        min_radius=min_radius,
        scale=(sx + sy) / 2.0,
        level=255,
        warnings=warnings,
        warned=set(),
        labeler=labeler,
        tex_spacing=tex_spacing,
        tex_thickness=tex_thickness,
        fill_map={},
        labels=[],
        boxes=[],
    )
    _walk(svg_root, raster, state, min_radius, None)
    # BANA touch-separability diagnostics (detection only — never moves the
    # author's geometry): element-to-element spacing, then the deferred labels
    # (label-vs-figure / label-vs-label overlap) which also paints them.
    feature_px = max(1, round(profile.min_feature_spacing_mm * ppm))
    _check_spacing(
        state.boxes, feature_px, profile.min_feature_spacing_mm, ppm, warnings
    )
    _place_labels(state.labels, raster, feature_px, warnings)
    return raster
