"""Post-load schema validation.

``validate_profile`` runs *after* :func:`brailix.core.config.load_profile`
has assembled the in-memory :class:`BrailleProfile`. Most malformed inputs
already fail during loading (unknown entity, unresolved ref, cycle); this
pass adds:

  1. profile.json top-level shape (required keys, table dict layout)
  2. structures.json top-level shape (must be ``{"structures": {...}}``,
     second level must be a dict)
  3. role values in symbols.json (must be one of the known set)
  4. ``big_op`` / ``script_prefix`` types (must be ``bool`` when set)
  5. per-language ``tables.<lang>`` slot (ARCHITECTURE §7.6): the slot is
     a dict of group → resource ref, required groups present, and the
     referenced cell tables well-shaped (so a typo'd ref / empty group no
     longer silently misses at first translation)

We re-read the raw JSON for the relevant files because the in-memory
profile has already done entity normalisation / ref resolution and we
need the raw entry keys / values to give helpful error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config._helpers import _is_metadata_key, _read_json
from brailix.core.config.profile import BrailleProfile
from brailix.core.errors import ConfigurationError

_VALID_SYMBOL_ROLES: frozenset[str] = frozenset({
    "op", "rel", "delim", "punct", "shape", "big_op", "accent",
})

# Default language subtag the loader assumes when a profile omits
# ``language`` (mirrors ``loader.DEFAULT_LANGUAGE`` so the validator
# resolves the same ``tables.<lang>`` slot the loader would).
_DEFAULT_LANGUAGE: str = "zh-CN"

# Per-language ``tables.<lang>`` groups that must be present and non-empty.
# Keyed by language subtag; the loader silently drops a missing / mistyped
# group, so we require the ones a backend actually reads. Languages absent
# from this map are allowed any (or no) groups — new languages opt in here.
_REQUIRED_LANG_GROUPS: dict[str, tuple[str, ...]] = {
    "ja": ("kana",),
}


# Required top-level keys on a profile JSON. ``name`` / ``tables`` must
# always be present. ``language`` / ``cell`` / ``features`` are optional
# (loader fills sensible defaults) but must be the right *type* when
# present.
_REQUIRED_PROFILE_KEYS: tuple[str, ...] = ("name", "tables")
_TYPED_OPTIONAL_KEYS: dict[str, type | tuple[type, ...]] = {
    "language": str,
    "cell": str,
    "features": dict,
}


def _validate_profile_shape(payload: dict[str, Any], file: str) -> None:
    """Validate the bare profile JSON shape *before* tables are loaded.

    Raises :class:`ConfigurationError` for missing or wrong-shape
    top-level fields. This catches the most common typos / broken
    profiles before we descend into table files.

    Required keys: ``name`` and ``tables``. Optional keys (``language``,
    ``cell``, ``features``) get type-checked when present; the loader
    supplies sensible defaults if they're absent.
    """
    if not isinstance(payload, dict):
        raise ConfigurationError(
            f"{file}: profile root must be a JSON object"
        )
    for key in _REQUIRED_PROFILE_KEYS:
        if key not in payload:
            raise ConfigurationError(
                f"{file}: missing required profile key {key!r}"
            )
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ConfigurationError(
            f"{file}: 'tables' must be an object, got {type(tables).__name__}"
        )
    for key, expected_type in _TYPED_OPTIONAL_KEYS.items():
        if key in payload and not isinstance(payload[key], expected_type):
            type_name = (
                expected_type.__name__
                if isinstance(expected_type, type)
                else "/".join(t.__name__ for t in expected_type)
            )
            raise ConfigurationError(
                f"{file}: {key!r} must be {type_name}, got "
                f"{type(payload[key]).__name__}"
            )


def validate_profile(
    profile: BrailleProfile,
    payload: dict[str, Any],
    base: Path,
    profile_file: str,
) -> None:
    """Walk the loaded profile + raw JSON and surface any remaining
    schema violations as :class:`ConfigurationError`.

    Runs at the end of :func:`load_profile`. Validates:

      * profile.json top-level keys (name / language / cell / tables;
        features optional; tables.math + tables.zh shapes when present)
      * per-language ``tables.<lang>`` slot (§7.6): dict shape, required
        groups present, referenced cell tables well-formed
      * symbols.json roles (must be one of the known set; missing role
        on a real entry is an error)
      * symbols.json / functions.json ``big_op`` / ``script_prefix``
        types (must be ``bool`` when set)
      * structures.json top-level / second-level shape

    Can also be called directly on a loaded profile to re-validate
    (e.g. after hot-reload). When called externally the top-level
    shape is re-checked too, so the ``payload`` argument must be the
    raw JSON dict the profile was loaded from.
    """
    _validate_profile_shape(payload, profile_file)

    tables = payload.get("tables", {})
    math_tables = tables.get("math")
    if math_tables is not None and not isinstance(math_tables, dict):
        raise ConfigurationError(
            f"{profile_file}: 'tables.math' must be an object pointing at "
            f"symbols/functions/structures/digits_lower, got "
            f"{type(math_tables).__name__}"
        )
    zh_tables = tables.get("zh")
    if zh_tables is not None and not isinstance(zh_tables, dict):
        raise ConfigurationError(
            f"{profile_file}: 'tables.zh' must be an object, got "
            f"{type(zh_tables).__name__}"
        )

    # Per-language cell-table slot (§7.6). The loader resolves the slot
    # named by the profile's language subtag (``ja-JP`` -> ``ja``); zh
    # keeps its welded loaders, so it's exempt from this generic check.
    lang_subtag = str(payload.get("language", _DEFAULT_LANGUAGE)).split("-")[0]
    if lang_subtag != "zh":
        _validate_lang_tables(base, tables.get(lang_subtag), lang_subtag, profile_file)

    # Sub-table content validation. Each helper re-reads the relevant
    # file so error messages can point at the offending raw entry.
    if isinstance(math_tables, dict):
        _validate_math_symbols(
            base, math_tables.get("symbols"), math_tables.get("structures")
        )
        _validate_math_functions(base, math_tables.get("functions"))
        _validate_math_structures(base, math_tables.get("structures"))


def _validate_lang_tables(
    base: Path,
    lang_section: Any,
    lang: str,
    profile_file: str,
) -> None:
    """Validate the per-language ``tables.<lang>`` slot (§7.6).

    The slot maps a group name (e.g. ``kana``) to a resource-file ref.
    The loader silently drops a group whose ref isn't a string and a
    cell entry it can't parse, so a typo / wrong shape would only surface
    as a missing cell at first translation. This check makes those
    startup errors:

      * the slot, when present, must be a dict;
      * every group ref must be a non-empty string;
      * required groups (``_REQUIRED_LANG_GROUPS[lang]``, e.g. ``kana``)
        must be present;
      * each referenced cell table must hold a non-empty group of
        well-shaped cell entries.

    A profile that simply omits the slot is allowed (a language may have
    no cell tables); only a *present-but-broken* slot, or one missing a
    required group, is an error.
    """
    required = _REQUIRED_LANG_GROUPS.get(lang, ())
    if lang_section is None:
        if required:
            raise ConfigurationError(
                f"{profile_file}: language is {lang!r} but 'tables.{lang}' is "
                f"missing; expected group(s) " + "/".join(required)
            )
        return
    if not isinstance(lang_section, dict):
        raise ConfigurationError(
            f"{profile_file}: 'tables.{lang}' must be an object mapping group "
            f"name to resource ref, got {type(lang_section).__name__}"
        )
    for group in required:
        if group not in lang_section:
            raise ConfigurationError(
                f"{profile_file}: 'tables.{lang}' is missing required group "
                f"{group!r}"
            )
    for group, ref in lang_section.items():
        if _is_metadata_key(group):
            continue
        if not isinstance(ref, str) or not ref:
            raise ConfigurationError(
                f"{profile_file}: 'tables.{lang}.{group}' must be a non-empty "
                f"resource-ref string, got {ref!r}"
            )
        _validate_lang_cell_table(base, ref, group)


def _validate_lang_cell_table(base: Path, relative: str, group: str) -> None:
    """Check a per-language cell table (e.g. kana.json) has a non-empty
    group of well-shaped cell entries.

    Entries live either under a group named ``group`` (matching the
    profile's ``tables.<lang>.<group>``) or at the file top level — the
    same fallback :func:`brailix.core.config.loader._load_lang_table`
    uses. Each real entry must be a recognizable cell spec: a non-empty
    string ref, a non-empty list of refs, or an object carrying ``cells``
    or ``dots`` — the shapes ``_resolve_table`` understands. Anything
    else is silently dropped by the loader, so we reject it here.
    """
    payload = _read_json(base / relative)
    section = payload.get(group)
    src = section if isinstance(section, dict) else payload
    entries = {
        k: v for k, v in src.items() if not _is_metadata_key(k)
    }
    if not entries:
        raise ConfigurationError(
            f"{relative}: group {group!r} has no cell entries (expected a "
            f"non-empty mapping of token to cell spec)"
        )
    for raw_key, spec in entries.items():
        if not _is_valid_cell_spec(spec):
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} is not a valid cell spec; "
                f"expected a cell ref string, a non-empty list of refs, or "
                f"an object with 'cells' or 'dots', got {spec!r}"
            )


def _is_valid_cell_spec(spec: Any) -> bool:
    """Whether ``spec`` is a shape :func:`_resolve_table` can resolve.

    Mirrors the spec shapes documented on ``_resolve_table``: a non-empty
    string ref, a non-empty list of refs, or an object with ``cells`` /
    ``dots``. Used to reject entries the loader would otherwise drop.
    """
    if isinstance(spec, str):
        return bool(spec)
    if isinstance(spec, list):
        return bool(spec)
    if isinstance(spec, dict):
        return "cells" in spec or "dots" in spec
    return False


def _accent_mark_kinds(base: Path, structures_relative: str | None) -> set[str]:
    """Accent-mark kinds declared in structures.json (``accent.mark.*``).

    An ``accent_mark`` field in symbols.json must name one of these. We
    derive the valid set from the resource (rather than hardcode
    ``{arrow, bar}``) so a profile that adds a new mark kind is accepted
    automatically, while a typo is rejected at load instead of silently
    producing empty braille for that accent.
    """
    if not structures_relative:
        return set()
    payload = _read_json(base / structures_relative)
    structures = payload.get("structures") if isinstance(payload, dict) else None
    accent = structures.get("accent") if isinstance(structures, dict) else None
    mark = accent.get("mark") if isinstance(accent, dict) else None
    return set(mark.keys()) if isinstance(mark, dict) else set()


def _validate_math_symbols(
    base: Path, relative: str | None, structures_relative: str | None = None
) -> None:
    """Check every symbols.json entry has a valid role and well-typed flags."""
    if not relative:
        return
    payload = _read_json(base / relative)
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        # Empty / absent symbols section is fine (no entries to check).
        return
    # ``accent_mark`` values are checked against the kinds declared in
    # structures.json (accent.mark.*), loaded lazily on first use.
    valid_accent_kinds: set[str] | None = None
    for raw_key, spec in symbols.items():
        if _is_metadata_key(raw_key):
            continue
        # Bare-list / bare-string specs are accepted by the loader, but
        # they can't carry a role — that's a hard error for symbols.
        if not isinstance(spec, dict):
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} must be a JSON object with "
                f"a 'role' field, got {type(spec).__name__}"
            )
        if "role" not in spec:
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} is missing required 'role' "
                f"field; expected one of "
                + "/".join(sorted(_VALID_SYMBOL_ROLES))
            )
        role = spec["role"]
        if role not in _VALID_SYMBOL_ROLES:
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} has invalid role {role!r}; "
                f"expected one of "
                + "/".join(sorted(_VALID_SYMBOL_ROLES))
            )
        _check_bool_flag(spec, "big_op", raw_key, relative)
        _check_bool_flag(spec, "script_prefix", raw_key, relative)
        _check_bool_flag(spec, "provisional", raw_key, relative)
        indicator = spec.get("indicator")
        if indicator is not None and not isinstance(indicator, str):
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} has non-string 'indicator': "
                f"{indicator!r} (expected a marker name like 'symbol' / "
                f"'operation' / 'negation')"
            )
        accent_mark = spec.get("accent_mark")
        if accent_mark is not None:
            if not isinstance(accent_mark, str) or not accent_mark:
                raise ConfigurationError(
                    f"{relative}: entry {raw_key!r} has non-string/empty "
                    f"'accent_mark': {accent_mark!r}"
                )
            if valid_accent_kinds is None:
                valid_accent_kinds = _accent_mark_kinds(base, structures_relative)
            if accent_mark not in valid_accent_kinds:
                raise ConfigurationError(
                    f"{relative}: entry {raw_key!r} has accent_mark "
                    f"{accent_mark!r} with no matching accent.mark.{accent_mark} "
                    f"in structures.json; known kinds: "
                    + ("/".join(sorted(valid_accent_kinds)) or "(none)")
                )


def _validate_math_functions(base: Path, relative: str | None) -> None:
    """Check ``big_op`` / ``script_prefix`` in functions.json are bools."""
    if not relative:
        return
    payload = _read_json(base / relative)
    functions = payload.get("functions")
    if not isinstance(functions, dict):
        return
    for raw_key, spec in functions.items():
        if _is_metadata_key(raw_key):
            continue
        if not isinstance(spec, dict):
            continue
        _check_bool_flag(spec, "big_op", raw_key, relative)
        _check_bool_flag(spec, "script_prefix", raw_key, relative)


def _validate_math_structures(base: Path, relative: str | None) -> None:
    """Check structures.json top-level + category-level shape.

    Requires:
      * top level is ``{"structures": {...}}`` (a JSON object with a
        ``structures`` key whose value is also an object);
      * each second-level entry (``fraction`` / ``script`` / ``sqrt`` /
        ``letter_prefix`` / ``function`` / future categories) is a dict.

    We deliberately don't enforce the set of known category names —
    profiles are allowed to add new categories so long as the second
    level is a dict.
    """
    if not relative:
        return
    payload = _read_json(base / relative)
    if not isinstance(payload, dict):
        raise ConfigurationError(
            f"{relative}: file root must be a JSON object"
        )
    if "structures" not in payload:
        raise ConfigurationError(
            f"{relative}: top level must contain a 'structures' key"
        )
    structures = payload["structures"]
    if not isinstance(structures, dict):
        raise ConfigurationError(
            f"{relative}: 'structures' must be an object, got "
            f"{type(structures).__name__}"
        )
    for category, body in structures.items():
        if _is_metadata_key(category):
            continue
        if not isinstance(body, dict):
            raise ConfigurationError(
                f"{relative}: category {category!r} must be an object, got "
                f"{type(body).__name__}"
            )


def _check_bool_flag(
    spec: dict[str, Any], flag: str, key: str, file: str
) -> None:
    """Reject non-bool values for a flag we treat as bool elsewhere."""
    if flag not in spec:
        return
    value = spec[flag]
    if not isinstance(value, bool):
        raise ConfigurationError(
            f"{file}: entry {key!r} has non-bool {flag!r}: {value!r} "
            f"(expected true or false)"
        )
