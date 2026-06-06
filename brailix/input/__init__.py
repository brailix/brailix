"""Input layer: document source → :class:`DocumentIR`.

Each adapter parses one source format (plain text, Markdown, LaTeX,
MathML, HTML, ...) and produces a :class:`~brailix.ir.document.DocumentIR`
with block-level structure populated. Inline content stays as raw
``Block.text`` until the Pipeline's frontend runs over it.

Currently shipping:

* :mod:`brailix.input.plain`    — one paragraph from a string.
* :mod:`brailix.input.markdown` — common Markdown subset
  (headings, paragraphs, ordered / unordered lists, block quotes,
  fenced code blocks, ``$$...$$`` math blocks, ``| col | col |`` tables).
* :mod:`brailix.input.docx`     — Word ``.docx`` / ``.docm`` (modern
  OOXML, incl. OMML / MathType / Equation 3.0 math) and legacy ``.doc``
  via LibreOffice ``soffice``.
* :mod:`brailix.input.music_xml` — score files: ``.musicxml`` / ``.xml``
  / ``.mxl`` directly, and ``.mid`` / ``.midi`` / ``.abc`` converted to
  MusicXML through the matching music source adapter.

To plug in a new format, write an adapter that returns a
``DocumentIR`` and (optionally) register it through whatever
discovery mechanism your application uses — the input layer doesn't
maintain a registry because the choice is usually static (file
extension or MIME type).

:func:`parse_file` is the one piece of suffix dispatch the input
layer keeps in-house, so GUIs / CLIs / scripts don't each reinvent
``read_text + pick parser``.
"""

from __future__ import annotations

import os
from pathlib import Path

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.input.docx import parse_doc, parse_docx
from brailix.input.markdown import parse_markdown
from brailix.input.music_xml import (
    ADAPTER_SCORE_SUFFIXES,
    parse_musicxml,
    parse_score_file,
)
from brailix.input.plain import parse_plain
from brailix.ir.document import DocumentIR

__all__ = (
    "parse_plain",
    "parse_markdown",
    "parse_docx",
    "parse_doc",
    "parse_musicxml",
    "parse_score_file",
    "parse_file",
)


_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_DOCX_SUFFIXES = frozenset({".docx", ".docm"})
_DOC_SUFFIXES = frozenset({".doc"})
# ``.musicxml`` / ``.mxl`` are score-only containers — route unconditionally.
# ``.xml`` is a generic container (MathML, DocBook, arbitrary XML), so it is
# sniffed (see ``_looks_like_musicxml``) before being handed to the music
# adapter; non-score ``.xml`` falls back to plain text instead of producing
# misleading MUSIC_* warnings / an empty score tree.
_MUSIC_SUFFIXES = frozenset({".musicxml", ".mxl"})
_SNIFFED_XML_SUFFIXES = frozenset({".xml"})


def _looks_like_musicxml(text: str) -> bool:
    """True if ``text`` opens a MusicXML score document.

    MusicXML's root element (after an optional ``<?xml?>`` / DOCTYPE) is
    ``<score-partwise>`` or ``<score-timewise>``; element names are
    lowercase per the schema. Only the document head is inspected so a
    large non-score XML file isn't fully scanned.
    """
    head = text[:4096]
    return "<score-partwise" in head or "<score-timewise" in head


def parse_file(
    path: str | os.PathLike[str],
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Read ``path`` and parse to :class:`DocumentIR` by suffix.

    Dispatch table:

    * ``.md`` / ``.markdown``  → :func:`parse_markdown`
    * ``.docx`` / ``.docm``    → :func:`parse_docx` (modern OOXML;
      requires the ``docx`` extra — ``pip install brailix[docx]``)
    * ``.doc``                 → :func:`parse_doc` (legacy binary;
      requires LibreOffice ``soffice`` on PATH for the
      .doc → .docx conversion)
    * ``.musicxml`` / ``.mxl``  → :func:`parse_musicxml`
    * ``.xml``                 → :func:`parse_musicxml` only when the
      document head looks like a MusicXML score
      (``<score-partwise>`` / ``<score-timewise>``); otherwise treated
      as plain text, since ``.xml`` is a generic container
    * ``.mid`` / ``.midi`` / ``.abc`` → :func:`parse_score_file`
      (converted to MusicXML through the matching music source adapter;
      ``.mid`` / ``.midi`` need the ``midi`` extra, ``.abc`` the ``abc``
      extra)
    * everything else (including ``.txt`` and no suffix) → :func:`parse_plain`

    Word formats are read as bytes by the underlying adapters; text
    formats are read here as UTF-8 so the dispatch can hand the parsers
    a ``str``. Callers wanting a non-default mapping (feeding a ``.tex``
    file through the markdown parser, say) should call the underlying
    ``parse_*`` directly after reading the file themselves.

    Errors propagate as-is: :class:`FileNotFoundError` when ``path``
    doesn't exist, :class:`UnicodeDecodeError` when text bytes aren't
    valid UTF-8, :class:`MissingExtraError` when a needed extra (``docx``
    for Word, ``midi`` / ``abc`` for those score formats) isn't
    installed, :class:`ParseError` for malformed Word documents.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _DOCX_SUFFIXES:
        return parse_docx(p, language=language, profile=profile)
    if suffix in _DOC_SUFFIXES:
        return parse_doc(p, language=language, profile=profile)
    if suffix in _MUSIC_SUFFIXES:
        # Music files (MusicXML / .mxl) go through the music input
        # adapter — produces a single-block DocumentIR wrapping a
        # ScoreBlock. Pipeline's _populate_music_block then runs the
        # music frontend to parse the XML into a MusicInline tree.
        return parse_musicxml(p, language=language, profile=profile)
    if suffix in ADAPTER_SCORE_SUFFIXES:
        # Score formats that need a source adapter to reach MusicXML
        # (.mid / .midi as bytes, .abc as text). Routed before the UTF-8
        # read below because MIDI is binary; parse_score_file reads the
        # file itself with the right mode and runs the adapter.
        return parse_score_file(p, language=language, profile=profile)
    text = p.read_text(encoding="utf-8")
    if suffix in _SNIFFED_XML_SUFFIXES:
        # Generic .xml: only treat as a score if it actually looks like one;
        # otherwise fall through to plain text.
        if _looks_like_musicxml(text):
            return parse_musicxml(p, language=language, profile=profile)
        return parse_plain(text, language=language, profile=profile)
    if suffix in _MARKDOWN_SUFFIXES:
        return parse_markdown(text, language=language, profile=profile)
    return parse_plain(text, language=language, profile=profile)
