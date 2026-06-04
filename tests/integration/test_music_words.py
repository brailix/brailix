"""Music ``<words>`` directions render via the text path (zh / latin).

Multi-word / non-ASCII expression text used to defer with a
MUSIC_UNSUPPORTED_NOTATION warning; the pipeline now injects a text
translator so it becomes real braille.
"""

from __future__ import annotations

from brailix import Pipeline
from brailix.ir.document import DocumentIR, ScoreBlock

_SCORE_WITH_WORDS = (
    "<score-partwise><part id='P1'><measure number='1'>"
    "<direction><direction-type><words>poco a poco</words>"
    "</direction-type></direction>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<type>quarter</type></note>"
    "</measure></part></score-partwise>"
)


def _translate(xml: str):
    pipe = Pipeline(profile="cn_current")
    doc = DocumentIR(blocks=[ScoreBlock(text=xml, source="musicxml")])
    return pipe.translate_document(doc)


def test_multi_word_direction_no_longer_deferred() -> None:
    res = _translate(_SCORE_WITH_WORDS)
    deferred = [
        w
        for w in res.warnings.warnings
        if w.code == "MUSIC_UNSUPPORTED_NOTATION"
        and "single-word ASCII" in w.message
    ]
    assert deferred == []  # "poco a poco" is translated, not deferred
    assert len(res.render("unicode")) > 0


def test_chinese_words_translated() -> None:
    xml = (
        "<score-partwise><part id='P1'><measure number='1'>"
        "<direction><direction-type><words>渐强</words></direction-type>"
        "</direction>"
        "<note><pitch><step>C</step><octave>4</octave></pitch>"
        "<type>quarter</type></note>"
        "</measure></part></score-partwise>"
    )
    res = _translate(xml)
    deferred = [
        w
        for w in res.warnings.warnings
        if "single-word ASCII" in w.message
    ]
    assert deferred == []
    assert len(res.render("unicode")) > 0


def test_translate_inline_text_renders_cells() -> None:
    pipe = Pipeline(profile="cn_current")
    assert len(pipe._translate_inline_text("poco")) > 0
    # Empty / whitespace-only → no cells (no recursion, no crash).
    assert pipe._translate_inline_text("") == []
    assert pipe._translate_inline_text("   ") == []
