"""S5 navigation markers (Table 20) + S7 chord symbols (Table 23).

* ``<sound>`` — read D.C. / D.S. / Segno / Coda attributes.
* ``<harmony>`` — render chord symbols (root letter + accidental +
  kind suffix + optional bass note).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import (
    emit_cells_for_entity,
    emit_dot_seq,
    first_child_text,
    numeral_dots,
)
from brailix.ir.braille import BrailleCell

# MusicXML <root-alter> → BANA Table 23 accidental entity.
_HARMONY_ACC_ENTITY: dict[int, str] = {
    1: "sharp",
    -1: "flat",
    0: "natural",
}

# Chord-kind emit recipes (MusicXML <kind> → list of [type, payload])
# now live in ``resources/music/chord_symbols.json`` under ``_kind_spec``,
# read via ``profile.music_spec("chord_symbols", "kind_spec")`` (S7 /
# BANA Table 23).  "entity" routes to a chord_symbols entity; "letters"
# synthesises lowercase letters via profile.bare_letter + lower-row
# digits via the numerals table.  Keeping the recipe in a resource lets a
# different transcription standard ship its own JSON without a code edit.


def _emit_sound(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """S5 (BANA Table 20): emit D.C. / D.S. / Segno / Coda cells
    from MusicXML ``<sound>`` attributes.

    MusicXML encodes these navigation markers as attributes on
    ``<sound>``:

    * ``dacapo="yes"``     → print da capo (D.C.)
    * ``dalsegno="X"``     → dal segno (jump back to segno X)
    * ``segno="X"``        → segno marker (start of jump target)
    * ``coda="X"``         → coda marker
    * ``tocoda="X"``       → to-coda directive

    Performance-only attributes (``tempo``, ``dynamics`` as numeric
    value, ``divisions``, etc.) are silently ignored.

    Gated by ``music.show_dynamics`` (these are navigational, not
    audible — but they appear in the same conceptual layer as
    dynamic markings and most users want one switch).
    """
    if not mctx.profile.feature("music.show_dynamics", True):
        return

    if elem.attrib.get("dacapo") == "yes":
        emit_cells_for_entity(
            cells, mctx,
            topic="dacapo", entity="print_da_capo",
            role="music_dacapo",
            source_text="D.C.",
        )
    segno_letter = elem.attrib.get("segno")
    if segno_letter:
        # The MusicXML segno attribute carries a label letter; BANA encodes a
        # per-letter braille marker. Resources ship only the generic
        # `braille_segno_with_letter` (letter "a"), so a non-"a" label can't be
        # rendered yet — warn that the specific letter is dropped rather than
        # silently presenting it as segno "a" (per-letter segno is a future
        # resource expansion).
        if segno_letter.strip().lower() != "a":
            mctx.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"segno label {segno_letter!r} has no per-letter braille "
                    "marker; emitting the generic segno sign without the letter"
                ),
                source="backend.music",
            )
        emit_cells_for_entity(
            cells, mctx,
            topic="dacapo", entity="braille_segno_with_letter",
            role="music_segno",
            source_text=f"segno:{segno_letter}",
        )
    dalsegno_letter = elem.attrib.get("dalsegno")
    if dalsegno_letter:
        # The label letter selects a per-letter marker (resources ship "a" and
        # "b"). Use the letter-specific entity so dal segno "B" reads as a jump
        # to B, not A; if no marker exists for this letter, warn and fall back
        # to "a" rather than silently mislabelling the jump.
        letter = dalsegno_letter.strip().lower()
        if not emit_cells_for_entity(
            cells, mctx,
            topic="dacapo", entity=f"braille_dal_segno_letter_{letter}",
            role="music_dal_segno",
            source_text=f"dal-segno:{dalsegno_letter}",
        ):
            mctx.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"dal segno label {dalsegno_letter!r} has no per-letter "
                    "braille marker; falling back to letter 'a'"
                ),
                source="backend.music",
            )
            emit_cells_for_entity(
                cells, mctx,
                topic="dacapo", entity="braille_dal_segno_letter_a",
                role="music_dal_segno",
                source_text=f"dal-segno:{dalsegno_letter}",
            )
    coda_letter = elem.attrib.get("coda")
    if coda_letter:
        emit_cells_for_entity(
            cells, mctx,
            topic="dacapo", entity="print_encircled_cross_coda",
            role="music_coda",
            source_text=f"coda:{coda_letter}",
        )
    tocoda_letter = elem.attrib.get("tocoda")
    if tocoda_letter:
        emit_cells_for_entity(
            cells, mctx,
            topic="dacapo", entity="print_encircled_cross_coda",
            role="music_coda",
            source_text=f"to-coda:{tocoda_letter}",
        )


def _emit_harmony(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """BANA Table 23 / Par. 23.1: emit chord-symbol cells from a
    MusicXML ``<harmony>`` element.

    Sequence: ``<root-step>`` letter (lowercase) → optional root
    accidental → ``<kind>`` suffix (per the ``kind_spec`` recipe read
    via ``profile.music_spec("chord_symbols", "kind_spec")``) →
    optional ``<bass>`` slash + bass letter.

    Gated by ``music.show_chord_symbols`` (default true). Unknown
    ``<kind>`` text falls back to bare root + ``MUSIC_UNSUPPORTED_NOTATION``
    warning so the reader at least sees the root.

    M S7 doesn't render ``<degree>`` (added / altered chord tones)
    yet — most exporters fold them into the ``<kind>`` text
    representation already; explicit degree handling lands later.
    """
    if not mctx.profile.feature("music.show_chord_symbols", True):
        return

    root_elem = elem.find("root")
    if root_elem is None:
        # MusicXML allows <function> instead of <root> for Roman-
        # numeral harmony; not supported here.
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message="<harmony> without <root> (e.g. roman <function>) not supported",
            source="backend.music",
        )
        return
    root_step = first_child_text(root_elem, "root-step")
    if not root_step:
        return
    root_alter_raw = first_child_text(root_elem, "root-alter")
    try:
        root_alter = int(root_alter_raw) if root_alter_raw is not None else 0
    except ValueError:
        root_alter = 0

    source_label = f"chord:{root_step}"

    # Root letter — BANA Par. 23.1 uses lowercase letters for chord
    # symbol roots (visually distinct from the uppercase used in
    # interval / lyric paths). MusicXML restricts <root-step> to
    # A-G; we reject anything outside that range.
    root_step_lc = root_step.lower()
    if root_step_lc not in {"a", "b", "c", "d", "e", "f", "g"}:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"chord root-step {root_step!r} not in A-G",
            source="backend.music",
        )
        return
    letter_dots = mctx.profile.bare_letter(root_step_lc)
    if letter_dots is None:
        # Defensive — bare_letter covers a-z, so this is impossible
        # after the A-G check above.
        return
    emit_dot_seq(
        cells, mctx, [letter_dots],
        role="music_chord_symbol",
        source_text=source_label,
    )

    # Root accidental (only when nonzero — natural is implicit).
    if root_alter != 0:
        acc_entity = _HARMONY_ACC_ENTITY.get(root_alter)
        if acc_entity is not None:
            emit_cells_for_entity(
                cells, mctx,
                topic="chord_symbols", entity=acc_entity,
                role="music_chord_symbol",
                source_text=f"{source_label}/alter:{root_alter}",
            )

    _emit_kind_suffix(cells, mctx, elem, source_label)
    _emit_bass(cells, mctx, elem, source_label)


def _emit_kind_suffix(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
    source_label: str,
) -> None:
    """Emit the ``<kind>`` suffix cells (BANA Table 23) per the
    ``kind_spec`` recipe read via
    ``profile.music_spec("chord_symbols", "kind_spec")``.

    ``entity`` routes to a chord_symbols entity; ``letters`` synthesises
    lowercase letters (via ``profile.bare_letter``) and lower-row digits
    (via the numerals table). An unknown ``<kind>`` warns and emits
    nothing — the caller has already emitted the bare root.
    """
    kind_raw = first_child_text(elem, "kind") or ""
    kind = kind_raw.strip().lower()
    if not kind:
        return
    spec = (mctx.profile.music_spec("chord_symbols", "kind_spec") or {}).get(
        kind
    )
    if spec is None:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"<harmony><kind>{kind_raw!r}</kind> not in S7 spec "
                f"(emitting bare root)"
            ),
            source="backend.music",
        )
        return
    for spec_kind, payload in spec:
        if spec_kind == "entity":
            emit_cells_for_entity(
                cells, mctx,
                topic="chord_symbols", entity=payload,
                role="music_chord_symbol",
                source_text=f"{source_label}/{kind}",
            )
        elif spec_kind == "letters":
            for ch in payload:
                if ch.isdigit():
                    # Lower-row digit cell.
                    emit_dot_seq(
                        cells, mctx,
                        [numeral_dots(mctx.profile, f"digit_lower_{ch}")],
                        role="music_chord_symbol",
                        source_text=f"{source_label}/{kind}",
                    )
                else:
                    dots = mctx.profile.bare_letter(ch)
                    if dots is not None:
                        emit_dot_seq(
                            cells, mctx, [dots],
                            role="music_chord_symbol",
                            source_text=f"{source_label}/{kind}",
                        )


def _emit_bass(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
    source_label: str,
) -> None:
    """Emit the optional bass note (chord-over-bass ``C/G`` form): a
    slash separator, the bass letter, and the bass accidental when
    nonzero. No ``<bass>`` (or no ``<bass-step>``) emits nothing."""
    bass_elem = elem.find("bass")
    if bass_elem is None:
        return
    bass_step = first_child_text(bass_elem, "bass-step")
    if not bass_step:
        return
    # Slash separator.
    emit_cells_for_entity(
        cells, mctx,
        topic="chord_symbols", entity="slash",
        role="music_chord_symbol",
        source_text=f"{source_label}/bass:{bass_step}",
    )
    bass_dots = mctx.profile.bare_letter(bass_step.lower())
    if bass_dots is not None:
        emit_dot_seq(
            cells, mctx, [bass_dots],
            role="music_chord_symbol",
            source_text=f"{source_label}/bass:{bass_step}",
        )
    # Bass alter (only when nonzero — natural is implicit).
    bass_alter_raw = first_child_text(bass_elem, "bass-alter")
    try:
        bass_alter = int(bass_alter_raw) if bass_alter_raw else 0
    except ValueError:
        bass_alter = 0
    if bass_alter == 0:
        return
    acc_entity = _HARMONY_ACC_ENTITY.get(bass_alter)
    if acc_entity is not None:
        emit_cells_for_entity(
            cells, mctx,
            topic="chord_symbols", entity=acc_entity,
            role="music_chord_symbol",
            source_text=f"{source_label}/bass-alter:{bass_alter}",
        )


_DISPATCH_PARTIAL = {
    # S7: chord symbols (BANA Table 23).
    "harmony": _emit_harmony,
    # S5: D.C. / D.S. / Coda from <sound> attributes (BANA Table 20).
    "sound": _emit_sound,
}
