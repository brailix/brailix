"""Tests for the profile JSON schema validator.

The validator runs at the end of :func:`load_profile` and raises
:class:`ConfigurationError` for every shape / value violation it can
detect. The error message always names the offending file and key so a
user can jump straight to the bad entry.

Most negative-path tests use :func:`_write_profile` to scaffold a
minimal tmp profile with just enough of cn_current's shape to load,
then introduce one defect and assert the validator catches it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from brailix.core.config import (
    BrailleProfile,
    load_profile,
    validate_profile,
)
from brailix.core.errors import BrailixError, ConfigurationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cells_pool(tmp_path: Path) -> None:
    """Write a tiny ``resources/cells.json`` covering everything the test
    fixtures reference."""
    cells_dir = tmp_path / "resources"
    cells_dir.mkdir(exist_ok=True)
    (cells_dir / "cells.json").write_text(
        json.dumps({
            "c_1": [1], "c_2": [2], "c_3": [3], "c_4": [4],
            "c_5": [5], "c_6": [6],
            "c_12": [1, 2], "c_15": [1, 5], "c_36": [3, 6],
            "c_56": [5, 6], "c_125": [1, 2, 5],
            "c_235": [2, 3, 5], "c_236": [2, 3, 6],
            "c_345": [3, 4, 5], "c_2356": [2, 3, 5, 6],
        }),
        encoding="utf-8",
    )


def _write_profile(
    tmp_path: Path,
    *,
    name: str = "demo",
    extra: dict[str, Any] | None = None,
    symbols: dict[str, Any] | None = None,
    functions: dict[str, Any] | None = None,
    structures: dict[str, Any] | None = None,
    tables_override: Any = None,
) -> str:
    """Write a minimal cn_current-style profile under ``tmp_path``.

    Returns the profile name suitable for ``load_profile(name, root=tmp_path)``.
    """
    _write_cells_pool(tmp_path)
    math_dir = tmp_path / "resources" / "math"
    math_dir.mkdir(parents=True, exist_ok=True)
    math_tables: dict[str, str] = {}
    if symbols is not None:
        (math_dir / "symbols.json").write_text(
            json.dumps({"symbols": symbols}), encoding="utf-8"
        )
        math_tables["symbols"] = "resources/math/symbols.json"
    if functions is not None:
        (math_dir / "functions.json").write_text(
            json.dumps({"functions": functions}), encoding="utf-8"
        )
        math_tables["functions"] = "resources/math/functions.json"
    if structures is not None:
        (math_dir / "structures.json").write_text(
            json.dumps({"structures": structures}), encoding="utf-8",
        )
        math_tables["structures"] = "resources/math/structures.json"

    profile_body: dict[str, Any] = {
        "name": name,
        "language": "zh-CN",
        "cell": "six_dot",
    }
    if tables_override is not None:
        profile_body["tables"] = tables_override
    else:
        tables: dict[str, Any] = {"cells": "resources/cells.json"}
        if math_tables:
            tables["math"] = math_tables
        profile_body["tables"] = tables
    if extra:
        profile_body.update(extra)
    prof = tmp_path / "profiles" / f"{name}.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(json.dumps(profile_body), encoding="utf-8")
    return name


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_cn_current_loads_without_error(self):
        """Regression: validation shouldn't break the shipped profile."""
        p = load_profile("cn_current")
        assert isinstance(p, BrailleProfile)
        assert p.name == "cn_current"


# ---------------------------------------------------------------------------
# Lookup / I/O failures
# ---------------------------------------------------------------------------


class TestProfileLookupErrors:
    """``load_profile`` must surface filesystem / parse failures with
    enough context that a user can act on the message."""

    def test_missing_profile_lists_available_names(self, tmp_path):
        # When the requested name isn't found, the error should hint
        # at what *is* available so the user doesn't have to grep.
        (tmp_path / "profiles").mkdir()
        (tmp_path / "profiles" / "alpha.json").write_text("{}", encoding="utf-8")
        (tmp_path / "profiles" / "beta.json").write_text("{}", encoding="utf-8")
        with pytest.raises(FileNotFoundError) as ei:
            load_profile("gamma", root=tmp_path)
        msg = str(ei.value)
        assert "gamma" in msg
        # Both available names should appear in the hint.
        assert "alpha" in msg
        assert "beta" in msg

    def test_missing_profile_with_empty_dir_says_so(self, tmp_path):
        (tmp_path / "profiles").mkdir()
        with pytest.raises(FileNotFoundError) as ei:
            load_profile("anything", root=tmp_path)
        assert "no profiles found" in str(ei.value)

    def test_missing_profiles_dir_says_so(self, tmp_path):
        # tmp_path has no 'profiles' subdir at all.
        with pytest.raises(FileNotFoundError) as ei:
            load_profile("anything", root=tmp_path)
        assert "no profiles found" in str(ei.value)

    def test_malformed_profile_json_raises_configuration_error(self, tmp_path):
        # Truncated / invalid JSON is the single most common authoring
        # mistake and must surface as the documented ConfigurationError
        # carrying the file path — NOT a raw json.JSONDecodeError that
        # dodges the framework's catch-all.  (This test used to lock the
        # raw-exception leak in as expected behaviour.)
        (tmp_path / "profiles").mkdir()
        (tmp_path / "profiles" / "broken.json").write_text(
            "{ this isn't valid json",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile("broken", root=tmp_path)
        assert "broken.json" in str(ei.value)

    def test_missing_referenced_table_file_raises_configuration_error(
        self, tmp_path
    ):
        # The profile references a tables file that doesn't exist on
        # disk: surface the missing path as ConfigurationError, not a
        # raw FileNotFoundError.
        prof_dir = tmp_path / "profiles"
        prof_dir.mkdir()
        (prof_dir / "demo.json").write_text(
            json.dumps({
                "name": "demo",
                "tables": {
                    "cells": "resources/cells.json",  # never created
                },
            }),
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile("demo", root=tmp_path)
        assert "cells.json" in str(ei.value)

    def test_table_with_non_object_top_level_raises_configuration_error(
        self, tmp_path
    ):
        # A table whose top level is a JSON array used to blow up later
        # as a bare AttributeError inside the loader's .items() walk.
        prof_dir = tmp_path / "profiles"
        prof_dir.mkdir()
        (prof_dir / "demo.json").write_text(
            json.dumps({
                "name": "demo",
                "tables": {
                    "cells": "resources/cells.json",
                },
            }),
            encoding="utf-8",
        )
        res_dir = tmp_path / "resources"
        res_dir.mkdir()
        (res_dir / "cells.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ConfigurationError) as ei:
            load_profile("demo", root=tmp_path)
        assert "object" in str(ei.value)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestConfigurationErrorHierarchy:
    """ConfigurationError must subclass both BrailixError (so the
    framework's catch-all works) and ValueError (so legacy call sites
    that caught ValueError still match)."""

    def test_subclasses_brailix_error(self):
        assert issubclass(ConfigurationError, BrailixError)

    def test_subclasses_value_error(self):
        assert issubclass(ConfigurationError, ValueError)


class TestLangTableMetadataKeys:
    def test_lang_table_metadata_key_is_skipped(self, tmp_path):
        # A documented tables.<lang> block can carry a ``_note`` metadata
        # string; the generic loader must skip ``_*`` keys rather than try
        # to load the doc string as a resource path (a raw FileNotFoundError
        # would otherwise escape load_profile).
        name = _write_profile(
            tmp_path,
            extra={"language": "xx-XX"},
            tables_override={
                "cells": "resources/cells.json",
                "xx": {"_note": "documentation, not a path"},
            },
        )
        p = load_profile(name, root=tmp_path)
        assert isinstance(p, BrailleProfile)
        assert p.language == "xx-XX"


class TestMathSymbolAccentMark:
    _STRUCT = {"accent": {"mark": {
        "arrow": {"single": ["c_2"], "double": ["c_2", "c_3"]}
    }}}

    def test_unknown_accent_mark_kind_rejected(self, tmp_path):
        # accent_mark must name a kind declared in structures.json
        # (accent.mark.*); a typo would otherwise load and silently yield
        # empty braille for that accent.
        name = _write_profile(
            tmp_path,
            symbols={"rarr": {
                "role": "accent", "accent_mark": "arow", "cells": ["c_2"]
            }},
            structures=self._STRUCT,
        )
        with pytest.raises(ConfigurationError, match="accent_mark"):
            load_profile(name, root=tmp_path)

    def test_known_accent_mark_kind_accepted(self, tmp_path):
        name = _write_profile(
            tmp_path,
            symbols={"rarr": {
                "role": "accent", "accent_mark": "arrow", "cells": ["c_2"]
            }},
            structures=self._STRUCT,
        )
        p = load_profile(name, root=tmp_path)
        assert isinstance(p, BrailleProfile)


# ---------------------------------------------------------------------------
# symbols.json: entity-key validation
# ---------------------------------------------------------------------------


class TestSymbolsEntityValidation:
    def test_unknown_entity_raises_configuration_error(self, tmp_path):
        name = _write_profile(tmp_path, symbols={
            "definitely_not_an_entity": {"cells": ["c_1"], "role": "op"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        assert "definitely_not_an_entity" in str(ei.value)
        # Error names the file path.
        assert "symbols.json" in str(ei.value)

    def test_multi_char_entity_rejected_with_clear_message(self, tmp_path):
        # fjlig is a real html5 entity but expands to "fj"; symbols.json
        # keys must resolve to a single Unicode char.
        name = _write_profile(tmp_path, symbols={
            "fjlig": {"cells": ["c_1"], "role": "op"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "fjlig" in msg
        assert "multi-character" in msg
        assert "symbols.json" in msg

    def test_unresolved_sibling_ref_raises(self, tmp_path):
        # plus references "does_not_exist" which is neither a cell-pool
        # ref nor a sibling entity name.
        name = _write_profile(tmp_path, symbols={
            "plus": {"cells": ["does_not_exist"], "role": "op"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        # Entity normalisation runs first; "does_not_exist" isn't a
        # real entity, so we see the entity error rather than the
        # unresolved-ref error. Either is acceptable per the spec
        # — both surface "missing config" with file context.
        assert "does_not_exist" in msg
        assert "symbols.json" in msg

    def test_unresolved_function_sibling_ref_raises(self, tmp_path):
        # functions don't get entity normalisation, so an unknown
        # sibling ref hits the unresolved-ref path cleanly.
        name = _write_profile(tmp_path, functions={
            "foo": {"cells": ["does_not_exist"]},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "does_not_exist" in msg
        assert "foo" in msg
        assert "functions.json" in msg

    def test_cycle_ref_raises_with_chain(self, tmp_path):
        # Two symbols that ref each other.
        name = _write_profile(tmp_path, symbols={
            "plus":  {"cells": ["minus"], "role": "op"},
            "minus": {"cells": ["plus"],  "role": "op"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "cycle" in msg
        # Cycle chain is included so the user can see what's circular.
        assert "chain:" in msg
        assert "symbols.json" in msg


# ---------------------------------------------------------------------------
# symbols.json: role validation
# ---------------------------------------------------------------------------


class TestSymbolRoleValidation:
    def test_bad_role_value_raises(self, tmp_path):
        name = _write_profile(tmp_path, symbols={
            "plus": {"cells": ["c_235"], "role": "frobnicate"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "plus" in msg
        assert "frobnicate" in msg
        # Message mentions the allowed roles.
        for role in ("op", "rel", "delim", "punct", "shape", "big_op", "accent"):
            assert role in msg

    def test_missing_role_field_raises(self, tmp_path):
        name = _write_profile(tmp_path, symbols={
            "plus": {"cells": ["c_235"]},  # no 'role'
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "plus" in msg
        assert "role" in msg.lower()

    def test_each_valid_role_accepted(self, tmp_path):
        # Smoke test every valid role to confirm none are accidentally
        # rejected. Use one valid entity per role.
        roles_to_entity = {
            "op":     "plus",
            "rel":    "equals",
            "delim":  "lpar",
            "punct":  "comma",
            "shape":  "utri",
            "big_op": "sum",
            "accent": "prime",
        }
        symbols = {
            entity: {"cells": ["c_1"], "role": role}
            for role, entity in roles_to_entity.items()
        }
        name = _write_profile(tmp_path, symbols=symbols)
        # Should not raise.
        load_profile(name, root=tmp_path)


# ---------------------------------------------------------------------------
# bool flag validation
# ---------------------------------------------------------------------------


class TestBoolFlagValidation:
    def test_non_bool_big_op_in_symbols_raises(self, tmp_path):
        name = _write_profile(tmp_path, symbols={
            "sum": {"cells": ["c_1"], "role": "big_op", "big_op": "yes"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "sum" in msg
        assert "big_op" in msg
        # "yes" is the offending value.
        assert "'yes'" in msg or '"yes"' in msg

    def test_non_bool_script_prefix_in_symbols_raises(self, tmp_path):
        name = _write_profile(tmp_path, symbols={
            "int": {
                "cells": ["c_1"], "role": "big_op", "script_prefix": 1,
            },
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "int" in msg
        assert "script_prefix" in msg

    def test_non_bool_big_op_in_functions_raises(self, tmp_path):
        name = _write_profile(tmp_path, functions={
            "lim": {"cells": ["c_1"], "big_op": "true"},
        })
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "lim" in msg
        assert "big_op" in msg

    def test_bool_true_accepted(self, tmp_path):
        # The actual ``true`` form should load just fine.
        name = _write_profile(tmp_path, symbols={
            "int": {
                "cells": ["c_1"], "role": "big_op", "script_prefix": True,
            },
        })
        p = load_profile(name, root=tmp_path)
        assert p.math_symbol_script_prefix("∫") is True

    def test_bool_false_accepted(self, tmp_path):
        # ``false`` is explicit (and ignored by the loader) but must
        # not be rejected.
        name = _write_profile(tmp_path, symbols={
            "plus": {"cells": ["c_1"], "role": "op", "big_op": False},
        })
        load_profile(name, root=tmp_path)


# ---------------------------------------------------------------------------
# profile.json shape
# ---------------------------------------------------------------------------


class TestProfileShape:
    def test_missing_tables_raises(self, tmp_path):
        # Create profile.json with no "tables" key at all.
        prof = tmp_path / "profiles" / "no_tables.json"
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(
            json.dumps({"name": "no_tables", "language": "zh-CN", "cell": "six_dot"}),
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile("no_tables", root=tmp_path)
        msg = str(ei.value)
        assert "tables" in msg
        assert "missing" in msg.lower() or "required" in msg.lower()

    def test_missing_name_raises(self, tmp_path):
        prof = tmp_path / "profiles" / "no_name.json"
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(json.dumps({"tables": {}}), encoding="utf-8")
        with pytest.raises(ConfigurationError) as ei:
            load_profile("no_name", root=tmp_path)
        msg = str(ei.value)
        assert "name" in msg

    def test_tables_not_dict_raises(self, tmp_path):
        # tables present but with wrong type.
        prof = tmp_path / "profiles" / "bad_tables.json"
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(
            json.dumps({"name": "bad_tables", "tables": "wrong"}),
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile("bad_tables", root=tmp_path)
        msg = str(ei.value)
        assert "tables" in msg

    def test_tables_math_not_dict_raises(self, tmp_path):
        # tables.math points at a string (path) instead of a dict of
        # sub-tables — that was an old layout the new design forbids.
        name = _write_profile(
            tmp_path,
            tables_override={
                "cells": "resources/cells.json",
                "math":  "resources/math/something.json",
            },
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.math" in msg
        assert "object" in msg or "dict" in msg.lower()

    def test_tables_zh_not_dict_raises(self, tmp_path):
        name = _write_profile(
            tmp_path,
            tables_override={
                "cells": "resources/cells.json",
                "zh":    "wrong",
            },
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.zh" in msg

    def test_features_not_dict_raises(self, tmp_path):
        name = _write_profile(
            tmp_path,
            extra={"features": "wrong"},
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "features" in msg


# ---------------------------------------------------------------------------
# structures.json shape
# ---------------------------------------------------------------------------


def _write_raw_structures(tmp_path: Path, name: str, raw_payload: Any) -> str:
    """Write a tmp profile that points at a hand-built structures.json
    payload (so tests can inject malformed shapes the loader rejects)."""
    _write_cells_pool(tmp_path)
    math_dir = tmp_path / "resources" / "math"
    math_dir.mkdir(parents=True, exist_ok=True)
    (math_dir / "structures.json").write_text(
        json.dumps(raw_payload), encoding="utf-8"
    )
    prof = tmp_path / "profiles" / f"{name}.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(
        json.dumps({
            "name": name,
            "tables": {
                "cells": "resources/cells.json",
                "math":  {"structures": "resources/math/structures.json"},
            },
        }),
        encoding="utf-8",
    )
    return name


class TestStructuresShape:
    def test_missing_structures_top_level_raises(self, tmp_path):
        # structures.json without the top-level "structures" key.
        name = _write_raw_structures(
            tmp_path, "bad_structures", {"fraction": {"bar": ["c_1"]}}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        assert "structures" in str(ei.value)

    def test_category_not_dict_raises(self, tmp_path):
        # structures.json's "fraction" value is a list rather than a dict.
        name = _write_raw_structures(
            tmp_path, "bad_cat", {"structures": {"fraction": ["c_1"]}}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        assert "fraction" in str(ei.value)

    def test_liberal_about_new_categories(self, tmp_path):
        # A future category (not in fraction/script/sqrt/letter_prefix/
        # function) should be accepted as long as it's a dict.
        name = _write_raw_structures(
            tmp_path, "future_cat",
            {"structures": {"future_category": {"marker": ["c_1"]}}},
        )
        # Should load without complaint.
        p = load_profile(name, root=tmp_path)
        assert p.math_structure("future_category.marker") == ((1,),)


# ---------------------------------------------------------------------------
# Direct call to validate_profile
# ---------------------------------------------------------------------------


class TestDirectValidateCall:
    """validate_profile is a public callable; some hosts may want to
    re-validate a profile (e.g. after hot-reload). Spot-check it can be
    invoked directly on an already-loaded profile."""

    def test_can_be_called_with_loaded_profile(self, tmp_path):
        # Construct a minimal valid profile, load it, then re-validate.
        name = _write_profile(tmp_path, symbols={
            "plus": {"cells": ["c_235"], "role": "op"},
        })
        p = load_profile(name, root=tmp_path)
        prof_path = tmp_path / "profiles" / f"{name}.json"
        payload = json.loads(prof_path.read_text(encoding="utf-8"))
        # No exception: re-validation passes.
        validate_profile(p, payload, tmp_path, str(prof_path))


# ---------------------------------------------------------------------------
# _validate_profile_shape — non-dict root
# ---------------------------------------------------------------------------


class TestProfileShapeNonDictRoot:
    def test_non_dict_root_raises(self, tmp_path):
        # profile.json content is a JSON array, not an object.
        prof = tmp_path / "profiles" / "list_root.json"
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ConfigurationError) as ei:
            load_profile("list_root", root=tmp_path)
        assert "JSON object" in str(ei.value)


# ---------------------------------------------------------------------------
# _validate_math_symbols — non-dict symbols section and metadata keys
# ---------------------------------------------------------------------------


def _write_raw_symbols(tmp_path: Path, name: str, raw_payload: Any) -> str:
    """Write a tmp profile pointed at a hand-built symbols.json (for
    tests that need to inject a non-dict ``symbols`` section)."""
    _write_cells_pool(tmp_path)
    math_dir = tmp_path / "resources" / "math"
    math_dir.mkdir(parents=True, exist_ok=True)
    (math_dir / "symbols.json").write_text(
        json.dumps(raw_payload), encoding="utf-8"
    )
    prof = tmp_path / "profiles" / f"{name}.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(
        json.dumps({
            "name": name,
            "tables": {
                "cells": "resources/cells.json",
                "math": {"symbols": "resources/math/symbols.json"},
            },
        }),
        encoding="utf-8",
    )
    return name


def _write_raw_functions(tmp_path: Path, name: str, raw_payload: Any) -> str:
    """Write a tmp profile pointed at a hand-built functions.json."""
    _write_cells_pool(tmp_path)
    math_dir = tmp_path / "resources" / "math"
    math_dir.mkdir(parents=True, exist_ok=True)
    (math_dir / "functions.json").write_text(
        json.dumps(raw_payload), encoding="utf-8"
    )
    prof = tmp_path / "profiles" / f"{name}.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(
        json.dumps({
            "name": name,
            "tables": {
                "cells": "resources/cells.json",
                "math": {"functions": "resources/math/functions.json"},
            },
        }),
        encoding="utf-8",
    )
    return name


class TestSymbolsValidationDefensiveBranches:
    def test_non_dict_symbols_section_is_tolerated(self, tmp_path):
        # symbols.json without a dict ``symbols`` section → validator
        # returns without raising (no entries to check).
        name = _write_raw_symbols(
            tmp_path, "no_symbols", {"symbols": "not a dict"}
        )
        # No ConfigurationError.
        load_profile(name, root=tmp_path)

    def test_metadata_keys_are_skipped(self, tmp_path):
        # ``_note`` should be silently ignored by the validator (the
        # ``continue`` branch). The good entry next to it loads fine.
        name = _write_profile(tmp_path, symbols={
            "_note": "comment only",
            "plus": {"cells": ["c_235"], "role": "op"},
        })
        load_profile(name, root=tmp_path)

    def test_bare_list_entry_raises_with_clear_message(self, tmp_path):
        # The loader accepts bare-list specs in other tables, but
        # symbols.json entries MUST be JSON objects carrying ``role``.
        # A bare list triggers the "must be a JSON object" branch.
        name = _write_raw_symbols(
            tmp_path, "bare_list",
            {"symbols": {"plus": ["c_235"]}},
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "plus" in msg
        assert "JSON object" in msg


class TestFunctionsValidationDefensiveBranches:
    def test_non_dict_functions_section_is_tolerated(self, tmp_path):
        # functions.json without a dict ``functions`` section → validator
        # returns without raising.
        name = _write_raw_functions(
            tmp_path, "no_functions",
            {"functions": "not a dict"},
        )
        load_profile(name, root=tmp_path)

    def test_metadata_keys_in_functions_are_skipped(self, tmp_path):
        # ``_note`` should be silently ignored; the next valid entry
        # still validates as bool-typed flags.
        name = _write_profile(tmp_path, functions={
            "_note": "this is a note",
            "lim": {"cells": ["c_1"], "big_op": True},
        })
        load_profile(name, root=tmp_path)

    def test_non_dict_function_spec_is_skipped(self, tmp_path):
        # A bare-list functions entry is accepted by the loader (it's
        # just a cell-ref list), so the validator should ``continue``
        # past it rather than raise. The bool-flag checks only apply
        # to dict-shaped entries.
        name = _write_profile(tmp_path, functions={
            "abbreviation_only": ["c_1"],
        })
        load_profile(name, root=tmp_path)


# ---------------------------------------------------------------------------
# _validate_math_structures — defensive shape paths
# ---------------------------------------------------------------------------


class TestStructuresValidationDefensiveBranches:
    def test_non_dict_root_payload_raises_when_validator_called_directly(
        self, tmp_path
    ):
        # The validator's "must be a JSON object" branch fires only
        # when ``validate_profile`` (or ``_validate_math_structures``)
        # is invoked on a hand-crafted file. ``load_profile`` itself
        # never reaches this branch because ``_read_section`` would
        # have already produced an empty dict for a malformed
        # structures.json. Test it via a direct call.
        from brailix.core.config import _validate_math_structures

        math_dir = tmp_path / "resources" / "math"
        math_dir.mkdir(parents=True, exist_ok=True)
        (math_dir / "structures.json").write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )
        with pytest.raises(ConfigurationError) as ei:
            _validate_math_structures(tmp_path, "resources/math/structures.json")
        assert "JSON object" in str(ei.value)

    def test_structures_field_not_dict_raises_when_validator_called_directly(
        self, tmp_path
    ):
        # structures.json with ``{"structures": "not a dict"}`` —
        # ``_load_math_table`` reads it through ``_read_section`` which
        # returns ``{}`` (not a dict) and silently drops the section,
        # so the loader never reaches the validator branch. Direct
        # call exposes the branch.
        from brailix.core.config import _validate_math_structures

        math_dir = tmp_path / "resources" / "math"
        math_dir.mkdir(parents=True, exist_ok=True)
        (math_dir / "structures.json").write_text(
            json.dumps({"structures": "not a dict"}), encoding="utf-8"
        )
        with pytest.raises(ConfigurationError) as ei:
            _validate_math_structures(tmp_path, "resources/math/structures.json")
        msg = str(ei.value)
        assert "structures" in msg.lower()
        assert "object" in msg

    def test_metadata_categories_are_skipped(self, tmp_path):
        # ``_note`` at the categories level should be skipped silently;
        # the next real entry validates normally.
        name = _write_raw_structures(
            tmp_path, "categories_with_meta",
            {"structures": {
                "_note": "free-form comment, must be skipped",
                "fraction": {"bar": ["c_1"]},
            }},
        )
        load_profile(name, root=tmp_path)


# ---------------------------------------------------------------------------
# Private helpers — direct exercise of branches the loader rarely reaches
# ---------------------------------------------------------------------------


class TestNormaliseSymbolsSpecHelpers:
    """``_normalise_symbols_spec`` walks a symbols-table value and
    rewrites entity-name refs to their Unicode characters. The string
    arm and the 'unknown shape' fall-through aren't exercised by
    realistic profile JSON because the loader prefers ``{"cells": [...]}``
    spec objects."""

    def test_string_spec_resolves_via_entity_or_pool(self):
        from brailix.core.config import _normalise_symbols_spec

        # A bare-string spec referring to a cell-pool entry survives
        # unchanged.
        out = _normalise_symbols_spec("c_1", {"c_1": (1,)}, file=None)
        assert out == "c_1"

    def test_non_str_non_list_non_dict_spec_passes_through(self):
        from brailix.core.config import _normalise_symbols_spec

        # Numeric / None / other scalar specs are returned untouched
        # (the loader will reject them downstream, but this helper
        # only normalises ref names).
        assert _normalise_symbols_spec(42, {}, file=None) == 42
        assert _normalise_symbols_spec(None, {}, file=None) is None
        assert _normalise_symbols_spec(True, {}, file=None) is True

    def test_list_spec_resolves_each_ref(self):
        # Sanity: the list arm of _normalise_symbols_spec runs ref-by-ref
        # and preserves cell-pool refs.
        from brailix.core.config import _normalise_symbols_spec

        out = _normalise_symbols_spec(["c_1", "c_2"], {"c_1": (1,), "c_2": (2,)})
        assert out == ["c_1", "c_2"]


class TestNormaliseOneRefHelper:
    def test_non_string_ref_passes_through(self):
        # Anything other than ``str`` is returned unchanged — the loader
        # never feeds non-strings to this helper through the public
        # path, but the defensive branch is here for safety.
        from brailix.core.config import _normalise_one_ref

        assert _normalise_one_ref(42, {}) == 42
        assert _normalise_one_ref(None, {}) is None


class TestFlagDictStrMetadata:
    """``_flag_dict_str`` strips out metadata keys before scanning for
    the flag. Hit the ``continue`` branch with an underscore-prefixed
    key alongside a real entry."""

    def test_metadata_keys_skipped(self):
        from brailix.core.config import _flag_dict_str

        payload = {
            "_note": {"role": "ignored"},
            "schema": {"role": "ignored"},
            "real": {"role": "op"},
        }
        out = _flag_dict_str(payload, "role")
        # Only ``real`` survives; metadata keys filtered out.
        assert out == {"real": "op"}


# ---------------------------------------------------------------------------
# _spec_to_cells — defensive return shapes
# ---------------------------------------------------------------------------


class TestSpecToCellsDefensive:
    def test_list_with_mixed_types_returns_none(self):
        # A list mixing ints and strings is neither an inline-dots
        # cell nor a ref list — the helper returns None and the loader
        # filters that entry out.
        from brailix.core.config import _resolve_table

        out = _resolve_table(
            {"junk": [1, "c_1"]}, {"c_1": (1,)}
        )
        # Mixed-type spec was rejected; key didn't survive.
        assert "junk" not in out

    def test_dict_with_dots_field_resolves_inline(self):
        # ``{"dots": [1, 2]}`` is an inline literal — bypasses ref
        # lookup entirely. Use a key that isn't in ``_METADATA_KEYS``
        # so the resolver actually visits the entry.
        from brailix.core.config import _resolve_table

        out = _resolve_table({"entry": {"dots": [1, 2]}}, {})
        assert out["entry"] == ((1, 2),)

    def test_dict_without_cells_or_dots_returns_none(self):
        # An object spec with neither ``cells`` nor ``dots`` is unknown
        # to the resolver; the entry is silently dropped.
        from brailix.core.config import _resolve_table

        out = _resolve_table(
            {"entry": {"role": "op"}},  # no cells, no dots
            {},
        )
        assert "entry" not in out


# ---------------------------------------------------------------------------
# _resolve_single — None and multi-cell paths
# ---------------------------------------------------------------------------


class TestResolveSingleDefensive:
    def test_none_spec_returns_empty_tuple(self):
        from brailix.core.config import _resolve_single

        assert _resolve_single(None, {}) == ()

    def test_multi_cell_spec_falls_through_to_empty(self):
        # When the spec resolves to more than one cell, the
        # single-cell convenience wrapper returns ``()`` rather than
        # silently picking one. Hit that branch via a multi-cell
        # inline ``dots`` field.
        from brailix.core.config import _resolve_single

        # Two cells inline → wrapper falls through to ``()``.
        assert _resolve_single({"dots": [[1, 2], [3, 4]]}, {}) == ()


# ---------------------------------------------------------------------------
# _load_letters_table — missing/non-dict ``letters`` section
# ---------------------------------------------------------------------------


class TestLoadLettersTable:
    def test_payload_without_letters_section_returns_empty_subgroups(
        self, tmp_path
    ):
        # letters file exists but has no ``letters`` key → loader
        # returns the default ``{"lower": {}, "upper": {}}``.
        latin_dir = tmp_path / "resources" / "latin"
        latin_dir.mkdir(parents=True, exist_ok=True)
        (latin_dir / "letters.json").write_text(
            json.dumps({"unrelated": "payload"}), encoding="utf-8"
        )
        from brailix.core.config import _load_letters_table

        out = _load_letters_table(
            tmp_path, "resources/latin/letters.json", {}
        )
        assert out == {"lower": {}, "upper": {}}


# ---------------------------------------------------------------------------
# _symbol_spacing_dict — metadata skip
# ---------------------------------------------------------------------------


class TestSymbolSpacingDict:
    def test_metadata_keys_skipped(self):
        from brailix.core.config import _symbol_spacing_dict

        payload = {
            "_note": {"space_before": True},
            "schema": {"space_after": True},
            "plus": {"space_before": True, "space_after": False},
        }
        out = _symbol_spacing_dict(payload)
        # Only ``plus`` survives.
        assert set(out) == {"plus"}
        assert out["plus"] == (True, False)


# ---------------------------------------------------------------------------
# _load_punct_spacing / _load_punct_table — non-grouped payload
# ---------------------------------------------------------------------------


class TestLoadPunctNonGrouped:
    """Both punct loaders accept either ``{"punctuation": {...}}`` or
    a flat dict at the root. The flat-root path doesn't appear in
    cn_current's shipped table; cover it explicitly."""

    def test_load_punct_spacing_from_flat_payload(self, tmp_path):
        # Punct payload without the ``punctuation`` wrapper key —
        # the loader falls back to treating the file root as the
        # punctuation table.
        zh_dir = tmp_path / "resources"
        zh_dir.mkdir(parents=True, exist_ok=True)
        (zh_dir / "punct_flat.json").write_text(
            json.dumps({
                ",": {"cells": ["c_2"], "space_after": True},
            }),
            encoding="utf-8",
        )
        from brailix.core.config import _load_punct_spacing

        out = _load_punct_spacing(tmp_path, "resources/punct_flat.json")
        assert "," in out
        assert out[","] == (False, True)

    def test_load_punct_table_from_flat_payload(self, tmp_path):
        zh_dir = tmp_path / "resources"
        zh_dir.mkdir(parents=True, exist_ok=True)
        (zh_dir / "cells.json").write_text(
            json.dumps({"c_2": [2]}), encoding="utf-8",
        )
        (zh_dir / "punct_flat.json").write_text(
            json.dumps({",": "c_2"}),
            encoding="utf-8",
        )
        from brailix.core.config import _load_punct_table

        out = _load_punct_table(
            tmp_path, "resources/punct_flat.json", {"c_2": (2,)}
        )
        assert out[","] == ((2,),)


# ---------------------------------------------------------------------------
# _coerce_dots_field — empty list / single-cell inline
# ---------------------------------------------------------------------------


class TestCoerceDotsField:
    def test_empty_list_returns_empty_tuple(self):
        # Empty inline ``dots`` = neutral / no-op cell sequence.
        from brailix.core.config import _coerce_dots_field

        assert _coerce_dots_field([]) == ()

    def test_int_list_wraps_as_single_cell(self):
        # ``[1, 2]`` = one inline cell (dots 1 and 2). The helper
        # wraps it into a one-element tuple.
        from brailix.core.config import _coerce_dots_field

        assert _coerce_dots_field([1, 2]) == ((1, 2),)


# ---------------------------------------------------------------------------
# Per-language ``tables.<lang>`` slot (§7.6)
# ---------------------------------------------------------------------------


def _write_ja_profile(
    tmp_path: Path,
    *,
    name: str = "ja_demo",
    kana_payload: Any = None,
    ja_section: Any = "__default__",
) -> str:
    """Write a minimal ja-language profile under ``tmp_path``.

    ``kana_payload`` is the raw kana.json content (defaults to a tiny but
    valid ``{"kana": {"ア": "c_1"}}``). ``ja_section`` is the value placed
    at ``tables.ja`` (defaults to a slot referencing the written kana
    file); pass ``None`` to omit the slot entirely, or any other value to
    inject a malformed slot.
    """
    _write_cells_pool(tmp_path)
    res_dir = tmp_path / "resources" / "ja"
    res_dir.mkdir(parents=True, exist_ok=True)
    if kana_payload is None:
        kana_payload = {"kana": {"ア": "c_1", "イ": "c_12"}}
    (res_dir / "kana.json").write_text(
        json.dumps(kana_payload), encoding="utf-8"
    )

    if ja_section == "__default__":
        ja_section = {"kana": "resources/ja/kana.json"}

    tables: dict[str, Any] = {"cells": "resources/cells.json"}
    if ja_section is not None:
        tables["ja"] = ja_section

    prof = tmp_path / "profiles" / f"{name}.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(
        json.dumps({
            "name": name,
            "language": "ja-JP",
            "cell": "six_dot",
            "tables": tables,
        }),
        encoding="utf-8",
    )
    return name


class TestLangTablesValidation:
    def test_ja_current_loads_without_error(self):
        # Regression: the shipped ja profile must still validate.
        p = load_profile("ja_current")
        assert p.name == "ja_current"
        assert p.lang_tables["ja"]["kana"]  # non-empty

    def test_minimal_ja_profile_loads(self, tmp_path):
        name = _write_ja_profile(tmp_path)
        p = load_profile(name, root=tmp_path)
        assert p.lang_tables["ja"]["kana"]["ア"] == ((1,),)

    def test_missing_ja_slot_raises(self, tmp_path):
        # language is ja but tables.ja is absent → required group missing.
        name = _write_ja_profile(tmp_path, ja_section=None)
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.ja" in msg
        assert "kana" in msg

    def test_ja_slot_not_dict_raises(self, tmp_path):
        name = _write_ja_profile(tmp_path, ja_section="resources/ja/kana.json")
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.ja" in msg
        assert "object" in msg or "dict" in msg.lower()

    def test_missing_required_kana_group_raises(self, tmp_path):
        # tables.ja present but missing the required ``kana`` group.
        name = _write_ja_profile(
            tmp_path, ja_section={"punct": "resources/ja/kana.json"}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.ja" in msg
        assert "kana" in msg

    def test_non_string_group_ref_raises(self, tmp_path):
        # A mistyped ref (object instead of a path string) is silently
        # dropped by the loader; the validator must reject it.
        name = _write_ja_profile(
            tmp_path, ja_section={"kana": {"oops": "not a ref"}}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "tables.ja.kana" in msg

    def test_empty_kana_table_raises(self, tmp_path):
        # kana.json has a ``kana`` group with no real entries.
        name = _write_ja_profile(
            tmp_path, kana_payload={"kana": {"_note": "comment only"}}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "kana" in msg
        assert "no cell entries" in msg

    def test_bad_cell_spec_in_kana_table_raises(self, tmp_path):
        # An entry whose value is neither a ref string, a non-empty list,
        # nor a cells/dots object — the loader drops it silently.
        name = _write_ja_profile(
            tmp_path, kana_payload={"kana": {"ア": "c_1", "イ": 42}}
        )
        with pytest.raises(ConfigurationError) as ei:
            load_profile(name, root=tmp_path)
        msg = str(ei.value)
        assert "イ" in msg
        assert "cell spec" in msg

    def test_valid_cell_spec_shapes_accepted(self, tmp_path):
        # str ref, bare list of refs, and dots/cells objects all load.
        name = _write_ja_profile(
            tmp_path,
            kana_payload={"kana": {
                "ア": "c_1",
                "イ": ["c_1", "c_2"],
                "ウ": {"dots": [1, 4]},
                "エ": {"cells": ["c_12"]},
            }},
        )
        # Should not raise.
        load_profile(name, root=tmp_path)

    def test_kana_table_top_level_fallback(self, tmp_path):
        # kana.json may hold entries at the top level (no ``kana``
        # wrapper) — the loader and validator both fall back to it.
        name = _write_ja_profile(
            tmp_path, kana_payload={"ア": "c_1", "イ": "c_12"}
        )
        p = load_profile(name, root=tmp_path)
        assert p.lang_tables["ja"]["kana"]["ア"] == ((1,),)
