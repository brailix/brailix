"""Container handlers: ``<score-partwise>`` / ``<part>`` / ``<measure>``.

These don't emit cells themselves — they walk children through the
dispatch table, managing per-level state (octave reset between parts,
``measure_accidentals`` reset at every bar line, multi-voice fan-out
for BANA Par. 11.1 in-accord).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.handlers.notes import _emit_chord_run
from brailix.backend.music.utils import emit_cells_for_entity, first_child_text
from brailix.ir.braille import BrailleCell


def _emit_note_sequence(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    children: list[ET.Element],
) -> None:
    """Dispatch a measure / voice child sequence, batching chord runs.

    A chord is a ``<note>`` without ``<chord/>`` immediately followed by
    one or more ``<note><chord/></note>`` siblings. Those are handed to
    :func:`_emit_chord_run` as a group so it can pick the written note by
    clef (BANA Par. 9.2); every other child (single notes, rests,
    barlines, directions, attributes, ...) dispatches individually,
    unchanged.
    """
    i = 0
    n = len(children)
    while i < n:
        child = children[i]
        if (
            child.tag == "note"
            and child.find("rest") is None
            and child.find("chord") is None
        ):
            j = i + 1
            while (
                j < n
                and children[j].tag == "note"
                and children[j].find("chord") is not None
            ):
                j += 1
            if j - i > 1:  # ≥1 chord note followed the root
                _emit_chord_run(cells, mctx, children[i:j])
                i = j
                continue
        _emit_element(cells, mctx, child)
        i += 1


def _emit_score_partwise(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Walk every ``<part>`` in declaration order.

    Consecutive parts are separated by a zero-width ``music_part_sep``
    boundary cell — the part-level sibling of ``music_measure_sep``.
    Bar-over-bar layout (BANA §28) splits on it to stack parts into
    measure-aligned parallels; single-line layout treats it as a
    break point.  Emitted only *between* parts (not before the first),
    so single-part scores are unaffected.
    """
    part_seen = False
    for child in elem:
        if child.tag == "part":
            if part_seen:
                cells.append(
                    BrailleCell(
                        dots=(),
                        role="music_part_sep",
                        source_span=mctx.span,
                    )
                )
            part_seen = True
        _emit_element(cells, mctx, child)


def _staff_of_note(note: ET.Element) -> str:
    """The note's ``<staff>`` text — default ``"1"`` when absent."""
    return first_child_text(note, "staff") or "1"


def _staff_of_direction(direction: ET.Element) -> str:
    """The direction's ``<staff>`` text — default ``"1"`` when absent, matching
    the unnumbered-clef convention in :func:`_attributes_for_staff`."""
    return first_child_text(direction, "staff") or "1"


def _part_staves(part: ET.Element) -> list[str]:
    """Distinct staff numbers used by the part's notes, sorted.

    Returns ``[]`` for a single-staff part (every note on the default
    staff) so single-staff scores keep the original, un-split path."""
    seen: set[str] = set()
    for measure in part:
        if measure.tag != "measure":
            continue
        for note in measure:
            if note.tag == "note":
                seen.add(_staff_of_note(note))
    return sorted(seen) if len(seen) > 1 else []


def _attributes_for_staff(attrs: ET.Element, staff: str) -> ET.Element:
    """Copy ``<attributes>`` keeping only ``staff``'s clef (matched by
    ``number``; an unnumbered clef belongs to staff 1) plus the shared
    context (divisions / key / time / …).  The ``<staves>`` hint is
    dropped from the per-staff view."""
    out = ET.Element(attrs.tag, attrs.attrib)
    for child in attrs:
        if child.tag == "clef":
            num = child.attrib.get("number")
            if num == staff or (num is None and staff == "1"):
                out.append(child)
        elif child.tag == "staves":
            continue
        else:
            out.append(child)
    return out


def _measure_for_staff(measure: ET.Element, staff: str) -> ET.Element:
    """A per-staff view of ``measure``: only that staff's notes, clef and
    directions, with the shared key / time / barline copied through so the
    staff's stream keeps full accidental + metre context.  A ``<direction>``
    (dynamics / wedge / words / pedal) is routed to its own staff — an
    unnumbered direction belongs to staff 1 — instead of copied to every
    staff (which sounded a one-hand dynamic on both hands and re-fired a
    single hairpin once per staff)."""
    out = ET.Element(measure.tag, measure.attrib)
    for child in measure:
        if child.tag == "note":
            if _staff_of_note(child) == staff:
                out.append(child)
        elif child.tag == "attributes":
            out.append(_attributes_for_staff(child, staff))
        elif child.tag == "direction":
            if _staff_of_direction(child) == staff:
                out.append(child)
        else:
            out.append(child)
    return out


def _emit_measures(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    children: list[ET.Element],
    separator: str,
) -> None:
    """Walk a measure sequence, spacing consecutive measures with a
    ``music_measure_sep`` (gated by ``music.measure_separator``)."""
    measure_seen = False
    for child in children:
        if child.tag == "measure":
            if measure_seen and separator == "space":
                cells.append(
                    BrailleCell(
                        dots=(),
                        role="music_measure_sep",
                        source_span=mctx.span,
                    )
                )
            measure_seen = True
        _emit_element(cells, mctx, child)


def _reset_part_reading_state(mctx: MusicBrailleContext) -> None:
    """Reset the reading state that must restart at a part / staff boundary.

    A part (BANA Par. 3.2.1) — and each staff stream of a multi-staff
    part — starts a fresh octave context and a fresh value-sign baseline
    (Par. 2.4), and never carries a hairpin across the boundary. The clef
    (Par. 9.2) is reset too so a part / staff that declares no clef of its
    own falls back to the default written direction instead of inheriting
    the previous one and reading its chords upside down. Voice boundaries
    *within* a measure reset only a subset (octave + value baseline — the
    clef and accidentals hold across voices), so they don't call this.
    """
    mctx.prev_pitch = None
    mctx.prev_value_category = None
    mctx.pending_hairpin = None
    mctx.current_clef_sign = None
    mctx.current_clef_line = None


def _emit_part(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Walk a part's measures.

    A single-staff part emits straight through, each part starting a fresh
    octave context, value-sign baseline, and clef (BANA Par. 3.2.1 / 9.2 —
    see :func:`_reset_part_reading_state`).  A multi-staff part (e.g. a
    piano with ``<staff>1`` right hand / ``<staff>2`` left hand) is split
    into one stream per staff, separated by ``music_part_sep`` so
    bar-over-bar layout aligns the hands as parallel parts (§29).  Each
    staff stream restarts octave inference and carries the shared key /
    time (accidental context stays correct); clefs split by ``number``;
    in-accord (multi-voice) runs within each staff.

    Consecutive measures are spaced by a ``music_measure_sep``
    (``music.measure_separator``, default ``"space"``).  M8 provenance:
    ``current_part_id`` is recorded so child cells carry it, restored on
    exit so siblings see the original value.
    """
    saved = mctx.current_part_id
    mctx.current_part_id = elem.attrib.get("id") or saved
    separator = mctx.profile.feature("music.measure_separator", "space")
    try:
        staves = _part_staves(elem)
        if not staves:
            _reset_part_reading_state(mctx)
            _emit_measures(cells, mctx, list(elem), separator)
            return
        for i, staff in enumerate(staves):
            if i > 0:
                cells.append(
                    BrailleCell(
                        dots=(),
                        role="music_part_sep",
                        source_span=mctx.span,
                    )
                )
            # Each staff stream restarts octave / value / clef (see
            # :func:`_reset_part_reading_state`): a staff that declares no
            # clef of its own (e.g. an unnumbered <clef> that MusicXML
            # assigns only to staff 1) must fall back to the default
            # written direction, not inherit the previous staff's clef.
            _reset_part_reading_state(mctx)
            children = [
                _measure_for_staff(c, staff) if c.tag == "measure" else c
                for c in elem
            ]
            _emit_measures(cells, mctx, children, separator)
    finally:
        mctx.current_part_id = saved


def _emit_measure(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Walk a measure's children.

    Resets per-measure state:
    * ``octave_rule="every_measure"`` resets ``prev_pitch`` so every
      measure's first note re-marks octave (default ``interval16``
      carries pitch state across bar lines).
    * **Always** resets ``measure_accidentals`` so the BANA Par. 6.2
      "accidental holds within the measure" rule starts fresh at
      every bar line.

    M4 (BANA Par. 11.1): if the measure carries notes from more than
    one ``<voice>``, route through :func:`_emit_multi_voice` which
    emits each voice's notes grouped, separated by the
    full-measure in-accord marker (``<>``). Single-voice measures
    keep the original sequential walk so per-element ordering
    (notes / barlines / directions) is unchanged.
    """
    if mctx.octave_rule == "every_measure":
        mctx.prev_pitch = None
    mctx.measure_accidentals = set()
    # Reset the chord root at every bar line: a chord never spans a
    # measure, so a measure that opens with an orphan <note><chord/>
    # (malformed) must fall into the None-guard warning rather than
    # silently measuring its interval against the previous measure's stale
    # root.  Normal music overwrites this with the measure's first real
    # note anyway, so this only changes the malformed case.
    mctx.chord_root = None
    # M8 provenance: record current measure number so child cells'
    # source_text carries it. Restored on exit.
    saved_measure = mctx.current_measure_number
    mctx.current_measure_number = (
        elem.attrib.get("number") or saved_measure
    )
    try:
        voices = _scan_voices(elem)
        if len(voices) <= 1:
            _emit_note_sequence(cells, mctx, list(elem))
        else:
            _emit_multi_voice(cells, mctx, elem, voices)
    finally:
        mctx.current_measure_number = saved_measure


def _voice_of(note: ET.Element) -> str:
    """The note's ``<voice>`` value, defaulting an unvoiced note to ``"1"``
    (MusicXML's implicit voice).  Shared by :func:`_scan_voices` and
    :func:`_emit_multi_voice` so the two can't drift on the default — a
    mismatch routes an unvoiced note to a bucket nobody counted, leaving an
    empty voice and a stray in-accord marker.
    """
    return first_child_text(note, "voice") or "1"


def _scan_voices(measure: ET.Element) -> list[str]:
    """Return the distinct ``<voice>`` strings present on ``<note>``
    children, preserved in first-encountered order.

    Notes without a ``<voice>`` child still count as one implicit
    voice — most simple scores omit ``<voice>`` and rely on the
    default 1. If every note lacks a voice (or all share the same
    voice), the result has length ≤ 1 and the caller stays on the
    single-voice fast path.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for child in measure:
        if child.tag != "note":
            continue
        v = _voice_of(child)
        if v not in seen_set:
            seen.append(v)
            seen_set.add(v)
    return seen


def _emit_multi_voice(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    measure: ET.Element,
    voices: list[str],
) -> None:
    """BANA Par. 11.1: full-measure in-accord emission.

    Layout:

    1. **Pre-globals** — children that appear *before* the first
       ``<note>`` / ``<backup>`` / ``<forward>``. These are typically
       ``<attributes>``, ``<barline location="left">``, opening
       ``<direction>``, ``<print>`` — they belong at the head of the
       measure regardless of voice.
    2. **Voice notes** — grouped by ``<voice>`` tag in source order;
       ``<backup>`` and ``<forward>`` (cursor controls) are skipped
       in M4 since timing reconstruction isn't required for full-
       measure in-accord. Between voices, insert
       ``general.in_accord.full_measure_in_accord`` (``<>``). At each
       voice boundary, ``prev_pitch`` resets so the new voice's first
       note re-marks octave (BANA Par. 3.2.1); ``measure_accidentals``
       stays shared so Par. 6.2 reads across voices.
    3. **Post-globals** — children that appear *after* the last
       ``<note>`` / ``<backup>`` / ``<forward>``. Typically
       ``<barline location="right">`` (the closing bar) and any
       trailing direction. These must land after all voice content
       so the final bar / dynamic marks the right place.

    The ``music.in_accord_marker`` feature gates the marker emission;
    when off, voices simply concatenate (lossy — proofread debug
    only, not BANA-valid). The ``music.in_accord_form`` feature is
    reserved for the part-measure variant (Par. 11.2); anything
    other than ``"full_measure"`` warns and falls back here.
    """
    form = mctx.profile.feature("music.in_accord_form", "full_measure")
    if form != "full_measure":
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"music.in_accord_form={form!r} not implemented "
                f"(M4 covers 'full_measure' only); falling back"
            ),
            source="backend.music",
        )

    children = list(measure)
    cursor_tags = {"note", "backup", "forward"}
    cursor_indices = [
        i for i, c in enumerate(children) if c.tag in cursor_tags
    ]
    # Defensive: no cursor elements means there are no voices to
    # group, but we only reach this branch when ``_scan_voices``
    # returned ≥2 voices — so we trust that and split anyway.
    first_cursor_idx = cursor_indices[0] if cursor_indices else len(children)
    last_cursor_idx = cursor_indices[-1] if cursor_indices else -1

    voice_notes: dict[str, list[ET.Element]] = {v: [] for v in voices}
    pre_globals: list[ET.Element] = []
    post_globals: list[ET.Element] = []
    current_voice: str | None = None
    # Inter-note cursor-zone elements (a <direction> etc.) are BUFFERED, not
    # appended immediately, so one landing between a chord root and its
    # <chord/> members can't split the chord (the run batcher in
    # _emit_note_sequence only groups a member that *immediately* follows the
    # root). They flush to the voice they followed when the next non-chord note
    # arrives — i.e. after the chord is complete — or at measure end.
    pending_inserts: list[ET.Element] = []
    for i, child in enumerate(children):
        if child.tag == "note":
            v = _voice_of(child)
            if (
                child.find("chord") is None
                and pending_inserts
                and current_voice is not None
            ):
                # A fresh (non-chord) note starts: the previous chord is done,
                # so flush the buffered inserts after it, before this note.
                voice_notes[current_voice].extend(pending_inserts)
                pending_inserts = []
            voice_notes.setdefault(v, []).append(child)
            current_voice = v
        elif child.tag in ("backup", "forward"):
            continue
        elif i < first_cursor_idx:
            pre_globals.append(child)
        elif i > last_cursor_idx:
            post_globals.append(child)
        else:
            # A non-note element between the first and last cursor — a
            # <direction> (dynamic / wedge), a mid-measure <attributes>,
            # etc. Buffer it (see pending_inserts above) so it stays in the
            # current voice but after any in-progress chord, instead of
            # hoisting it to the end of the measure (which would move e.g. a
            # dynamic onto the wrong note, or apply a mid-measure clef change
            # too late). If the cursor zone opened on a <backup> (no note seen
            # yet), fall back to the measure head.
            if current_voice is not None:
                pending_inserts.append(child)
            else:
                pre_globals.append(child)
    # Trailing inserts (a <direction> after the measure's last note) attach to
    # the voice they followed. (pending_inserts is only filled when a note —
    # hence a voice — has been seen, so current_voice is set here.)
    if pending_inserts and current_voice is not None:
        voice_notes[current_voice].extend(pending_inserts)

    for el in pre_globals:
        _emit_element(cells, mctx, el)

    show_marker = mctx.profile.feature("music.in_accord_marker", True)
    for i, voice in enumerate(voices):
        if i > 0:
            if show_marker:
                emit_cells_for_entity(
                    cells, mctx,
                    topic="in_accord", entity="full_measure_in_accord",
                    role="music_in_accord",
                    source_text="in-accord",
                )
            # Reset prev_pitch so the new voice's first note re-marks
            # the octave (BANA Par. 3.2.1 treats each voice's start
            # like the start of a line). measure_accidentals stays
            # shared — Par. 6.2 reads across voices within the bar.
            mctx.prev_pitch = None
            mctx.prev_value_category = None  # BANA Par. 2.4: fresh reading
            # A fresh voice is a fresh reading: a stray <chord/> at its start
            # must not interval against the previous voice's chord root, and a
            # dangling crescendo must not pair with this voice's stop. Mirrors
            # the part-boundary reset (_reset_part_reading_state).
            mctx.chord_root = None
            mctx.pending_hairpin = None
        _emit_note_sequence(cells, mctx, voice_notes[voice])

    for el in post_globals:
        _emit_element(cells, mctx, el)


_DISPATCH_PARTIAL = {
    "score-partwise": _emit_score_partwise,
    # Score-timewise: not supported in M2.3 (would need a transform).
    # M3+ will add normaliser-side conversion.
    "part": _emit_part,
    "measure": _emit_measure,
}
