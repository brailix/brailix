"""Tests for the unified NCB exceptions resource (`profile.zh_exceptions`).

Two layers:

* Unit tests on :class:`NcbCharOverrides` / :class:`NcbWordOverrides`
  lookups directly, with the loaded ``cn_ncb.zh_exceptions``
  table.  Data lives on the profile (filled by the profile loader at
  load time); there's no backend-side factory.
* Integration tests through :func:`translate_word` checking exact
  cell sequences match the documented worked examples.

These two layers used to live in three separate files
(test_zh_tone_policy + test_zh_word_shorthand + test_zh_disambiguation)
against three separate JSON resources.  After the NCB-resource
collapse (2026-05-25), all NCB data ships in
``resources/cn/ncb/exceptions.json`` and the tone-omission tests
keep their own file (the tone strategy is an adapter, not a table
lookup).  Char + word overrides live here.
"""

from __future__ import annotations

import pytest

from brailix.backend.zh import translate_word
from brailix.core.config import load_profile
from brailix.core.config.zh_ncb_tables import (
    NcbCharOverrides,
    NcbExceptions,
    NcbWordOverrides,
)
from brailix.core.context import BackendContext
from brailix.ir.inline import Word

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cn_current():
    return load_profile("cn_current")


@pytest.fixture(scope="module")
def cn_ncb():
    return load_profile("cn_ncb")


@pytest.fixture(scope="module")
def char_overrides(cn_ncb):
    exc = cn_ncb.zh_exceptions
    assert exc is not None
    assert exc.char_overrides is not None
    return exc.char_overrides


@pytest.fixture(scope="module")
def word_overrides(cn_ncb):
    exc = cn_ncb.zh_exceptions
    assert exc is not None
    assert exc.word_overrides is not None
    return exc.word_overrides


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


def _has_tone(cells) -> bool:
    return any(c.role == "zh_tone" for c in cells)


def _dots(cells):
    return [c.dots for c in cells]


# ---------------------------------------------------------------------------
# Loader / opt-in
# ---------------------------------------------------------------------------


class TestLoader:
    def test_profile_carries_exceptions_for_cn_ncb(self, cn_ncb):
        assert isinstance(cn_ncb.zh_exceptions, NcbExceptions)

    def test_profile_has_no_exceptions_for_cn_current(self, cn_current):
        assert cn_current.zh_exceptions is None

    def test_char_overrides_present(self, cn_ncb):
        assert isinstance(cn_ncb.zh_exceptions.char_overrides, NcbCharOverrides)

    def test_word_overrides_present(self, cn_ncb):
        assert isinstance(cn_ncb.zh_exceptions.word_overrides, NcbWordOverrides)

    def test_all_six_shorthand_chars_loaded(self, char_overrides):
        # 的 / 么 / 你 / 他 / 她 / 它 — all have a shorthand sub-record.
        for ch in ("的", "么", "你", "他", "她", "它"):
            entry = char_overrides.by_char.get(ch)
            assert entry is not None, f"{ch!r} missing"
            assert entry.shorthand is not None

    def test_disambiguation_chars_loaded(self, char_overrides):
        # 再 / 问 — keep_tone overrides (no shorthand sub-record).
        for ch in ("再", "问"):
            entry = char_overrides.by_char.get(ch)
            assert entry is not None, f"{ch!r} missing"
            assert entry.shorthand is None
            assert entry.keep_tone is True

    def test_boundary_exception_flags(self, char_overrides):
        # 5 of the 6 shorthand chars carry boundary_exception=True
        # (的/么/你/他/它); 她 doesn't (always shortens).
        for ch in ("的", "么", "你", "他", "它"):
            assert char_overrides.by_char[ch].shorthand.boundary_exception is True
        assert char_overrides.by_char["她"].shorthand.boundary_exception is False

    def test_word_overrides_loaded(self, word_overrides):
        assert "地道" in word_overrides.by_word
        assert word_overrides.by_word["地道"] == (False, True)


# ---------------------------------------------------------------------------
# NcbCharOverrides.shorthand_cells_for — shorthand chars + tone-omission
# ---------------------------------------------------------------------------


class TestShorthandLookup:
    """Each test names the shorthand rule it covers."""

    # -- 的 (de0) ---------------------------------------------------------

    def test_de_no_boundary(self, char_overrides):
        # No next syllable → shorthand applies.
        assert char_overrides.shorthand_cells_for("的") == ((1, 4, 5),)

    def test_de_next_zero_initial_falls_through(self, char_overrides):
        # 的 followed by zero-initial → boundary exc fires, 的 has no
        # boundary_spelling → return None (caller falls through).
        assert (
            char_overrides.shorthand_cells_for("的", next_is_zero_initial=True)
            is None
        )

    def test_de_next_has_initial_keeps_shorthand(self, char_overrides):
        assert char_overrides.shorthand_cells_for(
            "的", next_is_zero_initial=False
        ) == ((1, 4, 5),)

    # -- 么 (me0) ---------------------------------------------------------

    def test_me_keeps_shorthand_when_followed_by_consonant(self, char_overrides):
        assert char_overrides.shorthand_cells_for(
            "么", next_is_zero_initial=False
        ) == ((1, 3, 4),)

    def test_me_falls_through_when_followed_by_zero_initial(self, char_overrides):
        # 怎么样 — 么 followed by yang4 (zero-initial) → boundary exc
        # → fall through (no boundary_spelling for 么).
        assert (
            char_overrides.shorthand_cells_for("么", next_is_zero_initial=True)
            is None
        )

    # -- 你 (ni3) ---------------------------------------------------------

    def test_ni_shorthand(self, char_overrides):
        assert char_overrides.shorthand_cells_for("你") == ((1, 3, 4, 5),)

    def test_ni_fall_through_on_zero_initial_next(self, char_overrides):
        assert (
            char_overrides.shorthand_cells_for("你", next_is_zero_initial=True)
            is None
        )

    # -- 他 (ta1) — has boundary_spelling ---------------------------------

    def test_ta_male_shorthand_default(self, char_overrides):
        assert char_overrides.shorthand_cells_for("他") == ((2, 3, 4, 5),)
        assert char_overrides.shorthand_cells_for(
            "他", next_is_zero_initial=False
        ) == ((2, 3, 4, 5),)

    def test_ta_male_special_spelling_on_zero_initial_next(self, char_overrides):
        # 他用 — 他 followed by yong4 (zero-initial) → use boundary_spelling
        # ⠞⠔ (t + a, tone-1 still omitted).
        assert char_overrides.shorthand_cells_for(
            "他", next_is_zero_initial=True
        ) == ((2, 3, 4, 5), (3, 5))

    # -- 她 (ta1) — no boundary exception ---------------------------------

    def test_ta_female_always_shortened(self, char_overrides):
        # 她 not in boundary_exception → always shorthand ⠞⠁.
        assert char_overrides.shorthand_cells_for("她") == ((2, 3, 4, 5), (1,))
        assert char_overrides.shorthand_cells_for(
            "她", next_is_zero_initial=True
        ) == ((2, 3, 4, 5), (1,))

    # -- 它 (ta1) — has boundary_spelling with 4-dot prefix ---------------

    def test_ta_thing_shorthand_default(self, char_overrides):
        assert char_overrides.shorthand_cells_for("它") == ((4,), (2, 3, 4, 5))

    def test_ta_thing_special_spelling_on_zero_initial_next(self, char_overrides):
        assert char_overrides.shorthand_cells_for(
            "它", next_is_zero_initial=True
        ) == ((4,), (2, 3, 4, 5), (3, 5))

    # -- chars without shorthand (再 / 问) --------------------------------

    def test_zai_has_no_shorthand(self, char_overrides):
        # 再 only has keep_tone, no shorthand sub-record.
        assert char_overrides.shorthand_cells_for("再") is None

    def test_wen_has_no_shorthand(self, char_overrides):
        assert char_overrides.shorthand_cells_for("问") is None

    # -- Unknown char ----------------------------------------------------

    def test_unknown_char_returns_none(self, char_overrides):
        assert char_overrides.shorthand_cells_for("汉") is None


# ---------------------------------------------------------------------------
# NcbCharOverrides.should_force_keep_tone — char-level
# ---------------------------------------------------------------------------


class TestCharKeepTone:
    def test_zai_keeps_tone(self, char_overrides):
        assert char_overrides.should_force_keep_tone("再") is True

    def test_wen_keeps_tone(self, char_overrides):
        assert char_overrides.should_force_keep_tone("问") is True

    def test_shorthand_chars_do_not_keep_tone(self, char_overrides):
        # Shorthand chars have shorthand_cells but no keep_tone.
        for ch in ("的", "么", "你", "他", "她", "它"):
            assert char_overrides.should_force_keep_tone(ch) is False

    def test_unknown_char_returns_false(self, char_overrides):
        assert char_overrides.should_force_keep_tone("在") is False


# ---------------------------------------------------------------------------
# NcbWordOverrides.should_force_keep_tone — word-level
# ---------------------------------------------------------------------------


class TestWordKeepTone:
    def test_didao_second_position_kept(self, word_overrides):
        assert word_overrides.should_force_keep_tone(
            word_surface="地道", char_index_in_word=1
        ) is True

    def test_didao_first_position_not_kept(self, word_overrides):
        assert word_overrides.should_force_keep_tone(
            word_surface="地道", char_index_in_word=0
        ) is False

    def test_unknown_word_returns_false(self, word_overrides):
        assert word_overrides.should_force_keep_tone(
            word_surface="街道", char_index_in_word=1
        ) is False


# ---------------------------------------------------------------------------
# End-to-end: cn_ncb + zh backend — shorthand
# ---------------------------------------------------------------------------


class TestShorthandEndToEnd:
    def test_ni_alone(self, ctx, cn_ncb):
        # 你 → ⠝ (single shorthand cell).
        cells = translate_word(
            Word(surface="你", reading="ni3"), ctx, cn_ncb
        )
        assert _dots(cells) == [(1, 3, 4, 5)]
        assert cells[0].role == "zh_shorthand"

    def test_zen_me(self, ctx, cn_ncb):
        # 怎么 → ⠵⠴⠄⠍ (z+en+tone3 + m-shorthand).
        cells = translate_word(
            Word(surface="怎么", reading="zen3 me5"), ctx, cn_ncb
        )
        assert _dots(cells) == [
            (1, 3, 5, 6),  # z
            (3, 5, 6),     # en
            (3,),          # tone 3
            (1, 3, 4),     # 么 shorthand = m
        ]

    def test_ta_yong(self, ctx, cn_ncb):
        # 他用 → ⠞⠔⠽ — 他 boundary exc fires, falls back to special
        # spelling (t + a, tone-1 still omitted).
        cells = translate_word(
            Word(surface="他用", reading="ta1 yong4"), ctx, cn_ncb
        )
        # ta -> boundary_spelling [c_2345, c_35]; yong -> iong final
        # (zero-initial, default omit 4 → tone dropped).
        assert _dots(cells) == [
            (2, 3, 4, 5),  # t
            (3, 5),        # a (boundary_spelling, no tone)
            (1, 4, 5, 6),  # iong = ⠽
        ]


# ---------------------------------------------------------------------------
# End-to-end: cn_ncb + zh backend — tone disambiguation
# ---------------------------------------------------------------------------


class TestDisambiguationEndToEnd:
    """Worked examples produce the correct cell sequences."""

    def test_zai_keeps_tone(self, ctx, cn_ncb):
        # 再 zài → ⠵⠪⠆ (z + ai + tone-4 kept by char override).
        cells = translate_word(
            Word(surface="再", reading="zai4"), ctx, cn_ncb
        )
        assert _dots(cells) == [
            (1, 3, 5, 6),  # z
            (2, 4, 6),     # ai
            (2, 3),        # tone 4 (kept)
        ]

    def test_zai_vs_zai_contrast_with_distinct_chars(self, ctx, cn_ncb):
        # 在 zài → ⠵⠪ (no override, z-omit-4 applies, tone dropped).
        cells = translate_word(
            Word(surface="在", reading="zai4"), ctx, cn_ncb
        )
        assert _dots(cells) == [(1, 3, 5, 6), (2, 4, 6)]
        assert not _has_tone(cells)

    def test_wen_keeps_tone(self, ctx, cn_ncb):
        # 问 wèn → ⠒⠆ (uen + tone-4 for tactile anchor).
        cells = translate_word(
            Word(surface="问", reading="wen4"), ctx, cn_ncb
        )
        assert _dots(cells) == [(2, 5), (2, 3)]  # uen + tone-4

    def test_didao_word_keeps_second_tone(self, ctx, cn_ncb):
        # 地道 dì-dào → ⠙⠊⠙⠖⠆.
        cells = translate_word(
            Word(surface="地道", reading="di4 dao4"), ctx, cn_ncb
        )
        assert _dots(cells) == [
            (1, 4, 5),     # d
            (2, 4),        # i
            (1, 4, 5),     # d
            (2, 3, 5),     # ao
            (2, 3),        # tone 4 (kept by word override)
        ]

    def test_jiedao_word_no_override_both_tones_dropped(self, ctx, cn_ncb):
        # 街道 jiē-dào — NOT in word_overrides; both syllables follow
        # their initial-based rules.  jie1: j-omit-4 doesn't touch tone 1;
        # kept.  dao4: d-omit-4 drops tone.
        cells = translate_word(
            Word(surface="街道", reading="jie1 dao4"), ctx, cn_ncb
        )
        assert _dots(cells) == [
            (1, 2, 4, 5),  # j
            (1, 5),        # ie
            (1,),          # tone 1 kept
            (1, 4, 5),     # d
            (2, 3, 5),     # ao (tone 4 dropped, no override)
        ]

    def test_zai_in_compound_still_keeps_tone(self, ctx, cn_ncb):
        # 再次 zài-cì — char override on 再 still fires when 再 is the
        # first char of a multi-char Word.
        cells = translate_word(
            Word(surface="再次", reading="zai4 ci4"), ctx, cn_ncb
        )
        assert _dots(cells) == [
            (1, 3, 5, 6),  # z
            (2, 4, 6),     # ai
            (2, 3),        # tone 4 (kept by 再 override)
            (1, 4),        # c (syllabic-i)
            (2, 3),        # tone 4 (ci4 — c-omit-2 doesn't apply to tone 4)
        ]


class TestCnDefaultUnaffected:
    """Profiles without ``tables.zh.exceptions`` keep emitting via the
    legacy path: every non-neutral tone produces a tone cell."""

    def test_zai_cn_current_emits_tone(self, ctx, cn_current):
        cells = translate_word(
            Word(surface="再", reading="zai4"), ctx, cn_current
        )
        assert _has_tone(cells)

    def test_cn_current_carries_no_exceptions(self, cn_current):
        assert cn_current.zh_exceptions is None


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


class TestLoaderValidation:
    """Configuration errors fire at profile-load time, not on first translation."""

    def test_word_override_length_mismatch_raises(self, tmp_path):
        from brailix.core.config.loader import _load_zh_exceptions
        from brailix.core.errors import ConfigurationError

        bad = tmp_path / "bad_exceptions.json"
        bad.write_text(
            '{"word_overrides": {"entries": ['
            '{"_id": "x", "surface": "地道", "keep_tone_per_char": [true]}'
            "]}}",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="length 1"):
            _load_zh_exceptions(tmp_path, "bad_exceptions.json", {})

    def test_duplicate_char_override_raises(self, tmp_path):
        from brailix.core.config.loader import _load_zh_exceptions
        from brailix.core.errors import ConfigurationError

        bad = tmp_path / "bad_exceptions.json"
        bad.write_text(
            '{"char_overrides": {"entries": ['
            '{"_id": "a", "surface": "再", "keep_tone": true},'
            '{"_id": "b", "surface": "再", "keep_tone": true}'
            "]}}",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="duplicate"):
            _load_zh_exceptions(tmp_path, "bad_exceptions.json", {})

    def test_missing_surface_raises(self, tmp_path):
        from brailix.core.config.loader import _load_zh_exceptions
        from brailix.core.errors import ConfigurationError

        bad = tmp_path / "bad_exceptions.json"
        bad.write_text(
            '{"char_overrides": {"entries": [{"_id": "noop", "keep_tone": true}]}}',
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="surface"):
            _load_zh_exceptions(tmp_path, "bad_exceptions.json", {})

    def test_tone_omission_non_dict_by_initial_entry_raises(self, tmp_path):
        # A "b": "4" shorthand (instead of "b": {"omit_tone": "4"}) used to be
        # silently dropped by an isinstance filter, so the backend never saw
        # that initial's rule and emitted the tone anyway — wrong braille with
        # no diagnostic. It must now fail loudly at load, like the other
        # malformed-config paths.
        from brailix.core.config.loader import _load_zh_exceptions
        from brailix.core.errors import ConfigurationError

        bad = tmp_path / "bad_exceptions.json"
        bad.write_text(
            '{"tone_omission": {"by_initial": {"b": "4"}, "zero_initial": {}}}',
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="by_initial"):
            _load_zh_exceptions(tmp_path, "bad_exceptions.json", {})
