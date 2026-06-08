"""Note + rest emission (BANA Pars. 2-3 + 6 + 9).

Owns ``<note>`` (the sole dispatch entry) plus the helpers it calls:

* :func:`_emit_rest` (note with ``<rest/>``) — Table 5.
* :func:`_emit_chord_interval` — Table 9 interval cells for chord notes.
* :func:`_emit_note_accidental` — Pars. 6.1 / 6.2.
* :func:`_emit_dots` — Pars. 2.3 / 5.4 (shared with rests).

Per-note ornaments / connections (appoggiatura, tuplet marker, tie,
slur, fingering, ornaments, tremolo, lyrics) live in sibling submodules
and are pulled in by ``_emit_note`` in BANA emit order.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.handlers._common import serialise_short, warn_and_fallback
from brailix.backend.music.handlers.lyrics import _emit_lyrics
from brailix.backend.music.handlers.notations import (
    _emit_appoggiatura,
    _emit_notations_post_note,
    _emit_tuplet_marker,
)
from brailix.backend.music.utils import (
    accidental_entity_name,
    diatonic_position,
    emit_cells_for_entity,
    first_child_text,
    is_known_note_type,
    needs_octave_mark,
    note_entity_name,
    octave_entity_name,
    unknown_cell,
)
from brailix.ir.braille import BrailleCell


def _emit_note(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Handle a single ``<note>`` element.

    Two flavours:

    * ``<rest/>`` child → :func:`_emit_rest`
    * ``<pitch>`` child → emit (optional octave prefix) + note cell;
      update ``mctx.prev_pitch``.

    Missing or malformed pitch / duration falls through to an unknown
    cell plus a warning so the rest of the score still renders.
    """
    if elem.find("rest") is not None:
        _emit_rest(cells, mctx, elem)
        return

    pitch = elem.find("pitch")
    if pitch is None:
        warn_and_fallback(
            mctx, cells,
            code="MUSIC_UNSUPPORTED_NOTATION",
            message="<note> has neither <pitch> nor <rest>",
            source_text=serialise_short(elem),
        )
        return

    step = first_child_text(pitch, "step")
    octave_raw = first_child_text(pitch, "octave")
    type_name = first_child_text(elem, "type") or "quarter"

    if step is None or octave_raw is None:
        warn_and_fallback(
            mctx, cells,
            code="MUSIC_UNSUPPORTED_NOTATION",
            message="<pitch> missing <step> or <octave>",
            source_text=serialise_short(elem),
        )
        return
    try:
        octave = int(octave_raw)
    except ValueError:
        warn_and_fallback(
            mctx, cells,
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"<octave> not an integer: {octave_raw!r}",
            source_text=serialise_short(elem),
        )
        return

    curr_pitch = (step.upper(), octave)
    is_chord_note = elem.find("chord") is not None

    if is_chord_note:
        # S6: BANA Par. 9.1 — chord notes are represented as
        # interval cells (2nd / 3rd / ... / octave) relative to the
        # chord root, not as full note cells. The root has already
        # been emitted normally on the prior <note>; the chord notes
        # only add interval markers + accidental (if changed).
        _emit_note_accidental(cells, mctx, elem, curr_pitch)
        _emit_chord_interval(cells, mctx, mctx.chord_root, curr_pitch, type_name)
        # Don't reset prev_pitch (chord notes inherit root's octave
        # context for melodic inference). chord_root stays so any
        # following chord notes still measure from the same root.
        return

    # Order per BANA Par. 6.1 + 3.2 + Tables 8 / 10 / 13 / 15 / 16(A):
    #   [appoggiatura]? [tuplet marker]? [accidental]? [octave]?
    #   <note> [dot]* [tie] [slur] [finger]
    # "No other sign may come between octave and note" (Par. 3.2)
    # forces the accidental and tuplet marker before the octave when
    # all are present. The appoggiatura sits at the very front per
    # BANA Par. 16.2 — it's a grace note marker, applied before any
    # of the following note's modifiers.
    _emit_appoggiatura(cells, mctx, elem)
    _emit_tuplet_marker(cells, mctx, elem)
    _emit_note_accidental(cells, mctx, elem, curr_pitch)

    if (
        mctx.octave_rule == "always"
        or needs_octave_mark(mctx.prev_pitch, curr_pitch)
    ):
        octave_entity = octave_entity_name(octave)
        if not emit_cells_for_entity(
            cells, mctx,
            topic="octaves",
            entity=octave_entity,
            role="music_octave",
            source_text=octave_entity,
        ):
            mctx.backend.warnings.warn(
                code="MUSIC_UNKNOWN_OCTAVE",
                message=f"no octave entry for octave {octave}",
                source="backend.music",
            )

    # Unknown <type> degrades to a quarter note via note_entity_name's
    # fallback; warn so the silent mistranslation surfaces (e.g. a
    # bogus or unsupported value). breve / 256th / etc. are all known.
    if not is_known_note_type(type_name):
        mctx.backend.warnings.warn(
            code="MUSIC_DURATION_AMBIGUOUS",
            message=(
                f"unknown <type>{type_name!r}</type>; falling back to "
                f"quarter-note shape"
            ),
            source="backend.music",
        )

    note_entity = note_entity_name(step, type_name)
    if not emit_cells_for_entity(
        cells, mctx,
        topic="notes",
        entity=note_entity,
        role="music_note",
        source_text=f"{step}{octave}",
    ):
        mctx.backend.warnings.warn(
            code="MUSIC_UNKNOWN_NOTE",
            message=f"no note entry for {note_entity}",
            source="backend.music",
        )
        cells.append(unknown_cell(mctx, role="music_unknown", source_text=step))

    _emit_dots(cells, mctx, elem)
    _emit_notations_post_note(cells, mctx, elem)
    _emit_lyrics(cells, mctx, elem)

    # S6: remember the chord root so any immediately-following
    # ``<note><chord/></note>`` siblings can compute their interval.
    mctx.chord_root = curr_pitch
    mctx.prev_pitch = curr_pitch


_INTERVAL_ENTITY: dict[int, str] = {
    # Diatonic distance → BANA Table 9 entity. 0 (unison) isn't a
    # named entity — emitted as second by convention with a warning.
    1: "second",
    2: "third",
    3: "fourth",
    4: "fifth",
    5: "sixth",
    6: "seventh",
    7: "octave",
}


def _emit_chord_interval(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    root: tuple[str, int] | None,
    curr: tuple[str, int],
    type_name: str,
) -> None:
    """BANA Par. 9.1: emit interval cell(s) representing the chord
    note's distance from the chord root.

    Diatonic interval 1..7 maps directly to Table 9 entities; ≥8°
    compounds octave entities (one per octave) plus a remainder.
    Unison (0) warns — MusicXML chords don't normally contain
    duplicate pitches, and BANA has no named unison cell.

    Falls back to emitting an unknown cell if no chord root is set
    (caller error — a <chord/> note without a prior root note).
    """
    if root is None:
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                "<note><chord/></note> without a prior root note "
                "in the same measure; can't compute interval"
            ),
            source="backend.music",
        )
        cells.append(unknown_cell(mctx, role="music_unknown", source_text="chord-orphan"))
        return

    root_step, root_oct = root
    curr_step, curr_oct = curr
    root_pos = diatonic_position(root_step, root_oct)
    curr_pos = diatonic_position(curr_step, curr_oct)
    if root_pos is None or curr_pos is None:
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"unknown step name in chord interval: {root_step!r}/{curr_step!r}",
            source="backend.music",
        )
        return
    diatonic = abs(curr_pos - root_pos)

    source_label = f"chord:{root_step}{root_oct}->{curr_step}{curr_oct}"
    if diatonic == 0:
        # Doubled root — BANA has no named cell for unison; warn and
        # fall back to a second so something lands in the cell stream.
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message="chord interval of unison (doubled pitch) — emitting second cell as placeholder",
            source="backend.music",
        )
        emit_cells_for_entity(
            cells, mctx,
            topic="intervals", entity="second",
            role="music_interval",
            source_text=source_label,
        )
        return

    octaves, rest = divmod(diatonic, 7)
    # ``rest == 0`` means an exact octave / two-octave / ... chord —
    # all cells come from the octave entity.
    if rest == 0:
        for _ in range(octaves):
            emit_cells_for_entity(
                cells, mctx,
                topic="intervals", entity="octave",
                role="music_interval",
                source_text=source_label,
            )
        return

    # Compound interval (≥9°): one octave cell per full octave, then
    # the remainder. ≤7° just emits one entity directly.
    for _ in range(octaves):
        emit_cells_for_entity(
            cells, mctx,
            topic="intervals", entity="octave",
            role="music_interval",
            source_text=source_label,
        )
    entity = _INTERVAL_ENTITY[rest]
    emit_cells_for_entity(
        cells, mctx,
        topic="intervals", entity=entity,
        role="music_interval",
        source_text=source_label,
    )


def _emit_note_accidental(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
    curr_pitch: tuple[str, int],
) -> None:
    """Emit the accidental cells (if any) preceding a note.

    Reads MusicXML ``<accidental>`` text (BANA-relevant; ``<alter>``
    is for playback only). When ``music.accidental_persist_in_measure``
    is true (default, BANA Par. 6.2 behaviour) and the same
    ``(pitch, accidental)`` pair has already been printed earlier in
    this measure, this call is a no-op.

    Unrecognised accidental values warn ``MUSIC_UNSUPPORTED_NOTATION``
    rather than silently dropping — surfaces vendor-specific micro-
    tonal additions the cell table doesn't cover yet.
    """
    acc_elem = elem.find("accidental")
    if acc_elem is None or acc_elem.text is None:
        return
    raw = acc_elem.text.strip()
    if not raw:
        return
    entity = accidental_entity_name(raw)
    if entity is None:
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"unknown accidental {raw!r}",
            source="backend.music",
        )
        return

    if mctx.profile.feature("music.accidental_persist_in_measure", True):
        key = (curr_pitch, entity)
        if key in mctx.measure_accidentals:
            return
        mctx.measure_accidentals.add(key)

    emit_cells_for_entity(
        cells, mctx,
        topic="accidentals_key", entity=entity,
        role="music_accidental",
        source_text=raw,
    )


def _emit_dots(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """Emit one ``'`` (BANA Par. 2.3 / 5.4 dot-added-value, cell c_3)
    per ``<dot/>`` child element. MusicXML places dotted variants as
    repeated ``<dot/>`` siblings of ``<type>`` (one for a single
    dot, two for a double-dotted note). Both notes and rests share
    this cell.

    The ``music.dot_form`` feature is reserved for a future
    ``"combined"`` variant; M3.2 only implements ``"separate"`` (one
    cell per dot) so any other value falls back here with a warning.
    """
    dots = elem.findall("dot")
    if not dots:
        return
    form = mctx.profile.feature("music.dot_form", "separate")
    if form != "separate":
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"music.dot_form={form!r} not implemented (M3.2 covers "
                f"'separate' only); falling back"
            ),
            source="backend.music",
        )
    for _ in dots:
        emit_cells_for_entity(
            cells, mctx,
            topic="notes", entity="dot_added_value",
            role="music_dot",
            source_text="dot",
        )


def _emit_rest(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Emit the BANA rest cell for the note's ``<type>`` plus any
    dotted-value cells.

    Rests do **not** mutate ``mctx.prev_pitch`` — BANA Par. 3.2.2
    speaks of "consecutive notes", so a rest between two notes
    doesn't reset the octave reference.
    """
    type_name = first_child_text(elem, "type") or "quarter"
    rest_entity = _rest_entity_name(type_name)
    if not emit_cells_for_entity(
        cells, mctx,
        topic="rests",
        entity=rest_entity,
        role="music_rest",
        source_text=f"rest:{type_name}",
    ):
        mctx.backend.warnings.warn(
            code="MUSIC_UNKNOWN_REST",
            message=f"no rest entry for {rest_entity}",
            source="backend.music",
        )
        cells.append(unknown_cell(mctx, role="music_unknown", source_text="rest"))
        return
    _emit_dots(cells, mctx, elem)


_REST_FAMILY: dict[str, str] = {
    "whole":   "whole_or_16th_rest",
    "half":    "half_or_32nd_rest",
    "quarter": "quarter_or_64th_rest",
    "eighth":  "eighth_or_128th_rest",
    "16th":    "whole_or_16th_rest",
    "32nd":    "half_or_32nd_rest",
    "64th":    "quarter_or_64th_rest",
    "128th":   "eighth_or_128th_rest",
    "256th":   "rest_256th",
}


def _rest_entity_name(type_name: str) -> str:
    return _REST_FAMILY.get(type_name, "quarter_or_64th_rest")


_DISPATCH_PARTIAL = {
    "note": _emit_note,
}
