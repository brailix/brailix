import json
from pathlib import Path

import pytest

from brailix.core.config import (
    PACKAGE_ROOT,
    BrailleProfile,
    _dots_dict,
    _entity_to_char,
    _extract_dots,
    _to_dots,
    load_profile,
)


class TestPackageRoot:
    def test_package_root_exists(self):
        assert PACKAGE_ROOT.exists()
        assert (PACKAGE_ROOT / "__init__.py").exists()
        assert (PACKAGE_ROOT / "profiles").exists()
        assert (PACKAGE_ROOT / "resources").exists()


class TestCnDefaultProfile:
    def test_loads(self):
        p = load_profile("cn_current")
        assert isinstance(p, BrailleProfile)
        assert p.name == "cn_current"
        assert p.language == "zh-CN"
        assert p.cell == "six_dot"

    def test_features_present(self):
        p = load_profile("cn_current")
        # Legacy flat lookup still works (zh.tone alias).
        assert p.feature("tone") is True
        assert p.feature("number_sign") is True
        # New dotted lookup.
        assert p.feature("zh.tone") is True
        assert p.feature("zh.number_sign") is True
        assert p.feature("nonexistent", "fallback") == "fallback"

    def test_initials_table_loaded(self):
        p = load_profile("cn_current")
        assert "zh" in p.initials
        assert "b" in p.initials
        assert p.initials["b"] == (1, 2)
        # All values are tuples of ints
        for v in p.initials.values():
            assert isinstance(v, tuple)
            assert all(isinstance(d, int) for d in v)

    def test_finals_table_loaded(self):
        p = load_profile("cn_current")
        assert "a" in p.finals
        assert "uo" in p.finals
        assert "ü" in p.finals

    def test_tones_table_loaded(self):
        p = load_profile("cn_current")
        # All 5 tones present.
        assert set(p.tones) >= {"1", "2", "3", "4", "5"}
        assert p.tones["5"] == ()  # neutral tone is blank

    def test_punctuation_table_loaded(self):
        p = load_profile("cn_current")
        assert "，" in p.punctuation
        assert "。" in p.punctuation

    def test_number_data_loaded(self):
        p = load_profile("cn_current")
        assert p.number_sign == (3, 4, 5, 6)
        assert set(p.digits) >= set("0123456789")
        assert p.digits["1"] == (1,)
        assert p.digits["0"] == (2, 4, 5)

    def test_connector_cell_loaded(self):
        # Connector ⠤ (dots 3-6) — the backend renders this for a Connector
        # (letter+hanzi compound joiner, e.g. x轴). Both shipped zh
        # profiles declare ``tables.connector: "c_36"``.
        assert load_profile("cn_current").connector == (3, 6)
        assert load_profile("cn_ncb").connector == (3, 6)

    def test_notes_are_skipped(self):
        # The _note / _n*_section_ keys in JSON should never leak into
        # the dicts. A single-char ``_`` IS a legitimate punctuation key
        # (underscore = 36 dots), so the filter rejects only multi-char
        # ``_``-prefixed names.
        p = load_profile("cn_current")
        for table in (p.initials, p.finals, p.tones, p.punctuation, p.digits):
            assert all(not (len(k) > 1 and k.startswith("_")) for k in table)

    def test_math_symbol_provisional_default_false(self):
        # Symbols without an explicit ``provisional: true`` flag must
        # report False so proofread tooling doesn't surface bogus
        # "double-check" hints on rule-backed cells.
        p = load_profile("cn_current")
        # ``+`` is a foundational op — never provisional in shipped profiles.
        assert p.math_symbol_provisional("+") is False
        # Unmapped char also returns False (no provisional flag at all).
        assert p.math_symbol_provisional("☃") is False


class TestMissingProfile:
    def test_unknown_profile(self):
        with pytest.raises(FileNotFoundError):
            load_profile("does_not_exist")


class TestCustomRoot:
    def test_load_from_custom_root(self, tmp_path: Path):
        # Build a minimal profile tree under tmp_path.
        (tmp_path / "profiles").mkdir()
        (tmp_path / "resources").mkdir()
        (tmp_path / "resources" / "initials.json").write_text(
            json.dumps({"b": [1, 2]}), encoding="utf-8"
        )
        (tmp_path / "profiles" / "tiny.json").write_text(
            json.dumps({
                "name": "tiny",
                "language": "zh-CN",
                "cell": "six_dot",
                "features": {"tone": False},
                "tables": {"initials": "resources/initials.json"},
            }),
            encoding="utf-8",
        )
        p = load_profile("tiny", root=tmp_path)
        assert p.name == "tiny"
        assert p.initials == {"b": (1, 2)}
        assert p.feature("tone") is False
        # Missing tables become empty dicts.
        assert p.finals == {}
        # No ``tables.connector`` → empty tuple; a Connector node then
        # degrades to a blank cell rather than crashing.
        assert p.connector == ()


class TestExtraSearchPaths:
    """``load_profile(extra_search_paths=...)`` — user-folder profile lookup.

    Backs the portable-build story: a profile file
    dropped into ``<exe_dir>/profiles/`` (or any user-specified dir)
    is preferred over the same-named builtin.  Tables referenced by
    relative path inside the user profile still resolve against the
    ``root`` (i.e. the package's ``resources/``) — extras only override
    the top-level ``<name>.json``.
    """

    def _write_tiny_profile(
        self, root: Path, *, name: str, language: str
    ) -> None:
        """Drop a minimal ``<name>.json`` under ``root`` (creates dir)."""
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{name}.json").write_text(
            json.dumps({
                "name": name,
                "language": language,
                "cell": "six_dot",
                "features": {"tone": False},
                "tables": {},
            }),
            encoding="utf-8",
        )

    def test_user_path_wins_over_builtin(self, tmp_path: Path):
        # Drop a profile named "cn_current" into the user folder — it
        # must shadow the shipped one.  Distinguishable by language tag.
        user_dir = tmp_path / "user_profiles"
        self._write_tiny_profile(user_dir, name="cn_current", language="shadow")
        p = load_profile("cn_current", extra_search_paths=[user_dir])
        assert p.language == "shadow"

    def test_falls_back_to_builtin_when_not_in_user_dir(self, tmp_path: Path):
        empty_user_dir = tmp_path / "empty"
        empty_user_dir.mkdir()
        p = load_profile("cn_current", extra_search_paths=[empty_user_dir])
        assert p.name == "cn_current"
        assert p.language == "zh-CN"

    def test_multiple_extras_first_wins(self, tmp_path: Path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        self._write_tiny_profile(first, name="myproj", language="first")
        self._write_tiny_profile(second, name="myproj", language="second")
        p = load_profile("myproj", extra_search_paths=[first, second])
        assert p.language == "first"

    def test_missing_extras_dir_is_skipped(self, tmp_path: Path):
        # A user-folder pointer to a non-existent directory must not
        # crash — same as the empty-dir case, fall back to builtin.
        missing = tmp_path / "does_not_exist"
        p = load_profile("cn_current", extra_search_paths=[missing])
        assert p.name == "cn_current"

    def test_missing_everywhere_lists_both_sources(self, tmp_path: Path):
        user_dir = tmp_path / "user_profiles"
        self._write_tiny_profile(user_dir, name="my_only", language="zh-CN")
        with pytest.raises(FileNotFoundError) as exc:
            load_profile(
                "does_not_exist", extra_search_paths=[user_dir]
            )
        # Error hint should mention both the user profile (my_only) and
        # the builtins (cn_current / cn_ncb) so the user can fix
        # the typo without guessing.
        assert "available:" in str(exc.value)
        assert "my_only" in str(exc.value)
        assert "cn_current" in str(exc.value)

    def test_none_extras_behaves_like_omitted(self, tmp_path: Path):
        # Defensive: ``None`` and missing kwarg must both mean "just
        # use builtins" — the caller passes ``None`` when storage_dir
        # hasn't been configured yet.
        p = load_profile("cn_current", extra_search_paths=None)
        assert p.name == "cn_current"

    def test_pipeline_threads_extra_paths(self, tmp_path: Path):
        # Sanity: Pipeline.extra_profile_paths is end-to-end wired to
        # load_profile, so the caller can swap profile collections via a
        # single dataclass field.
        from brailix import Pipeline

        user_dir = tmp_path / "user_profiles"
        self._write_tiny_profile(user_dir, name="cn_current", language="from_pipeline")
        pipe = Pipeline(profile="cn_current", extra_profile_paths=(str(user_dir),))
        assert pipe._profile.language == "from_pipeline"


class TestHelpers:
    def test_to_dots(self):
        assert _to_dots([1, 2, 4]) == (1, 2, 4)
        assert _to_dots([]) == ()

    def test_dots_dict_skips_notes(self):
        out = _dots_dict({"_note": "skip me", "a": [1, 2], "b": "not-a-list"})
        assert out == {"a": (1, 2)}


class TestExtractDots:
    def test_none_returns_none(self):
        assert _extract_dots(None) is None

    def test_bare_empty_list_returns_empty_tuple(self):
        assert _extract_dots([]) == ()

    def test_non_int_list_returns_none(self):
        # Mixed-type lists are rejected so callers can skip them.
        assert _extract_dots([1, "x"]) is None
        assert _extract_dots(["a", "b"]) is None

    def test_dict_with_non_list_dots_returns_none(self):
        assert _extract_dots({"dots": "not-a-list"}) is None

    def test_unknown_value_type_returns_none(self):
        assert _extract_dots(42) is None
        assert _extract_dots("string") is None


class TestRichSchemaCompat:
    """Confirm the loader accepts the new cell-spec object schema used
    by ``resources/numbers.json`` (and forwards-compat for the
    other tables once they migrate)."""

    def test_bare_list_value(self):
        out = _dots_dict({"a": [1, 2, 4]})
        assert out == {"a": (1, 2, 4)}

    def test_cell_spec_object_value(self):
        out = _dots_dict({
            "a": {"dots": [1, 2, 4], "brf": "f", "role": "letter_a"},
        })
        assert out == {"a": (1, 2, 4)}

    def test_mixed_schemas_in_one_dict(self):
        out = _dots_dict({
            "a": [1],
            "b": {"dots": [1, 2], "brf": "b"},
        })
        assert out == {"a": (1,), "b": (1, 2)}

    def test_metadata_keys_skipped(self):
        out = _dots_dict({
            "schema": "v1",
            "name": "foo",
            "cell": "six_dot",
            "status": "unchecked",
            "source": {"type": "generated"},
            "version": "1.0",
            "_note": "hi",
            "b": {"dots": [1, 2]},
        })
        assert out == {"b": (1, 2)}

    def test_numbers_new_schema_loads(self):
        """The shipped resources/numbers.json uses the new schema —
        confirm every digit and the auxiliary cells parse correctly."""
        p = load_profile("cn_current")
        # All ten digits present and correctly converted.
        for digit in "0123456789":
            assert digit in p.digits
            assert isinstance(p.digits[digit], tuple)
        assert p.number_sign == (3, 4, 5, 6)
        assert p.decimal_point == (2,)
        assert p.thousands_sep == (3,)


class TestMathTable:
    """Math table loader: nested dotted-name structures + entity-name
    keyed symbols."""

    def test_loads_from_cn_current(self):
        p = load_profile("cn_current")
        # symbols/functions/structures populated; entity names normalised
        # so callers query by Unicode char.
        assert "+" in p.math_symbols
        # Dotted structure key.
        assert "fraction.bar" in p.math_structures

    def test_single_cell_symbol_is_one_tuple_sequence(self):
        p = load_profile("cn_current")
        # '+' is one cell (235); the value should be a 1-tuple of dot tuples.
        cells = p.math_symbol("+")
        assert cells == ((2, 3, 5),)

    def test_multi_cell_symbol_is_nested(self):
        p = load_profile("cn_current")
        # '≤' is two cells in the symbols table (composed via lt + equals).
        cells = p.math_symbol("≤")
        assert isinstance(cells, tuple)
        assert len(cells) == 2

    def test_helper_accessors(self):
        p = load_profile("cn_current")
        assert p.math_symbol("not-in-table") is None
        assert p.letter("not-in-table") is None
        # math_structure falls back to () for unknown names so callers
        # can simply iterate over nothing.
        assert p.math_structure("nope") == ()

    def test_extract_cells_accepts_bare_nested_list(self):
        from brailix.core.config import _extract_cells

        # Bare nested list (no dict wrapper).
        assert _extract_cells([[1, 2], [3]]) == ((1, 2), (3,))

    def test_extract_cells_rejects_dict_without_dots(self):
        from brailix.core.config import _extract_cells

        # A cell-spec dict that omits 'dots' should be rejected.
        assert _extract_cells({"role": "x"}) is None

    def test_extract_cells_rejects_unknown_types(self):
        from brailix.core.config import _extract_cells

        assert _extract_cells(None) is None
        assert _extract_cells(42) is None
        assert _extract_cells("hi") is None

    def test_extract_cells_rejects_invalid_nested_member(self):
        from brailix.core.config import _extract_cells

        # Mixed list (int + nested) is invalid.
        assert _extract_cells([[1, 2], "not-a-list"]) is None

    def test_extract_cells_non_list_dots_returns_none(self):
        from brailix.core.config import _extract_cells

        assert _extract_cells({"dots": "not-a-list"}) is None

    def test_no_math_table_in_profile_yields_empty(self, tmp_path: Path):
        # Build a minimal profile without a math table.
        prof = tmp_path / "profiles" / "no_math.json"
        prof.parent.mkdir(parents=True)
        prof.write_text(json.dumps({"name": "no_math", "tables": {}}), encoding="utf-8")
        p = load_profile("no_math", root=tmp_path)
        assert p.math_symbols == {}
        assert p.math_functions == {}
        assert p.math_structures == {}

    def test_letter_composes_lazily(self):
        # ``letter`` builds (script_prefix + letter_cell) on demand; we
        # used to precompose a dict of ALL letters at load, but now the
        # work happens per-character.
        p = load_profile("cn_current")
        # Composition shape: prefix cell(s) come before the letter body.
        # cn_current uses dot 56 for lower-latin, dot 6 for upper-latin.
        assert p.letter("a") == ((5, 6), (1,))
        assert p.letter("A") == ((6,), (1,))
        # Greek
        assert p.letter("π") == ((4, 6), (1, 2, 3, 4))
        assert p.letter("Π") == ((4, 5, 6), (1, 2, 3, 4))
        # Unknown character returns None
        assert p.letter("一") is None

    def test_letter_result_cached(self):
        # Second call returns the same tuple object (cached).
        p = load_profile("cn_current")
        first = p.letter("a")
        second = p.letter("a")
        assert first is second

    def test_letter_tables_loaded_as_neutral(self):
        # Letter tables themselves stay free of any context prefix so
        # downstream backends (math now, LatinBraille later) can apply
        # their own rules.
        p = load_profile("cn_current")
        assert p.latin_letters["lower"]["a"] == (1,)
        assert p.latin_letters["upper"]["A"] == (1,)
        assert p.greek_letters["lower"]["π"] == (1, 2, 3, 4)
        assert p.greek_letters["upper"]["Π"] == (1, 2, 3, 4)

    def test_letter_tables_absent_yield_empty(self, tmp_path: Path):
        # A profile without latin/greek table refs gets empty neutral
        # dicts and (consequently) None from letter().
        prof = tmp_path / "profiles" / "no_letters.json"
        prof.parent.mkdir(parents=True)
        prof.write_text(
            json.dumps({"name": "no_letters", "tables": {}}), encoding="utf-8"
        )
        p = load_profile("no_letters", root=tmp_path)
        assert p.latin_letters == {"lower": {}, "upper": {}}
        assert p.greek_letters == {"lower": {}, "upper": {}}
        assert p.letter("a") is None

    def test_functions_table_loaded(self):
        p = load_profile("cn_current")
        # sin is a single-cell abbreviation.
        assert p.math_function("sin") == ((2, 3, 4),)
        # ln spells out two letters.
        assert p.math_function("ln") == ((1, 2, 3), (1, 3, 4, 5))
        # arcsin per rules §53: a + s (function_prefix is added by backend).
        assert p.math_function("arcsin") == ((1,), (2, 3, 4))
        # lim per rules §62: just l + m (no i).
        assert p.math_function("lim") == ((1, 2, 3), (1, 3, 4))
        # Missing function returns None.
        assert p.math_function("frobnicate") is None


class TestMathSymbolEntityNormalisation:
    """The symbols.json keys are MathML entity names (``plus``, ``ne``,
    ``int``, ...). The loader runs them through ``html.entities.html5``
    to get the actual Unicode character used as the in-memory key."""

    def test_basic_entity_normalisation(self):
        # The shipped cn_current symbols table is keyed by entity name;
        # the loader normalises so the backend can query by char.
        p = load_profile("cn_current")
        assert p.math_symbol("+") is not None  # plus
        assert p.math_symbol("=") is not None  # equals
        assert p.math_symbol("∫") is not None  # int
        assert p.math_symbol("∑") is not None  # sum
        assert p.math_symbol("∮") is not None  # conint

    def test_entity_resolves_to_unicode_in_membership(self):
        # Entity names themselves are NOT in the dict — only the
        # resolved chars are.
        p = load_profile("cn_current")
        assert "plus" not in p.math_symbols
        assert "+" in p.math_symbols

    def test_sibling_ref_to_other_entity_resolves(self):
        # ne = c_4 + equals. The ``equals`` ref inside the cells array
        # must resolve to the equals entry (= U+003D) and its cells.
        p = load_profile("cn_current")
        # ne == "≠" U+2260; expected dots (4,) + equals (2,3,5,6)
        cells = p.math_symbol("≠")
        assert cells == ((4,), (2, 3, 5, 6))

    def test_sibling_ref_chained(self):
        # ncong = c_4 + cong, where cong itself is c_35 + c_2356.
        p = load_profile("cn_current")
        # ncong == "≇" U+2247
        cells = p.math_symbol("≇")
        assert cells == ((4,), (3, 5), (2, 3, 5, 6))

    def test_sibling_ref_via_rArr(self):
        # nrArr = c_4 + rArr; rArr = c_2356 + c_345.
        # nrArr → ⇏ U+21CF
        p = load_profile("cn_current")
        cells = p.math_symbol("⇏")
        assert cells == ((4,), (2, 3, 5, 6), (3, 4, 5))

    def test_divide_aliases_sol(self):
        # divide ≡ sol per the profile's display alias for ÷.
        p = load_profile("cn_current")
        # divide → ÷ U+00F7, sol → / U+002F
        assert p.math_symbol("÷") == p.math_symbol("/")

    def test_unknown_entity_raises_value_error(self, tmp_path: Path):
        # The loader looks up every symbols.json key in html5; anything
        # not there is a configuration bug that should fail at startup.
        prof = _write_split_math_demo(tmp_path, symbols={
            "thisisnotanyrealentity": {"cells": ["c_1"], "role": "op"},
        })
        with pytest.raises(ValueError, match="unknown HTML5 entity"):
            load_profile(prof, root=tmp_path)

    def test_multi_char_entity_rejected(self, tmp_path: Path):
        # fjlig is a real html5 entity but expands to the two-char
        # ligature "fj"; symbols keys must resolve to a single char.
        prof = _write_split_math_demo(tmp_path, symbols={
            "fjlig": {"cells": ["c_1"], "role": "op"},
        })
        with pytest.raises(ValueError, match="multi-character"):
            load_profile(prof, root=tmp_path)

    def test_entity_normalisation_skips_functions_table(self, tmp_path: Path):
        # functions.json keys are NOT entity names. ``sup`` is the
        # supremum function (lim equivalent), not the entity that
        # would resolve to ⊃ U+2283. The loader leaves the key alone.
        p = load_profile("cn_current")
        # If entity normalisation ran, the entry would be keyed by ⊃,
        # not by the string "sup".
        assert p.math_function("sup") is not None
        # And big_op flag is preserved.
        assert p.math_function_big_op("sup") is True

    def test_symbol_cell_pool_ref_not_treated_as_entity(self, tmp_path: Path):
        # ``c_1`` looks like a string but it's in the cells pool, so
        # the loader doesn't try to entity-normalise it.
        prof = _write_split_math_demo(tmp_path, symbols={
            "plus": {"cells": ["c_1"], "role": "op"},
        })
        p = load_profile(prof, root=tmp_path)
        assert p.math_symbol("+") == ((1,),)

    def test_symbol_cycle_raises_at_load(self, tmp_path: Path):
        # Two symbols that ref each other → composition cycle. The
        # loader detects this before returning. Use real entities so
        # normalisation doesn't error first.
        prof = _write_split_math_demo(tmp_path, symbols={
            "plus":  {"cells": ["minus"], "role": "op"},
            "minus": {"cells": ["plus"], "role": "op"},
        })
        with pytest.raises(ValueError, match="cycle"):
            load_profile(prof, root=tmp_path)

    def test_function_sibling_ref_resolves(self):
        # arcsin = c_1 + sin; the "sin" ref is to the sibling sin entry.
        p = load_profile("cn_current")
        assert p.math_function("arcsin") == ((1,), (2, 3, 4))
        # arccos = c_1 + cos.
        assert p.math_function("arccos") == ((1,), (1, 4))
        # sinh = sin + c_125.
        assert p.math_function("sinh") == ((2, 3, 4), (1, 2, 5))

    def test_function_unknown_ref_raises(self, tmp_path: Path):
        prof = _write_split_math_demo(tmp_path, functions={
            "foo": {"cells": ["does_not_exist"]},
        })
        with pytest.raises(ValueError, match="unknown"):
            load_profile(prof, root=tmp_path)


class TestEntityToChar:
    """Direct exercise of the entity-normalisation helper."""

    def test_simple_entity(self):
        assert _entity_to_char("plus") == "+"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown HTML5 entity"):
            _entity_to_char("definitely_not_an_entity")

    def test_multi_char_raises(self):
        # fjlig → "fj"
        with pytest.raises(ValueError, match="multi-character"):
            _entity_to_char("fjlig")

    def test_codepoint_literal(self):
        # U+XXXX escape hatch for math chars without an HTML5 entity.
        assert _entity_to_char("U+29F5") == "⧵"
        # 6-digit (astral plane) literal works too.
        assert _entity_to_char("U+1D49C") == "\U0001d49c"

    def test_codepoint_out_of_range_raises(self):
        with pytest.raises(ValueError, match="not a valid Unicode"):
            _entity_to_char("U+999999")

    def test_codepoint_surrogate_raises(self):
        with pytest.raises(ValueError, match="not a valid Unicode"):
            _entity_to_char("U+D800")

    def test_non_codepoint_u_name_falls_through_to_html5(self):
        # A key starting with 'U' that isn't the U+XXXX shape is treated
        # as an ordinary entity name (and here, an unknown one).
        with pytest.raises(ValueError, match="unknown HTML5 entity"):
            _entity_to_char("U_not_a_codepoint")


class TestMathSymbolRole:
    """math_symbol_role returns the role assigned in symbols.json."""

    def test_op_role(self):
        p = load_profile("cn_current")
        assert p.math_symbol_role("+") == "op"
        # "minus" entity is U+2212 (math minus), NOT ASCII hyphen-minus.
        assert p.math_symbol_role("−") == "op"
        assert p.math_symbol_role("×") == "op"

    def test_rel_role(self):
        p = load_profile("cn_current")
        assert p.math_symbol_role("=") == "rel"
        assert p.math_symbol_role("<") == "rel"
        assert p.math_symbol_role("≠") == "rel"

    def test_delim_role(self):
        p = load_profile("cn_current")
        assert p.math_symbol_role("(") == "delim"
        assert p.math_symbol_role(")") == "delim"
        assert p.math_symbol_role("[") == "delim"

    def test_punct_role(self):
        p = load_profile("cn_current")
        assert p.math_symbol_role(",") == "punct"
        assert p.math_symbol_role(".") == "punct"

    def test_shape_role_geometry_figures(self):
        # Figures / angles in the elementary-geometry section that **have a
        # Unicode character** are taken as role=shape (△□○◇▭∠∟, the docx
        # geometry-symbols section); those without a character (equilateral
        # triangle, etc.) or unlisted variants (small triangle ▵ U+25B5 ≠ the
        # listed △ U+25B3) stay None.
        # See math-symbols-plan §3 / math-redesign §3.6.
        p = load_profile("cn_current")
        for ch in "△□○◇▭∠∟":
            assert p.math_symbol_role(ch) == "shape"
        assert p.math_symbol_role("▵") is None

    def test_big_op_role(self):
        p = load_profile("cn_current")
        # sum = ∑ U+2211
        assert p.math_symbol_role("∑") == "big_op"
        # int = ∫ U+222B
        assert p.math_symbol_role("∫") == "big_op"
        # prod = ∏ U+220F
        assert p.math_symbol_role("∏") == "big_op"
        # conint = ∮ U+222E
        assert p.math_symbol_role("∮") == "big_op"

    def test_unknown_returns_none(self):
        p = load_profile("cn_current")
        assert p.math_symbol_role("一") is None
        assert p.math_symbol_role("") is None


class TestMathSymbolScriptPrefix:
    def test_int_has_prefix(self):
        p = load_profile("cn_current")
        assert p.math_symbol_script_prefix("∫") is True
        assert p.math_symbol_script_prefix("∮") is True

    def test_plus_no_prefix(self):
        p = load_profile("cn_current")
        assert p.math_symbol_script_prefix("+") is False

    def test_big_ops_take_prefix(self):
        # ∑ ∏ ⋃ ⋂ all take the 46-dot script prefix now (《盲文常用数学
        # 符号》: every big-op limit goes directly below / directly above,
        # ⠨⠡/⠨⠌).
        p = load_profile("cn_current")
        assert p.math_symbol_script_prefix("∑") is True
        assert p.math_symbol_script_prefix("∏") is True
        assert p.math_symbol_script_prefix("⋃") is True
        assert p.math_symbol_script_prefix("⋂") is True
        # big union ⋃ ⠸⠼ / big intersection ⋂ ⠰⠹ — new big_op entries.
        assert p.math_symbol("⋃") == ((4, 5, 6), (3, 4, 5, 6))
        assert p.math_symbol("⋂") == ((5, 6), (1, 4, 5, 6))
        assert p.math_symbol_role("⋃") == "big_op"
        assert p.math_symbol_role("⋂") == "big_op"

    def test_unknown_no_prefix(self):
        p = load_profile("cn_current")
        assert p.math_symbol_script_prefix("一") is False


class TestMathSymbolSupplement:
    """Set-theory / logic / calculus / inequality symbols added from
    《盲文常用数学符号》. Cells / roles / spacing are asserted directly
    (hand-derived from the reference doc, NOT generated from it)."""

    def test_set_relations(self):
        p = load_profile("cn_current")
        # ∉ not-an-element-of ⠘⠪ = 45+246 (this standard writes it tight, no spaces).
        assert p.math_symbol("∉") == ((4, 5), (2, 4, 6))
        assert p.math_symbol_role("∉") == "rel"
        assert p.math_symbol_spaces("∉") == (False, False)
        # ⊂ proper-subset-of ⠯ = 12346; ⊃ proper-superset-of ⠹ = 1456 (both spaced both sides).
        assert p.math_symbol("⊂") == ((1, 2, 3, 4, 6),)
        assert p.math_symbol("⊃") == ((1, 4, 5, 6),)
        assert p.math_symbol_spaces("⊂") == (True, True)
        # ⊆ = ⊂ + = ; ⊇ = ⊃ + = (sibling refs); front-spaced only.
        assert p.math_symbol("⊆") == ((1, 2, 3, 4, 6), (2, 3, 5, 6))
        assert p.math_symbol("⊇") == ((1, 4, 5, 6), (2, 3, 5, 6))
        assert p.math_symbol_spaces("⊆") == (True, False)

    def test_set_operators(self):
        p = load_profile("cn_current")
        # ∪ union ⠰⠴; ∩ intersection ⠰⠲ — role op, front-spaced.
        assert p.math_symbol("∪") == ((5, 6), (3, 5, 6))
        assert p.math_symbol("∩") == ((5, 6), (2, 5, 6))
        assert p.math_symbol_role("∪") == "op"
        assert p.math_symbol_spaces("∪") == (True, False)
        # ∖ set-difference ⠰⠤; ∅ empty-set ⠈⠴ — tight.
        assert p.math_symbol("∖") == ((5, 6), (3, 6))
        assert p.math_symbol("∅") == ((4,), (3, 5, 6))
        assert p.math_symbol_spaces("∖") == (False, False)
        # \setminus emits U+29F5 (⧵, no html5 entity); the U+XXXX literal
        # key aliases it to ∖'s cells.
        assert p.math_symbol("⧵") == p.math_symbol("∖")
        assert p.math_symbol_role("⧵") == "op"

    def test_logic_symbols(self):
        p = load_profile("cn_current")
        # ∧ conjunction ⠰⠢; ∨ disjunction ⠰⠔; ¬ negation ⠩.
        assert p.math_symbol("∧") == ((5, 6), (2, 6))
        assert p.math_symbol("∨") == ((5, 6), (3, 5))
        assert p.math_symbol("¬") == ((1, 4, 6),)
        # ∀ universal-quantifier ⠫⠄; ∃ existential-quantifier ⠫⠢. 1246 here is a bare cell, NOT a
        # ref to function.prefix (coincidental overlap).
        assert p.math_symbol("∀") == ((1, 2, 4, 6), (3,))
        assert p.math_symbol("∃") == ((1, 2, 4, 6), (2, 6))
        for ch in "∧∨¬∀∃":
            assert p.math_symbol_role(ch) == "op"

    def test_calculus_constants(self):
        p = load_profile("cn_current")
        # ∂ partial-derivative ⠹ = 1456 (tight, for ∂y/∂x); ∇ nabla ⠫⠴ = 1246+356.
        assert p.math_symbol("∂") == ((1, 4, 5, 6),)
        assert p.math_symbol("∇") == ((1, 2, 4, 6), (3, 5, 6))
        assert p.math_symbol_spaces("∂") == (False, False)

    def test_extra_inequalities(self):
        p = load_profile("cn_current")
        # not-greater-than ≯ ⠈⠕ = "not" + "greater than"; not-less-than ≮ ⠈⠪ = "not" + "less than".
        assert p.math_symbol("≯") == ((4,), (1, 3, 5))
        assert p.math_symbol("≮") == ((4,), (2, 4, 6))
        # much-greater-than ≫ ⠕⠕; much-less-than ≪ ⠪⠪ — doubled gt / lt.
        assert p.math_symbol("≫") == ((1, 3, 5), (1, 3, 5))
        assert p.math_symbol("≪") == ((2, 4, 6), (2, 4, 6))
        for ch in "≯≮≫≪":
            assert p.math_symbol_role(ch) == "rel"
            assert p.math_symbol_spaces(ch) == (True, True)

    def test_s2_brackets_and_relations(self):
        p = load_profile("cn_current")
        # angle brackets ⟨ ⠐⠪ / ⟩ ⠕⠂ — delim, no spaces.
        assert p.math_symbol("⟨") == ((5,), (2, 4, 6))
        assert p.math_symbol("⟩") == ((1, 3, 5), (2,))
        assert p.math_symbol_role("⟨") == "delim"
        # ≶ less-or-greater = lt+gt; ≷ greater-or-less = gt+lt — rel, spaced both sides.
        assert p.math_symbol("≶") == ((2, 4, 6), (1, 3, 5))
        assert p.math_symbol("≷") == ((1, 3, 5), (2, 4, 6))
        assert p.math_symbol_spaces("≶") == (True, True)
        # ⊄ not-subset-of = "not"+"proper subset of"; ⊅ not-superset-of =
        # "not"+"proper superset of" — rel, no spaces.
        assert p.math_symbol("⊄") == ((4,), (1, 2, 3, 4, 6))
        assert p.math_symbol("⊅") == ((4,), (1, 4, 5, 6))
        assert p.math_symbol_spaces("⊄") == (False, False)

    def test_contour_and_multiple_integrals(self):
        p = load_profile("cn_current")
        # ∮ contour-integral ⠮⠴ = ∫ + 356 (was the "same as int for now" placeholder).
        assert p.math_symbol("∮") == ((2, 3, 4, 6), (3, 5, 6))
        # ∬ (entity Int) double integral; ∭ triple integral — repeated ∫ cell.
        assert p.math_symbol("∬") == ((2, 3, 4, 6), (2, 3, 4, 6))
        assert p.math_symbol("∭") == ((2, 3, 4, 6),) * 3
        # ∫ itself is unchanged (single cell) despite conint refing it.
        assert p.math_symbol("∫") == ((2, 3, 4, 6),)
        for ch in "∮∬∭":
            assert p.math_symbol_role(ch) == "big_op"
            assert p.math_symbol_script_prefix(ch) is True

    def test_previously_provisional_now_confirmed(self):
        # ≤ ≥ ± ∓ ↔ ∮ were marked provisional; the reference doc confirms
        # their exact cells, so the flag is dropped.
        p = load_profile("cn_current")
        for ch in "≤≥±∓↔∮":
            assert p.math_symbol_provisional(ch) is False
        # Cells unchanged by the de-provisionalisation.
        assert p.math_symbol("≤") == ((2, 4, 6), (2, 3, 5, 6))
        assert p.math_symbol("≥") == ((1, 3, 5), (2, 3, 5, 6))
        assert p.math_symbol("↔") == ((2, 4, 6), (2, 5), (1, 3, 5))

    def test_accent_symbols(self):
        # Accent marker symbols from the docx annotation section (the
        # positional marker ⠘/⠰ is added on the fly by the backend).
        p = load_profile("cn_current")
        assert p.math_symbol("¯") == ((2, 5),)        # macr overline → horizontal line ⠒
        assert p.math_symbol("―") == ((2, 5),)        # horbar (\overline)
        assert p.math_symbol("˜") == ((2, 6),)        # tilde ⠢
        assert p.math_symbol("~") == ((2, 6),)        # U+007E (actual \tilde output)
        assert p.math_symbol("˙") == ((2,),)          # dot ⠂ (changed from the old ⠄)
        assert p.math_symbol("¨") == ((2,), (2,))     # die (two dots) ⠂⠂
        for ch in "¯―˜~˙¨":
            assert p.math_symbol_role(ch) == "accent"
        # The multiplication dot sdot ⋅ is still ⠄, distinct from the dot accent ⠂.
        assert p.math_symbol("⋅") == ((3,),)
        # accent_mark tag: →/← (\vec) render as an arrow sign in accent
        # position, ¯/―/‾ (\bar) as a short bar — independent of the global
        # role. → still keeps role=rel for ordinary relational use.
        assert p.math_accent_mark_kind("→") == "arrow"
        assert p.math_accent_mark_kind("←") == "arrow"
        for ch in "¯―‾":
            assert p.math_accent_mark_kind(ch) == "bar"
        assert p.math_symbol_role("→") == "rel"
        # Non-vector marks (tilde/dot) carry no accent_mark.
        assert p.math_accent_mark_kind("~") is None
        assert p.math_accent_mark_kind("˙") is None

    def test_geometry_shape_symbols(self):
        # Elementary geometry symbols (the docx geometry-symbols section):
        # angle ∠ + figures △□○◇▭ + right angle ∟, all role=shape, led by
        # ⠫(1246). Figures with a Unicode character get a written sign; those
        # without a character (equilateral triangle, etc.) are still not
        # included (see math-symbols-plan §3).
        p = load_profile("cn_current")
        assert p.math_symbol("∠") == ((1, 2, 4, 6), (2, 4, 6))     # angle ⠫⠪
        assert p.math_symbol("△") == ((1, 2, 4, 6), (2, 5, 6))     # triangle ⠫⠲
        assert p.math_symbol("□") == ((1, 2, 4, 6), (2, 3, 5, 6))  # square ⠫⠶
        assert p.math_symbol("○") == ((1, 2, 4, 6), (2,))          # circle ⠫⠂
        assert p.math_symbol("◇") == ((1, 2, 4, 6), (1, 4, 5))     # rhombus ⠫⠙
        assert p.math_symbol("▭") == ((1, 2, 4, 6), (1, 2, 3, 4, 5, 6))  # rectangle ⠫⠿
        assert p.math_symbol("∟") == ((1, 2, 4, 6), (2, 3, 6))     # right angle ⠫⠦
        for ch in "∠△□○◇▭∟":
            assert p.math_symbol_role(ch) == "shape"

    def test_geometry_relation_symbols(self):
        # Geometry relations (the docx geometry-symbols section):
        # perpendicular ⊥ ⠼⠄, similar ∼/∽ ⠔. latex2mathml codepoint trap —
        # \\perp emits U+27C2 (≠ entity perp's U+22A5), both codepoints map to ⠼⠄.
        p = load_profile("cn_current")
        assert p.math_symbol("⊥") == ((3, 4, 5, 6), (3,))        # U+22A5 (\bot / direct char)
        assert p.math_symbol("⟂") == ((3, 4, 5, 6), (3,))   # U+27C2 (output of \perp)
        assert p.math_symbol("∼") == ((3, 5),)                   # U+223C similar (\thicksim / direct)
        assert p.math_symbol("∽") == ((3, 5),)                   # U+223D similar (\backsim)
        for ch in ["⊥", "⟂", "∼", "∽"]:
            assert p.math_symbol_role(ch) == "rel"

    def test_percent_permil_arrows_order_relations(self):
        # docx fraction / arrow / set-theory sections: percent sign % ⠼⠚⠴
        # (overrides the original punctuation table's wrong translation ⠨),
        # per-mille sign ‰, vertical arrows ↑↓↕⇑⇓, order relations ≺≻⪯⪰.
        p = load_profile("cn_current")
        assert p.math_symbol("%") == ((3, 4, 5, 6), (2, 4, 5), (3, 5, 6))   # percent sign ⠼⠚⠴
        assert p.math_symbol("‰") == ((3, 4, 5, 6), (2, 4, 5), (3, 5, 6), (3, 5, 6))  # per-mille ⠼⠚⠴⠴
        assert p.math_symbol("↑") == ((5, 6), (3, 4))         # uarr ⠰⠌
        assert p.math_symbol("↓") == ((4, 5), (1, 6))         # darr ⠘⠡
        assert p.math_symbol("↕") == ((1, 4, 5, 6), (3,))     # varr ⠹⠄
        assert p.math_symbol("⇑") == ((3, 4), (3, 4))         # uArr ⠌⠌
        assert p.math_symbol("⇓") == ((1, 6), (1, 6))         # dArr ⠡⠡
        assert p.math_symbol("≺") == ((2, 5), (2, 4, 6))      # prec ⠒⠪
        assert p.math_symbol("≻") == ((1, 3, 5), (2, 5))      # succ ⠕⠒
        assert p.math_symbol("⪯") == ((2, 5), (2, 4, 6), (2, 3, 5, 6))  # preceq ⠒⠪⠶
        assert p.math_symbol("⪰") == ((1, 3, 5), (2, 5), (2, 3, 5, 6))  # succeq ⠕⠒⠶


class TestMathFunctionFlags:
    def test_new_function_abbreviations(self):
        # Function abbreviations added in the docx complex-number / calculus /
        # matrix sections (cells = the letters after function_prefix).
        p = load_profile("cn_current")
        assert p.math_function("arg") == ((1,),)                                  # ⠫⠁
        assert p.math_function("mod") == ((1, 3, 4),)                             # ⠫⠍
        assert p.math_function("sgn") == ((2, 3, 4), (1, 2, 4, 5), (1, 3, 4, 5))  # ⠫⠎⠛⠝
        assert p.math_function("Tr") == ((6,), (2, 3, 4, 5), (1, 2, 3, 5))        # ⠫⠠⠞⠗
        assert p.math_function("Sp") == ((6,), (2, 3, 4), (1, 2, 3, 4))           # ⠫⠠⠎⠏
        assert p.math_function("grad") == ((1, 2, 4, 5),)                         # ⠫⠛
        assert p.math_function("div") == ((1, 4, 5),)                             # ⠫⠙ (≠ division sign ÷)
        assert p.math_function("rot") == ((1, 2, 3, 5),)                          # ⠫⠗

    def test_lim_is_big_op(self):
        p = load_profile("cn_current")
        assert p.math_function_big_op("lim") is True
        assert p.math_function_big_op("max") is True
        assert p.math_function_big_op("min") is True
        assert p.math_function_big_op("sup") is True
        assert p.math_function_big_op("inf") is True

    def test_sin_not_big_op(self):
        p = load_profile("cn_current")
        assert p.math_function_big_op("sin") is False
        assert p.math_function_big_op("cos") is False
        assert p.math_function_big_op("ln") is False

    def test_lim_has_script_prefix(self):
        p = load_profile("cn_current")
        assert p.math_function_script_prefix("lim") is True

    def test_big_op_functions_take_script_prefix(self):
        # max/min/sup/inf now take the 46-dot script prefix too (all
        # big-op function limits go directly below / directly above per
        # 《盲文常用数学符号》).
        p = load_profile("cn_current")
        assert p.math_function_script_prefix("max") is True
        assert p.math_function_script_prefix("min") is True
        assert p.math_function_script_prefix("sup") is True
        assert p.math_function_script_prefix("inf") is True
        # Non-big-op functions still don't.
        assert p.math_function_script_prefix("sin") is False

    def test_unknown_function_flags_false(self):
        p = load_profile("cn_current")
        assert p.math_function_big_op("frobnicate") is False
        assert p.math_function_script_prefix("frobnicate") is False


class TestMathFunctionStandardAbbrev:
    """sec/csc/log/exp/max/min/det use the standard Chinese-braille
    abbreviations from rules.txt §21/§60/§61 + 《盲文常用数学符号》, NOT
    the previous self-invented full-English spellings."""

    def test_log_exp_single_letter(self):
        p = load_profile("cn_current")
        # log with any base = single letter l (rules.txt §21 \log_2{x}=⠫⠇…); exp = single letter e.
        assert p.math_function("log") == ((1, 2, 3),)
        assert p.math_function("exp") == ((1, 5),)
        # ln / lg / lb keep their second letter.
        assert p.math_function("ln") == ((1, 2, 3), (1, 3, 4, 5))
        assert p.math_function("lg") == ((1, 2, 3), (1, 2, 4, 5))

    def test_sec_csc_two_letters(self):
        p = load_profile("cn_current")
        # sec = s + c; csc = c + s (not s+e+c / c+s+c).
        assert p.math_function("sec") == ((2, 3, 4), (1, 4))
        assert p.math_function("csc") == ((1, 4), (2, 3, 4))
        # arcsec / arccsc follow via sibling ref → a + s + c / a + c + s.
        assert p.math_function("arcsec") == ((1,), (2, 3, 4), (1, 4))
        assert p.math_function("arccsc") == ((1,), (1, 4), (2, 3, 4))

    def test_max_min_det(self):
        p = load_profile("cn_current")
        # rules.txt §60/61 maximum/minimum = m+x / m+n; det = d+t.
        assert p.math_function("max") == ((1, 3, 4), (1, 3, 4, 6))
        assert p.math_function("min") == ((1, 3, 4), (1, 3, 4, 5))
        assert p.math_function("det") == ((1, 4, 5), (2, 3, 4, 5))


class TestMathStructureLookup:
    """math_structure works with both dotted and legacy flat names."""

    def test_dotted_lookup_fraction(self):
        p = load_profile("cn_current")
        assert p.math_structure("fraction.bar") == ((1, 2, 5, 6),)
        assert p.math_structure("fraction.open") == ((2, 3),)
        assert p.math_structure("fraction.close") == ((5, 6),)

    def test_dotted_lookup_script(self):
        p = load_profile("cn_current")
        assert p.math_structure("script.sup") == ((3, 4),)
        assert p.math_structure("script.sub") == ((1, 6),)
        assert p.math_structure("script.close") == ((1, 5, 6),)
        assert p.math_structure("script.big_op_prefix") == ((4, 6),)

    def test_dotted_lookup_sqrt(self):
        p = load_profile("cn_current")
        assert p.math_structure("sqrt.open") == ((1, 4, 6),)
        assert p.math_structure("sqrt.indicator") == ((1, 5, 6),)
        assert p.math_structure("sqrt.close") == ((1, 4, 5, 6),)

    def test_dotted_lookup_letter_prefix(self):
        p = load_profile("cn_current")
        assert p.math_structure("letter_prefix.latin_lower") == ((5, 6),)

    def test_dotted_lookup_accent(self):
        p = load_profile("cn_current")
        assert p.math_structure("accent.over") == ((4, 5),)    # directly above ⠘
        assert p.math_structure("accent.under") == ((5, 6),)   # directly below ⠰
        # Vector-mark single/double-letter cell (docx §15): arrow sign ⠒⠂/⠒⠆, short bar ⠒/⠒⠒.
        assert p.math_structure("accent.mark.arrow.single") == ((2, 5), (2,))
        assert p.math_structure("accent.mark.arrow.double") == ((2, 5), (2, 3))
        assert p.math_structure("accent.mark.bar.single") == ((2, 5),)
        assert p.math_structure("accent.mark.bar.double") == ((2, 5), (2, 5))
        assert p.math_structure("letter_prefix.latin_upper") == ((6,),)
        assert p.math_structure("letter_prefix.greek_lower") == ((4, 6),)
        assert p.math_structure("letter_prefix.greek_upper") == ((4, 5, 6),)

    def test_dotted_lookup_function(self):
        p = load_profile("cn_current")
        assert p.math_structure("function.prefix") == ((1, 2, 4, 6),)

    def test_missing_structure_returns_empty_tuple(self):
        p = load_profile("cn_current")
        assert p.math_structure("does_not_exist") == ()
        assert p.math_structure("fraction.does_not_exist") == ()
        assert p.math_structure("") == ()


class TestFeatureLookup:
    """feature() supports nested dotted lookup + legacy flat alias."""

    def test_dotted_math_feature(self):
        p = load_profile("cn_current")
        assert p.feature("math.simplify_fraction") is True
        assert p.feature("math.simplify_script") is True
        assert p.feature("math.op_spacing") is True

    def test_dotted_missing_intermediate_returns_default(self):
        p = load_profile("cn_current")
        assert p.feature("nonexistent.subkey", "fallback") == "fallback"
        assert p.feature("math.nonexistent_subkey", "fallback") == "fallback"

    def test_legacy_flat_math_feature(self):
        p = load_profile("cn_current")
        # math_simplify_fraction aliases math.simplify_fraction
        assert p.feature("math_simplify_fraction") == p.feature("math.simplify_fraction")
        assert p.feature("math_simplify_script") == p.feature("math.simplify_script")
        assert p.feature("math_op_spacing") == p.feature("math.op_spacing")

    def test_legacy_zh_feature(self):
        p = load_profile("cn_current")
        assert p.feature("tone") == p.feature("zh.tone")
        assert p.feature("number_sign") == p.feature("zh.number_sign")
        assert p.feature("tone_omit_neutral") == p.feature("zh.tone_omit_neutral")

    def test_unknown_feature_returns_default(self):
        p = load_profile("cn_current")
        assert p.feature("totally_made_up") is None
        assert p.feature("totally_made_up", "default") == "default"


# ---------------------------------------------------------------------------
# Helper to build a tmp_path math demo profile in the new split shape.
# ---------------------------------------------------------------------------


def _write_split_math_demo(
    tmp_path: Path,
    *,
    symbols: dict | None = None,
    functions: dict | None = None,
    structures: dict | None = None,
) -> str:
    """Write a minimal math-only profile under ``tmp_path``.

    Each non-None argument lands in its own JSON file under
    ``resources/math/`` with the correct top-level section name; the
    profile references them via ``tables.math.<section>`` per design §3.7.
    Returns the profile name ("demo") for ``load_profile`` to pick up.
    """
    math_dir = tmp_path / "resources" / "math"
    math_dir.mkdir(parents=True, exist_ok=True)
    cells_dir = tmp_path / "resources"
    cells_dir.mkdir(exist_ok=True)
    (cells_dir / "cells.json").write_text(
        json.dumps({
            "c_1": [1], "c_2": [2], "c_3": [3], "c_4": [4], "c_5": [5], "c_6": [6],
            "c_12": [1, 2], "c_36": [3, 6], "c_235": [2, 3, 5],
            "c_125": [1, 2, 5], "c_2356": [2, 3, 5, 6], "c_345": [3, 4, 5],
        }),
        encoding="utf-8",
    )
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
            json.dumps({"structures": structures}), encoding="utf-8"
        )
        math_tables["structures"] = "resources/math/structures.json"

    prof = tmp_path / "profiles" / "demo.json"
    prof.parent.mkdir(parents=True, exist_ok=True)
    prof.write_text(
        json.dumps({
            "name": "demo",
            "tables": {
                "cells": "resources/cells.json",
                "math": math_tables,
            },
        }),
        encoding="utf-8",
    )
    return "demo"


class TestSplitMathDemo:
    """The split-math test helper itself: confirm a tmp profile with
    just symbols loads as expected."""

    def test_minimal_symbols_only(self, tmp_path: Path):
        name = _write_split_math_demo(tmp_path, symbols={
            "plus":  {"cells": ["c_235"], "role": "op"},
        })
        p = load_profile(name, root=tmp_path)
        assert p.math_symbol("+") == ((2, 3, 5),)
        assert p.math_symbol_role("+") == "op"

    def test_no_math_section_yields_empty(self, tmp_path: Path):
        prof = tmp_path / "profiles" / "empty.json"
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(json.dumps({"name": "empty", "tables": {}}), encoding="utf-8")
        p = load_profile("empty", root=tmp_path)
        assert p.math_symbols == {}
        assert p.math_functions == {}
        assert p.math_structures == {}
