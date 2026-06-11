"""Music warnings carry the (part, measure) anchor.

``MusicBrailleContext.warn`` stamps every in-walk diagnostic with the
``current_part_id`` / ``current_measure_number`` provenance the cell
``source_text`` tags already use, filling :attr:`Warning.anchor` so a
downstream tool can navigate from the warning to its measure.  A bare
collector ``warn`` has no location — normalized MusicXML elements
carry no text offsets, so without the anchor a music warning names no
navigable place at all.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import MusicBrailleContext, emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def test_in_walk_warning_carries_part_and_measure(profile, ctx):
    # <type>bogus</type> → MUSIC_DURATION_AMBIGUOUS, emitted inside the
    # part/measure walk, so the anchor must name both — and name the
    # *second* measure, not the first one the walk passed through.
    score = ET.fromstring(
        "<score-partwise><part id='P1'>"
        "<measure number='1'><note>"
        "<pitch><step>C</step><octave>4</octave></pitch>"
        "<type>quarter</type></note></measure>"
        "<measure number='2'><note>"
        "<pitch><step>D</step><octave>4</octave></pitch>"
        "<type>bogus</type></note></measure>"
        "</part></score-partwise>"
    )
    emit_tree(score, ctx, profile)
    hits = [w for w in ctx.warnings if w.code == "MUSIC_DURATION_AMBIGUOUS"]
    assert hits, "expected the bogus <type> to warn"
    assert hits[0].anchor == {"part_id": "P1", "measure_number": "2"}


def test_warn_outside_walk_has_no_anchor(profile, ctx):
    """Score-level warns (no part / measure in effect) omit the anchor
    entirely — downstream reads None as "no narrower location"."""
    mctx = MusicBrailleContext(profile=profile, backend=ctx)
    mctx.warn(code="MUSIC_TEST", message="m")
    assert ctx.warnings.warnings[0].anchor is None


def test_warn_defaults_source_to_backend_music(profile, ctx):
    mctx = MusicBrailleContext(profile=profile, backend=ctx)
    mctx.warn(code="MUSIC_TEST", message="m")
    assert ctx.warnings.warnings[0].source == "backend.music"


def test_no_direct_collector_warns_left_in_handlers() -> None:
    """Every handler must warn through ``mctx.warn`` — a direct
    ``mctx.backend.warnings.warn`` silently drops the location anchor.
    Source-text scan (not grep: braille unicode makes grep skip files
    as binary)."""
    from pathlib import Path

    handlers_dir = (
        Path(__file__).resolve().parents[3]
        / "brailix"
        / "backend"
        / "music"
        / "handlers"
    )
    offenders = [
        py.name
        for py in handlers_dir.glob("*.py")
        if "backend.warnings.warn(" in py.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"handlers warn past mctx.warn (anchor lost): {offenders}"
    )
