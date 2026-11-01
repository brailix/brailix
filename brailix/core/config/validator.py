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
  5. per-language ``tables.<lang>`` slot (ARCHITECTURE#arch-language-slots): the slot is
     a dict of group → resource ref, required groups present, and the
     referenced cell tables well-shaped (so a typo'd ref / empty group no
     longer silently misses at first translation)
  6. music ``_spec`` sections' referential integrity: every
     ``["entity", name]`` recipe step must name an entity the same
     topic's cells table actually defines (a hand-edited profile with a
     typo'd entity name would otherwise emit no cells at that step, with
     no diagnostic — the harmony handler ignores the lookup miss)

Diagnostics split by severity: a shape the loader cannot resolve at all
raises :class:`ConfigurationError` (the loud-failure philosophy this module
has always had); a *recoverable* problem — data that loads, is silently
ignored by every reader, and would only mislead its author — is reported on
the optional ``warnings`` collector instead (unknown top-level keys,
unparseable zh phoneme-table entries, a zh slot declared by a non-zh
profile), so a profile with one typo still loads while the typo announces
itself.

We re-read the raw JSON for the relevant files because the in-memory
profile has already done entity normalisation / ref resolution and we
need the raw entry keys / values to give helpful error messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.config._helpers import _is_metadata_key, _read_json
from brailix.core.config.profile import BrailleProfile
from brailix.core.errors import ConfigurationError

if _TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from brailix.core.errors import WarningCollector

_VALID_SYMBOL_ROLES: frozenset[str] = frozenset({
    "op", "rel", "delim", "punct", "shape", "big_op", "accent",
})

# Required top-level keys on a profile JSON. ``name`` / ``language`` /
# ``tables`` must always be present — there is no built-in default
# language, so every profile declares its own (e.g. ``zh-CN`` / ``ja-JP``).
# ``cell`` / ``features`` are optional (loader fills sensible defaults).
# All typed keys are checked for shape when present.
_REQUIRED_PROFILE_KEYS: tuple[str, ...] = ("name", "language", "tables")
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

    Required keys: ``name``, ``language``, and ``tables`` — there is no
    built-in default language, so every profile declares its own. Optional
    keys (``cell``, ``features``) get type-checked when present; the loader
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
    # Every table entry is a string reference (a resource path / inline cell
    # spec) or a group object of string references; metadata keys (leading
    # underscore, e.g. a group's ``_required``) are exempt at both levels.
    # Checked here — BEFORE any table loads — so a wrong-shape entry raises
    # a ConfigurationError naming the key instead of crashing a loader
    # downstream with a bare TypeError (``base / relative`` on a dict).
    for tbl_key, tbl_value in tables.items():
        if _is_metadata_key(tbl_key):
            continue
        if isinstance(tbl_value, str):
            continue
        if isinstance(tbl_value, dict):
            for sub_key, sub_value in tbl_value.items():
                if _is_metadata_key(sub_key):
                    continue
                if not isinstance(sub_value, str):
                    raise ConfigurationError(
                        f"{file}: 'tables.{tbl_key}.{sub_key}' must be a "
                        f"string table reference, got "
                        f"{type(sub_value).__name__}"
                    )
            continue
        raise ConfigurationError(
            f"{file}: 'tables.{tbl_key}' must be a string table reference "
            f"or an object of references, got {type(tbl_value).__name__}"
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
    language = payload.get("language")
    if isinstance(language, str) and not language.strip():
        raise ConfigurationError(
            f"{file}: 'language' must be a non-empty language tag "
            f"(e.g. 'zh-CN'), got an empty string"
        )


def validate_profile(
    profile: BrailleProfile,
    payload: dict[str, Any],
    base: Path,
    profile_file: str,
    *,
    warnings: WarningCollector | None = None,
) -> None:
    """Walk the loaded profile + raw JSON and surface any remaining
    schema violations as :class:`ConfigurationError`.

    Runs at the end of :func:`load_profile`. Validates:

      * profile.json top-level keys (name / language / cell / tables;
        features optional; tables.math + tables.zh shapes when present)
      * per-language ``tables.<lang>`` slot (ARCHITECTURE#arch-language-slots):
        dict shape, required
        groups present, referenced cell tables well-formed
      * symbols.json roles (must be one of the known set; missing role
        on a real entry is an error) and cell specs (a role-only entry
        would load with its braille silently dropped)
      * symbols.json / functions.json ``big_op`` / ``script_prefix``
        types (must be ``bool`` when set)
      * structures.json top-level / second-level shape
      * music ``_spec`` entity references (must resolve in the same
        topic's cells table)

    Recoverable oddities — data that loads but no reader will ever touch —
    go to ``warnings`` instead of raising, when a collector is supplied:
    unknown top-level keys (a typo'd ``"featuers"``), unparseable zh
    phoneme-table entries, and a zh slot declared by a non-zh profile.
    Without a collector those stay unreported (the profile still loads;
    supplying one is how :class:`~brailix.pipeline.Pipeline` surfaces
    them on its compile results).

    Can also be called directly on a loaded profile to re-validate
    (e.g. after hot-reload). When called externally the top-level
    shape is re-checked too, so the ``payload`` argument must be the
    raw JSON dict the profile was loaded from.
    """
    _validate_profile_shape(payload, profile_file)

    if warnings is not None:
        _warn_unknown_profile_keys(payload, profile_file, warnings)

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

    # Per-language cell-table slot (ARCHITECTURE#arch-language-slots).
    # The loader resolves the slot
    # named by the profile's language subtag (``ja-JP`` -> ``ja``); zh
    # keeps its welded loaders, so it's exempt from this generic check.
    lang_subtag = str(payload["language"]).split("-")[0]
    if lang_subtag != "zh":
        _validate_lang_tables(base, tables.get(lang_subtag), lang_subtag, profile_file)
        if warnings is not None:
            _warn_unreachable_zh_slot(
                tables, payload["language"], profile_file, warnings
            )
    else:
        if isinstance(zh_tables, dict):
            # zh keeps welded loaders (exempt from the generic per-language slot
            # check above), but its hand-authored punctuation table still resolves
            # through _resolve_table, which SILENTLY drops an entry it can't parse
            # (e.g. an object with neither 'cells' nor 'dots') — surfacing only as a
            # missing cell at first translation. Validate it the same way as any
            # other cell table so an authoring typo fails loud at load. The helper
            # keys on payload.get("punctuation"), exactly as _load_punct_table does.
            punct_ref = zh_tables.get("punctuation")
            if isinstance(punct_ref, str) and punct_ref:
                _validate_lang_cell_table(base, punct_ref, "punctuation")
        if warnings is not None:
            _warn_zh_cell_table_entries(base, tables, profile_file, warnings)

    # Sub-table content validation. Each helper re-reads the relevant
    # file so error messages can point at the offending raw entry.
    if isinstance(math_tables, dict):
        _validate_math_symbols(
            base, math_tables.get("symbols"), math_tables.get("structures")
        )
        _validate_math_functions(base, math_tables.get("functions"))
        _validate_math_structures(base, math_tables.get("structures"))

    # Music spec integrity works from the in-memory profile (both sides
    # of the reference are already loaded there), so it re-reads nothing.
    _validate_music_spec_entities(profile)


def _validate_music_spec_entities(profile: BrailleProfile) -> None:
    """Every ``["entity", name]`` step in a music ``_spec`` recipe must
    name an entity the **same topic's** cells table defines.

    The spec sections (``chord_symbols.json``'s ``_kind_spec`` is the
    shipped one) drive backend emission from data: an ``entity`` step
    routes through :meth:`BrailleProfile.music_cell`, and the harmony
    handler ignores the miss — a typo'd entity name in a hand-edited
    profile therefore emitted no cells for that step, silently (the
    shipped tables are consistent; this guards the custom-profile case).
    The check mirrors the ``accent_mark`` / ``indicator`` cross-checks in
    :func:`_validate_math_symbols`: a reference the data cannot resolve
    is a load-time error, not a translation-time blank.

    The walk is deliberately shape-tolerant: a spec section is a dict of
    recipes, a recipe is a list of steps, and only a step that *is* a
    two-element list headed ``"entity"`` is a reference. Anything else
    (``["letters", "m7"]``, a future step kind, a non-list value) is
    rule data the backend owns, not a cell-table reference, and is left
    alone.
    """
    for topic, sections in profile.music_specs.items():
        known_entities = profile.music.get(topic, {})
        for section, spec in sections.items():
            if not isinstance(spec, dict):
                continue
            for key, recipe in spec.items():
                if not isinstance(recipe, list):
                    continue
                for step in recipe:
                    if (
                        not isinstance(step, list)
                        or len(step) != 2
                        or step[0] != "entity"
                        or not isinstance(step[1], str)
                    ):
                        continue
                    if step[1] not in known_entities:
                        raise ConfigurationError(
                            f"music spec {topic}.{section} key {key!r} "
                            f"references entity {step[1]!r}, which the "
                            f"{topic} cells table does not define; the "
                            f"backend would emit no cells for that step "
                            f"(known entities: "
                            + (
                                ", ".join(sorted(known_entities))
                                if known_entities
                                else "(none)"
                            )
                            + ")"
                        )


def _validate_lang_tables(
    base: Path,
    lang_section: Any,
    lang: str,
    profile_file: str,
) -> None:
    """Validate the per-language ``tables.<lang>`` slot
        (ARCHITECTURE#arch-language-slots).

    The slot maps a group name (e.g. ``kana``) to a resource-file ref.
    The loader silently drops a group whose ref isn't a string and a
    cell entry it can't parse, so a typo / wrong shape would only surface
    as a missing cell at first translation. This check makes those
    startup errors:

      * the slot, when present, must be a dict;
      * every group ref must be a non-empty string;
      * the groups the **profile itself declares required** via the
        ``_required`` metadata key (e.g. ``["kana"]``) must be present;
      * each referenced cell table must hold a non-empty group of
        well-shaped cell entries.

    Which groups are mandatory is **data, not code** — it comes from the
    profile's own ``tables.<lang>._required`` list, so the validator stays
    language-agnostic (ARCHITECTURE#arch-language-slots) and a new
        language opts in by declaring
    ``_required`` in its resource, not by editing this module. A profile
    that omits the slot — or declares no ``_required`` — is allowed; only a
    *present-but-broken* slot, or one missing a self-declared required
    group, is an error.
    """
    if lang_section is None:
        # No slot → nothing declared, nothing to enforce (a language may
        # ship no cell tables; the requirement is self-declared per profile).
        return
    if not isinstance(lang_section, dict):
        raise ConfigurationError(
            f"{profile_file}: 'tables.{lang}' must be an object mapping group "
            f"name to resource ref, got {type(lang_section).__name__}"
        )
    required = lang_section.get("_required", [])
    if not isinstance(required, list) or not all(
        isinstance(g, str) for g in required
    ):
        raise ConfigurationError(
            f"{profile_file}: 'tables.{lang}._required' must be a list of "
            f"group names, got {required!r}"
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
    string ref, a list of refs (an **empty list included** — it resolves to
    the documented empty cell sequence, the neutral tone's "emit nothing"),
    or an object with ``cells`` / ``dots``. Used to reject entries the
    loader would otherwise drop.
    """
    if isinstance(spec, str):
        return bool(spec)
    if isinstance(spec, list):
        return True
    if isinstance(spec, dict):
        return "cells" in spec or "dots" in spec
    return False


# Top-level profile keys this release understands, beyond the metadata a
# table file may carry. Mirrors the ``properties`` of
# ``tests/schemas/profile.schema.json`` (required ``name`` / ``language`` /
# ``tables``, optional ``cell`` / ``features``, plus metadata like
# ``reference`` and any ``_*`` note) — a key outside this set is unknown,
# which a typo'd ``"featuers"`` used to be silently.
_KNOWN_PROFILE_KEYS: frozenset[str] = frozenset(
    set(_REQUIRED_PROFILE_KEYS) | set(_TYPED_OPTIONAL_KEYS)
)

# zh's welded phoneme tables — the per-language groups whose unparseable
# entries are warned (with a count) rather than raised on, see
# :func:`_warn_zh_cell_table_entries`.
_ZH_CELL_TABLE_GROUPS: tuple[str, ...] = ("initials", "finals", "tones")


def _table_ref_or_flat(tables: dict[str, Any], key: str) -> Any:
    """Resolve a zh table ref the way the loader does: nested
    ``tables.zh.<key>`` first, flat ``tables.<key>`` as the fallback."""
    zh_section = tables.get("zh")
    if isinstance(zh_section, dict) and key in zh_section:
        return zh_section[key]
    return tables.get(key)


def _warn_unknown_profile_keys(
    payload: dict[str, Any],
    profile_file: str,
    warnings: WarningCollector,
) -> None:
    """Report top-level profile keys nothing reads (a typo'd key used to
    be silently ignored, so ``feature()`` fell back to its default and the
    flag the author meant to set never moved).

    A warning, not an error: the key may be a forward declaration a newer
    release reads (the schema grows), and a profile that merely carries an
    unknown key is otherwise perfectly loadable.
    """
    unknown = [
        key
        for key in payload
        if key not in _KNOWN_PROFILE_KEYS and not _is_metadata_key(key)
    ]
    if not unknown:
        return
    warnings.warn(
        code="CONFIG_PROFILE_UNKNOWN_KEY",
        message=(
            f"{profile_file}: unknown top-level key(s) {', '.join(repr(k) for k in sorted(unknown))};"
            f" this release reads {sorted(_KNOWN_PROFILE_KEYS)} plus"
            f" metadata (``_*`` / reference) — a typo here used to be"
            f" silently ignored"
        ),
        source="config",
    )


def _warn_zh_cell_table_entries(
    base: Path,
    tables: dict[str, Any],
    profile_file: str,
    warnings: WarningCollector,
) -> None:
    """Count and report zh phoneme-table entries the loader cannot parse.

    zh's initials / finals / tones resolve through ``_resolve_dots_table``,
    which silently skips an entry that isn't a cell spec — a hand-edit that
    breaks a real initial then surfaces only as a blank cell or a
    translation-time warning, never at load. The punctuation table's
    sibling check raises (:func:`_validate_lang_cell_table`); the phoneme
    tables warn instead, with the count and the offending keys, because one
    bad entry in a 21-initial table should not make the whole profile
    unloadable.
    """
    for group in _ZH_CELL_TABLE_GROUPS:
        ref = _table_ref_or_flat(tables, group)
        if not isinstance(ref, str) or not ref:
            continue
        payload = _read_json(base / ref)
        section = payload.get(group)
        src = section if isinstance(section, dict) else payload
        bad = [
            key
            for key, spec in src.items()
            if not _is_metadata_key(key) and not _is_valid_cell_spec(spec)
        ]
        if not bad:
            continue
        warnings.warn(
            code="CONFIG_ZH_TABLE_BAD_ENTRIES",
            message=(
                f"{ref}: {len(bad)} entry(ies) in group {group!r} are not"
                f" valid cell specs and were dropped at load (a cell ref"
                f" string, a non-empty list of refs, or an object with"
                f" 'cells' / 'dots'): {sorted(bad)}"
            ),
            source="config",
        )


def _warn_unreachable_zh_slot(
    tables: dict[str, Any],
    language: Any,
    profile_file: str,
    warnings: WarningCollector,
) -> None:
    """Report a ``tables.zh`` slot declared by a profile whose language is
    not Chinese.

    ``tables.zh.exceptions`` / ``compounds`` (nested or flat) load into the
    per-language ``lang_specs["zh"]`` slot, and :meth:`BrailleProfile.lang_spec`
    only ever reads the profile's OWN language subtag — so on a ``ja`` (or
    any non-zh) profile the resource is loaded into a slot no code path
    reaches, with no diagnostic. Almost always a zh profile copied as a
    starting point with the language changed; the warning says so instead
    of policing the shape (the data itself is valid zh data).
    """
    declared = sorted(
        key
        for key in ("exceptions", "compounds")
        if isinstance(_table_ref_or_flat(tables, key), str)
    )
    if not declared:
        return
    warnings.warn(
        code="CONFIG_UNREACHABLE_LANG_SLOT",
        message=(
            f"{profile_file}: tables.zh declares {', '.join(repr(k) for k in declared)},"
            f" but this profile's language is {language!r} — the zh data"
            f" loads into a slot no code path reads. Move it under"
            f" tables.{str(language).split('-')[0]!r} or drop it"
        ),
        source="config",
    )


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


def _indicator_kinds(base: Path, structures_relative: str | None) -> set[str]:
    """Class-marker indicator kinds declared in structures.json
    (``indicator.*`` — e.g. ``symbol`` / ``operation`` / ``negation``).

    An ``indicator`` field in symbols.json must name one of these. We
    derive the valid set from the resource (rather than hardcode the three
    shipped kinds) so a profile that adds a new indicator is accepted
    automatically, while a typo is rejected at load instead of silently
    emitting no marker — ``math_structure('indicator.<typo>')`` returns an
    empty tuple, which the backend's ``_emit_mo`` skips, so the symbol would
    lose its class marker with no diagnostic.
    """
    if not structures_relative:
        return set()
    payload = _read_json(base / structures_relative)
    structures = payload.get("structures") if isinstance(payload, dict) else None
    indicator = structures.get("indicator") if isinstance(structures, dict) else None
    return set(indicator.keys()) if isinstance(indicator, dict) else set()


def _validate_math_symbols(
    base: Path, relative: str | None, structures_relative: str | None = None
) -> None:
    """Check every symbols.json entry has a valid role, a resolvable cell
    spec, and well-typed flags."""
    if not relative:
        return
    payload = _read_json(base / relative)
    symbols = payload.get("symbols")
    if not isinstance(symbols, dict):
        # Empty / absent symbols section is fine (no entries to check).
        return
    # ``accent_mark`` / ``indicator`` values are checked against the kinds
    # declared in structures.json (accent.mark.* / indicator.*), loaded
    # lazily on first use.
    valid_accent_kinds: set[str] | None = None
    valid_indicator_kinds: set[str] | None = None
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
        # The same cell-spec check functions.json gets: a role-only entry
        # (``{"role": "rel"}`` with no cells / dots) passes every check
        # above while the loader's resolver silently drops its braille —
        # the symbol keeps its role in ``math_symbol_roles`` but never
        # appears in ``math_symbols``, surfacing only as
        # MATH_UNKNOWN_SYMBOL at translation time.
        if not _is_valid_cell_spec(spec):
            raise ConfigurationError(
                f"{relative}: entry {raw_key!r} carries no braille — "
                f"expected a cell ref string, a non-empty list of refs, or "
                f"an object with 'cells' or 'dots' alongside 'role' "
                f"(a role-only entry loads, but its cells are silently "
                f"dropped and the symbol translates to nothing)"
            )
        _check_bool_flag(spec, "big_op", raw_key, relative)
        _check_bool_flag(spec, "script_prefix", raw_key, relative)
        _check_bool_flag(spec, "provisional", raw_key, relative)
        indicator = spec.get("indicator")
        if indicator is not None:
            if not isinstance(indicator, str) or not indicator:
                raise ConfigurationError(
                    f"{relative}: entry {raw_key!r} has non-string/empty "
                    f"'indicator': {indicator!r} (expected a marker name like "
                    f"'symbol' / 'operation' / 'negation')"
                )
            if valid_indicator_kinds is None:
                valid_indicator_kinds = _indicator_kinds(base, structures_relative)
            if indicator not in valid_indicator_kinds:
                raise ConfigurationError(
                    f"{relative}: entry {raw_key!r} has indicator {indicator!r} "
                    f"with no matching indicator.{indicator} in structures.json; "
                    f"known kinds: "
                    + ("/".join(sorted(valid_indicator_kinds)) or "(none)")
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
    """Validate functions.json entries.

    Each non-metadata entry must be a cell spec :func:`_resolve_table` can
    resolve (a ref string, a non-empty ref list, or an object with
    ``cells`` / ``dots``). An entry that carries only flags (e.g.
    ``{"big_op": true}`` with no ``cells``) would otherwise be silently
    dropped by the loader, leaving that function name with no braille and
    no diagnostic. ``big_op`` / ``script_prefix`` flags, when present, must
    be bool.
    """
    if not relative:
        return
    payload = _read_json(base / relative)
    functions = payload.get("functions")
    if not isinstance(functions, dict):
        return
    for raw_key, spec in functions.items():
        if _is_metadata_key(raw_key):
            continue
        if not _is_valid_cell_spec(spec):
            raise ConfigurationError(
                f"{relative}: function entry {raw_key!r} is not a valid cell "
                f"spec; expected a cell ref string, a non-empty list of refs, "
                f"or an object with 'cells' or 'dots', got {spec!r}"
            )
        if isinstance(spec, dict):
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
