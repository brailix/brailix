"""MusicXML normalisation.

After a source adapter produces a MusicXML string, the normalizer
cleans it up so downstream consumers (the music backend) have fewer
special cases to handle:

* drop XML namespaces from element tags so the backend can match on
  bare local names (``note``, ``pitch``, ...) instead of Clark notation;
* drop XML declarations / DOCTYPEs that some vendors prepend;
* ``<score-timewise>`` is NOT transposed to ``<score-partwise>`` here —
  a timewise root passes through unchanged and the backend dispatch falls
  to ``_emit_unsupported`` (one ``MUSIC_UNSUPPORTED_NOTATION`` warning, no
  cells), since timewise scores are rare in practice;
* strip pure-whitespace text nodes that confuse element iteration.

The normalizer never raises — malformed input is wrapped into a single
``<music-error>`` document and returned. The backend turns that into a
fallback cell sequence with a ``MUSIC_*`` warning and the pipeline
keeps running (see ``ARCHITECTURE.md``).

**Attribute preservation**: the normalizer rewrites ``elem.tag`` (to
drop namespaces) but never touches ``elem.attrib`` — provenance / data
attributes set by adapters survive.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from brailix.core._xml import strip_namespace, strip_whitespace_text
from brailix.frontend.music.adapters.musicxml import music_error_wrap

if TYPE_CHECKING:
    from brailix.core.context import MusicContext


def normalize(
    musicxml: str, ctx: MusicContext | None = None
) -> ET.Element:
    """Parse a MusicXML string and return a normalised
    :class:`Element` tree with namespaces stripped.

    Soft-failure contract: invalid XML is wrapped into a
    ``<music-error>`` document via :func:`music_error_wrap` so the
    caller always gets a usable tree (rooted at ``<score-partwise>``).
    """
    try:
        root = ET.fromstring(musicxml)
    except ET.ParseError as e:
        root = ET.fromstring(
            music_error_wrap(musicxml, reason=f"parse error: {e}")
        )
    strip_namespace(root)
    strip_whitespace_text(root)
    _normalize_voice_numbers(root)
    _inherit_chord_member_staff_voice(root)
    _infer_missing_note_types(root, ctx)
    return root


def _normalize_voice_numbers(root: ET.Element) -> None:
    """Remap each ``<part>``'s ``<voice>`` numbers to a dense ``1..N``
    sequence (``ARCHITECTURE.md`` vendor-dialect table).

    Finale emits per-staff voice blocks (1/5/9/13...), Sibelius uses
    1/2; the backend's in-accord grouping (BANA Par. 11) expects
    contiguous 1-based voices. Remap per part, preserving ascending
    order so voice→staff intent and in-accord ordering stay stable.
    Parts already dense (or single-voice ``1``) are left untouched.

    ``<voice>`` appears under ``<note>`` / ``<backup>`` / ``<forward>``;
    ``part.iter("voice")`` rewrites all of them consistently. Numeric
    voices sort by value; any non-numeric voice sorts last by string so
    a malformed score still gets a deterministic, total order.
    """
    for part in root.findall("part"):
        seen = {
            v.text.strip()
            for v in part.iter("voice")
            if v.text and v.text.strip()
        }
        if not seen:
            continue
        ordered = sorted(
            seen,
            # ``isdecimal`` matches int()'s actual domain — ``isdigit``
            # also accepts circled / superscript digits ("①", "²") that
            # int() rejects, so a malformed <voice> used to raise out of
            # the normalizer's never-raises contract instead of sorting
            # into the non-numeric bucket.
            key=lambda s: (0, int(s), s) if s.isdecimal() else (1, 0, s),
        )
        remap = {old: str(i + 1) for i, old in enumerate(ordered)}
        if all(old == new for old, new in remap.items()):
            continue
        for v in part.iter("voice"):
            key = v.text.strip() if v.text else None
            if key in remap:
                v.text = remap[key]


def _note_part_text(note: ET.Element, tag: str) -> str | None:
    """Stripped text of ``note``'s direct ``<tag>`` child, or ``None`` when
    the child is absent or empty."""
    el = note.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _inherit_chord_member_staff_voice(root: ET.Element) -> None:
    """Backfill a chord member's missing ``<staff>`` / ``<voice>`` from its
    chord root (``ARCHITECTURE.md`` vendor-dialect table).

    MusicXML lets a chord member (a ``<note>`` carrying ``<chord/>``) omit
    its own ``<staff>`` / ``<voice>`` and inherit the chord root's — Sibelius
    / Finale / MuseScore do this in single-voice blocks. The backend routes
    notes to staff / voice buckets by their *own* ``<staff>`` / ``<voice>``,
    defaulting a missing one to ``"1"``; so a member that omits them while its
    root sits on staff or voice 2 is split off into bucket ``"1"`` — silently
    tearing the chord apart (the root left as a lone note, the member
    orphaned with no root to attach to). Backfill per measure in document
    order so a chord's root and members always land in the same bucket.
    Members that already carry an explicit value are left untouched, so a
    score that spells every member out is unaffected.
    """
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            root_staff: str | None = None
            root_voice: str | None = None
            for note in measure.findall("note"):
                if note.find("chord") is None:
                    # Chord root / standalone note: remember its staff+voice
                    # for any chord members that follow it.
                    root_staff = _note_part_text(note, "staff")
                    root_voice = _note_part_text(note, "voice")
                    continue
                if root_staff is not None and note.find("staff") is None:
                    ET.SubElement(note, "staff").text = root_staff
                if root_voice is not None and note.find("voice") is None:
                    ET.SubElement(note, "voice").text = root_voice


# MusicXML ``<type>`` → its quarter-note ratio as (numerator, denominator):
# an un-dotted note of this type lasts ``divisions * num / den`` units.
_TYPE_QUARTER_RATIO: dict[str, tuple[int, int]] = {
    "breve": (8, 1),
    "whole": (4, 1),
    "half": (2, 1),
    "quarter": (1, 1),
    "eighth": (1, 2),
    "16th": (1, 4),
    "32nd": (1, 8),
    "64th": (1, 16),
    "128th": (1, 32),
    "256th": (1, 64),
}


def _duration_to_type(duration: int, divisions: int) -> str | None:
    """Recover a ``<type>`` name from a ``<duration>`` (in divisions)
    when the note is a plain power-of-two value.

    Returns ``None`` when the duration doesn't land exactly on an
    un-dotted type — dotted notes, tuplets and odd divisions stay
    ambiguous, and the caller warns rather than guess a wrong shape.
    Integer cross-multiply (``duration * den == divisions * num``)
    sidesteps float rounding.
    """
    if duration <= 0 or divisions <= 0:
        return None
    for type_name, (num, den) in _TYPE_QUARTER_RATIO.items():
        if duration * den == divisions * num:
            return type_name
    return None


def _infer_missing_note_types(
    root: ET.Element, ctx: MusicContext | None = None
) -> None:
    """Fill a ``<note>``'s missing ``<type>`` from its ``<duration>``
    and the prevailing ``<divisions>`` (``ARCHITECTURE.md``).

    Old / minimal exporters omit ``<type>``, but the backend needs it
    to pick a note shape. ``<divisions>`` (declared in ``<attributes>``,
    possibly re-declared mid-part) sets the unit and carries forward
    until the next declaration. Grace notes (no ``<duration>``) are
    skipped; a duration that doesn't map to a plain type emits
    ``MUSIC_DURATION_AMBIGUOUS`` and is left untouched — we never guess
    a shape, the backend's own fallback handles it. The recovered
    ``<type>`` is appended (the backend reads by tag, order-independent).
    """
    for part in root.findall("part"):
        divisions: int | None = None
        for measure in part.findall("measure"):
            # Walk children in document order so a mid-measure
            # <attributes>/<divisions> re-declaration governs the notes that
            # follow it. ``findtext`` would only ever see the first one.
            for child in measure:
                if child.tag == "attributes":
                    div_text = child.findtext("divisions")
                    # isdecimal, not isdigit: isdigit also accepts circled /
                    # superscript digits ("①", "²") that int() rejects, which
                    # would raise ValueError straight out of the normalizer's
                    # never-raises contract and degrade the whole score. Matches
                    # the isdecimal guard in _normalize_voice_numbers.
                    if div_text and div_text.strip().isdecimal():
                        divisions = int(div_text.strip())
                    continue
                if child.tag != "note":
                    continue
                note = child
                if note.find("type") is not None:
                    continue
                dur_text = note.findtext("duration")
                # isdecimal, not isdigit — see the divisions note above; a
                # non-decimal numeric (superscript / circled digit) must skip
                # inference rather than raise out of the never-raises contract.
                if not (dur_text and dur_text.strip().isdecimal()):
                    continue  # grace note / malformed — nothing to infer
                if divisions is None:
                    continue  # no unit yet
                duration = int(dur_text.strip())
                type_name = _duration_to_type(duration, divisions)
                if type_name is None:
                    if ctx is not None:
                        ctx.warnings.warn(
                            code="MUSIC_DURATION_AMBIGUOUS",
                            message=(
                                f"<note> without <type>: duration={duration} "
                                f"divisions={divisions} is not a plain note "
                                f"value (dotted / tuplet?); left to backend "
                                f"fallback"
                            ),
                            source="frontend.music",
                        )
                    continue
                ET.SubElement(note, "type").text = type_name
