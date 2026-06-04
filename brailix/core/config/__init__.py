"""Profile loading.

A profile is a small JSON file under :mod:`brailix.profiles` that
declares which braille standard / variant a pipeline should follow.
It references **table** files (initials, finals, tones, punctuation,
numbers, math) by paths relative to the package root.

The :class:`BrailleProfile` dataclass (in :mod:`.profile`) eagerly
resolves those tables into plain dicts (with dot tuples) so the backend
never needs to re-read files at translation time.

Math symbol table keys are MathML standard entity names (e.g.
``"plus"``, ``"ne"``, ``"int"``); the loader normalises them to
Unicode characters via :mod:`html.entities.html5` so callers can keep
querying ``profile.math_symbol("+")``.

Math structures live in a nested form (``fraction.bar``,
``script.sub``).

Two table-cell schemas are accepted, transparently:

* **Bare list** — ``"b": [1, 2]`` (terse, for hand-written tables).
* **Cell-spec object** — ``"b": {"dots": [1, 2], "brf": "b", "role":
  "..."}`` (rich, supports BRF encoding and provenance metadata).

Reserved keys at the top of each table file are ignored as metadata:
``schema``, ``name``, ``cell``, ``status``, ``source``, ``version``,
and any ``_*``.

Missing entries are simply absent — the backend treats a missing key
as "unknown" and emits a warning rather than crashing.

The package is split into four modules so each one stays readable:

* :mod:`._helpers` — entity-name resolution, JSON I/O, feature aliases
* :mod:`.profile`  — the :class:`BrailleProfile` dataclass
* :mod:`.loader`   — :func:`load_profile` + every table parser
* :mod:`.validator` — :func:`validate_profile` + per-table schema checks

All names below are re-exported from this ``__init__`` to keep the
historical ``from brailix.core.config import ...`` import paths
working unchanged.
"""

from brailix.core.config._helpers import (
    _FEATURE_DOTTED_TO_FLAT,
    _FEATURE_FLAT_ALIASES,
    _METADATA_KEYS,
    _dots_dict,
    _entity_to_char,
    _extract_dots,
    _feature_keys_to_try,
    _feature_lookup,
    _read_json,
    _to_dots,
)
from brailix.core.config.loader import (
    PACKAGE_ROOT,
    _coerce_dots_field,
    _extract_cells,
    _flag_dict_bool,
    _flag_dict_str,
    _is_spec_object,
    _list_available_profiles,
    _load_cells_pool,
    _load_letters_table,
    _load_math_table,
    _load_numbers_table,
    _load_punct_spacing,
    _load_punct_table,
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
    iter_builtin_profiles,
    load_profile,
)
from brailix.core.config.profile import BrailleProfile
from brailix.core.config.validator import (
    _VALID_SYMBOL_ROLES,
    _check_bool_flag,
    _validate_math_functions,
    _validate_math_structures,
    _validate_math_symbols,
    _validate_profile_shape,
    validate_profile,
)

__all__ = (
    "BrailleProfile",
    "PACKAGE_ROOT",
    "iter_builtin_profiles",
    "load_profile",
    "validate_profile",
)
