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

from brailix.frontend._xml import strip_namespace, strip_whitespace_text
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
            seen, key=lambda s: (0, int(s)) if s.isdigit() else (1, 0, s)
        )
        remap = {old: str(i + 1) for i, old in enumerate(ordered)}
        if all(old == new for old, new in remap.items()):
            continue
        for v in part.iter("voice"):
            key = v.text.strip() if v.text else None
            if key in remap:
                v.text = remap[key]


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
            div_text = measure.findtext("attributes/divisions")
            if div_text and div_text.strip().isdigit():
                divisions = int(div_text.strip())
            for note in measure.findall("note"):
                if note.find("type") is not None:
                    continue
                dur_text = note.findtext("duration")
                if not (dur_text and dur_text.strip().isdigit()):
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
