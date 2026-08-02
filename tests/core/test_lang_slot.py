"""The generic per-language table slot (ARCHITECTURE#arch-language-slots).

A non-zh profile loads its ``tables.<lang>`` section into
``profile.lang_tables[<lang>]``; zh keeps its welded
``initials`` / ``finals`` / ``tones`` fields and leaves the slot empty.
``profile.lang_table(name)`` reads the current language's table.
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
