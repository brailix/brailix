"""BANA Par. 2.4 — larger / smaller value signs.

Every printed note shape stands for a "large" value (8th-and-larger)
and its 1/16 "small" value (16th-and-smaller); a 256th is a third tier.
A value sign is placed before the change of value so the reader isn't
left guessing (e.g. a half immediately followed by a 32nd). 256th
passages always carry their own sign.
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


def _note(step: str, octave: int, type_name: str) -> str:
    return (
        f"<note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        f"<duration>4</duration><type>{type_name}</type></note>"
    )


def _measure(*notes: str, attrs: str = "") -> ET.Element:
    return ET.fromstring(
        '<measure number="1">' + attrs + "".join(notes) + "</measure>"
    )


def _value_sign_runs(cells) -> list[str]:
    """Ordered value-sign occurrences (each sign is a multi-cell entity,
    so collapse a contiguous run of identical signs to one)."""
    runs: list[str] = []
    prev = None
    for c in cells:
        if c.role == "music_value_sign":
            cat = (c.source_text or "").split()[0]  # "value:small ..."
            if cat != prev:
                runs.append(cat)
            prev = cat
        else:
            prev = None
    return runs


class TestValueSigns:
    def test_uniform_durations_emit_no_sign(self, profile, ctx):
        cells = emit_tree(
            _measure(
                _note("C", 4, "quarter"),
                _note("D", 4, "quarter"),
                _note("E", 4, "quarter"),
            ),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == []

    def test_half_then_32nd_emits_smaller_sign(self, profile, ctx):
        # BANA Example 2.4-1: half immediately followed by a 32nd.
        cells = emit_tree(
            _measure(_note("C", 4, "half"), _note("D", 4, "32nd")),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == ["value:small"]

    def test_32nd_then_half_emits_larger_sign(self, profile, ctx):
        cells = emit_tree(
            _measure(_note("C", 4, "32nd"), _note("D", 4, "half")),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == ["value:large"]

    def test_back_and_forth_emits_both(self, profile, ctx):
        cells = emit_tree(
            _measure(
                _note("C", 4, "half"),
                _note("D", 4, "32nd"),
                _note("E", 4, "half"),
            ),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == ["value:small", "value:large"]

    def test_unknown_type_sets_large_baseline_for_next_note(self, profile, ctx):
        # An unknown <type> renders the quarter (large) fallback, so its
        # value category must update the baseline to "large".  Otherwise
        # [16th, BOGUS, 16th] keeps the stale "small" baseline and the
        # second 16th drops its sign; with the fix the second 16th re-marks
        # small (small -> large(bogus) -> small).
        cells = emit_tree(
            _measure(
                _note("C", 4, "16th"),
                _note("D", 4, "bogus"),
                _note("E", 4, "16th"),
            ),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == ["value:small"]

    def test_first_note_small_is_baseline_no_sign(self, profile, ctx):
        # First note establishes the baseline silently (the note-count
        # heuristic resolves a uniform small measure).
        cells = emit_tree(
            _measure(_note("C", 4, "16th"), _note("D", 4, "16th")),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == []

    def test_256th_always_signs_even_first(self, profile, ctx):
        cells = emit_tree(_measure(_note("C", 4, "256th")), ctx, profile)
        assert _value_sign_runs(cells) == ["value:v256"]

    def test_consecutive_256ths_are_one_passage(self, profile, ctx):
        cells = emit_tree(
            _measure(_note("C", 4, "256th"), _note("D", 4, "256th")),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == ["value:v256"]

    def test_rest_participates_in_value_stream(self, profile, ctx):
        # A 32nd rest after a half note carries the smaller sign too.
        rest_32 = "<note><rest/><duration>1</duration><type>32nd</type></note>"
        cells = emit_tree(
            _measure(_note("C", 4, "half"), rest_32), ctx, profile
        )
        assert _value_sign_runs(cells) == ["value:small"]

    def test_feature_off_disables_value_signs(self, profile, ctx, monkeypatch):
        # ``features.music.value_signs = false`` suppresses the rule.
        original = type(profile).feature

        def _patched(self, name, default=None):
            if name == "music.value_signs":
                return False
            return original(self, name, default)

        monkeypatch.setattr(type(profile), "feature", _patched)
        cells = emit_tree(
            _measure(_note("C", 4, "half"), _note("D", 4, "32nd")),
            ctx,
            profile,
        )
        assert _value_sign_runs(cells) == []
