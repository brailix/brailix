"""Small pure helpers for the music backend.

Octave inference (BANA Par. 3.2.2), MusicXML type → BANA family
mapping, cell-sequence → BrailleCell list emission, and the
unknown-cell fallback all live here so handler files stay narrow.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence

from brailix.backend.music.context import MusicBrailleContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell

# ---------------------------------------------------------------------------
# Pitch / duration mapping
# ---------------------------------------------------------------------------
#
# MusicXML ``<type>`` values (whole / half / quarter / eighth / 16th /
# 32nd / 64th / 128th / 256th) collapse to four BANA families, since
# BANA reuses one cell shape for two durations (whole == 16th,
# half == 32nd, ...). 256th notes are the exception: they take a
# leading value-sign prefix (``;<1``, see Par. 2.4.1).

_TYPE_TO_FAMILY: dict[str, str] = {
    "whole":   "whole_or_16th",
    "half":    "half_or_32nd",
    "quarter": "quarter_or_64th",
    "eighth":  "eighth_or_128th",
    "16th":    "whole_or_16th",
    "32nd":    "half_or_32nd",
    "64th":    "quarter_or_64th",
    "128th":   "eighth_or_128th",
    "256th":   "note_256th",
    # breve (double whole note) — BANA Table 2 spells it as the
    # whole-note shape followed by the breve suffix cell (family
    # ``breve_a`` in notes.json). Without this entry the type fell
    # through to the quarter default and was silently mistranslated.
    "breve":   "breve_a",
}

# Diatonic position (within one octave) for each pitch step. Used by
# the octave-interval rule below.
_STEP_INDEX: dict[str, int] = {
    "C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6,
}

# MusicXML uses Helmholtz octave numbering: middle C = C4. BANA also
# numbers from "lowest C on piano" upward (Par. 3.1) — these align so
# the MusicXML octave integer maps 1:1 to BANA's "first/.../seventh"
# scheme. Octave 0 = sub octave (below first), octave 8 = super
# octave (above seventh); both use a doubled prefix per Table 3.
_OCTAVE_ENTITY: dict[int, str] = {
    0: "below_first_octave",
    1: "first_octave",
    2: "second_octave",
    3: "third_octave",
    4: "fourth_octave",
    5: "fifth_octave",
    6: "sixth_octave",
    7: "seventh_octave",
    8: "above_seventh_octave",
}


def is_known_note_type(type_name: str) -> bool:
    """True if ``type_name`` is a MusicXML ``<type>`` value the backend
    maps to a real BANA notes family. Callers use this to warn
    ``MUSIC_DURATION_AMBIGUOUS`` before falling back, so unknown types
    don't degrade silently to a quarter note."""
    return type_name in _TYPE_TO_FAMILY


def note_entity_name(step: str, type_name: str) -> str:
    """MusicXML (step, type) → BANA notes-table entry name.

    Defaults to ``quarter_or_64th_<step>`` for unknown type values so
    the backend always emits *something*; the caller can attach a
    ``MUSIC_DURATION_AMBIGUOUS`` warning at the same time (use
    :func:`is_known_note_type` to detect the fallback case).
    """
    family = _TYPE_TO_FAMILY.get(type_name, "quarter_or_64th")
    return f"{family}_{step.upper()}"


# MusicXML ``<accidental>`` element values map directly to BANA Table 6 /
# Par. 6.1-6.3 entries. Anything not listed here triggers a
# ``MUSIC_UNSUPPORTED_NOTATION`` warning at the handler level (e.g.
# ``sharp-up`` / ``flat-down`` are MusicXML 4.0 micro-tonal additions
# without a BANA cell yet). ``<alter>`` is intentionally ignored —
# BANA cares about printed appearance, not pitch math (Sibelius /
# MuseScore / Finale all emit ``<accidental>`` whenever ink is shown).
_ACCIDENTAL_ENTITY_MAP: dict[str, str] = {
    "sharp":               "sharp",
    "flat":                "flat",
    "natural":             "natural",
    "double-sharp":        "double_sharp",
    "sharp-sharp":         "double_sharp",    # rarely used synonym
    "double-flat":         "double_flat",
    "flat-flat":           "double_flat",     # MusicXML's preferred spelling
    "quarter-sharp":       "quarter_step_sharp",
    "quarter-flat":        "quarter_step_flat",
    "three-quarters-sharp": "three_quarter_step_sharp",
    "three-quarters-flat":  "three_quarter_step_flat",
}


def accidental_entity_name(musicxml_value: str) -> str | None:
    """Map a MusicXML ``<accidental>`` element value to a BANA Table 6
    entity name. Returns ``None`` for unrecognised input so the caller
    can warn + skip rather than silently emit garbage."""
    return _ACCIDENTAL_ENTITY_MAP.get(musicxml_value.strip().lower())


def octave_entity_name(octave: int) -> str:
    """MusicXML octave number → BANA octaves-table entry name. Clamps
    octaves below 0 to ``below_first_octave`` and above 8 to
    ``above_seventh_octave`` — out-of-range octaves still get a prefix
    so transcription doesn't lose pitch information silently."""
    if octave < 0:
        return "below_first_octave"
    if octave > 8:
        return "above_seventh_octave"
    return _OCTAVE_ENTITY[octave]


def diatonic_position(step: str, octave: int) -> int | None:
    """Absolute diatonic position: ``octave * 7 + step-within-octave``.

    C=0 .. B=6 within an octave; multiplying the BANA octave number by 7
    yields a single monotonic index, so the interval between two pitches
    is just ``abs(pos_a - pos_b) + 1``.  Both the octave-inference rule
    (:func:`needs_octave_mark`) and the chord-interval rule
    (``handlers.notes._emit_chord_interval``) build on this.  Returns
    ``None`` for an unknown step name so callers take their own
    conservative branch instead of guessing.
    """
    idx = _STEP_INDEX.get(step)
    if idx is None:
        return None
    return octave * 7 + idx


def needs_octave_mark(
    prev: tuple[str, int] | None, curr: tuple[str, int]
) -> bool:
    """BANA Par. 3.2.2 octave-inference rule.

    Returns True if an octave prefix must be emitted before ``curr``.

    Rules:

    * No previous pitch -> always mark (first note of line, Par. 3.2.1).
    * Interval < 4°  (≤ 3°)  -> never mark.
    * Interval > 5°  (≥ 6°)  -> always mark.
    * Interval = 4° or 5°    -> mark only when the BANA octave number
      actually changes between ``prev`` and ``curr``.
    """
    if prev is None:
        return True
    prev_step, prev_oct = prev
    curr_step, curr_oct = curr
    # Distance + 1 on the diatonic scale gives the conventional interval
    # label: same position = unison (1°), one step = 2nd, etc.
    prev_pos = diatonic_position(prev_step, prev_oct)
    curr_pos = diatonic_position(curr_step, curr_oct)
    if prev_pos is None or curr_pos is None:
        # Unknown step name — be conservative and mark.
        return True
    interval = abs(prev_pos - curr_pos) + 1
    if interval < 4:
        return False
    if interval > 5:
        return True
    # 4° or 5° — depends on whether the octave number changed.
    return prev_oct != curr_oct


# ---------------------------------------------------------------------------
# Cell emission helpers
# ---------------------------------------------------------------------------


def emit_dot_seq(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    seq: Sequence[tuple[int, ...]],
    *,
    role: str,
    source_text: str | None,
) -> None:
    """Append one cell per dots in ``seq``, sharing the score span and the
    part/measure-annotated source text.

    The common tail of every synthesized cell-sequence emitter (key / time
    signatures, word and tuplet markers, and the entity lookup) plus the
    single-cell chord-symbol appends in :mod:`.handlers.harmony`, which pass
    a one-element ``seq``.
    """
    span = mctx.span
    annotated = _annotate_source_text(source_text, mctx)
    for dots in seq:
        cells.append(
            BrailleCell(
                dots=dots,
                role=role,
                source_span=span,
                source_text=annotated,
            )
        )


def emit_cells_for_entity(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    topic: str,
    entity: str,
    *,
    role: str,
    source_text: str | None = None,
) -> bool:
    """Look up ``(topic, entity)`` in the profile's music tables and
    append one :class:`BrailleCell` per cell in the sequence.

    Returns True on success, False if the entity is absent — the
    caller is responsible for emitting a ``MUSIC_UNKNOWN_*`` warning
    and a fallback cell when so.

    M8 provenance: ``source_text`` is augmented with the current
    part / measure if either is set on ``mctx``, so proofread tools
    can attribute every cell back to its origin. Existing callers
    don't need to repeat the part / measure themselves — the suffix
    is added here.
    """
    seq = mctx.profile.music_cell(topic, entity)
    if seq is None:
        return False
    emit_dot_seq(cells, mctx, seq, role=role, source_text=source_text)
    return True


def _annotate_source_text(
    source_text: str | None, mctx: MusicBrailleContext
) -> str | None:
    """Append ``[p=<part>][m=<measure>]`` provenance to ``source_text``.

    Either component may be missing (frontends sometimes omit
    ``<part id>`` or ``<measure number>``); the suffix only includes
    set fields. Returns ``source_text`` unchanged when nothing is
    annotated, which preserves backwards-compatible behaviour for
    tests that match on bare entity names.
    """
    parts: list[str] = []
    if mctx.current_part_id is not None:
        parts.append(f"p={mctx.current_part_id}")
    if mctx.current_measure_number is not None:
        parts.append(f"m={mctx.current_measure_number}")
    if not parts:
        return source_text
    suffix = "[" + ",".join(parts) + "]"
    if source_text is None:
        return suffix
    return f"{source_text} {suffix}"


def emit_if_enabled(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    *,
    feature: str,
    topic: str,
    entity: str,
    role: str,
    source_text: str | None = None,
    default: bool = True,
) -> bool:
    """The §6.4 handler template helper — every M3+ handler funnels
    through here.

    Two-step composition:

    1. **Feature gate** — bail out (return False) if the profile has
       ``features.music.<feature>`` set to false.
    2. **Resource lookup + emit** — delegate to
       :func:`emit_cells_for_entity` for the topic/entity pair.

    Returns True if cells were appended; False when the feature is
    gated off **or** the entity is absent. Callers that need to
    distinguish those two cases should re-check the feature flag
    themselves (see ``_emit_clef`` for the pattern) — handlers that
    don't care just treat False as "skip, no warning needed".

    ``feature`` is the bare key name *without* the ``music.`` prefix;
    the helper prepends it. ``default`` controls behaviour when the
    feature isn't set in the profile (default ``True`` matches the
    "opt-out" semantics every M3+ feature uses per §7.1).
    """
    if not mctx.profile.feature(f"music.{feature}", default):
        return False
    return emit_cells_for_entity(
        cells, mctx,
        topic=topic, entity=entity, role=role, source_text=source_text,
    )


def _unknown_cell_seq(
    surface: str, span: Span | None
) -> list[BrailleCell]:
    """Build the per-char fallback used when we can't parse a music
    fragment at all (no IR tree, frontend never ran)."""
    out: list[BrailleCell] = []
    base = span.start if span else 0
    for i, ch in enumerate(surface):
        out.append(
            BrailleCell(
                dots=(),
                role="music_unknown",
                source_text=ch,
                source_span=Span(base + i, base + i + 1) if span else None,
            )
        )
    return out


def unknown_cell(
    mctx: MusicBrailleContext, *, role: str, source_text: str | None = None
) -> BrailleCell:
    """A single placeholder cell for an unrecognised element."""
    return BrailleCell(
        dots=(),
        role=role,
        source_span=mctx.span,
        source_text=source_text,
    )


def first_child_text(elem: ET.Element, tag: str) -> str | None:
    """Return the text of the first child with the given tag, or
    ``None`` if no such child / empty text."""
    child = elem.find(tag)
    if child is None:
        return None
    return (child.text or "").strip() or None


# ---------------------------------------------------------------------------
# Numeric digit synthesis (S1: BANA Pars. 6.5 / 7.1 / 8.5)
# ---------------------------------------------------------------------------
#
# When BANA composes a value from digits — 5/6/7-sharp key signature,
# arbitrary time signature ``#<num><den>``, irregular N-tuplet
# ``_<N>'`` — handlers can't go through ``emit_cells_for_entity`` per
# digit. Instead the atoms come from the ``numerals`` resource table
# (``number_sign`` / ``digit_upper_N`` / ``digit_lower_N`` / ``word_sign``
# / ``abbreviation_period`` / ``tuplet_prefix``) plus the shared neutral
# letter table — every cell still flows from ``cells.json`` via the
# profile, never a dot literal in code (music-design.md §10).


def numeral_dots(profile, entity: str) -> tuple[int, ...]:
    """Single dot tuple for a ``numerals`` entity, or ``()`` if absent.
    Every ``numerals`` entry is one cell."""
    seq = profile.music_cell("numerals", entity)
    return seq[0] if seq else ()


def _accidental_dots(profile, *, sharp: bool) -> tuple[int, ...]:
    """Sharp / flat dots from the shared ``accidentals_key`` table."""
    seq = profile.music_cell("accidentals_key", "sharp" if sharp else "flat")
    return seq[0] if seq else ()


def _digits_upper(profile, n: int) -> list[tuple[int, ...]]:
    """Render an integer as upper-row digit cells, MSB first."""
    return [numeral_dots(profile, f"digit_upper_{ch}") for ch in str(abs(int(n)))]


def _digits_lower(profile, n: int) -> list[tuple[int, ...]]:
    """Render an integer as lower-row digit cells, MSB first."""
    return [numeral_dots(profile, f"digit_lower_{ch}") for ch in str(abs(int(n)))]


def emit_synthesized_key_signature(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    fifths: int,
) -> None:
    """Synthesize a key-signature cell sequence per BANA Par. 6.5
    for ``|fifths| >= 5``.

    Form: ``#<digit><accidental>`` where ``<digit>`` is the upper-row
    letter for ``|fifths|`` and ``<accidental>`` is sharp / flat
    based on sign. Example: 6 flats → ``#f<`` = (3456)(124)(126).

    Doesn't gate on ``show_key_signature`` — caller is expected to
    have done so.
    """
    profile = mctx.profile
    seq: list[tuple[int, ...]] = [numeral_dots(profile, "number_sign")]
    seq.extend(_digits_upper(profile, abs(fifths)))
    seq.append(_accidental_dots(profile, sharp=fifths > 0))
    emit_dot_seq(
        cells, mctx, seq,
        role="music_key_signature",
        source_text=f"key:{fifths}",
    )


def emit_synthesized_time_signature(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    beats: int,
    beat_type: int,
) -> None:
    """Synthesize a time-signature cell sequence per BANA Par. 7.1.

    Form: ``#<numerator-upper><denominator-lower>``. Example: 3/4 →
    ``#c4`` = (3456)(14)(256). Multi-digit numerators and denominators
    work too (e.g. 12/8 → ``#ab8`` ... well, 12 → "1" "2" upper → a + b).
    """
    profile = mctx.profile
    seq: list[tuple[int, ...]] = [numeral_dots(profile, "number_sign")]
    seq.extend(_digits_upper(profile, beats))
    seq.extend(_digits_lower(profile, beat_type))
    emit_dot_seq(
        cells, mctx, seq,
        role="music_time_signature",
        source_text=f"{beats}/{beat_type}",
    )


def emit_synthesized_word(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    text: str,
    *,
    role: str,
    source_text: str | None = None,
    with_period: bool = False,
) -> None:
    """Synthesize a word-form cell sequence per BANA Par. 22.3:
    ``>`` (word-sign) + lowercase ASCII letter cells + optional
    ``'`` (abbreviation period).

    Used as a fallback for dynamics / verbal expressions that don't
    have a pre-built entity in ``nuances.json``. Letters outside
    a-z are skipped silently — punctuation / digits inside a
    dynamic name are vanishingly rare and would need a dedicated
    expansion if they show up.
    """
    profile = mctx.profile
    seq: list[tuple[int, ...]] = [numeral_dots(profile, "word_sign")]
    for ch in text.lower():
        dots = profile.bare_letter(ch)
        if dots is not None:
            seq.append(dots)
    if with_period:
        seq.append(numeral_dots(profile, "abbreviation_period"))
    emit_dot_seq(cells, mctx, seq, role=role, source_text=source_text or text)


def emit_synthesized_tuplet_marker(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    n: int,
) -> None:
    """Synthesize a three-cell tuplet marker per BANA Par. 8.5
    for N-tuplets without a pre-built entity.

    Form: ``_<digits>'`` where ``_`` (4,5,6) prefixes, lower-row
    digits encode N, and ``'`` (3) terminates. Example: 5-tuplet →
    ``_5'`` = (456)(26)(3); 11-tuplet → ``_11'`` = (456)(2)(2)(3).
    """
    profile = mctx.profile
    seq: list[tuple[int, ...]] = [numeral_dots(profile, "tuplet_prefix")]
    seq.extend(_digits_lower(profile, n))
    seq.append(numeral_dots(profile, "abbreviation_period"))
    emit_dot_seq(
        cells, mctx, seq,
        role="music_tuplet",
        source_text=f"tuplet:{n}",
    )
