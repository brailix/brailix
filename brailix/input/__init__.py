"""Input layer: document source → :class:`DocumentIR`.

Each adapter parses one source format (plain text, Markdown, Word
``.docx``, MusicXML, ...) and produces a
:class:`~brailix.ir.document.DocumentIR` with block-level structure
populated. Inline content stays as raw ``Block.text`` until the
Pipeline's frontend runs over it.

Embedded foreign math / music sources follow one boundary rule
(ARCHITECTURE §1). A **text** dialect (Word OMML / EQ field, LaTeX, ABC)
is left raw and deferred to the frontend — inline ones travel as a
source-tagged island (:mod:`brailix.core.inline_math`) inside
``Block.text``, block ones as ``MathBlock(source=...)`` /
``ScoreBlock(source=...)``. A **binary** dialect (MathType MTEF, MIDI,
the ``.mxl`` ZIP) is decoded here at the input boundary, because the text
IR can't carry binary. So this layer imports no math / music *frontend*
for the text dialects; only the binary decoders reach across.

Currently shipping:

* :mod:`brailix.input.plain`    — one paragraph from a string.
* :mod:`brailix.input.markdown` — common Markdown subset
  (headings, paragraphs, ordered / unordered lists, block quotes,
  fenced code blocks, ``$$...$$`` math blocks, ``| col | col |`` tables).
* :mod:`brailix.input.docx`     — Word ``.docx`` / ``.docm`` (modern
  OOXML, incl. OMML / MathType / Equation 3.0 math) and legacy ``.doc``
  via LibreOffice ``soffice``.
* :mod:`brailix.input.music_xml` — score files: ``.musicxml`` / ``.xml``
  / ``.mxl`` directly, ``.mid`` / ``.midi`` decoded to MusicXML at input
  (binary), and ``.abc`` kept raw and deferred to the frontend (text).

To plug in a new format, write an adapter that returns a
``DocumentIR``. Which adapter handles a given file is driven by the
file itself (extension / content), not by the profile — so, unlike
the profile-selected subsystems (zh analyzer, pinyin, math / music
source), this layer keeps no name→implementation registry. Discovery
of *which* formats an application offers (file-dialog filters,
fallback rules, third-party adapters) is an application concern and
lives there: an application can wrap these functions as registered
adapters behind its own registry.

:func:`parse_file` is the in-house convenience dispatcher, so
GUIs / CLIs / scripts don't each reinvent ``read_text + pick parser``.
Its routing is a **data table** (:data:`_FORMAT_ROUTES`) mapping a
suffix set to a handler — adding a built-in format is one more row
plus its ``parse_*`` adapter, not a new branch.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from brailix.input.docx import parse_doc, parse_docx
from brailix.input.markdown import parse_markdown
from brailix.input.music_xml import (
    BINARY_SCORE_SUFFIXES,
    DEFERRED_SCORE_SUFFIXES,
    MUSIC_SUFFIXES,
    _read_xml_text,
    parse_deferred_score,
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
    "parse_deferred_score",
    "parse_file",
)


_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_DOCX_SUFFIXES = frozenset({".docx", ".docm"})
_DOC_SUFFIXES = frozenset({".doc"})
# ``.xml`` is a generic container (MathML, DocBook, arbitrary XML), so it is
# sniffed (see ``_looks_like_musicxml``) before being handed to the music
# adapter; non-score ``.xml`` falls back to plain text instead of producing
# misleading MUSIC_* warnings / an empty score tree.
_SNIFFED_XML_SUFFIXES = frozenset({".xml"})
# ``.musicxml`` / ``.mxl`` are score-only containers — route unconditionally.
# Derived from the music adapter's own suffix set (the single source of truth)
# minus the sniffed generic ``.xml`` container, so a new MusicXML-family suffix
# added there flows through here automatically instead of needing a second
# hand-maintained literal that could silently drift.
_MUSIC_SUFFIXES = MUSIC_SUFFIXES - _SNIFFED_XML_SUFFIXES


def _looks_like_musicxml(text: str) -> bool:
    """True if ``text`` opens a MusicXML score document.

    MusicXML's root element (after an optional ``<?xml?>`` / DOCTYPE) is
    ``<score-partwise>`` or ``<score-timewise>``; element names are
    lowercase per the schema. Only the document head is inspected so a
    large non-score XML file isn't fully scanned.
    """
    head = text[:4096]
    return "<score-partwise" in head or "<score-timewise" in head


@dataclass
class _FileCtx:
    """Everything a route handler needs to parse one file.

    ``text`` is read lazily (and cached) so a handler that consumes the
    path directly — every binary format (``.docx`` / ``.mid`` / ``.mxl``
    / ...) — never decodes the file as UTF-8. Text formats read it once.
    """

    path: Path
    language: str
    profile: str
    mathtype_fallback: str
    chem_detection: bool
    _text: str | None = field(default=None, init=False, repr=False)

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = _decode_text(self.path)
        return self._text


def _decode_text(path: Path) -> str:
    """Decode a text file, tolerating the UTF-16 BOM Windows Notepad writes.

    Notepad's "save as .txt" historically emits UTF-16 LE with a BOM, and
    ``.md`` / ``.txt`` files from non-technical users are exactly that —
    the same UTF-16 reality the ``.xml`` route already handles via
    :func:`_read_xml_text`. ``utf-8-sig`` alone raises UnicodeDecodeError on
    those valid files. Sniff the UTF-16 BOM (the codec reads it for
    endianness); otherwise fall back to ``utf-8-sig`` (which strips a UTF-8
    BOM so a Markdown heading on line one still matches, else behaves like
    utf-8). Genuinely non-UTF-8/16 bytes still raise.
    """
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


# Route handlers: each takes a :class:`_FileCtx` and returns a ``DocumentIR``.
# Path-based handlers leave ``ctx.text`` untouched (no UTF-8 read); text-based
# ones consume it.


def _route_docx(ctx: _FileCtx) -> DocumentIR:
    return parse_docx(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        mathtype_fallback=ctx.mathtype_fallback,
        chem_detection=ctx.chem_detection,
    )


def _route_doc(ctx: _FileCtx) -> DocumentIR:
    return parse_doc(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        chem_detection=ctx.chem_detection,
    )


def _route_musicxml(ctx: _FileCtx) -> DocumentIR:
    # MusicXML / .mxl → single-block DocumentIR wrapping a ScoreBlock;
    # FrontendDriver._populate_music_block later runs the music frontend over it.
    return parse_musicxml(ctx.path, language=ctx.language, profile=ctx.profile)


def _route_binary_score(ctx: _FileCtx) -> DocumentIR:
    # .mid / .midi (binary) → MusicXML via the midi source adapter, eagerly
    # at input (§1 rule 2: the text IR can't carry binary). parse_score_file
    # reads the bytes itself, so this stays a path handler (never UTF-8
    # decoded).
    return parse_score_file(ctx.path, language=ctx.language, profile=ctx.profile)


def _route_deferred_score(ctx: _FileCtx) -> DocumentIR:
    # .abc (text) → kept raw, deferred to the frontend (§1 rule 1), exactly
    # like a LaTeX MathBlock. parse_deferred_score reads the text (BOM-aware)
    # and imports no frontend, so no music adapter / extra is touched here.
    return parse_deferred_score(
        ctx.path, language=ctx.language, profile=ctx.profile
    )


def _route_xml(ctx: _FileCtx) -> DocumentIR:
    # Generic .xml: only treat as a score if the head looks like one;
    # otherwise plain text, so a non-score .xml (MathML, DocBook, arbitrary
    # XML) doesn't yield misleading MUSIC_* warnings / an empty score tree.
    #
    # Sniff via the BOM-aware reader parse_musicxml uses, NOT ctx.text's flat
    # utf-8-sig: XML may legitimately be UTF-16 (Finale / some Windows
    # exporters emit a BOM), and utf-8-sig raises UnicodeDecodeError on those
    # valid files before the sniff runs — so a UTF-16 score .xml used to crash
    # where the byte-identical .musicxml parsed fine. Both routes now agree.
    text = _read_xml_text(ctx.path)
    if _looks_like_musicxml(text):
        return parse_musicxml(ctx.path, language=ctx.language, profile=ctx.profile)
    return parse_plain(text, language=ctx.language, profile=ctx.profile)


def _route_markdown(ctx: _FileCtx) -> DocumentIR:
    return parse_markdown(ctx.text, language=ctx.language, profile=ctx.profile)


def _route_plain(ctx: _FileCtx) -> DocumentIR:
    return parse_plain(ctx.text, language=ctx.language, profile=ctx.profile)


_Handler = Callable[[_FileCtx], DocumentIR]

# Suffix → handler routing table — the data that replaces a chain of
# ``if suffix in ...`` branches. Adding a built-in format is one more row plus
# its ``parse_*`` adapter, no new branch. The suffix sets are disjoint, so the
# flattened lookup is unambiguous; an unlisted suffix — and the no-suffix case
# — falls through to :func:`_route_plain` (the default in :func:`parse_file`).
_FORMAT_ROUTES: tuple[tuple[frozenset[str], _Handler], ...] = (
    (_DOCX_SUFFIXES, _route_docx),
    (_DOC_SUFFIXES, _route_doc),
    (_MUSIC_SUFFIXES, _route_musicxml),
    (BINARY_SCORE_SUFFIXES, _route_binary_score),
    (DEFERRED_SCORE_SUFFIXES, _route_deferred_score),
    (_SNIFFED_XML_SUFFIXES, _route_xml),
    (_MARKDOWN_SUFFIXES, _route_markdown),
)
_SUFFIX_ROUTES: dict[str, _Handler] = {
    suffix: handler for suffixes, handler in _FORMAT_ROUTES for suffix in suffixes
}


def parse_file(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    mathtype_fallback: str = "off",
    chem_detection: bool = False,
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
    * ``.mid`` / ``.midi`` → :func:`parse_score_file` (binary, decoded to
      MusicXML at input; needs the ``midi`` extra)
    * ``.abc`` → :func:`parse_deferred_score` (text, kept raw and deferred
      to the frontend; the ``abc`` extra is needed at frontend time, not
      here)
    * everything else (including ``.txt`` and no suffix) → :func:`parse_plain`

    Word formats are read as bytes by the underlying adapters; text
    formats are read here as UTF-8 so the dispatch can hand the parsers
    a ``str``. Callers wanting a non-default mapping (feeding a ``.tex``
    file through the markdown parser, say) should call the underlying
    ``parse_*`` directly after reading the file themselves.

    ``mathtype_fallback`` is forwarded to :func:`parse_docx` for ``.docx`` /
    ``.docm`` (ignored for every other format, the same way
    ``chem_detection`` is). It defaults to ``"off"`` — the native MTEF
    adapter only, so old MTEF files it can't decode come back as
    ``<merror>`` placeholders. Pass ``"auto"`` (or ``"libreoffice"``) to
    engage the LibreOffice safety net, where the document is re-parsed
    through ``soffice`` so the math becomes readable. The default stays
    ``"off"`` so this convenience dispatch never shells out to an external
    converter implicitly; :meth:`brailix.pipeline.Pipeline.parse_file`
    drives the value from the ``input.docx.mathtype_fallback`` profile
    feature.

    Errors propagate as-is: :class:`FileNotFoundError` when ``path``
    doesn't exist, :class:`UnicodeDecodeError` when text bytes aren't
    valid UTF-8, :class:`MissingExtraError` when a needed extra (``docx``
    for Word, ``midi`` for MIDI) isn't installed at input time — ``.abc``
    defers its ``abc`` extra to frontend time, so reading one never raises
    here — :class:`ParseError` for malformed Word documents.
    """
    ctx = _FileCtx(
        path=Path(path),
        language=language,
        profile=profile,
        mathtype_fallback=mathtype_fallback,
        chem_detection=chem_detection,
    )
    handler = _SUFFIX_ROUTES.get(ctx.path.suffix.lower(), _route_plain)
    return handler(ctx)
