"""Blank-cell measure separator — ``_emit_part`` inserts one blank cell
between adjacent measures.

BANA music braille separates in-line measures with a single blank cell.
MusicXML usually omits the ``<barline>`` for a regular bar line, so the
separator is emitted at the measure boundary (``_emit_part``) rather than
by the barline handler. It is controlled by ``music.measure_separator``
(default ``"space"``; ``"none"`` disables it).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext

_NOTE = (
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<type>quarter</type></note>"
)


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _score(*measure_bodies: str) -> ET.Element:
    measures = "".join(
        f"<measure number='{i + 1}'>{body}</measure>"
        for i, body in enumerate(measure_bodies)
    )
    return ET.fromstring(
        f"<score-partwise><part id='P1'>{measures}</part></score-partwise>"
    )


def _seps(cells):
    return [c for c in cells if c.role == "music_measure_sep"]


def test_blank_cell_between_consecutive_measures(profile, ctx):
    cells = emit_tree(_score(_NOTE, _NOTE, _NOTE), ctx, profile)
    seps = _seps(cells)
    # 3 measures → 2 separators (none before the first).
    assert len(seps) == 2
    assert all(c.dots == () for c in seps)


def test_no_separator_before_first_measure(profile, ctx):
    cells = emit_tree(_score(_NOTE), ctx, profile)
    assert _seps(cells) == []


def test_separator_none_disables(profile, ctx, monkeypatch):
    # BrailleProfile uses slots — mutate the live features dict (feature()
    # honours live changes) rather than setattr-ing the bound method.
    monkeypatch.setitem(profile.features["music"], "measure_separator", "none")
    cells = emit_tree(_score(_NOTE, _NOTE), ctx, profile)
    assert _seps(cells) == []


def test_separator_coexists_with_explicit_barline(profile, ctx):
    """A final/repeat <barline> still emits its own cells; the measure
    separator is independent of it."""
    m1 = _NOTE + "<barline><bar-style>light-heavy</bar-style></barline>"
    cells = emit_tree(_score(m1, _NOTE), ctx, profile)
    assert len(_seps(cells)) == 1  # between the two measures
    assert [c for c in cells if c.role == "music_bar_line"]  # bar still present
