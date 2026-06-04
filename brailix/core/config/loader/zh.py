"""NCB (National Common Braille) optional tables loader.

Each loader returns ``None`` when the profile doesn't declare the
table path — that's how cn_current opts out without changes to the
NCB backend modules. Missing-file / bad-shape raise
:class:`ConfigurationError` so the failure happens at startup, not
when the backend first reaches for the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config._helpers import _read_json
from brailix.core.config.loader._refs import _resolve_cell_refs
from brailix.core.config.zh_ncb_tables import (
    NcbCharOverrides,
    NcbExceptions,
    NcbToneOmission,
    NcbWordOverrides,
    _CharOverride,
    _Shorthand,
)
from brailix.core.errors import ConfigurationError


def _load_compounds(base: Path, relative: str | None) -> frozenset[str]:
    """Load the letter+hanzi compound lexicon → frozenset of surfaces.

    Scheme-neutral Chinese language data: which letter↔hanzi runs are one
    word (taking a connector instead of a blank cell). The zh frontend reads
    it from ``profile.zh_compounds``. Absent ``tables.zh.compounds`` → empty set.
    """
    if not relative:
        return frozenset()
    path = base / relative
    if not path.exists():
        raise ConfigurationError(f"compounds resource not found: {path}")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ConfigurationError(f"compounds resource must be a JSON object: {path}")
    return frozenset(
        c for c in payload.get("compounds", []) if isinstance(c, str) and c
    )


def _load_zh_exceptions(
    base: Path,
    relative: str | None,
    cells_pool: dict[str, tuple[int, ...]],
) -> NcbExceptions | None:
    """Load the one-file NCB exceptions resource.

    By design, one JSON file with three sections —
    ``tone_omission`` / ``char_overrides`` / ``word_overrides`` —
    carries every NCB-specific data point.

    Returns ``None`` if the profile doesn't declare
    ``tables.zh.exceptions`` (cn_current opt-out).  Missing-file /
    bad-shape raises :class:`ConfigurationError` at load time, not at
    first translation.

    Validation per sub-section:

    * ``tone_omission``: optional.  If present, ``by_initial`` and
      ``zero_initial`` must be dicts.
    * ``char_overrides``: optional.  ``entries`` must be a list; each
      entry must have ``surface`` (single char) and optionally a
      ``shorthand`` sub-object and/or ``keep_tone`` flag.
    * ``word_overrides``: optional.  ``entries`` must be a list; each
      entry's ``keep_tone_per_char`` length must equal len(surface).
    """
    if not relative:
        return None
    path = base / relative
    if not path.exists():
        raise ConfigurationError(f"NCB exceptions resource not found: {path}")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ConfigurationError(
            f"NCB exceptions resource {path} must be a JSON object"
        )

    tone_omission = _load_zh_exceptions_tone_omission(
        payload.get("tone_omission"), path
    )
    char_overrides = _load_zh_exceptions_char_overrides(
        payload.get("char_overrides"), cells_pool, path
    )
    word_overrides = _load_zh_exceptions_word_overrides(
        payload.get("word_overrides"), path
    )

    return NcbExceptions(
        tone_omission=tone_omission,
        char_overrides=char_overrides,
        word_overrides=word_overrides,
    )


def _load_zh_exceptions_tone_omission(
    section: Any, path: Path
) -> NcbToneOmission | None:
    """Build :class:`NcbToneOmission` from the ``tone_omission`` section.

    Returns ``None`` when the section is absent — a profile can ship
    char/word overrides without per-initial omission rules (cn_current
    won't, but a hypothetical "lite NCB" might).
    """
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"{path}: 'tone_omission' must be an object, got "
            f"{type(section).__name__}"
        )
    by_initial = section.get("by_initial")
    zero_initial = section.get("zero_initial")
    if not isinstance(by_initial, dict) or not isinstance(zero_initial, dict):
        raise ConfigurationError(
            f"{path}: 'tone_omission' missing required "
            f"'by_initial' / 'zero_initial' sub-objects"
        )
    boundary = section.get("boundary_rule", {})
    return NcbToneOmission(
        by_initial={k: v for k, v in by_initial.items() if isinstance(v, dict)},
        zero_initial=dict(zero_initial),
        boundary_rule_enabled=bool(
            boundary.get("enabled", True) if isinstance(boundary, dict) else True
        ),
    )


def _load_zh_exceptions_char_overrides(
    section: Any,
    cells_pool: dict[str, tuple[int, ...]],
    path: Path,
) -> NcbCharOverrides | None:
    """Build :class:`NcbCharOverrides` from the ``char_overrides`` section.

    Entries are an array of objects (not a dict keyed by chars) — see
    :mod:`brailix.core.config.zh_ncb_tables` for the rationale.  Each
    entry has ``surface`` (single Chinese character) and one or both of:

    * ``shorthand`` sub-object with ``cells`` (required), optional
      ``boundary_exception`` (default false), optional
      ``boundary_spelling`` (default null).
    * ``keep_tone`` boolean (default false).

    Duplicate ``surface`` across entries → :class:`ConfigurationError`
    at load (an authoring bug — same char with two overrides is
    almost certainly wrong; merge into one entry).
    """
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"{path}: 'char_overrides' must be an object, got "
            f"{type(section).__name__}"
        )
    entries = section.get("entries")
    if not isinstance(entries, list):
        raise ConfigurationError(
            f"{path}: 'char_overrides.entries' must be a list"
        )

    by_char: dict[str, _CharOverride] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        surface = entry.get("surface")
        if not isinstance(surface, str) or not surface:
            raise ConfigurationError(
                f"{path}: char_overrides entry missing 'surface' (id "
                f"{entry.get('_id', '?')!r})"
            )
        if surface in by_char:
            raise ConfigurationError(
                f"{path}: duplicate char_overrides for surface {surface!r}"
            )
        shorthand_spec = entry.get("shorthand")
        shorthand = (
            _build_shorthand(shorthand_spec, cells_pool, surface, path)
            if shorthand_spec is not None
            else None
        )
        keep_tone = bool(entry.get("keep_tone", False))
        by_char[surface] = _CharOverride(
            surface=surface,
            shorthand=shorthand,
            keep_tone=keep_tone,
        )
    return NcbCharOverrides(by_char=by_char)


def _build_shorthand(
    spec: Any,
    cells_pool: dict[str, tuple[int, ...]],
    surface: str,
    path: Path,
) -> _Shorthand:
    """Build a :class:`_Shorthand` from a per-char shorthand sub-object."""
    if not isinstance(spec, dict):
        raise ConfigurationError(
            f"{path}: char_overrides[{surface!r}].shorthand must be an object"
        )
    cells_refs = spec.get("cells")
    cells = _resolve_cell_ref_list(
        cells_refs, cells_pool, surface, "shorthand.cells", path,
        required=True,
    )
    boundary_exception = bool(spec.get("boundary_exception", False))
    boundary_spelling_refs = spec.get("boundary_spelling")
    boundary_spelling = _resolve_cell_ref_list(
        boundary_spelling_refs, cells_pool, surface, "shorthand.boundary_spelling", path,
        required=False,
    )
    return _Shorthand(
        cells=cells,  # type: ignore[arg-type]  # required=True → non-None
        boundary_exception=boundary_exception,
        boundary_spelling=boundary_spelling,
    )


def _resolve_cell_ref_list(
    refs: object,
    cells_pool: dict[str, tuple[int, ...]],
    surface: str,
    field_label: str,
    path: Path,
    *,
    required: bool,
) -> tuple[tuple[int, ...], ...] | None:
    """Resolve a ``["c_145", ...]`` cell-pool ref list to dot tuples.

    Centralised so char_overrides shorthand entries don't fork a
    bespoke resolver.  ``required=True`` means missing/empty raises;
    otherwise None is returned for missing.
    """
    return _resolve_cell_refs(
        refs,
        cells_pool,
        err_ctx=f"char_overrides[{surface!r}].{field_label}",
        file=str(path),
        required=required,
    )


def _load_zh_exceptions_word_overrides(
    section: Any,
    path: Path,
) -> NcbWordOverrides | None:
    """Build :class:`NcbWordOverrides` from the ``word_overrides`` section.

    Entries are an array of objects; each has ``surface`` (the multi-
    char word) and ``keep_tone_per_char`` (a bool list of equal length).
    Length-mismatch raises — catches the common authoring bug of editing
    the surface without updating the flag list.
    """
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"{path}: 'word_overrides' must be an object, got "
            f"{type(section).__name__}"
        )
    entries = section.get("entries")
    if not isinstance(entries, list):
        raise ConfigurationError(
            f"{path}: 'word_overrides.entries' must be a list"
        )
    by_word: dict[str, tuple[bool, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        surface = entry.get("surface")
        if not isinstance(surface, str) or not surface:
            raise ConfigurationError(
                f"{path}: word_overrides entry missing 'surface' (id "
                f"{entry.get('_id', '?')!r})"
            )
        flags = entry.get("keep_tone_per_char")
        if not isinstance(flags, list):
            raise ConfigurationError(
                f"{path}: word_overrides[{surface!r}].keep_tone_per_char "
                f"must be a list, got {type(flags).__name__}"
            )
        if len(flags) != len(surface):
            raise ConfigurationError(
                f"{path}: word_overrides[{surface!r}].keep_tone_per_char "
                f"length {len(flags)} != surface length {len(surface)}"
            )
        if surface in by_word:
            raise ConfigurationError(
                f"{path}: duplicate word_overrides for surface {surface!r}"
            )
        by_word[surface] = tuple(bool(x) for x in flags)
    return NcbWordOverrides(by_word=by_word)
