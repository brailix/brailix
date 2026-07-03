"""Tactile rendering profile — device-independent adaptation parameters.

A tactile profile is the graphics vertical's small counterpart to a
:class:`~brailix.core.config.BrailleProfile`: it bundles the millimetre
based touch-adaptation knobs (minimum line width, minimum feature
spacing) plus the one device-dependent dial, ``dpi``, and a default page
size. Profiles live as JSON under ``resources/tactile/<name>.json``.

Deliberately **device-independent** (``ARCHITECTURE.md`` / §7): there is no per-embosser model table. Every adaptation
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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brailix.core.errors import ConfigurationError

# resources/ lives at the package root: this file is
# brailix/backend/tactile/profile.py, so parents[2] == the ``brailix``
# package directory (mirrors core.config.loader.PACKAGE_ROOT).
_PACKAGE_ROOT: Path = Path(__file__).resolve().parents[2]
_TACTILE_DIR: Path = _PACKAGE_ROOT / "resources" / "tactile"

DEFAULT_PROFILE = "generic"


@dataclass(frozen=True, slots=True)
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


def _require_positive(value: Any, field: str, path: Path) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{path}: tactile profile field {field!r} must be a number, "
            f"got {value!r}"
        ) from None
    if num <= 0:
        raise ConfigurationError(
            f"{path}: tactile profile field {field!r} must be > 0, got {num}"
        )
    return num


def load_tactile_profile(name: str = DEFAULT_PROFILE) -> TactileProfile:
    """Load the tactile profile named ``name`` from ``resources/tactile``.

    Raises :class:`~brailix.core.errors.ConfigurationError` for a missing
    file, invalid JSON, or an out-of-range parameter — the same failure
    contract the braille-profile loader uses, so a front-end can surface
    one error type.
    """
    path = _TACTILE_DIR / f"{name}.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError as e:
        raise ConfigurationError(
            f"{path}: tactile profile {name!r} not found"
        ) from e
    except OSError as e:
        raise ConfigurationError(f"{path}: unreadable ({e})") from e
    except json.JSONDecodeError as e:
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
