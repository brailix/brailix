"""Resource limits for a graphic's asset reads — the ``<image>`` href budget.

The tactile backend resolves an SVG ``<image>`` ``href`` to bytes at rasterize
time (a ``data:`` URI or a filesystem path — see
:func:`brailix.backend.tactile._image._resolve_href`), and for a long time
that read had two properties that made it an attack surface (adversarial
review 2026-10-31, red-3 F1, blue-2 CONFIRMED P0):

* **unbounded** — ``read_bytes()`` with no ceiling and ``b64decode`` with no
  length cap, so a single href could pull a multi-hundred-megabyte file into
  process memory the instant a document was opened and auto-compiled, from
  any absolute path the document cared to name;
* **chatty on failure** — the ``OSError`` text (or its absence) rode into the
  warning message, turning every compile into a file-existence probe: a
  missing path, an unreadable one and a non-image one were three visibly
  different messages.

:class:`GraphicAssetLimits` is the budget that closes both, in the shape the
input layer already established for whole documents
(:class:`~brailix.input.limits.InputLimits` — the same philosophy, the same
``fstat``-on-the-open-descriptor reading discipline, the same generous-default
/ tighten-for-untrusted / :meth:`unlimited`-opts-out stance). Every byte an
``<image>`` href delivers goes through it: a file read through
:meth:`GraphicAssetLimits.read_bounded`, a ``data:`` URI through
:meth:`GraphicAssetLimits.check_uri_payload` before decoding and
:meth:`GraphicAssetLimits.check_bytes` after, and the decoded pixels through
:meth:`GraphicAssetLimits.check_pixels` before Pillow allocates the buffer.

The pixel gate is the backend's copy of the dims door the graphics frontend's
``image`` source adapter already keeps on its own path (a path only enters the
tree there once a decodable size could be read). Pass-through routes — a
``graphic`` / ``graphic-svg`` fence, a re-opened serialized project — have no
such door in front of the backend, so the backend keeps one itself.
"""

from __future__ import annotations

import os as _os
import stat as _stat
import sys as _sys
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

# The backend does not import the input layer (ARCHITECTURE#arch-layers), so
# this leaf re-states the reading discipline rather than reusing
# ``InputLimits.read_bounded``: fstat the open descriptor (not the path),
# refuse a non-regular file, read one byte past the ceiling, verify. Like
# ``_raster_cap``, it imports nothing from this package, so the package init
# can pull it in without a cycle.
from brailix.backend.tactile._raster_cap import _MAX_RASTER_PIXELS
from brailix.core.errors import BrailixError

# 32 MiB per asset. A tactile source image — line art PNG, a photo for the
# ``edge`` mode, a hand-authored SVG — is orders of magnitude smaller; this
# only stops the pathological case. Deliberately far under the input layer's
# 512 MiB whole-document budget: a document can carry many images, and each
# one is decoded (not merely stored), so per-asset headroom and per-document
# headroom are different quantities.
_DEFAULT_MAX_ASSET_BYTES = 32 * 1024 * 1024

# Matches the raster cap (``_raster_cap._MAX_RASTER_PIXELS``) on purpose: a
# decoded source image is resampled down to its on-page device box before it
# is sampled, and that box is itself capped at the same order — so a source
# admitted above the cap could only ever be downscaled, and the working
# buffer it cost to find that out is exactly what this bound refuses.
_DEFAULT_MAX_ASSET_PIXELS = _MAX_RASTER_PIXELS


class GraphicAssetTooLargeError(BrailixError):
    """Raised when a graphic asset exceeds a :class:`GraphicAssetLimits`
    ceiling.

    Carries the human-readable ``str`` plus the machine-usable ``kind``
    (``"asset_bytes"`` / ``"asset_pixels"``), the offending ``actual`` size
    and the ``limit`` it crossed — the same shape
    :class:`~brailix.input.InputTooLargeError` gives a service, so a
    deployment can log a precise "asset too large" line.

    Note for callers surfacing this to users: whether an asset was refused
    for size or failed to load is *not* a distinction to display — a size
    refusal answers "a file this big exists at that path", which is a
    file-existence probe in exactly the way a read failure is. The tactile
    backend therefore reports both through one fixed warning message.
    """

    def __init__(self, kind: str, actual: int, limit: int, *, detail: str = ""):
        unit = "bytes" if kind == "asset_bytes" else "pixels"
        msg = (
            f"graphic asset exceeds the {kind} limit: "
            f"{actual} {unit} > {limit} {unit}"
        )
        if detail:
            msg = f"{msg} ({detail})"
        super().__init__(msg)
        self.kind = kind
        self.actual = actual
        self.limit = limit


@_dataclass(frozen=True, slots=True)
class GraphicAssetLimits:
    """A read budget for one graphic asset (one ``<image>`` href).

    * ``max_asset_bytes`` — the ceiling on the bytes one href may deliver,
      checked on the open descriptor before and during the read (files) and
      around the decode (``data:`` URIs). Always on; the primary memory guard.
    * ``max_asset_pixels`` — the ceiling on a decoded image's pixel count,
      checked from the image header (before the decode allocates). Bounds the
      decode of a small-but-huge image — a few MiB of PNG can declare tens of
      gigapixels — and, for the resvg path, of the PNG an external SVG
      rasterizes to.
    * ``allow_filesystem`` — whether a non-``data:`` href may touch the
      filesystem at all. ``False`` refuses every bare path before any read
      attempt, so in-document assets (``data:`` URIs, e.g. a ``.docx``
      picture the resolver inlined) keep working while nothing on disk is
      probed.

    Frozen so an instance can be shared freely (one compile-wide policy,
    threaded :func:`brailix.translate_graphic` → ``rasterize`` →
    ``_resolve_href``). The default is desktop-first, matching
    :data:`~brailix.input.DEFAULT_INPUT_LIMITS` in spirit: a user authoring a
    graphic beside their own source file is the documented use of the
    filesystem fallback
    (:class:`~brailix.core.protocols.GraphicAssetResolver` — "one authored by
    hand sits beside the source file"), so reads stay allowed and the ceilings
    sit far above anything real. A caller compiling documents it was sent —
    the open-someone-else's-file case, which is how the P0 was reached —
    passes ``allow_filesystem=False``; a trusted local caller that wants the
    raw pre-limit behaviour passes :meth:`unlimited`.
    """

    max_asset_bytes: int = _DEFAULT_MAX_ASSET_BYTES
    max_asset_pixels: int = _DEFAULT_MAX_ASSET_PIXELS
    allow_filesystem: bool = True

    def __post_init__(self) -> None:
        """Reject a ceiling that isn't a non-negative ``int`` and a switch
        that isn't a ``bool``.

        The same arguments as :meth:`InputLimits.__post_init__`: a negative
        ceiling is not a smaller budget but one no asset can satisfy, failing
        far from the construction that got it wrong; a ``bool`` sneaking past
        the ``int`` check would make ``True`` a one-byte ceiling; and
        ``allow_filesystem`` given as a truthy string / int is a caller
        confusing the switch with a magnitude. Zero is allowed: "only empty
        assets pass" is a coherent, if extreme, policy.
        """
        for name in ("max_asset_bytes", "max_asset_pixels"):
            value = getattr(self, name)
            # ``bool`` first: it *is* an ``int`` subclass, so the isinstance
            # check below would wave True/False straight through.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"GraphicAssetLimits.{name} must be an int, got "
                    f"{type(value).__name__} ({value!r})"
                )
            if value < 0:
                raise ValueError(
                    f"GraphicAssetLimits.{name} must be >= 0, got "
                    f"{value!r} — a negative ceiling rejects every image "
                    f"rather than loosening the budget (use "
                    f"GraphicAssetLimits.unlimited() for no ceiling)"
                )
        if not isinstance(self.allow_filesystem, bool):
            raise ValueError(
                f"GraphicAssetLimits.allow_filesystem must be a bool, got "
                f"{type(self.allow_filesystem).__name__} "
                f"({self.allow_filesystem!r})"
            )

    @classmethod
    def unlimited(cls) -> GraphicAssetLimits:
        """A :class:`GraphicAssetLimits` that never rejects anything.

        For a trusted local caller (a desktop tool reading the user's own
        files) that wants the raw pre-limit behaviour, filesystem fallback
        included. Implemented with ``sys.maxsize`` sentinels so the gates are
        plain comparisons with no special-casing."""
        return cls(
            max_asset_bytes=_sys.maxsize,
            max_asset_pixels=_sys.maxsize,
            allow_filesystem=True,
        )

    def check_bytes(self, n: int, *, detail: str = "") -> None:
        """Reject ``n`` bytes if larger than ``max_asset_bytes``.

        The post-decode gate for an inline payload: the bytes exist, so count
        them exactly."""
        if n > self.max_asset_bytes:
            raise GraphicAssetTooLargeError(
                "asset_bytes", n, self.max_asset_bytes, detail=detail
            )

    def check_uri_payload(self, chars: int) -> None:
        """Reject a ``data:`` URI payload too long to be worth decoding.

        The pre-decode gate: base64 inflates by 4/3 (four characters carry
        three bytes) and percent-encoding only shrinks (three characters
        become one), so one bound on the *character* count covers both
        encodings — a huge inline payload is refused without first being
        materialized as an even huger byte string. Exact enforcement of the
        byte ceiling still belongs to :meth:`check_bytes` after the decode;
        this is the cheap refuse-by-arithmetically-cannot-fit."""
        if chars > self.max_asset_bytes * 4 // 3 + 8:
            raise GraphicAssetTooLargeError(
                "asset_bytes", chars, self.max_asset_bytes,
                detail="data URI payload characters",
            )

    def check_pixels(self, width: int, height: int) -> None:
        """Reject a decoded image larger than ``max_asset_pixels``.

        The gate must run on the header dimensions, before any pixel data is
        touched: Pillow allocates the full pixel buffer the moment decoding
        begins, so a cap applied after the decode bounds nothing. Called from
        :func:`brailix.backend.tactile._image.load_tactile_image` on every
        route — a direct file href, a ``data:`` URI, and the PNG an external
        SVG rendered to — which is what extends the frontend ``image``
        adapter's own dims door to the pass-through routes that lack it."""
        n = width * height
        if n > self.max_asset_pixels:
            raise GraphicAssetTooLargeError(
                "asset_pixels", n, self.max_asset_pixels,
                detail=f"{width}x{height}",
            )

    def read_bounded(self, href: str) -> bytes:
        """Read the file ``href`` names, consuming at most ``max_asset_bytes``.

        The same discipline as :meth:`InputLimits.read_bounded
        <brailix.input.limits.InputLimits.read_bounded>`:

        * ``fstat`` on the **open descriptor** describes the object being
          read, not whatever the name resolves to a moment later — a path
          swapped to a different file, or a symlink repointed after a check,
          cannot smuggle a larger one in;
        * the read itself asks for one byte past the ceiling, so even a file
          growing *while* it is being read stops there;
        * a non-regular file is refused outright — ``st_size`` is meaningless
          for a FIFO or a device, which is the size gate reading as "empty,
          go ahead" on the one input that can never be bounded by it.

        Returns the file's bytes. Raises :class:`GraphicAssetTooLargeError`
        when the content exceeds the ceiling, and propagates
        :class:`OSError` / :class:`FileNotFoundError` (a missing path, an
        unreadable one) as an ordinary read would — classifying those apart
        is deliberately not this method's job, for the oracle reason given
        on :class:`GraphicAssetTooLargeError`."""
        with _Path(href).open("rb") as fp:
            info = _os.fstat(fp.fileno())
            if not _stat.S_ISREG(info.st_mode):
                raise GraphicAssetTooLargeError(
                    "asset_bytes",
                    0,
                    self.max_asset_bytes,
                    detail=f"{href} is not a regular file",
                )
            if info.st_size > self.max_asset_bytes:
                raise GraphicAssetTooLargeError(
                    "asset_bytes", info.st_size, self.max_asset_bytes,
                    detail=href,
                )
            # One past the ceiling: enough to prove it was exceeded without
            # loading a byte more than that proof needs. ``unlimited()``
            # spells its ceiling ``sys.maxsize``, and ``read(maxsize + 1)``
            # overflows the index-sized argument — there is nothing to bound
            # there anyway, so read straight through.
            if self.max_asset_bytes >= _sys.maxsize:
                data = fp.read()
            else:
                data = fp.read(self.max_asset_bytes + 1)
        if len(data) > self.max_asset_bytes:
            raise GraphicAssetTooLargeError(
                "asset_bytes", len(data), self.max_asset_bytes,
                detail=f"{href} (grew while being read)",
            )
        return data


# The default policy applied when a caller passes no explicit ``limits`` —
# the graphic-asset counterpart of
# :data:`~brailix.input.DEFAULT_INPUT_LIMITS`. Ceilings generous enough that
# a desktop author never notices them; the filesystem switch on, because the
# author's own image beside the source is the documented use of the fallback.
DEFAULT_GRAPHIC_ASSET_LIMITS = GraphicAssetLimits()
