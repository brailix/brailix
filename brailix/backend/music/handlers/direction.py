"""M3.4 direction: dynamics + words + wedge (hairpin) + metronome — BANA Table 22.

``<direction>`` is the container; each ``<direction-type>`` holds one
marker (``<dynamics>``, ``<words>``, ``<wedge>``, ``<metronome>``). All
emissions are gated by per-marker features (``show_dynamics`` /
``show_words``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.backend._inline import rebase_translated_cells
from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.handlers._common import warn_feature_unimplemented
from brailix.backend.music.utils import (
    emit_cells_for_entity,
    emit_dot_seq,
    emit_synthesized_word,
    is_known_note_type,
    note_entity_name,
    numeral_dots,
)
from brailix.ir.braille import BrailleCell

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET


def _emit_direction(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<direction>`` is a pure container — descend into
    ``<direction-type>`` children. ``<sound>`` (a legal sibling: D.C. /
    D.S. / coda repeats, MuseScore exports it inside ``<direction>``)
    re-enters the main dispatch table so its own handler applies.
    Other siblings like ``<offset>`` and ``<staff>`` carry positioning
    hints irrelevant to braille."""
    for child in elem:
        if child.tag == "direction-type":
            _emit_element(cells, mctx, child)
        elif child.tag == "sound":
            _emit_element(cells, mctx, child)


def _emit_direction_type(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<direction-type>`` is a thin wrapper around the real marker
    (``<dynamics>`` / ``<words>`` / ``<wedge>`` / ...). Dispatch each
    child through the main table so per-marker feature gates apply
    at the leaf handler."""
    for child in elem:
        _emit_element(cells, mctx, child)


# --- Dynamics --------------------------------------------------------------


# MusicXML ``<dynamics>`` symbol-name → BANA Table 22 (A-C) entity.
# Only the common symbols are pre-built here; any other MusicXML
# symbol (mp, sf, sfz, ...) is synthesized as a ``>``-word form
# (BANA Par. 22.3) by the fallback below — no warning.
_DYNAMICS_ENTITY: dict[str, str] = {
    "pp": "dynamic_pp",
    "p":  "dynamic_p",
    "mf": "dynamic_mf",
    "f":  "dynamic_f",
    "ff": "dynamic_ff",
}


def _emit_dynamics(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<dynamics><p/><mf/>...</dynamics>`` — each child is one
    dynamic symbol. Each maps to a BANA Table 22 (A-C) entity.

    The BANA word-form dynamics shipped in
    ``resources/music/nuances.json`` (pp / p / mf / f / ff) use their
    pre-built entities; any other MusicXML symbol (mp / sf / sfz / fp /
    fz / niente, ...) is synthesized as a ``>``-word form per BANA
    Par. 22.3 by the fallback below — not warned.
    """
    if not mctx.profile.feature("music.show_dynamics", True):
        return
    form = mctx.profile.feature("music.dynamics_form", "abbreviated")
    if form != "abbreviated":
        warn_feature_unimplemented(
            mctx, "music.dynamics_form", form, "M3.4 covers 'abbreviated' only"
        )
    for child in elem:
        if child.tag == "other-dynamics":
            text = (child.text or "").strip()
            if text:
                # S2: synthesize ``>...`` for any custom dynamic text.
                emit_synthesized_word(
                    cells, mctx, text,
                    role="music_dynamic",
                    source_text=f"other-dynamics:{text}",
                )
            continue
        entity = _DYNAMICS_ENTITY.get(child.tag)
        if entity is not None:
            emit_cells_for_entity(
                cells, mctx,
                topic="nuances", entity=entity,
                role="music_dynamic",
                source_text=child.tag,
            )
            continue
        # S2: synthesized fallback for any MusicXML dynamic tag without
        # a pre-built entity (mp, sf, sfz, fp, sfp, sfpp, fz, sffz,
        # rf, rfz, pf, n, and the longer ppp/pppp/fff/ffff family).
        # BANA Par. 22.3's word-form rule (``>`` + letters) covers
        # every printable dynamic name with a single uniform shape.
        emit_synthesized_word(
            cells, mctx, child.tag,
            role="music_dynamic",
            source_text=child.tag,
        )


# --- Words -----------------------------------------------------------------


# Common textual cues that map to BANA Table 22 (C) word-sign entries.
# Keys are normalised lowercased + period-stripped — input matching is
# case-insensitive and tolerates trailing ``.``.
_WORDS_ENTITY: dict[str, str] = {
    "cresc":       "dynamic_cresc",
    "crescendo":   "dynamic_cresc",
    "decresc":     "dynamic_decresc",
    "decrescendo": "dynamic_decresc",
    "dim":         "dynamic_dimin",
    "dimin":       "dynamic_dimin",
    "diminuendo":  "dynamic_dimin",
}


def _emit_words(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<words>cresc.</words>`` — textual dynamic / expression cues.

    Known dynamics aliases (cresc / decresc / dim) map to nuance
    entities; a single ASCII word synthesizes a ``>``-word; multi-word
    or non-ASCII text (teaching notes, ``(M.M. ...)``, Chinese) routes
    through the injected ``inline_text_translator`` (zh / latin path) so
    it becomes real braille — or, when no translator is wired (bare
    backend / tests), defers with a warning."""
    if not mctx.profile.feature("music.show_words", True):
        return
    raw = (elem.text or "").strip()
    if not raw:
        return
    normalised = raw.lower().rstrip(".").strip()
    entity = _WORDS_ENTITY.get(normalised)
    if entity is not None:
        emit_cells_for_entity(
            cells, mctx,
            topic="nuances", entity=entity,
            role="music_word",
            source_text=raw,
        )
        return
    # S2: synthesize ``>...`` for arbitrary single-word text. BANA
    # Par. 22.3 word-form covers any expression that fits the
    # ``>`` + letters + optional ``'`` mould (poco, sub, sempre,
    # accel, rit, pesante, dolce, ...). Multi-word phrases / non-
    # ASCII text still need the full Latin / zh path (deferred).
    if (
        not normalised
        or " " in normalised
        or not normalised.isascii()
        or not all(c.isalpha() for c in normalised)
    ):
        # Multi-word / non-ASCII text (teaching notes, "(M.M. ...)",
        # Chinese ...): route through the injected text translator (zh /
        # latin path) so it becomes real braille.  No translator wired
        # (bare backend / tests) → keep the deferred warning.
        translator = mctx.backend.inline_text_translator(
            domain="music_words", span=mctx.span
        )
        if translator is not None:
            # Keep the translator's own role (latin / zh) on these cells —
            # unlike lyrics (``_emit_lyrics_inline`` retags to 'music_lyric'),
            # <words> text deliberately stays tagged by its language path so
            # it reads as ordinary expression text, not a music-specific run.
            # Spans DO get rebased: the translator's cells carry
            # throwaway-document coordinates.
            cells.extend(
                rebase_translated_cells(translator(raw), mctx.span)
            )
        else:
            mctx.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"<words>{raw!r}</words> outside the single-word ASCII "
                    f"synthesis path; multi-word / non-ASCII text needs the "
                    f"Latin / zh frontend (deferred)"
                ),
                source="backend.music",
            )
        return
    emit_synthesized_word(
        cells, mctx, normalised,
        role="music_word",
        source_text=raw,
        with_period=raw.rstrip().endswith("."),
    )


# --- Wedge (hairpin) -------------------------------------------------------


def _emit_wedge(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<wedge type="crescendo|diminuendo|stop|continue"/>``.

    BANA Table 22 (C) uses paired markers: opening hairpin cell at
    the start, terminator cell at the end. ``mctx.pending_hairpin``
    bridges the two emissions — a ``crescendo`` arms it, ``stop``
    consults it to pick the matching terminator.

    Gated by ``music.show_dynamics`` (hairpins are graphical
    dynamics; one switch covers both kinds of marker).
    """
    if not mctx.profile.feature("music.show_dynamics", True):
        return
    wedge_type = elem.attrib.get("type", "").strip().lower()
    if wedge_type == "crescendo":
        emit_cells_for_entity(
            cells, mctx,
            topic="nuances", entity="diverging_hairpin",
            role="music_hairpin",
            source_text="cresc<",
        )
        mctx.pending_hairpin = "crescendo"
    elif wedge_type == "diminuendo":
        emit_cells_for_entity(
            cells, mctx,
            topic="nuances", entity="converging_hairpin",
            role="music_hairpin",
            source_text="dim>",
        )
        mctx.pending_hairpin = "diminuendo"
    elif wedge_type == "stop":
        if mctx.pending_hairpin == "crescendo":
            emit_cells_for_entity(
                cells, mctx,
                topic="nuances", entity="diverging_hairpin_terminator",
                role="music_hairpin",
                source_text="cresc-stop",
            )
        elif mctx.pending_hairpin == "diminuendo":
            emit_cells_for_entity(
                cells, mctx,
                topic="nuances", entity="converging_hairpin_terminator",
                role="music_hairpin",
                source_text="dim-stop",
            )
        # If no pending hairpin, the stop is orphaned — silently
        # ignore (probably an upstream MusicXML inconsistency, not
        # the score's intent).
        mctx.pending_hairpin = None
    elif wedge_type == "continue":
        # MusicXML ``continue`` is a no-op; the hairpin keeps going
        # until its ``stop``.
        return
    else:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"unknown wedge type {wedge_type!r}",
            source="backend.music",
        )


# --- Metronome (tempo mark) ------------------------------------------------


# The metronome mark's beat-unit note is pitch-less (a tempo reference,
# not a sounded note). Braille note cells are keyed by (step, value), so
# the mark is written on the C (do) row — the scale's reference pitch —
# through the ordinary notes-table entity; no new cell shape is invented.
_METRONOME_NOTE_STEP = "C"


def _emit_metronome(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<metronome><beat-unit>quarter</beat-unit><per-minute>120`` —
    the tempo mark ``M.M. ♩ = 120``.

    Synthesised from existing table entries, BANA Table 1 shape
    ``note-value cell(s) + metronome_equals ⠶ + number sign + digits``:

    * ``<beat-unit>`` (a MusicXML note-type value: quarter / eighth /
      half / ...) renders through the ordinary notes-table cell for that
      value — the same entity a sounded note of the type uses, written
      on the pitch-less C reference row (see
      :data:`_METRONOME_NOTE_STEP`);
    * each ``<beat-unit-dot/>`` adds the dot-added-value cell, exactly
      as a sounded dotted note;
    * ``<per-minute>`` renders as number sign + upper-row digits
      (numerals table — the same synthesis a time signature uses).

    Gated by ``music.show_words`` — a tempo mark is expression text
    (the sibling ``<words>(M.M. ...)</words>`` that often prefixes it is
    already gated there). Unknown beat-unit values degrade to the
    quarter-note shape with the same ``MUSIC_DURATION_AMBIGUOUS``
    warning the note handler raises, so the silent-mistranslation guard
    covers this path too."""
    if not mctx.profile.feature("music.show_words", True):
        return
    for child in elem:
        tag = child.tag
        if tag == "beat-unit":
            type_name = (child.text or "").strip() or "quarter"
            if not is_known_note_type(type_name):
                mctx.warn(
                    code="MUSIC_DURATION_AMBIGUOUS",
                    message=(
                        f"unknown <beat-unit>{type_name!r}</beat-unit>; "
                        "falling back to quarter-note shape"
                    ),
                    source="backend.music",
                )
            entity = note_entity_name(_METRONOME_NOTE_STEP, type_name)
            if not emit_cells_for_entity(
                cells, mctx,
                topic="notes",
                entity=entity,
                role="music_tempo_note",
                source_text=f"metronome:{type_name}",
            ):
                mctx.warn(
                    code="MUSIC_UNSUPPORTED_NOTATION",
                    message=f"no note entry for metronome beat-unit {entity}",
                    source="backend.music",
                )
        elif tag == "beat-unit-dot":
            emit_cells_for_entity(
                cells, mctx,
                topic="notes", entity="dot_added_value",
                role="music_dot",
                source_text="metronome:dot",
            )
        elif tag == "per-minute":
            raw = (child.text or "").strip()
            digits = [ch for ch in raw if ch.isdigit()]
            if not digits:
                mctx.warn(
                    code="MUSIC_UNSUPPORTED_NOTATION",
                    message=f"<per-minute> without digits: {raw!r}",
                    source="backend.music",
                )
                continue
            # The metronome equals sign ⠶ (BANA Table 1) joins the note
            # value to the tempo digits.
            emit_cells_for_entity(
                cells, mctx,
                topic="general", entity="metronome_equals",
                role="music_tempo_equals",
                source_text="metronome:equals",
            )
            profile = mctx.profile
            seq: list[tuple[int, ...]] = [numeral_dots(profile, "number_sign")]
            for ch in digits:
                seq.append(numeral_dots(profile, f"digit_upper_{ch}"))
            emit_dot_seq(
                cells, mctx, seq,
                role="music_tempo_bpm",
                source_text=f"metronome:{raw}",
            )
        # Other children (positioning, <sound> playback hints) carry no
        # braille meaning; they are skipped silently, matching how
        # <direction> itself skips <offset> / <staff>.


_DISPATCH_PARTIAL = {
    # M3.4: direction container + leaf markers.
    "direction": _emit_direction,
    "direction-type": _emit_direction_type,
    "dynamics": _emit_dynamics,
    "words": _emit_words,
    "wedge": _emit_wedge,
    "metronome": _emit_metronome,
}
