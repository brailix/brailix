"""End-to-end music test: a COMPLETE multi-measure score driven through
the frontend normalizer + backend translator.

Every other test in this package feeds ``emit_tree`` a synthetic
single-element fragment, so none of them exercise a real ``<score-
partwise>`` flowing through ``normalize`` (namespace strip, voice
numbering, note-type inference) into ``translate`` (octave inference
across measures, measure separators).  This pins that integration.
"""

from __future__ import annotations

from brailix.backend.music import translate
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.frontend.music.normalizer import normalize
from brailix.ir.inline import MusicInline

_SCORE = """<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>D</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
      <note><pitch><step>F</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
    </measure>
    <measure number="2">
      <note><pitch><step>G</step><octave>4</octave></pitch>
        <duration>4</duration><type>whole</type></note>
    </measure>
  </part>
</score-partwise>"""


def test_complete_two_measure_score_end_to_end():
    profile = load_profile("cn_current")
    ctx = BackendContext(profile="cn_current", block_type="score")
    tree = normalize(_SCORE)
    node = MusicInline(surface="", source="musicxml", score=tree)
    cells = translate(node, ctx, profile)

    roles = [c.role for c in cells]
    # Real braille came out — NOT the MUSIC_NO_IR raw-surface fallback.
    assert cells
    assert not any(w.code == "MUSIC_NO_IR" for w in ctx.warnings.warnings)
    # All five notes (C D E F in measure 1, G whole in measure 2) land.
    assert roles.count("music_note") == 5
    # Every emitted cell carries dots (no blank/garbage cells).
    assert all(c.dots is not None for c in cells)


def test_namespaced_root_is_stripped_and_translates():
    # Some exporters wrap the score in a default XML namespace; normalize
    # must strip it so the backend's tag dispatch (which matches bare local
    # names) still fires instead of falling through to MUSIC_NO_IR.
    ns_score = _SCORE.replace(
        '<score-partwise version="3.1">',
        '<score-partwise version="3.1" xmlns="http://www.w3.org/2021/musicxml">',
    )
    profile = load_profile("cn_current")
    ctx = BackendContext(profile="cn_current", block_type="score")
    node = MusicInline(surface="", source="musicxml", score=normalize(ns_score))
    cells = translate(node, ctx, profile)
    assert cells
    assert not any(w.code == "MUSIC_NO_IR" for w in ctx.warnings.warnings)
    assert [c.role for c in cells].count("music_note") == 5


_MULTIVOICE = """<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions></attributes>
      <note><pitch><step>C</step><octave>5</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type></note>
      <note><pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration><voice>2</voice><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>"""


def test_multi_voice_measure_emits_both_voices():
    # Two voices in one measure must both reach braille (in-accord), not get
    # merged or dropped — guards the voice-grouping container path.
    profile = load_profile("cn_current")
    ctx = BackendContext(profile="cn_current", block_type="score")
    node = MusicInline(
        surface="", source="musicxml", score=normalize(_MULTIVOICE)
    )
    cells = translate(node, ctx, profile)
    assert cells
    assert not any(w.code == "MUSIC_NO_IR" for w in ctx.warnings.warnings)
    assert [c.role for c in cells].count("music_note") == 2
