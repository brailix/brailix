"""The generic per-language slots (ARCHITECTURE#arch-language-slots).

Two of them, one per kind of thing a language brings:

* ``lang_tables[<lang>][<name>]`` — its **cell** tables (Japanese kana,
  Chinese initials / finals / tones), read through
  ``profile.lang_table(name)``;
* ``lang_specs[<lang>][<name>]`` — its **non-cell** rules (the NCB exceptions
  record), read through ``profile.lang_spec(name)``.

Both exist so a language, or a standard, arrives without the shared
:class:`~brailix.core.config.BrailleProfile` growing a field for it — which is
what makes "adding a language is registration" true of the profile too, and
what the last test in this file pins directly.
"""

from __future__ import annotations

from brailix.core.config import load_profile


def test_ja_profile_populates_slot():
    profile = load_profile("ja_current")
    assert "ja" in profile.lang_tables
    kana = profile.lang_table("kana")
    assert kana  # non-empty
    # single-cell + two-cell entries both resolve from the cells pool.
    assert kana["ア"] == ((1,),)
    assert kana["ガ"] == ((5,), (1, 6))


def test_zh_reads_its_phoneme_tables_from_the_same_slot():
    # Chinese used to be the exception: three welded fields on the shared
    # dataclass while every other language used the slot. It is now read the
    # same way, which is what makes "adding a language is registration"
    # true of the profile too.
    profile = load_profile("cn_current")
    assert sorted(profile.lang_tables["zh"]) == ["finals", "initials", "tones"]
    assert profile.lang_table("initials")["b"] == ((1, 2),)
    # A table this language doesn't define still answers empty.
    assert profile.lang_table("kana") == {}


def test_language_neutral_tables_stay_out_of_the_slot():
    """``tables.zh`` also declares punctuation / numbers / compounds.

    Those are not per-language cell tables — punctuation and numbers are
    read by every language, and compounds is a word list — so the generic
    loader must not sweep them in just because of where they're declared.
    """
    profile = load_profile("cn_current")
    assert set(profile.lang_tables["zh"]) == {"initials", "finals", "tones"}
    assert profile.punctuation  # still on its own field
    assert profile.zh_compounds  # word list, not a cell table


def test_lang_table_missing_name_returns_empty():
    profile = load_profile("ja_current")
    assert profile.lang_table("nonexistent") == {}


# ---------------------------------------------------------------------------
# The non-cell half of the slot
# ---------------------------------------------------------------------------


def test_the_ncb_record_arrives_through_the_spec_slot():
    from brailix.core.config.zh_ncb_tables import NcbExceptions

    profile = load_profile("cn_ncb")
    assert isinstance(profile.lang_spec("ncb_exceptions"), NcbExceptions)
    # Same subtag keying as the table slot: a profile in another language
    # never sees another language's rules.
    assert set(profile.lang_specs) == {"zh"}


def test_a_profile_that_declares_no_spec_answers_the_default():
    profile = load_profile("cn_current")
    assert profile.lang_spec("ncb_exceptions") is None
    assert profile.lang_spec("ncb_exceptions", "fallback") == "fallback"


def test_the_spec_slot_is_keyed_by_the_profile_language():
    # ``lang_spec`` looks under the profile's own subtag, so a record filed
    # under a different language is not visible to it — the property that
    # keeps one standard's rules from reaching another's backend.
    from dataclasses import replace

    profile = load_profile("cn_current")
    other = replace(profile, lang_specs={"ja": {"ncb_exceptions": object()}})
    assert other.lang_spec("ncb_exceptions") is None


def test_the_shared_profile_names_no_concrete_standard():
    """The architectural point both slots exist for.

    ``BrailleProfile`` is the type every language and every standard compiles
    through. It carried ``zh_exceptions: NcbExceptions`` — a field, and an
    import, naming one concrete Chinese standard — while the comment beside
    the table slot said a new language should arrive through the slot rather
    than by growing this dataclass. Nothing was broken; what made it worth
    undoing is that the next standard had an obvious place to put
    ``ja_exceptions``, and the one after that ``ko_exceptions``, each one
    dragging the loader, the fingerprint and the annotations along.

    Checked by shape rather than by a list of banned names: a field whose
    declared type comes from a language- or standard-specific module is the
    thing to catch, whichever standard writes it next.
    """
    import re
    from dataclasses import fields

    from brailix.core.config.profile import BrailleProfile

    # Fields whose NAME claims a language subtag: ``zh_compounds`` is the one
    # legitimate case (a scheme-neutral zh word list, not a standard's rules),
    # so this asserts the set rather than emptiness — a new entry has to be
    # argued for here.
    named = {
        f.name
        for f in fields(BrailleProfile)
        if re.match(r"^[a-z]{2}_", f.name)
    }
    assert named == {"zh_compounds"}, (
        f"BrailleProfile grew a per-language field: {sorted(named)}. "
        f"Per-language cell tables go in lang_tables, everything else in "
        f"lang_specs — see this module's docstring."
    )

    # And the import side. Read from the module's real imports rather than
    # from its text: the field's removal is explained in a comment right
    # where it used to be, and a text search cannot tell an explanation of
    # the old coupling from the coupling.
    import ast

    imported = {
        alias.asname or alias.name
        for node in ast.walk(ast.parse(_profile_source()))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    standard_specific = {
        name for name in imported if name.startswith(("Ncb", "Bana"))
    }
    assert not standard_specific, (
        f"brailix/core/config/profile.py imports a concrete standard's "
        f"types {sorted(standard_specific)}; a standard's record belongs in "
        f"lang_specs, opaque to the shared model"
    )


def _profile_source() -> str:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("brailix.core.config.profile")
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).read_text(encoding="utf-8")
