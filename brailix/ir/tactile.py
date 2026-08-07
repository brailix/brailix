"""Tactile raster IR: the device-independent dot grid the tactile
backend writes and the tactile renderers consume.

This is the tactile-graphics vertical's counterpart to
:mod:`brailix.ir.braille` — a **Product IR** (what the backend produces
for the renderer to encode), not the *semantic* IR. The semantic IR for
graphics is the SVG tree itself (an
:class:`xml.etree.ElementTree.Element`; see
:mod:`brailix.frontend.graphics`), exactly as MathML is the math IR and
MusicXML the music IR. The tactile backend rasterizes that SVG tree into
a :class:`TactileRaster` (a 2-D grid of *raise levels*), and the tactile
renderers encode the raster into bytes (``.bmp``) or a refreshable-display
preview (U+2800 braille).

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

The raster carries its own physical ``page_width_mm`` / ``page_height_mm``
(plus the ``dpi`` it was produced at) so a renderer can stamp correct
physical-size metadata without consulting the profile — the renderer is a
dumb encoder, mirroring how :class:`~brailix.ir.braille.BrailleCell`
already carries everything the unicode / BRF renderers need.

Physical size
-------------

**The millimetre pair is the single source of truth for how big the page
is.** ``width``/``height`` say how many pixels cover it, so the density
follows: ``width / page_width_mm`` pixels per millimetre, per axis. Every
encoder derives what it stamps from those two facts
(:mod:`brailix.renderer._raster_encoding`), which is what makes the ``.bmp``,
``.png`` and ``.pdf`` of one raster the same physical page.

``dpi`` is *nominal*: the resolution the raster was rasterized at, kept as
metadata (and as the number a caller reads back to ask "how fine is this?").
It is a single scalar, so it cannot describe a grid whose two axes ended up
at slightly different densities — pixel counts round, and the raster cap can
scale a page down — which is exactly why it is not what an encoder consults.
An encoder that reads it instead disagrees with one that reads the
millimetres whenever the two are not exactly in step, and the same drawing
embosses at one size and prints at another.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import Any as _Any

from brailix.core.measure import as_positive_finite

MAX_LEVEL = 255

# Bit depths a tactile raster can be encoded at: an 8-bit grayscale master
# (grayscale = dot height) or a 1-bit bilevel degradation. Shared with the
# encoders so "which depths exist" is stated once
# (:func:`brailix.renderer.bmp.raster_to_bmp` validates its own argument
# against it).
SUPPORTED_BIT_DEPTHS = frozenset({1, 8})


# The physical pair's own check is :func:`brailix.core.measure.as_positive_finite`
# — shared with
# :class:`~brailix.backend.tactile.profile.TactileProfile`, which validates the
# same measurements coming out of a JSON file. Only the *diagnosis* differs and
# stays here: a profile's bad value is a ``ConfigurationError`` naming the file,
# while a raster is built in code, so a bad value is a caller's bug and stays a
# ``ValueError`` like the dimension checks below.


def _pixel_count(value: _Any, field_name: str) -> int:
    """``value`` as a non-negative ``int``, or ``ValueError`` if it is neither.

    The pixel pair is not checked the way the physical pair is. Millimetres and
    DPI are *measurements*, so anything that converts to a finite positive float
    is a legitimate way to spell one; ``width`` / ``height`` are **counts of
    array elements**, so only an ``int`` is a value at all — and each wrong type
    fails somewhere else, late and in a language that names neither the field
    nor the raster:

    * ``True`` is an ``int`` subclass, so a bare ``< 0`` test waves it through
      as a **one-pixel** axis and the caller gets a 1×N raster instead of an
      error (:class:`~brailix.input.InputLimits` rejects bools first for the
      same reason);
    * ``1.5`` passes the same test and dies two lines on in
      ``bytearray(width * height)`` with "cannot convert 'float' object to
      bytearray";
    * ``"4"`` doesn't even reach that far — the comparison itself raises
      ``TypeError: '<' not supported between instances of 'str' and 'int'``.

    Zero stays legal: a zero-area raster is a valid IR value (see
    :meth:`TactileRaster.require_renderable`, which is where encoding refuses
    it). Only negatives are rejected.
    """
    # ``bool`` first: it *is* an ``int`` subclass, so the isinstance check
    # below would let True / False straight through.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"raster {field_name} must be an int, got "
            f"{type(value).__name__} ({value!r})"
        )
    if value < 0:
        raise ValueError(f"raster {field_name} must be >= 0, got {value!r}")
    return value


@_dataclass(slots=True)
class TactileRaster:
    """A 2-D grid of raise levels plus the physical metadata a renderer
    needs to emit a correctly-scaled image.

    ``data`` is a flat row-major ``bytearray`` of length
    ``width * height`` (each byte a raise level ``0..255``). It defaults
    to an all-flat grid of the right size when omitted, so callers can
    write ``TactileRaster(w, h, dpi=..., page_width_mm=..., ...)`` and
    then paint into it. A read-only bytes-like (``bytes`` / ``memoryview``)
    of the right length is accepted and copied; anything else is a
    ``ValueError`` naming the field, never a blank grid — see
    :meth:`__post_init__`.

    The page is ``page_width_mm × page_height_mm`` (both > 0); see the
    module docstring for why that pair, and not ``dpi``, is what an encoder
    reads.
    """

    width: int
    height: int
    dpi: float
    page_width_mm: float
    page_height_mm: float
    data: bytearray = _field(default_factory=bytearray)
    # Which encoding this raster is meant to be written at (8 = grayscale
    # master, 1 = bilevel). The data is always *stored* as 0..255 raise levels
    # regardless — this says how to pack them, and
    # :class:`~brailix.renderer.bmp.BmpRenderer` reads it, so
    # ``GraphicResult.render("bmp")`` on a 1-bit raster produces a 1-bit BMP.
    # A renderer constructed with its own explicit depth overrides it. (The
    # depth-less encoders — PNG, PDF — are grayscale by format and ignore it.)
    bit_depth: int = 8
    # Optional element → touched-pixel provenance (flat indices), for the
    # editor's cross-pane highlight. ``None``
    # = not recording (the default; export / headless pay nothing). Enabled
    # by :meth:`enable_provenance`; the backend tags pixels via
    # :meth:`begin_element`. ``compare=False`` so it's metadata, not identity.
    provenance: dict[str, set[int]] | None = _field(
        default=None, compare=False, repr=False
    )
    _owner: str | None = _field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Check every field a downstream encoder trusts, at construction.

        A raster is a *boundary* object — the backend hands it to renderers,
        an editor builds one to preview, a caller may build one by hand — and
        each field below is read without a second look somewhere downstream.
        A page size of ``0`` or ``NaN`` reaches a PDF ``MediaBox``; a
        non-finite ``dpi`` raises inside the BMP header; a ``True`` width is a
        one-pixel axis nobody asked for; a ``bytes`` ``data`` of the right
        length passes for a grid until the first :meth:`set_raise` fails on it.
        Every one of those surfaces far from the line that built the raster,
        and some only when a reader opens the file — so they are refused here
        instead.
        """
        self.width = _pixel_count(self.width, "width")
        self.height = _pixel_count(self.height, "height")
        self.dpi = as_positive_finite(self.dpi, "dpi")
        self.page_width_mm = as_positive_finite(self.page_width_mm, "page_width_mm")
        self.page_height_mm = as_positive_finite(
            self.page_height_mm, "page_height_mm"
        )
        # ``bool`` first, for the reason the pixel pair rejects it: ``True ==
        # 1``, so a bare membership test accepts it as the 1-bit depth and
        # stores a *bool* in a field every encoder reads back as an int. The
        # ``int`` test is not redundant either — membership on a frozenset
        # hashes its operand, so an unhashable ``bit_depth`` (a list, a dict)
        # left this constructor as ``TypeError: unhashable type`` instead of
        # the field-level ``ValueError`` the rest of the dataclass raises.
        if isinstance(self.bit_depth, bool) or not isinstance(self.bit_depth, int):
            raise ValueError(
                f"bit_depth must be an int, got "
                f"{type(self.bit_depth).__name__} ({self.bit_depth!r})"
            )
        if self.bit_depth not in SUPPORTED_BIT_DEPTHS:
            raise ValueError(
                f"bit_depth must be one of {sorted(SUPPORTED_BIT_DEPTHS)}, "
                f"got {self.bit_depth!r}"
            )
        # The TYPE first, before anything reads a length. Testing ``if not
        # self.data`` instead reads "falsy" as "omitted": ``None``, ``False``,
        # ``0``, ``[]`` and ``{}`` all match it, so a value that contradicts
        # the declared type — an unset variable, a deserialiser returning
        # ``None`` for a missing key — is silently rewritten into a blank grid
        # of the right size. The call then *succeeds* and produces an all-flat
        # page, which is worse than failing: an embosser run comes back empty
        # and nothing upstream ever reported a fault. The other half is as bad
        # in the other direction — a non-falsy value with no ``len()`` (an
        # ``object()``, an ``int``) raises a bare ``TypeError`` from the length
        # check, naming neither the field nor the raster.
        if isinstance(self.data, bytearray):
            grid = self.data
        elif isinstance(self.data, (bytes, memoryview)):
            # Right length, wrong mutability: a bytes / memoryview passes the
            # length check and every read, then makes set_raise raise on its
            # first write. Copy into the writable grid the type promises.
            grid = bytearray(self.data)
        else:
            raise ValueError(
                f"data must be a bytearray (or a bytes-like to copy from), "
                f"got {type(self.data).__name__}"
            )
        expected = self.width * self.height
        if not grid:
            # Omitted — the ``bytearray()`` default — so allocate the flat grid
            # the docstring promises. An explicitly passed empty bytes-like is
            # indistinguishable from omission and means the same thing.
            grid = bytearray(expected)
        elif len(grid) != expected:
            raise ValueError(
                f"data length {len(grid)} does not match "
                f"{self.width}x{self.height} = {expected}"
            )
        self.data = grid

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
        """Construct an all-flat raster of the given size.

        The grid is deliberately *not* allocated here: an omitted ``data`` is
        what :meth:`__post_init__` fills with ``bytearray(width * height)``,
        after the field checks. Allocating in this factory would put the
        allocator ahead of those checks, so the same illegal ``width`` that
        ``TactileRaster(width=...)`` refuses with a ``ValueError`` naming the
        field comes back from here as whichever error ``bytearray`` happens to
        raise — ``TypeError: cannot convert 'float' object to bytearray`` for
        ``1.5``, ``TypeError: string argument without an encoding`` for
        ``"4"``, a bare ``negative count`` for ``-1``. One type, one
        construction contract: every caller can catch ``ValueError`` and show
        the field it names, whichever way the raster was built.
        """
        return cls(
            width=width,
            height=height,
            dpi=dpi,
            page_width_mm=page_width_mm,
            page_height_mm=page_height_mm,
            bit_depth=bit_depth,
        )

    def require_renderable(self) -> None:
        """Raise ``ValueError`` if this raster can't be encoded to an image.

        Construction deliberately allows a zero-width / zero-height raster
        (``__post_init__`` rejects negative sizes, not empty ones — a 0-sized
        blank grid is a valid IR value the ``max(1, round(...))`` callers rely
        on, and the *physical* fields it checks stay positive either way). But a
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
