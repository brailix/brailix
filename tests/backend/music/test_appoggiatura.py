"""Tests for S4: appoggiatura (BANA Par. 16.2 / Table 16 A).

MusicXML's ``<grace>`` element on a ``<note>`` marks the note as a
grace / appoggiatura. The ``slash`` attribute (``"yes"`` vs absent)
distinguishes the short (acciaccatura) from the long form. BANA
emits the marker cell in front of all other per-note modifiers per
Par. 16.2.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _roles(cells):
    return [c.role for c in cells]


def _dots(cells):
    return [c.dots for c in cells]


# ---------------------------------------------------------------------------
# Long vs short appoggiatura
# ---------------------------------------------------------------------------


class TestAppoggiatura:
    def test_long_appoggiatura(self, profile, ctx):
        # <grace/> without slash → long appoggiatura ("5)
        note = ET.fromstring(
            "<note><grace/>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>eighth</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # long_appoggiatura = '"5' = (5,)(2,6)
        assert _dots(cells)[:2] == [(5,), (2, 6)]
        assert _roles(cells)[:2] == [
            "music_appoggiatura", "music_appoggiatura",
        ]

    def test_short_appoggiatura(self, profile, ctx):
        # <grace slash="yes"/> → short appoggiatura (5)
        note = ET.fromstring(
            '<note><grace slash="yes"/>'
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>eighth</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        # short_appoggiatura = '5' = (2,6)
        assert _dots(cells)[0] == (2, 6)
        assert _roles(cells)[0] == "music_appoggiatura"

    def test_appoggiatura_comes_before_octave(self, profile, ctx):
        note = ET.fromstring(
            "<note><grace/>"
            "<pitch><step>G</step><octave>5</octave></pitch>"
            "<duration>1</duration><type>eighth</type></note>"
        )
        roles = _roles(emit_tree(note, ctx, profile))
        # [appoggiatura(2 cells), octave, note]
        # First non-appoggiatura cell is the octave mark.
        first_oct_idx = roles.index("music_octave")
        first_app_idx = roles.index("music_appoggiatura")
        assert first_app_idx < first_oct_idx


# ---------------------------------------------------------------------------
# Feature gating + combined ordering
# ---------------------------------------------------------------------------


class TestFeatureGating:
    def test_show_ornaments_false_suppresses(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_ornaments",
            False,
        )
        note = ET.fromstring(
            "<note><grace/>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration><type>eighth</type></note>"
        )
        cells = emit_tree(note, ctx, profile)
        assert "music_appoggiatura" not in _roles(cells)


class TestCombinedOrdering:
    def test_appoggiatura_with_accidental_and_dot(self, profile, ctx):
        note = ET.fromstring(
            "<note><grace/>"
            "<pitch><step>C</step><alter>1</alter><octave>4</octave></pitch>"
            "<duration>3</duration><type>eighth</type><dot/>"
            "<accidental>sharp</accidental>"
            "</note>"
        )
        roles = _roles(emit_tree(note, ctx, profile))
        # [appoggiatura(2), accidental, octave, note, dot]
        # Verify appoggiatura first, then accidental, then octave.
        assert roles[:2] == ["music_appoggiatura", "music_appoggiatura"]
        assert roles[2] == "music_accidental"
        assert "music_octave" in roles[3:]
        assert roles[-1] == "music_dot"


# ---------------------------------------------------------------------------
# Rest doesn't get appoggiatura
# ---------------------------------------------------------------------------


class TestRestNoAppoggiatura:
    def test_rest_ignores_grace(self, profile, ctx):
        # MusicXML doesn't really allow <grace> on a rest, but if it
        # showed up, _emit_rest doesn't route through _emit_note, so
        # the appoggiatura wouldn't fire. Verifies that path stays
        # quiet without crashing.
        rest = ET.fromstring(
            "<note><grace/><rest/>"
            "<duration>1</duration><type>quarter</type></note>"
        )
        cells = emit_tree(rest, ctx, profile)
        assert "music_appoggiatura" not in _roles(cells)
