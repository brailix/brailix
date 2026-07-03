"""Tactile raster IR: the device-independent dot grid the tactile
backend writes and the tactile renderers consume.

This is the tactile-graphics vertical's counterpart to
:mod:`brailix.ir.braille` — a backend **product**, not a semantic IR.
The semantic IR for graphics is the SVG tree itself (an
:class:`xml.etree.ElementTree.Element`; see
:mod:`brailix.frontend.graphics`), exactly as MathML is the math IR and
MusicXML the music IR. The tactile backend rasterizes that SVG tree into
a :class:`TactileRaster` (a 2-D grid of *raise levels*), and the tactile
renderers encode the raster into bytes (``.bmp``) or a refreshable-display
preview (U+2800 braille, a later phase).

Coordinate / value model
------------------------

* Origin top-left, ``x`` increasing rightward, ``y`` increasing
  downward — SVG's own convention, so the rasterizer needs no flip.
* Row-major: the value at ``(x, y)`` is ``data[y * width + x]``.
* Each value is a **raise level** in ``0..255`` where ``0`` = flat (no
  raised dot) and ``255`` = fully raised. An 8-bit grayscale master keeps
  the full range ("grayscale = dot height" for height-modulating
  embossers such as ViewPlus Tiger); a 1-bit device thresholds it
  (raised / not raised). The *renderer* owns the byte layout and polarity
  (e.g. raised → black pixel for swell paper and dot embossers); the
  raster stays device-independent.

The raster carries its own ``dpi`` and physical ``page_width_mm`` /
``page_height_mm`` so a renderer can stamp correct physical-size metadata
(BMP pixels-per-metre) without consulting the profile — the renderer is a
dumb encoder, mirroring how :class:`~brailix.ir.braille.BrailleCell`
already carries everything the unicode / BRF renderers need.

See ``ARCHITECTURE.md`` for where this sits in the
data flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LEVEL = 255


@dataclass(slots=True)
class TactileRaster:
    """A 2-D grid of raise levels plus the physical metadata a renderer
    needs to emit a correctly-scaled image.

    ``data`` is a flat row-major ``bytearray`` of length
    ``width * height`` (each byte a raise level ``0..255``). It defaults
    to an all-flat grid of the right size when omitted, so callers can
    write ``TactileRaster(w, h, dpi=..., page_width_mm=..., ...)`` and
    then paint into it.
    """

    width: int
    height: int
    dpi: float
    page_width_mm: float
    page_height_mm: float
    data: bytearray = field(default_factory=bytearray)
    # Informational: which encoding the backend intends (8 = grayscale
    # master, 1 = bilevel). The data is always stored as 0..255 raise
    # levels regardless; the renderer decides how to pack the bytes.
    bit_depth: int = 8
    # Optional element → touched-pixel provenance (flat indices), for the
    # editor's cross-pane highlight (ARCHITECTURE.md). ``None``
    # = not recording (the default; export / headless pay nothing). Enabled
    # by :meth:`enable_provenance`; the backend tags pixels via
    # :meth:`begin_element`. ``compare=False`` so it's metadata, not identity.
    provenance: dict[str, set[int]] | None = field(
        default=None, compare=False, repr=False
    )
    _owner: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError(
                f"raster dimensions must be non-negative, got "
                f"{self.width}x{self.height}"
            )
        expected = self.width * self.height
        if not self.data:
            self.data = bytearray(expected)
        elif len(self.data) != expected:
            raise ValueError(
                f"data length {len(self.data)} does not match "
                f"{self.width}x{self.height} = {expected}"
            )

    @classmethod
    def blank(
        cls,
        width: int,
        height: int,
        *,
        dpi: float,
        page_width_mm: float,
        page_height_mm: float,
        bit_depth: int = 8,
    ) -> TactileRaster:
        """Construct an all-flat raster of the given size."""
        return cls(
            width=width,
            height=height,
            dpi=dpi,
            page_width_mm=page_width_mm,
            page_height_mm=page_height_mm,
            data=bytearray(width * height),
            bit_depth=bit_depth,
        )

    def require_renderable(self) -> None:
        """Raise ``ValueError`` if this raster can't be encoded to an image.

        Construction deliberately allows a zero-width / zero-height raster
        (``__post_init__`` only rejects negatives — a 0-sized blank grid is a
        valid IR value the ``max(1, round(...))`` callers rely on). But a
        zero-area raster has no valid image encoding: a PNG IHDR, a PDF
        MediaBox and a BMP header all require positive dimensions. Renderers
        call this up front so the failure is an explicit ``ValueError`` (like
        :func:`rasters_to_pdf` on an empty sequence) rather than a silently
        corrupt byte stream that only fails when the reader opens it."""
        if self.width == 0 or self.height == 0:
            raise ValueError(
                f"cannot render a zero-area raster ({self.width}x{self.height}"
                "): image formats require positive dimensions"
            )

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> int:
        """Raise level at ``(x, y)``; ``0`` for out-of-bounds reads so
        callers can probe freely without guarding edges."""
        if not self.in_bounds(x, y):
            return 0
        return self.data[y * self.width + x]

    def set_raise(self, x: int, y: int, level: int) -> None:
        """Raise ``(x, y)`` to at least ``level`` (clamped to ``0..255``).

        Uses *max*, never overwrite, so overlapping strokes can only add
        height — a line crossing another never punches a flat gap through
        it. Out-of-bounds writes are ignored (clipping to the page).
        """
        if not self.in_bounds(x, y):
            return
        if level > MAX_LEVEL:
            level = MAX_LEVEL
        elif level < 0:
            level = 0
        i = y * self.width + x
        if level > self.data[i]:
            self.data[i] = level
        # Record provenance even when the max-guard above kept an existing
        # higher pixel: the current element still *touched* this pixel, so a
        # later highlight of that element should include it.
        if self.provenance is not None and self._owner is not None:
            self.provenance.setdefault(self._owner, set()).add(i)

    # ---------------- Provenance (editor highlight) ------------------

    def enable_provenance(self) -> None:
        """Start recording element → touched-pixel provenance.

        Opt-in (the editor calls it); export / headless never do, so the hot
        :meth:`set_raise` path stays free of bookkeeping by default."""
        if self.provenance is None:
            self.provenance = {}

    def begin_element(self, gid: str | None) -> None:
        """Attribute subsequent :meth:`set_raise` pixels to element ``gid``
        (a no-op unless provenance recording is enabled)."""
        self._owner = gid

    def raised_count(self, threshold: int = 1) -> int:
        """How many cells are raised at or above ``threshold`` — a cheap
        handle for tests / sanity checks (``> 0`` means "something was
        drawn")."""
        return sum(1 for v in self.data if v >= threshold)
