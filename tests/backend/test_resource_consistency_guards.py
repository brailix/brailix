"""Resource-consistency guards — the backend's last-resort warnings for an
internally inconsistent resource table.

These fire when a symbol resolves to cells but carries no role, or when the
dispatcher asks for a music entity the table doesn't have. The profile
validator forbids both for shipped profiles (math roles are required; the
shipped music tables are complete), so they are only reachable with a
hand-edited / corrupted resource JSON. The inconsistency is injected here (a
role stripped, a topic dropped — at the class level so the frozen profile
instance is untouched) so the warning, and for rests the fallback cell, has
behaviour coverage, not just an i18n string.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import _emit_tree as math_emit_tree
from brailix.backend.music import _emit_tree as music_emit_tree
from brailix.core.config import load_profile
from brailix.core.config.profile import BrailleProfile
from brailix.core.context import BackendContext


@pytest.fixture
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _codes(ctx: BackendContext) -> list[str]:
    return [w.code for w in ctx.warnings.warnings]


def _drop_music_topic(monkeypatch, topic: str) -> None:
    """Make ``music_cell`` miss for one topic, delegating every other.

    Patched on the class (the frozen profile instance is read-only) and
    auto-restored by monkeypatch.
    """
    original = BrailleProfile.music_cell
    monkeypatch.setattr(
        BrailleProfile,
        "music_cell",
        lambda self, t, entity: (
            None if t == topic else original(self, t, entity)
        ),
    )


def test_math_symbol_with_cells_but_no_role_warns(
    profile, ctx, monkeypatch
) -> None:
    # A symbol that resolves to cells but has no role (a corrupted symbols.json
    # the validator would reject) → MATH_SYMBOL_MISSING_ROLE, defaulting to op.
    assert profile.math_symbol("+") is not None  # has cells
    monkeypatch.setattr(BrailleProfile, "math_symbol_role", lambda self, ch: None)
    math_emit_tree(ET.fromstring("<math><mo>+</mo></math>"), ctx, profile)
    assert "MATH_SYMBOL_MISSING_ROLE" in _codes(ctx)


def test_unknown_octave_warns(profile, ctx, monkeypatch) -> None:
    _drop_music_topic(monkeypatch, "octaves")
    note = ET.fromstring(
        "<note><pitch><step>C</step><octave>4</octave></pitch>"
        "<duration>4</duration><type>quarter</type></note>"
    )
    music_emit_tree(note, ctx, profile)
    assert "MUSIC_UNKNOWN_OCTAVE" in _codes(ctx)


def test_unknown_rest_warns_and_emits_fallback(
    profile, ctx, monkeypatch
) -> None:
    _drop_music_topic(monkeypatch, "rests")
    rest = ET.fromstring(
        "<note><rest/><duration>4</duration><type>quarter</type></note>"
    )
    cells = music_emit_tree(rest, ctx, profile)
    assert "MUSIC_UNKNOWN_REST" in _codes(ctx)
    assert any(c.role == "music_unknown" for c in cells)


def test_unknown_time_signature_warns(profile, ctx, monkeypatch) -> None:
    # 4/4 maps to the named meter entity "four_four_time" (table lookup);
    # dropping the meter topic makes that lookup miss → MUSIC_UNKNOWN_TIME.
    _drop_music_topic(monkeypatch, "meter")
    attrs = ET.fromstring(
        "<attributes><time><beats>4</beats>"
        "<beat-type>4</beat-type></time></attributes>"
    )
    music_emit_tree(attrs, ctx, profile)
    assert "MUSIC_UNKNOWN_TIME" in _codes(ctx)
