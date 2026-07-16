"""M5: lyrics (BANA Tables 31-32) — marker form + inline form (M5.x).

Called from inside :func:`brailix.backend.music.handlers.notes._emit_note`;
not registered with the main dispatch table because MusicXML lyrics are
attached to the note element rather than appearing as siblings.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend._inline import rebase_translated_cells
from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import emit_cells_for_entity
from brailix.ir.braille import BrailleCell


def _emit_lyrics(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """Emit a note's ``<lyric>`` children, dispatched by
    ``music.lyrics_form`` (gated by ``music.show_lyrics``):

    * ``"marker"`` (default) — one Table 22 ``word_sign`` per ``<lyric>``,
      full metadata in ``source_text`` for proofread UIs.
    * ``"inline"`` (M5.x) — the real syllable characters, translated
      through the injected zh / latin text path (:func:`_emit_lyrics_inline`).
    * ``"below_score"`` / ``"interleaved"`` — the full BANA double-row
      layout; needs renderer cooperation, not wired yet → warn + fall
      back to ``marker``.
    """
    if not mctx.profile.feature("music.show_lyrics", True):
        return
    form = mctx.profile.feature("music.lyrics_form", "marker")
    if form == "inline":
        _emit_lyrics_inline(cells, mctx, elem)
        return
    if form != "marker":
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"music.lyrics_form={form!r} needs renderer support "
                f"(handled: 'marker' / 'inline'); falling back to marker"
            ),
            source="backend.music",
        )
    _emit_lyrics_marker(cells, mctx, elem)


def _emit_lyrics_marker(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """marker form: one ``word_sign`` cell (Table 22 C, ``>`` = dots
    3,4,5) per ``<lyric>``; the full lyric metadata
    (``"lyric[<verse>/<syllabic>]:<text>"``) goes in ``source_text`` so
    a proofread tool can render the words alongside while the cell stream
    stays well-formed for downstream renderers."""
    for lyric in elem.findall("lyric"):
        text = _lyric_text(lyric)
        if text is None:
            continue
        syllabic_el = lyric.find("syllabic")
        syllabic = (
            (syllabic_el.text or "").strip().lower()
            if syllabic_el is not None
            else "single"
        )
        verse = lyric.attrib.get("number", "1").strip() or "1"
        emit_cells_for_entity(
            cells, mctx,
            topic="nuances", entity="word_sign",
            role="music_lyric_marker",
            source_text=f"lyric[{verse}/{syllabic}]:{text}",
        )


def _emit_lyrics_inline(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    elem: ET.Element,
) -> None:
    """inline form (M5.x): emit the real syllable characters.

    Each ``<lyric>``'s text is translated through the injected
    ``inline_text_translator`` (the zh / latin text path the pipeline
    wires onto ``BackendContext.options``) and the resulting cells are
    retagged ``music_lyric`` so a renderer / proofread tool can still tell
    lyric cells from music cells. With no translator wired (bare backend
    or unit test) we can't produce characters → warn and fall back to
    the marker form.

    Deferred: syllabic hyphenation across ``begin`` / ``middle`` /
    ``end`` syllables, and the BANA word-line vs music-line split
    (renderer layout). This pass just gets real characters into the
    stream so a single-verse song reads as words, not markers.
    """
    translator = mctx.backend.inline_text_translator(
        domain="music_lyrics", span=mctx.span
    )
    if translator is None:
        mctx.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                "music.lyrics_form='inline' needs the inline_text_translator "
                "(zh / latin frontend); none wired, falling back to marker"
            ),
            source="backend.music",
        )
        _emit_lyrics_marker(cells, mctx, elem)
        return
    for lyric in elem.findall("lyric"):
        text = _lyric_text(lyric)
        if text is None:
            continue
        # Retag to music_lyric AND rebase the spans — the translator's
        # cells carry throwaway-document coordinates.
        cells.extend(
            rebase_translated_cells(
                translator(text), mctx.span, role="music_lyric"
            )
        )


def _lyric_text(lyric: ET.Element) -> str | None:
    """The ``<lyric>``'s syllable text, or None when absent / blank
    (exporters often emit placeholder ``<lyric><text/></lyric>`` for
    un-set syllables — skip those silently, no warning)."""
    text_el = lyric.find("text")
    if text_el is None:
        return None
    text = (text_el.text or "").strip()
    return text or None
