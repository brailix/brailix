"""Test the music section of cn_current's loaded BrailleProfile.

Verifies the loader hooks the new ``tables.music`` block in
:mod:`brailix.core.config.loader` and exposes the cells through
:meth:`BrailleProfile.music_cell` / :meth:`BrailleProfile.music_topic`.

Resources come from ``brailix/resources/music/`` — already covered for
correctness by ``tests/resources/test_music_tables.py``; this file
focuses on the load + lookup path, not on the cell values per se.
"""

from __future__ import annotations

import pytest

from brailix.core.config import load_profile


@pytest.fixture(scope="module")
def cn_current():
    return load_profile("cn_current")


class TestMusicFeatures:
    def test_features_dotted_lookup(self, cn_current):
        assert cn_current.feature("music.standard") == "bana_2015"
        assert cn_current.feature("music.octave_rule") == "interval16"
        assert cn_current.feature("music.show_lyrics") is True
        assert cn_current.feature("music.in_accord_marker") is True

    def test_unknown_music_feature_returns_default(self, cn_current):
        assert cn_current.feature("music.nope", "fb") == "fb"


class TestMusicSpecs:
    def test_chord_kind_spec_loaded(self, cn_current):
        # Chord-kind emit recipes moved from a hard-coded backend table
        # into chord_symbols.json's ``_kind_spec`` section, loaded as a
        # declarative spec the harmony handler reads.
        spec = cn_current.music_spec("chord_symbols", "kind_spec")
        assert isinstance(spec, dict)
        assert spec["major"] == []
        assert spec["minor"] == [["letters", "m"]]
        assert spec["half-diminished"] == [["entity", "half_diminished"]]

    def test_kind_spec_does_not_leak_into_cells_topic(self, cn_current):
        # The ``_``-prefixed spec section is skipped by the cells loader.
        chord_cells = cn_current.music.get("chord_symbols", {})
        assert "kind_spec" not in chord_cells
        assert "_kind_spec" not in chord_cells
        # The real cell entities still load.
        assert cn_current.music_cell("chord_symbols", "plus") is not None

    def test_music_spec_absent_returns_none(self, cn_current):
        assert cn_current.music_spec("chord_symbols", "nope") is None
        assert cn_current.music_spec("notes", "kind_spec") is None


class TestMusicTopicsLoaded:
    def test_top_level_topics(self, cn_current):
        # Every file-level topic referenced from tables.music must load.
        expected = {
            "general", "notes", "octaves", "clefs", "rests",
            "accidentals_key", "meter", "grouping", "intervals", "tie",
            "in_accord", "stems", "slur", "tremolo", "fingerings",
            "ornaments", "print_repeats", "braille_repeats",
            "numeral_repeats", "dacapo", "annotations", "nuances",
            "chord_symbols", "numerals",
        }
        assert expected.issubset(set(cn_current.music.keys()))

    def test_subdirectory_topics_flattened(self, cn_current):
        # tables.music value "resources/music/instruments/" gets expanded
        # into one topic per JSON: instruments.<stem>
        assert "instruments.keyboard" in cn_current.music
        assert "instruments.strings" in cn_current.music
        assert "instruments.names_en" in cn_current.music
        assert "vocal.music_lines" in cn_current.music
        assert "vocal.word_lines" in cn_current.music

    def test_total_topic_count(self, cn_current):
        # 24 file-level + 10 instruments + 2 vocal = 36.
        music_topics = [
            k for k in cn_current.music
            if not k.startswith("_")
        ]
        assert len(music_topics) == 36


class TestMusicCellLookup:
    """Spot-checks: a handful of entries must decode through
    profile.music_cell() to the right NABCC-derived dot tuples."""

    def test_notes_whole_C(self, cn_current):
        # BANA Table 2: whole/16th C = Y = dots 1,3,4,5,6
        cells = cn_current.music_cell("notes", "whole_or_16th_C")
        assert cells == ((1, 3, 4, 5, 6),)

    def test_notes_quarter_E(self, cn_current):
        # BANA Table 2: quarter/64th E = $ = dots 1,2,4,6
        assert cn_current.music_cell("notes", "quarter_or_64th_E") == ((1, 2, 4, 6),)

    def test_octave_prefixes(self, cn_current):
        # BANA Table 3: 7 octave prefixes are 1-cell each.
        prefixes = {
            "first_octave":   (4,),
            "second_octave":  (4, 5),
            "third_octave":   (4, 5, 6),
            "fourth_octave":  (5,),
            "fifth_octave":   (4, 6),
            "sixth_octave":   (5, 6),
            "seventh_octave": (6,),
        }
        for name, dots in prefixes.items():
            assert cn_current.music_cell("octaves", name) == (dots,)

    def test_treble_clef_is_three_cells(self, cn_current):
        # BANA Table 4: G clef (treble) = > / l => 3 cells.
        cells = cn_current.music_cell("clefs", "g_clef_treble")
        assert cells == ((3, 4, 5), (3, 4), (1, 2, 3))

    def test_accidentals_sharp_and_double_sharp(self, cn_current):
        assert cn_current.music_cell("accidentals_key", "sharp") == ((1, 4, 6),)
        assert cn_current.music_cell("accidentals_key", "double_sharp") == (
            (1, 4, 6), (1, 4, 6),
        )

    def test_slur_blank_cell_sentinel(self, cn_current):
        # "Cc c" -> 4 cells; the third one is empty (c_blank -> ()).
        cells = cn_current.music_cell("slur", "doubled_long_slur")
        assert cells == ((1, 4), (1, 4), (), (1, 4))

    def test_unknown_topic_returns_none(self, cn_current):
        assert cn_current.music_cell("nope", "entity") is None

    def test_unknown_entity_returns_none(self, cn_current):
        assert cn_current.music_cell("notes", "nope") is None


class TestMusicTopicAccess:
    def test_topic_dict_complete(self, cn_current):
        # The "octaves" topic should expose all 9 entries (7 prefixes
        # plus below_first / above_seventh) via music_topic().
        octaves = cn_current.music_topic("octaves")
        assert set(octaves.keys()) == {
            "first_octave", "second_octave", "third_octave",
            "fourth_octave", "fifth_octave", "sixth_octave",
            "seventh_octave", "below_first_octave", "above_seventh_octave",
        }

    def test_topic_dict_empty_for_unknown(self, cn_current):
        assert cn_current.music_topic("nope") == {}

    def test_stubbed_topic_is_empty_dict(self, cn_current):
        # Most instruments tables are still skeletons (keyboard /
        # figured_bass / ... deferred); strings was populated in M-instr1.
        assert cn_current.music_topic("instruments.keyboard") == {}
        assert cn_current.music_topic("instruments.figured_bass") == {}

    def test_strings_topic_populated(self, cn_current):
        # Table 24 (B) bowed + clear (A) technical signs (M-instr1).
        strings = cn_current.music_topic("instruments.strings")
        assert strings  # no longer an empty stub
        assert "down_bow" in strings and "up_bow" in strings

    def test_vocal_topics_populated(self, cn_current):
        # S8 populated Tables 31 / 32 from BANA PDF pp. 56-57.
        music_lines = cn_current.music_topic("vocal.music_lines")
        word_lines = cn_current.music_topic("vocal.word_lines")
        assert "soprano_identifier" in music_lines
        assert "single_syllabic_slur" in music_lines
        assert "soprano_identifier" in word_lines
        assert "repetition_word_or_phrase" in word_lines


class TestTongyongMusicParity:
    """National Common Braille (NCB) (GF0019-2018) shares the BANA 2015
    music tables with Current Chinese Braille. The two profiles diverge on
    Chinese-specific tables only; music wiring must stay identical so NCB
    users can translate scores too."""

    @pytest.fixture(scope="class")
    def cn_ncb(self):
        return load_profile("cn_ncb")

    def test_music_features_match_cn_current(self, cn_ncb, cn_current):
        assert cn_ncb.features.get("music") == cn_current.features.get("music")

    def test_music_topics_match_cn_current(self, cn_ncb, cn_current):
        assert set(cn_ncb.music.keys()) == set(cn_current.music.keys())

    def test_music_cells_match_cn_current(self, cn_ncb, cn_current):
        # Spot-check that resolved cells in every topic agree — catches
        # the case where ncb ever points music tables at a different
        # resource directory.
        for topic in cn_current.music:
            assert cn_ncb.music_topic(topic) == cn_current.music_topic(topic), (
                f"music topic diverges between profiles: {topic!r}"
            )
