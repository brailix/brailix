"""M3.1 attributes: clef (Table 4), key signature (Table 6, Par. 6.5),
time signature (Table 7).

``<attributes>`` is a thin container; each leaf (``<clef>`` / ``<key>``
/ ``<time>``) has its own feature gate so a profile can independently
toggle the three outputs.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.dispatch import _emit_element
from brailix.backend.music.utils import (
    emit_cells_for_entity,
    emit_if_enabled,
    emit_synthesized_key_signature,
    emit_synthesized_time_signature,
    first_child_text,
)
from brailix.ir.braille import BrailleCell


def _emit_attributes(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Walk an ``<attributes>`` block, dispatching each child to its
    handler.

    Per §6.4 template: this is a pure container handler — every
    feature gate lives inside the leaf handlers (``_emit_clef`` etc.)
    so a profile can independently toggle each output.

    Technical-only children (``<divisions>``, ``<staves>``,
    ``<instruments>``, ``<staff-details>``) carry no cells.
    """
    for child in elem:
        if child.tag in _ATTRIBUTE_SKIP:
            continue
        _emit_element(cells, mctx, child)


_ATTRIBUTE_SKIP: frozenset[str] = frozenset({
    "divisions",        # internal parser metric
    "staves",           # multi-staff hint (handled at part level in M4)
    "instruments",      # part metadata
    "staff-details",    # staff appearance hint
    "transpose",        # pitch transposition — not applied (score written as-is)
    "directive",        # vendor-specific
    "measure-style",    # multi-measure rest etc., M3.3+ may revisit
})


# --- Clef ------------------------------------------------------------------


# MusicXML (sign, line) → BANA Table 4 entry name.
# Missing line falls back to the sign-only entry below.
_CLEF_ENTITY_MAP: dict[tuple[str, int], str] = {
    ("G", 2): "g_clef_treble",
    ("G", 1): "g_clef_first_line",
    ("F", 4): "f_clef_bass",
    ("F", 3): "f_clef_third_line",
    ("C", 3): "c_clef_alto",
    ("C", 4): "c_clef_fourth_line",
}

_CLEF_SIGN_DEFAULT: dict[str, str] = {
    "G": "g_clef_treble",
    "F": "f_clef_bass",
    "C": "c_clef_alto",
}


def _clef_entity_name(sign: str, line: int | None) -> str:
    """Map MusicXML clef (sign, line) → BANA Table 4 entity name.

    Unknown signs / unusual lines fall back to the matching sign's
    default; ``g_clef_treble`` is the final resort.
    """
    if line is not None and (sign, line) in _CLEF_ENTITY_MAP:
        return _CLEF_ENTITY_MAP[(sign, line)]
    return _CLEF_SIGN_DEFAULT.get(sign, "g_clef_treble")


def _emit_clef(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """BANA Table 4: emit clef cells based on ``<sign>`` + ``<line>``.

    Per §6.4 template: feature gate → entity lookup → emit. Profile
    can disable per-clef output via ``features.music.show_clef``.
    """
    sign = (first_child_text(elem, "sign") or "G").upper()
    line_raw = first_child_text(elem, "line")
    try:
        line = int(line_raw) if line_raw is not None else None
    except ValueError:
        line = None
    if sign not in _CLEF_SIGN_DEFAULT:
        # Percussion / TAB / anything outside BANA Table 4.  Emitting a
        # treble-clef cell here (the old fallback) silently misstated
        # the part — a drum part read as a G clef — and letting the
        # sign linger would feed the chord-direction rule (Par. 9.2 is
        # G / F / C only).  Warn, emit nothing, neutralise the state.
        mctx.current_clef_sign = None
        mctx.current_clef_line = None
        mctx.warn(
            code="MUSIC_UNKNOWN_CLEF",
            message=(
                f"unsupported clef sign {sign!r} (BANA Table 4 covers "
                f"G / F / C) — no clef cell emitted"
            ),
            source="backend.music",
        )
        return
    # BANA Par. 9.2: remember the clef so chord emission picks the
    # written note's direction. Set even when ``show_clef`` is off — the
    # interval direction follows the clef whether or not the clef *cell*
    # is printed.
    mctx.current_clef_sign = sign
    mctx.current_clef_line = line
    entity = _clef_entity_name(sign, line)
    emitted = emit_if_enabled(
        cells, mctx,
        feature="show_clef",
        topic="clefs", entity=entity,
        role="music_clef",
        source_text=f"clef:{sign}{line if line is not None else ''}",
    )
    # If the feature was on but the entity missing, warn so an
    # unfamiliar clef variant doesn't vanish silently.
    if not emitted and mctx.profile.feature("music.show_clef", True):
        mctx.warn(
            code="MUSIC_UNKNOWN_CLEF",
            message=(
                f"no clef entry for {entity!r} "
                f"(sign={sign!r}, line={line!r})"
            ),
            source="backend.music",
        )


# --- Key signature ---------------------------------------------------------


# Named entries from BANA Par. 6.5 / Table 6. 1-3 sharps/flats are
# composed by repeating the bare accidental (BANA Par. 6.5 text rule);
# 4-7 take a numeral prefix.
_KEY_NAMED_ENTRY: dict[int, str] = {
    4: "four_sharp_signature",
    -4: "four_flat_signature",
}


def _emit_key(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """BANA Par. 6.5 / Table 6: emit key signature cells based on
    ``<fifths>`` (positive = sharps, negative = flats, 0 = C / A
    minor → no output).

    Composition rules (M3.1 coverage):

    * ``fifths == 0`` → no cells (BANA omits the natural key signature)
    * ``1 ≤ |fifths| ≤ 3`` → repeat the bare ``sharp`` / ``flat``
      accidental N times
    * ``|fifths| == 4`` → use ``four_sharp_signature`` /
      ``four_flat_signature`` (numeral prefix + accidental)
    * ``|fifths| ≥ 5`` → synthesize ``#<digit><accidental>`` (numeral
      prefix + repeated accidental) per BANA Par. 6.5 — covers any N
      that has no pre-built named entry.
    """
    fifths_text = first_child_text(elem, "fifths")
    if fifths_text is None:
        return
    try:
        fifths = int(fifths_text)
    except ValueError:
        return

    # Feature gate up-front — saves us from emitting anything when
    # the profile has turned off key signatures wholesale.
    if not mctx.profile.feature("music.show_key_signature", True):
        return

    if fifths == 0:
        return  # natural key — BANA shows nothing

    accidental_entity = "sharp" if fifths > 0 else "flat"
    n = abs(fifths)

    if n <= 3:
        for _ in range(n):
            emit_cells_for_entity(
                cells, mctx,
                topic="accidentals_key", entity=accidental_entity,
                role="music_key_signature",
                source_text=f"key:{fifths}",
            )
        return

    if fifths in _KEY_NAMED_ENTRY:
        emit_cells_for_entity(
            cells, mctx,
            topic="accidentals_key", entity=_KEY_NAMED_ENTRY[fifths],
            role="music_key_signature",
            source_text=f"key:{fifths}",
        )
        return

    # |fifths| in 5..7 (or wider — synthesized form covers any N).
    # S1: synthesize ``#<digit><accidental>`` per BANA Par. 6.5.
    emit_synthesized_key_signature(cells, mctx, fifths)


# --- Time signature --------------------------------------------------------


# (beats, beat_type) → BANA Table 7 entry name. Symbol attribute is
# consulted *before* numeric matching so a ``symbol="common"`` 4/4
# routes to the C-symbol rather than the four_four_time entry.
_TIME_NUMERIC_MAP: dict[tuple[int, int], str] = {
    (4, 4): "four_four_time",
    (6, 8): "six_eight_time",
}


def _emit_time(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """BANA Table 7: emit time-signature cells.

    M3.1 coverage:

    * ``symbol="common"`` → ``common_time`` (the C symbol)
    * ``symbol="cut"`` → ``alla_breve_cut_time`` (¢)
    * 4/4 → ``four_four_time`` (#d4)
    * 6/8 → ``six_eight_time`` (#f8)
    * any other numeric meter (3/4, 2/4, 9/8, ...) → synthesize
      ``#<numerator-upper><denominator-lower>`` per BANA Par. 7.1
    * additive meters (``beats="3+2"``) and compound / interchangeable
      meters (multiple beats / beat-type groups) → warn
      ``MUSIC_UNSUPPORTED_NOTATION``
    """
    if not mctx.profile.feature("music.show_time_signature", True):
        return

    symbol = elem.attrib.get("symbol", "").lower()
    if symbol == "common":
        _emit_time_entity(cells, mctx, "common_time", source="C")
        return
    if symbol == "cut":
        _emit_time_entity(cells, mctx, "alla_breve_cut_time", source="cut")
        return

    beats_elems = elem.findall("beats")
    beat_type_elems = elem.findall("beat-type")
    if not beats_elems or not beat_type_elems:
        # Genuinely empty <time> (senza-misura writes no beats at all)
        # — nothing numeric to print, and silence is correct.
        return
    if len(beats_elems) > 1 or len(beat_type_elems) > 1:
        # Interchangeable / compound meter (3/4 + 3/8) writes multiple
        # beats / beat-type groups.  Only the first group is rendered;
        # dropping the rest silently misstated the meter.
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                "compound time signature (multiple beats/beat-type "
                "groups) — only the first group is rendered"
            ),
            source="backend.music",
        )
    beats_text = (beats_elems[0].text or "").strip()
    beat_type_text = (beat_type_elems[0].text or "").strip()
    try:
        beats = int(beats_text)
        beat_type = int(beat_type_text)
    except ValueError:
        # Additive meters write beats="3+2"; there is no cell mapping
        # yet, and the old silent return left the score unmetered with
        # no trace at all.
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"unsupported time signature beats={beats_text!r} "
                f"beat-type={beat_type_text!r} — no meter cells emitted"
            ),
            source="backend.music",
        )
        return

    entity = _TIME_NUMERIC_MAP.get((beats, beat_type))
    if entity is not None:
        _emit_time_entity(cells, mctx, entity, source=f"{beats}/{beat_type}")
        return

    # S1: synthesized form ``#<numerator-upper><denominator-lower>``
    # per BANA Par. 7.1 — covers every numeric meter we don't have
    # a pre-built entry for (3/4, 2/4, 9/8, 12/8, 5/4, 7/8, ...).
    emit_synthesized_time_signature(cells, mctx, beats, beat_type)


def _emit_time_entity(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    entity: str,
    *,
    source: str,
) -> None:
    """Direct-emit helper for time entities once we've decided which
    entity to use. Skips the feature gate (caller already checked)."""
    if not emit_cells_for_entity(
        cells, mctx,
        topic="meter", entity=entity,
        role="music_time_signature",
        source_text=source,
    ):
        mctx.warn(
            code="MUSIC_UNKNOWN_TIME",
            message=f"no meter entry for {entity!r}",
            source="backend.music",
        )


_DISPATCH_PARTIAL = {
    "attributes": _emit_attributes,
    "clef": _emit_clef,
    "key": _emit_key,
    "time": _emit_time,
}
