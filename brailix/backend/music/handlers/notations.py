"""Per-note ornaments / connections (M3.5 + S4 + M6).

Includes:

* :func:`_emit_appoggiatura` — BANA Par. 16.2 / Table 16 (A).
* :func:`_emit_tuplet_marker` — Table 8 N-tuplet markers (pre-note).
* :func:`_emit_notations_post_note` — tie / slur / fingering / ornaments
  emitted *after* the note's main cells.
* :func:`_emit_ornaments` + :func:`_emit_tremolo` — BANA Tables 14, 16
  decoration handlers, dispatched from inside
  :func:`_emit_notations_post_note`.

None of these are registered with the main dispatch table — they're
all called from within :func:`brailix.backend.music.handlers.notes._emit_note`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import (
    emit_cells_for_entity,
    emit_synthesized_tuplet_marker,
    first_child_text,
)
from brailix.ir.braille import BrailleCell

# Hand-built N-tuplet → BANA Table 8 entity. Triplet (N=3) is selected
# inside _emit_tuplet_marker so the tuplet_form feature can flip between
# the single-cell (Par. 8.4) and three-cell (Par. 8.5) forms.
_TUPLET_NUMBER_ENTITY: dict[int, str] = {
    2:  "group_of_two_notes",
    10: "group_of_ten_notes",
}


_FINGERING_ENTITY: dict[str, str] = {
    "1": "first_finger",
    "2": "second_finger",
    "3": "third_finger",
    "4": "fourth_finger",
    "5": "fifth_finger",
}


# MusicXML ``<technical>`` leaf tag → BANA Table 24 string-instrument
# entity (M-instr1). ``<harmonic>`` is handled separately because its
# natural/artificial split lives in a child element, not the tag.
_STRING_TECHNIQUE_ENTITY: dict[str, str] = {
    "up-bow":         "up_bow",
    "down-bow":       "down_bow",
    "open-string":    "open_string",
    "thumb-position": "left_hand_thumb",
}


# MusicXML ``<ornaments>`` leaf tag → BANA Table 16 entity name.
# Note: MusicXML ``mordent`` is the "upper" mordent in BANA naming
# (the standard symbol); ``inverted-mordent`` is the lower one. Both
# the MusicXML name and the BANA term agree on visual direction —
# the naming flip lives only in the table key.
_ORNAMENT_ENTITY: dict[str, str] = {
    "trill-mark":       "trill",
    "turn":             "turn_between_notes",
    "inverted-turn":    "inverted_turn_between_notes",
    "mordent":          "upper_mordent",
    "inverted-mordent": "lower_mordent",
    "glissando":        "glissando_line_between_notes",
}

# MusicXML ``<tremolo>`` stroke count → BANA Table 14 entity.
# stroke 1 = 8ths, 2 = 16ths, ..., 5 = 128ths.
_TREMOLO_REPETITION_ENTITY: dict[int, str] = {
    1: "repetition_8ths",
    2: "repetition_16ths",
    3: "repetition_32nds",
    4: "repetition_64ths",
    5: "repetition_128ths",
}

_TREMOLO_ALTERNATION_ENTITY: dict[int, str] = {
    1: "alternation_8ths",
    2: "alternation_16ths",
    3: "alternation_32nds",
    4: "alternation_64ths",
    5: "alternation_128ths",
}


def _emit_appoggiatura(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """BANA Par. 16.2 / Table 16 (A): emit the appoggiatura prefix
    when the note carries a ``<grace>`` child.

    * ``<grace/>``               → ``long_appoggiatura`` (``"5``)
    * ``<grace slash="yes"/>``   → ``short_appoggiatura`` (``5``)

    Gated by ``music.show_ornaments`` (consistent with all other
    Table 16 markers). MusicXML also marks the grace note's pitch /
    duration on the same ``<note>`` element, which the rest of
    ``_emit_note`` handles normally — the appoggiatura cell sits in
    front of all other per-note modifiers per Par. 16.2.
    """
    grace = elem.find("grace")
    if grace is None:
        return
    if not mctx.profile.feature("music.show_ornaments", True):
        return
    slash = grace.attrib.get("slash", "no").strip().lower() == "yes"
    entity = "short_appoggiatura" if slash else "long_appoggiatura"
    emit_cells_for_entity(
        cells, mctx,
        topic="ornaments", entity=entity,
        role="music_appoggiatura",
        source_text="appoggiatura:short" if slash else "appoggiatura:long",
    )


def _emit_tuplet_marker(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """BANA Table 8: emit the irregular-grouping marker before the
    *first* note of an N-tuplet.

    Recognises the start of a tuplet by ``<notations>/<tuplet
    type="start">`` and reads N from
    ``<time-modification>/<actual-notes>``. Subsequent notes inside
    the tuplet (or its ``type="stop"`` marker) don't re-emit the
    marker — BANA spec puts it once at the head.

    Triplet (N=3) honours the ``music.tuplet_form`` feature:

    * ``"single_cell"`` (default, Par. 8.4) → ``2`` (cell c_23)
    * ``"three_cell"`` (Par. 8.5)            → ``_3'``

    N=2 / N=10 use the pre-built BANA exemplars; any other N is
    synthesized as the general ``_<digits>'`` form (BANA Par. 8.5) by
    :func:`emit_synthesized_tuplet_marker` — not warned.
    """
    notations = elem.find("notations")
    if notations is None:
        return
    tuplet = notations.find("tuplet")
    if tuplet is None:
        return
    if tuplet.attrib.get("type", "").strip().lower() != "start":
        return

    time_mod = elem.find("time-modification")
    if time_mod is None:
        return
    actual_raw = first_child_text(time_mod, "actual-notes")
    if actual_raw is None:
        return
    try:
        n = int(actual_raw)
    except ValueError:
        return

    form = mctx.profile.feature("music.tuplet_form", "single_cell")

    if n == 3:
        if form == "single_cell":
            entity = "triplet_single_cell"
        elif form == "three_cell":
            entity = "triplet_three_cell"
        else:
            mctx.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"music.tuplet_form={form!r} not implemented "
                    f"(M3.5 covers 'single_cell' / 'three_cell'); "
                    f"falling back to single_cell"
                ),
                source="backend.music",
            )
            entity = "triplet_single_cell"
    elif n in _TUPLET_NUMBER_ENTITY:
        entity = _TUPLET_NUMBER_ENTITY[n]
    else:
        # S1: synthesize ``_<digits>'`` per BANA Par. 8.5 — covers
        # 4/5/6/7/8/9/11/12... any non-pre-built N-tuplet.
        emit_synthesized_tuplet_marker(cells, mctx, n)
        return

    emit_cells_for_entity(
        cells, mctx,
        topic="grouping", entity=entity,
        role="music_tuplet",
        source_text=f"tuplet:{n}",
    )


def _emit_notations_post_note(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
    *,
    in_chord: bool = False,
) -> None:
    """Emit the per-note connection / annotation markers that follow
    the note (and any dots): tie (Table 10), slur (Table 13),
    fingering (Table 15).

    Only ``type="start"`` markers emit cells — tie / slur ``stop``
    are implicit from the next note's pairing in BANA. Multiple
    markers on the same note are emitted in tie → slur → fingering
    order so the cell stream reflects the conventional reading order.

    ``in_chord=True`` (the written note of a chord run) suppresses the
    single-note tie: a tied chord takes ONE chord-tie sign
    (``tie_between_chords``, Table 10 / Par. 10.2), emitted by
    ``notes._emit_chord_run`` after its interval cells — any member may
    carry the source ``<tied>``, not just whichever note the clef
    reorder made the written one.  Slur / ornament / fingering markers
    still belong to the written note.
    """
    notations = elem.find("notations")
    if notations is None:
        return

    # Tie (Table 10) — only the start side prints a cell.
    if not in_chord:
        for tied in notations.findall("tied"):
            if tied.attrib.get("type", "").strip().lower() == "start":
                emit_cells_for_entity(
                    cells, mctx,
                    topic="tie", entity="tie_between_single_notes",
                    role="music_tie",
                    source_text="tie",
                )
                break

    # Slur (Table 13) — M3.5 emits ``simple_short_slur`` at start;
    # long-slur / convergent variants land in a later milestone.
    for slur in notations.findall("slur"):
        if slur.attrib.get("type", "").strip().lower() == "start":
            emit_cells_for_entity(
                cells, mctx,
                topic="slur", entity="simple_short_slur",
                role="music_slur",
                source_text="slur",
            )
            break

    # M6: ornaments + tremolo (Tables 14 / 16) — single dispatcher per
    # <ornaments> container; gated by show_ornaments at the leaf.
    ornaments = notations.find("ornaments")
    if ornaments is not None:
        _emit_ornaments(cells, mctx, ornaments)

    # <technical>: fingering (Table 15) + string techniques (Table 24).
    technical = notations.find("technical")
    if technical is not None:
        _emit_technical_signs(cells, mctx, technical)


def _emit_technical_signs(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    technical: ET.Element,
) -> None:
    """``<technical>`` children: fingering (Table 15, M3.5) + string
    techniques (Table 24, M-instr1).

    Fingering keeps its own ``show_fingering`` gate; the Table 24
    string signs (bowing / harmonics / open string / thumb position)
    share ``show_string_techniques``. ``<harmonic>`` picks natural vs
    artificial from its child element, not the tag. Arco / left-hand
    pizzicato are in the resource table but ride on ``<words>``
    directions (no clean ``<technical>`` element), so not wired here.
    """
    if mctx.profile.feature("music.show_fingering", True):
        for fingering in technical.findall("fingering"):
            text = (fingering.text or "").strip()
            entity = _FINGERING_ENTITY.get(text)
            if entity is None:
                mctx.warn(
                    code="MUSIC_UNSUPPORTED_NOTATION",
                    message=(
                        f"<fingering>{text!r}</fingering> not in M3.5 "
                        f"set (1-5); skipped"
                    ),
                    source="backend.music",
                )
                continue
            emit_cells_for_entity(
                cells, mctx,
                topic="fingerings", entity=entity,
                role="music_fingering",
                source_text=f"finger:{text}",
            )

    if not mctx.profile.feature("music.show_string_techniques", True):
        return
    for child in technical:
        tag = child.tag
        if tag == "harmonic":
            entity = (
                "artificial_harmonic"
                if child.find("artificial") is not None
                else "natural_harmonic"
            )
        else:
            entity = _STRING_TECHNIQUE_ENTITY.get(tag)
        if entity is None:
            continue  # fingering handled above; other tags not in M-instr1
        emit_cells_for_entity(
            cells, mctx,
            topic="instruments.strings", entity=entity,
            role="music_string_technique",
            source_text=f"technical:{tag}",
        )


# --- Ornaments / tremolo (M6: BANA Tables 14, 16) --------------------------


def _emit_ornaments(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    ornaments_elem: ET.Element,
) -> None:
    """Walk ``<ornaments>`` children and emit each decoration.

    Gated by ``music.show_ornaments`` (one switch covers tremolo +
    ornaments + glissando — they share the same "decorative" status
    and most use cases want them all on or all off together).

    ``<accidental-mark>`` (a trill / turn modifier indicating the
    upper note's accidental) is silently skipped — handling it
    requires merging with the prior ornament, which lands in a later
    milestone. Unknown ornament tags warn.
    """
    if not mctx.profile.feature("music.show_ornaments", True):
        return
    for child in ornaments_elem:
        tag = child.tag
        if tag == "tremolo":
            _emit_tremolo(cells, mctx, child)
            continue
        if tag == "accidental-mark":
            # M6 doesn't merge accidental-mark into the preceding
            # ornament — silently skip rather than spam warnings on
            # every trill+accidental combo in a real score.
            continue
        entity = _ORNAMENT_ENTITY.get(tag)
        if entity is None:
            mctx.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"<ornaments><{tag}/></ornaments> not in M6 ornament "
                    f"set (trill/turn/mordent/glissando + inverted forms)"
                ),
                source="backend.music",
            )
            continue
        emit_cells_for_entity(
            cells, mctx,
            topic="ornaments", entity=entity,
            role="music_ornament",
            source_text=tag,
        )


def _emit_tremolo(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """BANA Table 14: tremolo / chord repetition / alternation.

    ``<tremolo type="single">N</tremolo>`` → note/chord repetition;
    N strokes ↔ repetition family (1 = 8ths, ..., 5 = 128ths).

    ``<tremolo type="start">N</tremolo>`` → alternation between
    this note and the next (the matching ``type="stop"`` carries
    no cell — BANA shows the alternation marker once on the first
    note of the pair).

    ``type="unmeasured"`` (non-rhythmic shimmer) is not supported
    in M6 — warns and skips.
    """
    tremolo_type = elem.attrib.get("type", "single").strip().lower()
    raw = (elem.text or "").strip()
    try:
        strokes = int(raw) if raw else 1
    except ValueError:
        strokes = 1

    if tremolo_type == "single":
        entity_map = _TREMOLO_REPETITION_ENTITY
    elif tremolo_type == "start":
        entity_map = _TREMOLO_ALTERNATION_ENTITY
    elif tremolo_type == "stop":
        return  # alternation stop side — no cell
    elif tremolo_type == "unmeasured":
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message="<tremolo type='unmeasured'/> not supported in M6",
            source="backend.music",
        )
        return
    else:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"unknown tremolo type {tremolo_type!r}",
            source="backend.music",
        )
        return

    entity = entity_map.get(strokes)
    if entity is None:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"tremolo with {strokes} strokes not supported "
                f"(M6 covers 1–5)"
            ),
            source="backend.music",
        )
        return
    emit_cells_for_entity(
        cells, mctx,
        topic="tremolo", entity=entity,
        role="music_tremolo",
        source_text=f"tremolo:{tremolo_type}:{strokes}",
    )
