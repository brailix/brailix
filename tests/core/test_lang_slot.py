"""The generic per-language table slot (ARCHITECTURE §7.6).

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


def test_zh_profile_leaves_slot_empty():
    # zh uses the welded initials/finals/tones fields, not the generic
    # slot — loading cn_current must not populate lang_tables (and the
    # accessor returns {} for a table the language doesn't define).
    profile = load_profile("cn_current")
    assert profile.lang_tables == {}
    assert profile.lang_table("kana") == {}


def test_lang_table_missing_name_returns_empty():
    profile = load_profile("ja_current")
    assert profile.lang_table("nonexistent") == {}
