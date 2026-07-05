"""Profile + table loading.

The public entry point is :func:`load_profile`. Internally it composes
the per-topic loaders below: a cells pool first, then per-section table
parsers (zh / math / music / latin / greek / numbers / punct), then a
post-load schema check.

Subpackage map:

* :mod:`._refs`   — shared spec / ref / flag resolvers (used by every
  other module here)
* :mod:`.math`    — symbols / functions / structures / digits_lower
* :mod:`.music`   — BANA Music Braille topic resources
* :mod:`.letters` — neutral latin / greek letter tables
* :mod:`.numbers` — number_sign + digits + decimal / thousands
* :mod:`.punct`   — punctuation cells + spacing flags
* :mod:`.zh`      — NCB-specific exceptions (tone omission / char /
  word overrides)

All private helpers are re-exported here so callers that historically
did ``from brailix.core.config.loader import _load_zh_exceptions`` (or
any other underscore-prefixed helper) keep working unchanged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from brailix.core.config._helpers import _is_metadata_key, _read_json
from brailix.core.config.loader._refs import (
    _coerce_dots_field,
    _extract_cells,
    _flag_dict_bool,
    _flag_dict_str,
    _is_spec_object,
    _load_cells_pool,
    _load_table,
    _normalise_one_ref,
    _normalise_symbols_payload,
    _normalise_symbols_spec,
    _read_section,
    _resolve_digits,
    _resolve_dots_table,
    _resolve_nested_structures,
    _resolve_single,
    _resolve_table,
    _section,
    _spec_to_cells,
    _symbol_spacing_dict,
    _table_ref,
)
from brailix.core.config.loader.letters import _load_letters_table
from brailix.core.config.loader.math import _load_math_table
from brailix.core.config.loader.music import (
    _load_music_specs,
    _load_music_tables,
    _load_one_music_file,
    _music_body,
    _resolve_music_cells,
)
from brailix.core.config.loader.numbers import _load_numbers_table
from brailix.core.config.loader.punct import _load_punct_spacing, _load_punct_table
from brailix.core.config.loader.zh import (
    _build_shorthand,
    _load_compounds,
    _load_zh_exceptions,
    _load_zh_exceptions_char_overrides,
    _load_zh_exceptions_tone_omission,
    _load_zh_exceptions_word_overrides,
    _resolve_cell_ref_list,
)
from brailix.core.config.profile import BrailleProfile
from brailix.core.config.validator import (
    _validate_profile_shape,
    validate_profile,
)

PACKAGE_ROOT: Path = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_profile(
    name: str,
    root: Path | None = None,
    *,
    extra_search_paths: list[Path] | tuple[Path, ...] | None = None,
) -> BrailleProfile:
    """Load a profile by name from ``brailix/profiles/<name>.json``.

    ``root`` overrides the package root (useful for tests).

    ``extra_search_paths`` lets the caller (any front-end) inject user-
    folder profile directories ahead of the builtin ones: a packaged
    front-end can wire its portable ``<exe_dir>/profiles/`` here so a
    user-dropped profile can override a same-named builtin without
    touching the package.  Tables (``resources/...``) referenced from a
    user-folder profile still resolve against ``root`` — only the top-
    level ``<name>.json`` file is looked up in the extra paths.

    Raises :class:`FileNotFoundError` if the profile is missing from
    every candidate path; the message lists the union of profile names
    found across all paths so users see the available names. Raises
    :class:`ConfigurationError` if the profile JSON or any referenced
    table is malformed (bad entity name, unresolved ref, cycle, bad
    role, missing required field, ...).
    """
    base = root if root is not None else PACKAGE_ROOT
    extras = tuple(Path(p) for p in (extra_search_paths or ()))

    profile_path = _resolve_profile_path(name, base, extras)

    payload = _read_json(profile_path)
    # Up-front shape check: catches the catastrophic cases (root not a
    # dict, missing 'name' / 'tables') before we start chasing tables
    # downstream. Per-table content checks happen at the end via
    # :func:`validate_profile`.
    _validate_profile_shape(payload, str(profile_path))
    tables = payload["tables"]

    # Cells pool loaded first — all spec tables reference its names
    # (``c_*``) via the ``cells`` field. Empty if the profile doesn't
    # include the pool (each spec then must inline cells via the
    # literal ``dots`` form).
    cells_pool = _load_cells_pool(base, tables.get("cells"))

    # Tables can live either at the top level (older / fixture
    # profiles) or nested under ``zh`` / ``math`` (new shape per
    # design §3.7). :func:`_table_ref` resolves both shapes.
    def t(key: str) -> Any:
        return _table_ref(tables, "zh", key)

    initials = _load_table(base, t("initials"), cells_pool, group="initials")
    finals = _load_table(base, t("finals"), cells_pool, group="finals")
    tones = _load_table(base, t("tones"), cells_pool, group="tones")
    punctuation = _load_punct_table(base, t("punctuation"), cells_pool)
    punctuation_spacing = _load_punct_spacing(base, t("punctuation"))
    numbers = _load_numbers_table(base, t("numbers"), cells_pool)
    math_tables = _section(tables, "math")
    math = _load_math_table(base, math_tables, cells_pool)
    music = _load_music_tables(base, _section(tables, "music"), cells_pool)
    music_specs = _load_music_specs(base, _section(tables, "music"))
    latin_letters = _load_letters_table(base, tables.get("latin"), cells_pool)
    greek_letters = _load_letters_table(base, tables.get("greek"), cells_pool)

    # English IPA phonetic table (top-level ``tables.phonetic``) — a
    # single file of IPA phoneme -> cell sequence, language/scheme-neutral
    # the way ``tables.connector`` is. Absent → {} and the phonetic
    # backend flags any phonetic region it meets.
    phonetic = _load_phonetic_table(base, tables.get("phonetic"), cells_pool)

    # connector (⠤) — single cell, used by the backend for letter+hanzi
    # compound joiners (Connector). Top-level ``tables.connector`` (a
    # bare cells-pool ref like ``"c_36"``); absent → () and Connector
    # nodes degrade to a blank cell.
    connector = _resolve_single(tables.get("connector"), cells_pool)

    # NCB exceptions — one optional resource per profile that contains
    # all NCB-specific data (tone omission rules, char overrides, word
    # overrides).  Profile loader is the only entry point for JSON I/O;
    # backend just reads ``profile.zh_exceptions``.  cn_current
    # doesn't declare it → field stays None → all NCB call sites no-op.
    zh_exceptions = _load_zh_exceptions(
        base, _table_ref(tables, "zh", "exceptions"), cells_pool
    )
    zh_compounds = _load_compounds(base, t("compounds"))

    # Generic per-language table slot (ARCHITECTURE §7.6): load every
    # ``tables.<lang>`` cell-sequence table for a non-zh language.
    lang_tables = _load_lang_tables(base, payload, tables, cells_pool)

    features = dict(payload.get("features", {}))

    profile = BrailleProfile(
        name=payload.get("name", name),
        language=payload["language"],
        cell=payload.get("cell", "six_dot"),
        features=features,
        initials=initials,
        finals=finals,
        tones=tones,
        punctuation=punctuation,
        punctuation_spacing=punctuation_spacing,
        digits=numbers["digits"],
        number_sign=numbers["number_sign"],
        decimal_point=numbers["decimal_point"],
        thousands_sep=numbers["thousands_sep"],
        connector=connector,
        zh_compounds=zh_compounds,
        math_symbols=math["symbols"],
        math_functions=math["functions"],
        math_structures=math["structures"],
        math_digits_lower=math["digits_lower"],
        math_symbol_spacing=math["symbol_spacing"],
        math_symbol_roles=math["symbol_roles"],
        math_symbol_accent_marks=math["symbol_accent_mark"],
        math_symbol_script_prefix_flags=math["symbol_script_prefix"],
        math_symbol_provisional_flags=math["symbol_provisional"],
        math_symbol_indicator_flags=math["symbol_indicator"],
        math_function_big_op_flags=math["function_big_op"],
        math_function_script_prefix_flags=math["function_script_prefix"],
        latin_letters=latin_letters,
        greek_letters=greek_letters,
        zh_exceptions=zh_exceptions,
        music=music,
        music_specs=music_specs,
        phonetic=phonetic,
        lang_tables=lang_tables,
    )
    validate_profile(profile, payload, base, str(profile_path))
    return profile


def _load_phonetic_table(
    base: Path,
    relative: str | None,
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Load the English IPA phonetic table (``tables.phonetic``).

    One resource file mapping each IPA phoneme string to a cell sequence
    (multi-cell for diphthongs / affricates / long vowels). Entries live
    under a top-level ``phonetic`` group so the metadata keys (schema /
    name / reference) sit beside them and aren't mistaken for cell refs —
    the same shape the math loader reads its ``symbols`` section through.
    Absent ref → ``{}`` (no phonetic support; the backend flags any
    phonetic region it meets)."""
    if not relative:
        return {}
    payload = _read_json(base / relative)
    group = payload.get("phonetic")
    src = group if isinstance(group, dict) else payload
    return _resolve_table(src, cells_pool)


def _load_lang_table(
    base: Path,
    key: str,
    relative: str,
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Load one per-language cell-sequence table (e.g. ja ``kana``).

    Entries may be single- or multi-cell (a Japanese dakuon / youon
    mora is two cells), so this goes through the cell-sequence resolver
    :func:`_resolve_table` rather than the single-cell one. Entries live
    either under a group named ``key`` (matching the profile's
    ``tables.<lang>.<key>``) or at the file's top level.
    """
    payload = _read_json(base / relative)
    group = payload.get(key)
    src = group if isinstance(group, dict) else payload
    return _resolve_table(src, cells_pool)


def _load_lang_tables(
    base: Path,
    payload: dict[str, Any],
    tables: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, dict[str, dict[str, tuple[tuple[int, ...], ...]]]]:
    """Load the generic per-language table slot (ARCHITECTURE §7.6).

    For any non-zh language, load every cell-sequence table declared
    under ``tables.<lang>`` into ``{<lang>: {<name>: table}}`` keyed by
    the same name. zh keeps its welded loaders (initials / finals /
    tones / exceptions / compounds); when zh migrates to this slot, drop
    the guard. The subtag is taken before the hyphen so ``ja-JP`` ->
    ``ja``.
    """
    lang_tables: dict[
        str, dict[str, dict[str, tuple[tuple[int, ...], ...]]]
    ] = {}
    lang_subtag = payload["language"].split("-")[0]
    lang_section = tables.get(lang_subtag)
    if lang_subtag != "zh" and isinstance(lang_section, dict):
        loaded: dict[str, dict[str, tuple[tuple[int, ...], ...]]] = {}
        for tbl_key, ref in lang_section.items():
            # ``_note`` / other ``_*`` metadata keys carry doc strings, not
            # table paths; skip them so a documented ``tables.<lang>`` block
            # doesn't try to load the metadata value as a resource file
            # (a raw FileNotFoundError would otherwise escape load_profile).
            if _is_metadata_key(tbl_key):
                continue
            if isinstance(ref, str):
                loaded[tbl_key] = _load_lang_table(
                    base, tbl_key, ref, cells_pool
                )
        if loaded:
            lang_tables[lang_subtag] = loaded
    return lang_tables


def iter_builtin_profiles(
    root: Path | None = None,
    *,
    extra_search_paths: list[Path] | tuple[Path, ...] | None = None,
) -> list[str]:
    """Return sorted profile names (without ``.json``) discoverable by
    :func:`load_profile`.

    ``root`` overrides the package root (same semantics as
    :func:`load_profile`).  ``extra_search_paths`` lets front-ends
    enumerate user-folder profiles alongside the builtin ones — pass
    the same paths you'd hand to :func:`load_profile` and the returned
    list reflects everything ``load_profile`` could resolve.

    Front-end equivalent of "what profiles ship with this install"
    without coupling to the filesystem layout.
    """
    base = root if root is not None else PACKAGE_ROOT
    extras = tuple(Path(p) for p in (extra_search_paths or ()))
    return _list_available_profiles(base, extras)


@lru_cache(maxsize=1)
def load_builtin_numbers_table() -> dict[str, Any]:
    """Parse the builtin universal numbers resource:
    ``resources/numbers.json`` resolved against the builtin
    ``resources/cells.json`` pool — the same parse a profile that
    references the builtin tables gets, minus the profile.

    For callers below the profile layer that need the universal digit
    cells without adopting a language — the layout paginator reads its
    page-number cells here.  Returns the :func:`_load_numbers_table`
    shape (``number_sign`` / ``digits`` / ``decimal_point`` /
    ``thousands_sep``).  Cached: treat the returned dict as read-only.
    """
    cells_pool = _load_cells_pool(PACKAGE_ROOT, "resources/cells.json")
    return _load_numbers_table(
        PACKAGE_ROOT, "resources/numbers.json", cells_pool
    )


def _resolve_profile_path(
    name: str, base: Path, extras: tuple[Path, ...]
) -> Path:
    """Locate ``<name>.json``. User-folder ``extras`` win — a same-named
    user profile shadows the builtin — then ``base/profiles`` as the
    fallback. Raises :class:`FileNotFoundError`, listing the union of
    available profile names, when it is found in none of them."""
    for candidate_dir in extras:
        candidate = candidate_dir / f"{name}.json"
        if candidate.exists():
            return candidate
    builtin_path = base / "profiles" / f"{name}.json"
    if builtin_path.exists():
        return builtin_path

    available = _list_available_profiles(base, extras)
    if available:
        hint = f"; available: {', '.join(available)}"
    else:
        searched = ", ".join(str(p) for p in (*extras, base / "profiles"))
        hint = f"; no profiles found under {searched}"
    target = base / "profiles" / f"{name}.json"
    raise FileNotFoundError(f"profile not found: {target}{hint}")


def _list_available_profiles(
    base: Path, extras: tuple[Path, ...] = ()
) -> list[str]:
    """Return the names (without .json) of profiles found under
    ``base/profiles`` and any extra search paths.  Used to make
    ``load_profile`` errors actionable instead of just naming the
    missing file."""
    names: set[str] = set()
    for directory in (*extras, base / "profiles"):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            names.add(path.stem)
    return sorted(names)


__all__ = (
    # Public API
    "BrailleProfile",
    "PACKAGE_ROOT",
    "iter_builtin_profiles",
    "load_builtin_numbers_table",
    "load_profile",
    # Private helpers re-exported for backward compat (tests + the
    # ``brailix.core.config`` package facade depend on these import
    # paths).  Listed in alphabetical order for readability.
    "_build_shorthand",
    "_coerce_dots_field",
    "_extract_cells",
    "_flag_dict_bool",
    "_flag_dict_str",
    "_is_spec_object",
    "_list_available_profiles",
    "_load_cells_pool",
    "_load_lang_table",
    "_load_lang_tables",
    "_load_letters_table",
    "_load_math_table",
    "_load_music_tables",
    "_load_numbers_table",
    "_load_one_music_file",
    "_load_phonetic_table",
    "_load_punct_spacing",
    "_load_punct_table",
    "_load_table",
    "_load_zh_exceptions",
    "_load_zh_exceptions_char_overrides",
    "_load_zh_exceptions_tone_omission",
    "_load_zh_exceptions_word_overrides",
    "_music_body",
    "_normalise_one_ref",
    "_normalise_symbols_payload",
    "_normalise_symbols_spec",
    "_read_section",
    "_resolve_cell_ref_list",
    "_resolve_digits",
    "_resolve_dots_table",
    "_resolve_music_cells",
    "_resolve_nested_structures",
    "_resolve_profile_path",
    "_resolve_single",
    "_resolve_table",
    "_section",
    "_spec_to_cells",
    "_symbol_spacing_dict",
    "_table_ref",
)
