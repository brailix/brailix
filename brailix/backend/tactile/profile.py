"""Tactile rendering profile — device-independent adaptation parameters.

A tactile profile is the graphics vertical's small counterpart to a
:class:`~brailix.core.config.BrailleProfile`: it bundles the millimetre
based touch-adaptation knobs (minimum line width, minimum feature
spacing) plus the one device-dependent dial, ``dpi``, and a default page
size. Profiles live as JSON under ``resources/tactile/<name>.json``.

Deliberately **device-independent**: there is no per-embosser model table. Every adaptation
parameter is in millimetres so it survives any device; the renderer turns
millimetres into pixels with the single ``dpi`` knob the user sets to
match their own embossing software. The shipped ``generic`` profile is a
reasonable default that the user can override field by field — "device
independent" does not mean "no defaults".

This loader is intentionally self-contained (it does not reach into the
braille-profile config machinery), so the tactile vertical stays an
independently replaceable component.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass as _dataclass
from dataclasses import fields as _fields
from pathlib import Path as _Path
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.errors import ConfigurationError
from brailix.core.measure import as_positive_finite
from brailix.core.paths import resolve_named_resource

if _TYPE_CHECKING:
    from typing import Any

# resources/ lives at the package root: this file is
# brailix/backend/tactile/profile.py, so parents[2] == the ``brailix``
# package directory (mirrors core.config.loader.PACKAGE_ROOT).
_PACKAGE_ROOT: _Path = _Path(__file__).resolve().parents[2]
_TACTILE_DIR: _Path = _PACKAGE_ROOT / "resources" / "tactile"

DEFAULT_PROFILE = "generic"


@_dataclass(frozen=True, slots=True)
class TactileProfile:
    """Resolved tactile adaptation parameters.

    ``dpi`` is the only device-dependent value (user matches it to their
    embosser software); everything else is in millimetres so it is device
    independent. ``min_feature_spacing_mm`` is carried now but only
    consumed once the touch-spacing rules land (a later phase) — declaring
    it here keeps the profile schema stable.
    """

    name: str
    dpi: float
    page_width_mm: float
    page_height_mm: float
    min_line_width_mm: float
    min_feature_spacing_mm: float
    # Braille label metrics (mm): raised-dot size, within-cell dot spacing,
    # and cell-to-cell advance. Defaults follow standard Library-of-Congress
    # braille so labels stay readable at physical size.
    braille_dot_radius_mm: float
    braille_dot_spacing_mm: float
    braille_cell_spacing_mm: float
    # Line-to-line advance (mm) for braille text laid out on a page — the
    # distance from one line's dot-1 to the next line's dot-1. Standard
    # Library-of-Congress interline spacing is ~10 mm (independent of the
    # within-cell dot spacing). Consumed by the mixed-page compositor
    # (:mod:`brailix.backend.tactile.page`) to stack text lines; labels on a
    # single graphic don't use it. Defaulted so older profiles keep loading.
    braille_line_spacing_mm: float = 10.0

    def __post_init__(self) -> None:
        """Every metric is a finite ``float`` greater than zero.

        The invariant belongs to the *type*, not only to
        :func:`load_tactile_profile`: everything downstream — the mm→px
        transform, the page compositor, the raster cap — multiplies and rounds
        these values, and a profile built in code (an editor's settings pane, a
        test, a caller passing ``translate_graphic(tactile_profile=...)`` an
        object rather than a name) reaches all of that without touching the
        loader. Checking here is what makes "positive and finite" a property a
        consumer may rely on instead of a habit the JSON path happens to keep.

        The converted value is **stored**, not just inspected. ``_check_positive``
        accepts anything ``float()`` accepts — which is what lets the loader take
        a quoted ``"100"`` out of a hand-edited JSON file — so validating without
        keeping the result let a value that merely *converts* live on in a field
        declared ``float``: ``TactileProfile(dpi="100", ...)`` passed every check
        and then raised ``TypeError`` in ``profile.dpi / 25.4``, deep in page
        composition. Assigning through ``object.__setattr__`` because the
        dataclass is frozen and this runs during its own construction — the same
        move :class:`brailix.core.errors.Warning` makes to freeze its anchor.
        """
        for f in _fields(self):
            if f.name == "name":
                continue
            object.__setattr__(
                self, f.name, _check_positive(getattr(self, f.name), f.name)
            )


def _check_positive(value: Any, field: str, prefix: str = "") -> float:
    """``value`` as a ``float``, or :class:`ConfigurationError` if it is not a
    finite number greater than zero.

    The arithmetic is :func:`brailix.core.measure.as_positive_finite`, shared
    with :class:`~brailix.ir.tactile.TactileRaster`, which checks the same
    measurements on a raster built in code. What stays here is the diagnosis:
    a profile comes out of a JSON file, so a bad value is a *configuration*
    error naming that file, not the ``ValueError`` a caller's own bad argument
    earns. See that module for why each step is there; the rest of this
    docstring records what the checks cost when they were missing.

    ``NaN`` and ``Infinity`` are the reason this is not a bare ``<= 0`` test.
    Both are ordinary ``float`` values that JSON can carry (Python's decoder
    accepts the ``NaN`` / ``Infinity`` literals by default), and both slip
    through a comparison: every ``<=`` against ``NaN`` is false, and infinity
    genuinely is greater than zero. What they fail is later and elsewhere —
    ``round(nan)`` raises :class:`ValueError`, ``int(inf)``
    :class:`OverflowError` — deep in a transform or an encoder, long after the
    load this loader promises to fail at.

    ``bool`` is refused for the neighbouring reason: it is an ``int`` subclass,
    so ``"dpi": true`` would quietly resolve to a 1-DPI profile.
    """
    return as_positive_finite(
        value,
        f"{prefix}tactile profile field {field!r}",
        error=ConfigurationError,
    )


def _require_positive(value: Any, field: str, path: _Path) -> float:
    """:func:`_check_positive` with the offending file named, so a bad JSON
    value points at the profile that carries it."""
    return _check_positive(value, field, prefix=f"{path}: ")


def load_tactile_profile(name: str = DEFAULT_PROFILE) -> TactileProfile:
    """Load the tactile profile named ``name`` from ``resources/tactile``.

    Raises :class:`~brailix.core.errors.ConfigurationError` for a missing
    file, invalid JSON, an out-of-range parameter, or a ``name`` that is a
    path rather than a name — the same failure contract the braille-profile
    loader uses, so a front-end can surface one error type. The name check is
    :func:`~brailix.core.paths.resolve_named_resource`, shared with that
    loader: ``"../../config/device"`` used to be read and parsed from outside
    ``resources/tactile`` entirely, and an absolute name from anywhere on the
    filesystem.
    """
    path = resolve_named_resource(_TACTILE_DIR, name, "tactile profile")
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = _json.load(f)
    except FileNotFoundError as e:
        raise ConfigurationError(
            f"{path}: tactile profile {name!r} not found"
        ) from e
    except OSError as e:
        raise ConfigurationError(f"{path}: unreadable ({e})") from e
    except _json.JSONDecodeError as e:
        raise ConfigurationError(f"{path}: invalid JSON ({e})") from e
    if not isinstance(payload, dict):
        raise ConfigurationError(
            f"{path}: top level must be a JSON object, "
            f"got {type(payload).__name__}"
        )
    return TactileProfile(
        name=str(payload.get("name", name)),
        dpi=_require_positive(payload.get("dpi"), "dpi", path),
        page_width_mm=_require_positive(
            payload.get("page_width_mm"), "page_width_mm", path
        ),
        page_height_mm=_require_positive(
            payload.get("page_height_mm"), "page_height_mm", path
        ),
        min_line_width_mm=_require_positive(
            payload.get("min_line_width_mm"), "min_line_width_mm", path
        ),
        # Spacing is reserved for a later phase; default to the line width
        # if a profile omits it so the schema stays forgiving.
        min_feature_spacing_mm=_require_positive(
            payload.get(
                "min_feature_spacing_mm", payload.get("min_line_width_mm")
            ),
            "min_feature_spacing_mm",
            path,
        ),
        # Braille label metrics default to standard values when omitted, so
        # older profiles keep loading.
        braille_dot_radius_mm=_require_positive(
            payload.get("braille_dot_radius_mm", 0.75),
            "braille_dot_radius_mm",
            path,
        ),
        braille_dot_spacing_mm=_require_positive(
            payload.get("braille_dot_spacing_mm", 2.5),
            "braille_dot_spacing_mm",
            path,
        ),
        braille_cell_spacing_mm=_require_positive(
            payload.get("braille_cell_spacing_mm", 6.0),
            "braille_cell_spacing_mm",
            path,
        ),
        braille_line_spacing_mm=_require_positive(
            payload.get("braille_line_spacing_mm", 10.0),
            "braille_line_spacing_mm",
            path,
        ),
    )


def list_tactile_profiles() -> list[str]:
    """Names of the built-in tactile profiles (``*.json`` stems), sorted —
    what a settings dropdown can offer."""
    if not _TACTILE_DIR.is_dir():
        return []
    return sorted(p.stem for p in _TACTILE_DIR.glob("*.json"))
