"""M3.4 direction: dynamics + words + wedge (hairpin) — BANA Table 22.

``<direction>`` is the container; each ``<direction-type>`` holds one
marker (``<dynamics>``, ``<words>``, ``<wedge>``). All emissions are
gated by per-marker features (``show_dynamics`` / ``show_words``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend._inline import rebase_translated_cells
from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.utils import (
    emit_cells_for_entity,
    emit_synthesized_word,
)
from brailix.ir.braille import BrailleCell


def _emit_direction(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """``<direction>`` is a pure container — descend into
    ``<direction-type>`` children. Other siblings like ``<offset>``
    and ``<staff>`` carry positioning hints irrelevant to braille."""
    for child in elem:
        if child.tag == "direction-type":
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
# Only the M1-shipped entities are covered; extra MusicXML symbols
# (mp, sf, sfz, ...) warn until the resource table grows in a later
# milestone.
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

    M3.4 only supports the BANA word-form dynamics shipped in
    ``resources/music/nuances.json``: pp / p / mf / f / ff. Other
    MusicXML symbols (mp / sf / sfz / fp / fz / niente) warn —
    resource expansion lands in a later milestone, not in M3.4's
    handler-only scope.
    """
    if not mctx.profile.feature("music.show_dynamics", True):
        return
    form = mctx.profile.feature("music.dynamics_form", "abbreviated")
    if form != "abbreviated":
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"music.dynamics_form={form!r} not implemented "
                f"(M3.4 covers 'abbreviated' only); falling back"
            ),
            source="backend.music",
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
        translator = mctx.backend.inline_text_translator()
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


_DISPATCH_PARTIAL = {
    # M3.4: direction container + leaf markers.
    "direction": _emit_direction,
    "direction-type": _emit_direction_type,
    "dynamics": _emit_dynamics,
    "words": _emit_words,
    "wedge": _emit_wedge,
}
