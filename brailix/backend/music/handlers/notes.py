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

# BANA Par. 2.4: every printed note shape stands for a "large" value
# (8th-and-larger) and the "small" value 1/16 of it (16th-and-smaller).
# A 256th is a third tier with its own sign. A change between these
# categories needs a value sign so the reader isn't left guessing.
_VALUE_CATEGORY: dict[str, str] = {
    "breve": "large",
    "whole": "large",
    "half": "large",
    "quarter": "large",
    "eighth": "large",
    "16th": "small",
    "32nd": "small",
    "64th": "small",
    "128th": "small",
    "256th": "v256",
}
_VALUE_SIGN_ENTITY: dict[str, str] = {
    "large": "value_sign_8ths_and_larger",
    "small": "value_sign_16ths_and_smaller",
    "v256": "value_sign_256th_notes",
}


def _emit_value_sign(
    cells: list[BrailleCell], mctx: MusicBrailleContext, type_name: str
) -> None:
    """BANA Par. 2.4: emit a larger / smaller value sign before a note or
    rest when its value category changes from the previous one.

    Categories: ``large`` (8th-and-larger), ``small`` (16th-and-smaller),
    ``v256`` (256th — always signed, "any use of the 256th ... requires a
    value sign for each such passage"). The first note of a reading
    establishes the baseline silently unless it is a 256th; consecutive
    notes in the same category (incl. a 256th run = one passage) add no
    further sign. Gated by ``features.music.value_signs`` (default on).

    Notes and rests share one stream — :func:`_emit_rest` calls this too,
    so a half-note followed by a 32nd-rest is marked correctly.
    """
    # Advance the value-category baseline BEFORE the feature gate, so
    # prev_value_category always tracks the real previous note. The gate
    # controls only whether a sign is *emitted*; decoupling the two keeps
    # the next transition correct even if music.value_signs is toggled
    # mid-score, and mirrors the unknown-type branch below which likewise
    # advances the baseline before returning.
    category = _VALUE_CATEGORY.get(type_name)
    if category is None:
        # Unknown <type> — the note body warns + renders the quarter
        # fallback (a "large" value).  Set the baseline to "large" too,
        # else prev_value_category keeps its stale value and the NEXT
        # note's large/small transition is computed against the wrong
        # baseline (e.g. [16th, BOGUS, 16th] would drop the second sign).
        mctx.prev_value_category = "large"
        return
    prev = mctx.prev_value_category
    mctx.prev_value_category = category
    if not mctx.profile.feature("music.value_signs", True):
        return
    if category == prev:
        return  # no change (consecutive same category / 256th passage)
    if prev is None and category != "v256":
        return  # first note establishes the baseline silently
    emit_cells_for_entity(
        cells, mctx,
        topic="notes",
        entity=_VALUE_SIGN_ENTITY[category],
        role="music_value_sign",
        source_text=f"value:{category}",
    )


def _emit_note(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
    *,
    chord_role: str | None = None,
) -> None:
    """Handle a single ``<note>`` element.

    Two flavours:

    * ``<rest/>`` child → :func:`_emit_rest`
    * ``<pitch>`` child → emit (optional octave prefix) + note cell;
      update ``mctx.prev_pitch``.

    ``chord_role`` overrides the source ``<chord/>`` marker so
    :func:`_emit_chord_run` can pick the *written* note per the clef
    (BANA Par. 9.2): ``"root"`` forces the full-note path, ``"interval"``
    forces the interval path, ``None`` (the dispatch default) detects via
    ``<chord/>`` as before.

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
    if chord_role == "interval":
        is_chord_note = True
    elif chord_role == "root":
        is_chord_note = False
    else:
        is_chord_note = elem.find("chord") is not None

    if is_chord_note:
        # S6: BANA Par. 9.1 — chord notes are represented as
        # interval cells (2nd / 3rd / ... / octave) relative to the
        # chord root, not as full note cells. The root has already
        # been emitted normally on the prior <note>; the chord notes
        # only add interval markers + accidental (if changed).
        # Ties and lyrics carried by interval members are NOT dropped:
        # _emit_chord_run collects them at the chord level (chord tie
        # after the intervals, member lyrics after that) — the clef
        # reorder routinely turns the source note that carries the
        # <tied> / <lyric> into an interval member.
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
    # BANA Par. 2.4: the value sign (if the large/small category changed)
    # precedes the whole note, ahead of the grace / accidental / octave.
    _emit_value_sign(cells, mctx, type_name)
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
            mctx.warn(
                code="MUSIC_UNKNOWN_OCTAVE",
                message=f"no octave entry for octave {octave}",
                source="backend.music",
            )

    # Unknown <type> degrades to a quarter note via note_entity_name's
    # fallback; warn so the silent mistranslation surfaces (e.g. a
    # bogus or unsupported value). breve / 256th / etc. are all known.
    if not is_known_note_type(type_name):
        mctx.warn(
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
        mctx.warn(
            code="MUSIC_UNKNOWN_NOTE",
            message=f"no note entry for {note_entity}",
            source="backend.music",
        )
        cells.append(unknown_cell(mctx, role="music_unknown", source_text=step))

    _emit_dots(cells, mctx, elem)
    # Inside a chord run the written note must not emit the single-note
    # tie — the run emits ONE chord-tie sign for the whole chord.
    _emit_notations_post_note(
        cells, mctx, elem, in_chord=(chord_role == "root")
    )
    _emit_lyrics(cells, mctx, elem)

    # S6: remember the chord root so any immediately-following
    # ``<note><chord/></note>`` siblings can compute their interval.
    mctx.chord_root = curr_pitch
    mctx.prev_pitch = curr_pitch


def _chord_written_is_top(mctx: MusicBrailleContext) -> bool:
    """BANA Par. 9.2: is the *uppermost* chord note the written note?

    Treble (G) and alto (C clef, line 3) write the uppermost note with
    intervals read downward → ``True``. Bass (F) and tenor (C clef, line
    4) write the lowermost with intervals upward → ``False``. Only
    consulted when a clef is set (see :func:`_emit_chord_run`).
    """
    sign = mctx.current_clef_sign
    if sign == "G":
        return True
    if sign == "C":
        # Alto (line 3) groups with treble; tenor (line 4) with bass.
        # An unspecified C-clef line defaults to alto.
        return mctx.current_clef_line != 4
    return False  # F (bass) / tenor → lowermost written


def _emit_chord_run(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    run: list[ET.Element],
) -> None:
    """Emit a chord — a root ``<note>`` plus its following
    ``<note><chord/></note>`` siblings — in BANA Par. 9.2 order.

    The *written* note is the uppermost (treble / alto) or lowermost
    (bass / tenor) member; the rest become interval cells read from it.
    Interval cells are size-only (a 3rd is a 3rd whether read up or down —
    :func:`_emit_chord_interval`), so only the choice of written note and
    the reading order change with the clef. Members are sorted by pitch
    rather than trusting MusicXML order, which the spec does not
    guarantee.

    Without a clef (test fragments) or with an unreadable pitch, the
    source order is kept (first = written), exactly the pre-9.2 path — so
    clef-less input renders identically.

    Chord-level data travels with the chord, not with whichever member
    the reorder picked as written: a ``<tied>`` on ANY member emits one
    ``tie_between_chords`` sign after the intervals (Par. 10.2 — the
    per-note tie is suppressed inside the run), and the non-written
    members' ``<lyric>`` children are emitted after that (the syllable
    is usually authored on the first source note, which the reorder may
    have demoted to an interval).  Known limitation: slurs / fingering /
    ornaments on non-written members are still not *represented*, but a
    member that carries them now raises ``MUSIC_UNSUPPORTED_NOTATION`` so
    the loss is visible to the proofreader instead of silent.
    """
    measured: list[tuple[int, ET.Element]] = []
    usable = True
    for note in run:
        pitch = note.find("pitch")
        step = first_child_text(pitch, "step") if pitch is not None else None
        octave_raw = (
            first_child_text(pitch, "octave") if pitch is not None else None
        )
        pos: int | None = None
        if step is not None and octave_raw is not None:
            try:
                pos = diatonic_position(step.upper(), int(octave_raw))
            except ValueError:
                pos = None
        if pos is None:
            usable = False
            break
        measured.append((pos, note))

    if usable and mctx.current_clef_sign is not None:
        measured.sort(
            key=lambda pe: pe[0], reverse=_chord_written_is_top(mctx)
        )
        ordered = [note for _, note in measured]
    else:
        ordered = run

    _emit_note(cells, mctx, ordered[0], chord_role="root")
    for note in ordered[1:]:
        _emit_note(cells, mctx, note, chord_role="interval")

    # Chord tie (Table 10 / Par. 10.2): one sign for the whole chord,
    # after the intervals.  Any member may carry the source <tied> —
    # the conservative trigger is "any member starts a tie"; partial
    # chord ties (only some members tied) are a later refinement.
    if any(_has_tie_start(note) for note in ordered):
        emit_cells_for_entity(
            cells, mctx,
            topic="tie", entity="tie_between_chords",
            role="music_tie",
            source_text="tie(chord)",
        )

    # Lyrics attach to the chord as a whole.  The written note's own
    # <lyric> children were already emitted by its full single-note
    # path; collect the remaining members' here so a syllable authored
    # on a note the reorder demoted to an interval still lands.
    for note in ordered[1:]:
        _emit_lyrics(cells, mctx, note)

    # The interval path emits only a size cell, so any <notations> a
    # non-written member carries (slur / ornaments / articulations /
    # technical fingering, …) are dropped — tie and lyric were recovered
    # above, everything else is not. Warn rather than drop silently, so a
    # slur authored on a member the clef reorder demoted is at least flagged.
    dropped = sorted(
        {
            child.tag
            for note in ordered[1:]
            for notations in note.findall("notations")
            for child in notations
            if child.tag != "tied"
        }
    )
    if dropped:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                "chord member(s) reordered to an interval carry notations "
                f"not represented on intervals: {', '.join(dropped)}"
            ),
            source="backend.music",
        )


def _has_tie_start(elem: ET.Element) -> bool:
    """Whether this ``<note>`` starts a tie (``<notations><tied
    type="start">``) — mirrors the detection in
    :func:`~.notations._emit_notations_post_note`."""
    notations = elem.find("notations")
    if notations is None:
        return False
    return any(
        tied.attrib.get("type", "").strip().lower() == "start"
        for tied in notations.findall("tied")
    )


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
        mctx.warn(
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
        mctx.warn(
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
        mctx.warn(
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
        mctx.warn(
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
        mctx.warn(
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

    A full-measure rest (``<rest measure="yes"/>``) is written with a
    whole rest whatever the time signature (BANA Par. 5.1). Exporters
    emit it without a ``<type>``, so it must be special-cased — otherwise
    it defaulted to ``"quarter"`` and produced a quarter rest in every
    non-4/4 measure.

    Rests do **not** mutate ``mctx.prev_pitch`` — BANA Par. 3.2.2
    speaks of "consecutive notes", so a rest between two notes
    doesn't reset the octave reference.
    """
    rest_el = elem.find("rest")
    if rest_el is not None and rest_el.get("measure") == "yes":
        # measure="yes" lives on the <rest> child, not the <note>.
        type_name = "whole"
    else:
        type_name = first_child_text(elem, "type") or "quarter"
    # BANA Par. 2.4: a rest carries a value sign on a category change too
    # (rests share the note value shapes), before the rest cell.
    _emit_value_sign(cells, mctx, type_name)
    rest_entity = _rest_entity_name(type_name)
    if not emit_cells_for_entity(
        cells, mctx,
        topic="rests",
        entity=rest_entity,
        role="music_rest",
        source_text=f"rest:{type_name}",
    ):
        mctx.warn(
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
    # breve (double-whole) rest — BANA Table 5 form A (whole-rest cell +
    # breve suffix), parallel to the breve entry in _TYPE_TO_FAMILY. Without
    # it a breve rest fell through to the quarter-rest default and was
    # silently mistranslated.
    "breve":   "breve_rest_a",
}


def _rest_entity_name(type_name: str) -> str:
    return _REST_FAMILY.get(type_name, "quarter_or_64th_rest")


_DISPATCH_PARTIAL = {
    "note": _emit_note,
}
