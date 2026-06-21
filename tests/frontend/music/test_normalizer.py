"""MusicXML normalizer passes (``brailix.frontend.music.normalizer``).

Focus here: the chord-member ``<staff>`` / ``<voice>`` inheritance backfill.
MusicXML lets a chord member omit its own staff / voice and inherit the
chord root's; the backend buckets notes by their *own* staff / voice
(defaulting a missing one to "1"), so without the backfill an omitting
member is torn off into bucket "1" and the chord is silently split.
"""

from __future__ import annotations

import pytest

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.frontend.music.normalizer import normalize


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


def _ctx():
    return BackendContext(profile="cn_current", block_type="score")


class TestChordMemberStaffVoiceInheritance:
    def test_backfills_missing_staff_and_voice_from_root(self):
        # The chord member omits both <staff> and <voice>; after normalize it
        # shares the chord root's staff and voice bucket. (Voice numbers are
        # densified to 1..N first, so the member inherits the root's *effective*
        # voice, whatever that remapped to — the invariant is "same bucket".)
        score = normalize(
            '<score-partwise><part id="P1"><measure number="1">'
            "<note><pitch><step>C</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type>"
            "<staff>2</staff><voice>2</voice></note>"
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        notes = score.findall("part/measure/note")
        root, member = notes[0], notes[1]
        assert member.find("chord") is not None
        assert member.findtext("staff") == root.findtext("staff") == "2"
        assert member.findtext("voice") == root.findtext("voice")

    def test_keeps_explicit_member_value(self):
        # A member that spells its own <staff> out is left untouched.
        score = normalize(
            '<score-partwise><part id="P1"><measure number="1">'
            "<note><pitch><step>C</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type><staff>2</staff></note>"
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type><staff>1</staff></note>"
            "</measure></part></score-partwise>"
        )
        notes = score.findall("part/measure/note")
        assert notes[1].findtext("staff") == "1"

    def test_first_note_without_root_is_not_invented(self):
        # A leading chord member with no preceding root has nothing to inherit
        # — don't fabricate a staff/voice (the backend's "1" default applies).
        score = normalize(
            '<score-partwise><part id="P1"><measure number="1">'
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
            "<duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        member = score.findall("part/measure/note")[0]
        assert member.find("staff") is None
        assert member.find("voice") is None

    def test_omitted_staff_chord_emits_same_braille_as_explicit(self, profile):
        # End-to-end equivalence: after normalization, a chord member that
        # omits <staff> must produce byte-identical braille to one that spells
        # <staff>2 out. Before the backfill the omitting member was split off
        # into staff 1, so the two diverged (the chord was torn apart).
        def score(member_staff: str) -> str:
            return (
                '<score-partwise version="4.0"><part id="P1">'
                '<measure number="1">'
                "<attributes><divisions>1</divisions></attributes>"
                "<note><pitch><step>G</step><octave>4</octave></pitch>"
                "<duration>1</duration><type>quarter</type><staff>1</staff></note>"
                "<note><pitch><step>C</step><octave>3</octave></pitch>"
                "<duration>1</duration><type>quarter</type><staff>2</staff></note>"
                "<note><chord/><pitch><step>E</step><octave>3</octave></pitch>"
                f"<duration>1</duration><type>quarter</type>{member_staff}</note>"
                "</measure></part></score-partwise>"
            )

        omitted = emit_tree(normalize(score("")), _ctx(), profile)
        explicit = emit_tree(normalize(score("<staff>2</staff>")), _ctx(), profile)
        assert [(c.role, c.dots) for c in omitted] == [
            (c.role, c.dots) for c in explicit
        ]
