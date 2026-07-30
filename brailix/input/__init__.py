"""Input layer: document source → :class:`DocumentIR`.

Each adapter parses one source format (plain text, Markdown, Word
``.docx``, MusicXML, ...) and produces a
:class:`~brailix.ir.document.DocumentIR` with block-level structure
populated. Inline content stays as raw ``Block.text`` until the
Pipeline's frontend runs over it.

Embedded foreign math / music sources follow one boundary rule
(ARCHITECTURE#arch-layers). A **text** dialect (Word OMML / EQ field, LaTeX, ABC)
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
from brailix.input.limits import (
    DEFAULT_INPUT_LIMITS,
    InputLimits,
    InputTooLargeError,
)
from brailix.input.markdown import parse_markdown
from brailix.input.music_xml import (
    BINARY_SCORE_SUFFIXES as _BINARY_SCORE_SUFFIXES,
)
from brailix.input.music_xml import (
    DEFERRED_SCORE_SUFFIXES as _DEFERRED_SCORE_SUFFIXES,
)
from brailix.input.music_xml import (
    MUSIC_SUFFIXES as _MUSIC_SUFFIXES_ALL,
)
from brailix.input.music_xml import (
    _score_document,
    parse_deferred_score,
    parse_musicxml,
    parse_score_file,
)
from brailix.input.plain import parse_plain
from brailix.ir.document import DocumentIR as _DocumentIR

__all__ = (
    "parse_plain",
    "parse_markdown",
    "parse_docx",
    "parse_doc",
    "parse_musicxml",
    "parse_score_file",
    "parse_deferred_score",
    "parse_file",
    "InputLimits",
    "InputTooLargeError",
    "DEFAULT_INPUT_LIMITS",
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
_MUSIC_SUFFIXES = _MUSIC_SUFFIXES_ALL - _SNIFFED_XML_SUFFIXES


# MusicXML's two root elements; element names are lowercase per the schema.
_MUSICXML_ROOTS = frozenset({"score-partwise", "score-timewise"})


def _skip_quoted(text: str, i: int) -> int:
    """Index just past the string literal opening at ``text[i]`` (a quote),
    or the end of the text when it is never closed."""
    end = text.find(text[i], i + 1)
    return len(text) if end < 0 else end + 1


def _skip_internal_subset(text: str, i: int) -> int:
    """Index just past the ``]`` closing the internal DTD subset that opens at
    ``text[i] == "["``.

    Not ``text.find("]")``: a subset is markup, and a ``]`` inside one of its
    declarations closes nothing. It can sit in an attribute default
    (``<!ATTLIST part id CDATA "a]b">``), in an entity value, or in a comment
    — and taking the first one for the end of the subset then leaves the scan
    inside the DOCTYPE, where the next quoted ``>`` reads as the end of the
    declaration and the root element is never seen. So quotes, comments and
    processing instructions are skipped whole, and bracket depth is counted
    (a ``<![INCLUDE[ … ]]>`` conditional section nests one level).
    """
    n = len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if ch in "\"'":
            i = _skip_quoted(text, i)
        elif text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = n if end < 0 else end + 3
        elif text.startswith("<?", i):
            end = text.find("?>", i + 2)
            i = n if end < 0 else end + 2
        elif ch == "[":
            depth += 1
            i += 1
        elif ch == "]":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return n


def _xml_root_element(text: str) -> str:
    """The name of ``text``'s first start element, or ``""``.

    A deliberately small hand-written prologue scanner rather than a real
    parser. Everything before the root element is skippable — whitespace, the
    ``<?xml?>`` declaration and other processing instructions, comments, and a
    ``<!DOCTYPE …>`` whose internal ``[…]`` subset may itself contain ``>`` or
    ``]`` inside quotes and comments (:func:`_skip_internal_subset`) — and once
    the first ``<name`` is reached there is nothing left to decide. Feeding the
    document to :mod:`xml.etree` instead would expand entities declared in that
    internal subset before this function ever returned, which is a parser to
    point at untrusted input only deliberately; this reads the head and stops.

    A namespace prefix is dropped (``<mx:score-partwise>`` reports
    ``score-partwise``). MusicXML is not namespaced, so this only makes the
    answer harder to get wrong.
    """
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != "<":
            return ""
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            if end < 0:
                return ""
            i = end + 3
        elif text.startswith("<?", i):
            end = text.find("?>", i + 2)
            if end < 0:
                return ""
            i = end + 2
        elif text.startswith("<!", i):
            # DOCTYPE (or any other markup declaration): find its closing
            # ``>``, skipping over quoted strings — a public identifier can
            # contain one — and over a whole internal subset if present.
            i += 2
            while i < n and text[i] != ">":
                if text[i] in "\"'":
                    i = _skip_quoted(text, i)
                elif text[i] == "[":
                    i = _skip_internal_subset(text, i)
                else:
                    i += 1
            i += 1
        else:
            start = i + 1
            j = start
            while j < n and not (text[j].isspace() or text[j] in "/>"):
                j += 1
            name = text[start:j]
            return name.rsplit(":", 1)[-1]
    return ""


def _looks_like_musicxml(text: str) -> bool:
    """True if ``text`` opens a MusicXML score document.

    Decided by the **root element** (:func:`_xml_root_element`), not by
    whether the two score tag names appear somewhere near the top. The
    substring test this replaces was wrong in both directions: a plain XML
    document mentioning ``<score-partwise`` in a comment, a CDATA section or a
    DTD was routed to the music adapter, while a genuine score whose
    declaration, comments or internal DTD subset ran past the 4096-character
    window it looked at was handed to the plain-text parser instead.
    """
    return _xml_root_element(text) in _MUSICXML_ROOTS


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
    # Required, deliberately: a size policy that can be defaulted-in is a
    # policy that can be lost by omission, which is how two routes ended up
    # reading under ``DEFAULT_INPUT_LIMITS`` instead of the caller's ceiling.
    # ``parse_file`` is the only constructor and always passes its own.
    limits: InputLimits
    _text: str | None = field(default=None, init=False, repr=False)

    @property
    def text(self) -> str:
        # The file-byte gate ran in ``parse_file`` before this read, so the
        # wholesale decode is already bounded; apply the decoded-character gate
        # once the text exists (the size the frontend then walks per character).
        if self._text is None:
            # Reads through the caller's own limits (see
            # :meth:`InputLimits.read_bounded_text`): the bytes actually
            # consumed are capped on the handle being read, because the
            # ``stat()`` gate in :func:`parse_file` describes the path at one
            # instant and this opens it again afterwards.
            decoded = self.limits.read_bounded_text(self.path)
            self.limits.check_text_length(decoded)
            self._text = decoded
        return self._text


# Route handlers: each takes a :class:`_FileCtx` and returns a ``DocumentIR``.
# Path-based handlers leave ``ctx.text`` untouched (no UTF-8 read); text-based
# ones consume it.


def _route_docx(ctx: _FileCtx) -> _DocumentIR:
    # ``limits`` rides along so the caller's byte ceiling binds the archive
    # the adapter actually reads, not just the ``stat()`` parse_file did.
    return parse_docx(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        mathtype_fallback=ctx.mathtype_fallback,
        chem_detection=ctx.chem_detection,
        limits=ctx.limits,
    )


def _route_doc(ctx: _FileCtx) -> _DocumentIR:
    return parse_doc(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        chem_detection=ctx.chem_detection,
        limits=ctx.limits,
    )


def _route_musicxml(ctx: _FileCtx) -> _DocumentIR:
    # MusicXML / .mxl → single-block DocumentIR wrapping a ScoreBlock;
    # _populate.populate_music_block later runs the music frontend over it.
    # The adapter reads the path itself (so this stays off ``ctx.text``) and
    # applies the decoded-character gate to the resolved MusicXML.
    return parse_musicxml(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        limits=ctx.limits,
    )


def _route_binary_score(ctx: _FileCtx) -> _DocumentIR:
    # .mid / .midi (binary) → MusicXML via the midi source adapter, eagerly
    # at input (ARCHITECTURE#arch-layers rule 2: the text IR can't carry
    # binary). parse_score_file
    # reads the bytes itself, so this stays a path handler (never UTF-8
    # decoded); the char gate applies to the MusicXML it decodes to.
    return parse_score_file(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        limits=ctx.limits,
    )


def _route_deferred_score(ctx: _FileCtx) -> _DocumentIR:
    # .abc (text) → kept raw, deferred to the frontend
    # (ARCHITECTURE#arch-layers rule 1), exactly
    # like a LaTeX MathBlock. parse_deferred_score reads the text (BOM-aware)
    # and imports no frontend, so no music adapter / extra is touched here.
    # It applies the char gate to that text, so a score suffix is not a way
    # around a tightened ``max_text_chars``.
    return parse_deferred_score(
        ctx.path,
        language=ctx.language,
        profile=ctx.profile,
        limits=ctx.limits,
    )


def _route_xml(ctx: _FileCtx) -> _DocumentIR:
    # Generic .xml: only treat as a score if the head looks like one;
    # otherwise plain text, so a non-score .xml (MathML, DocBook, arbitrary
    # XML) doesn't yield misleading MUSIC_* warnings / an empty score tree.
    #
    # Sniff via the BOM-aware reader parse_musicxml uses, NOT ctx.text's flat
    # utf-8-sig: XML may legitimately be UTF-16 (Finale / some Windows
    # exporters emit a BOM), and utf-8-sig raises UnicodeDecodeError on those
    # valid files before the sniff runs — so a UTF-16 score .xml used to crash
    # where the byte-identical .musicxml parsed fine. Both routes now agree.
    #
    # Through ``ctx.limits``, not a defaulted reader: this route used to call
    # a free function whose ``limits`` parameter defaulted to
    # ``DEFAULT_INPUT_LIMITS``, so a caller's tightened ``max_file_bytes``
    # was silently dropped here and an ``.xml`` replaced after the stat gate
    # was read up to the *default* ceiling instead.
    # ONE read, for both the decision and the document. Sniffing here and then
    # handing ``parse_musicxml`` the *path* opened and decoded the file a
    # second time, so the content that was classified and the content that was
    # parsed were only the same bytes as long as nothing replaced the file in
    # between — the same "preflight and parse must see one snapshot" rule the
    # ``.docx`` route is already held to. Both branches build from this
    # snapshot: ``_score_document`` is the tail of ``parse_musicxml`` for a
    # text-suffix score, which is precisely what the sniff has established.
    text = ctx.limits.read_bounded_text(ctx.path, normalize_newlines=True)
    # This path reads directly (not via ``ctx.text``), so apply the decoded-
    # character gate here too — the file-byte gate already ran in parse_file.
    ctx.limits.check_text_length(text)
    if _looks_like_musicxml(text):
        return _score_document(
            text, language=ctx.language, profile=ctx.profile
        )
    return parse_plain(text, language=ctx.language, profile=ctx.profile)


def _route_markdown(ctx: _FileCtx) -> _DocumentIR:
    return parse_markdown(ctx.text, language=ctx.language, profile=ctx.profile)


def _route_plain(ctx: _FileCtx) -> _DocumentIR:
    return parse_plain(ctx.text, language=ctx.language, profile=ctx.profile)


_Handler = Callable[[_FileCtx], _DocumentIR]

# Suffix → handler routing table — the data that replaces a chain of
# ``if suffix in ...`` branches. Adding a built-in format is one more row plus
# its ``parse_*`` adapter, no new branch. The suffix sets are disjoint, so the
# flattened lookup is unambiguous; an unlisted suffix — and the no-suffix case
# — falls through to :func:`_route_plain` (the default in :func:`parse_file`).
_FORMAT_ROUTES: tuple[tuple[frozenset[str], _Handler], ...] = (
    (_DOCX_SUFFIXES, _route_docx),
    (_DOC_SUFFIXES, _route_doc),
    (_MUSIC_SUFFIXES, _route_musicxml),
    (_BINARY_SCORE_SUFFIXES, _route_binary_score),
    (_DEFERRED_SCORE_SUFFIXES, _route_deferred_score),
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
    limits: InputLimits = DEFAULT_INPUT_LIMITS,
) -> _DocumentIR:
    """Read ``path`` and parse to :class:`DocumentIR` by suffix.

    Dispatch table:

    * ``.md`` / ``.markdown``  → :func:`parse_markdown`
    * ``.docx`` / ``.docm``    → :func:`parse_docx` (modern OOXML;
      requires the ``docx`` extra — ``pip install brailix[docx]``)
    * ``.doc``                 → :func:`parse_doc` (legacy binary;
      requires LibreOffice ``soffice`` on PATH for the
      .doc → .docx conversion)
    * ``.musicxml`` / ``.mxl``  → :func:`parse_musicxml`
    * ``.xml``                 → the same single-block score document
      :func:`parse_musicxml` builds, but only when the document head looks
      like a MusicXML score (``<score-partwise>`` / ``<score-timewise>``);
      otherwise treated as plain text, since ``.xml`` is a generic container.
      The head is read once and both the decision and the document come from
      that one snapshot
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

    ``limits`` is the input size budget (see :class:`InputLimits`), in two
    parts. The file-byte ceiling is a ``stat()`` gate **before** any byte is
    read, so an oversized file is refused without being loaded into memory —
    the guard a service accepting untrusted uploads needs (a multi-GB
    ``.txt`` / ``.mid`` / ``.mxl`` otherwise spikes memory the instant it is
    read). The decoded-character ceiling then bounds the text handed to the
    frontend, and every route that produces text applies it — plain /
    Markdown / ``.xml`` here, and the ``.abc`` / MusicXML / ``.mxl`` /
    ``.mid`` score text inside its own adapter — so no suffix is a way
    around it. The default (:data:`DEFAULT_INPUT_LIMITS`) is deliberately
    generous — far above any realistic document — so a desktop caller opening
    its own files never trips it; a service tightens it, and
    :meth:`InputLimits.unlimited` opts out. The archive-internal caps (a
    ``.mxl`` / ``.docx`` member's decompressed size, the zip-bomb defence)
    are separate and always on, in their adapters.

    Errors propagate as-is: :class:`FileNotFoundError` when ``path``
    doesn't exist, :class:`InputTooLargeError` when its bytes or its decoded
    text exceed ``limits``,
    :class:`UnicodeDecodeError` when text bytes aren't valid UTF-8,
    :class:`MissingExtraError` when a needed extra (``docx`` for Word,
    ``midi`` for MIDI) isn't installed at input time — ``.abc`` defers its
    ``abc`` extra to frontend time, so reading one never raises here —
    :class:`ParseError` for malformed Word documents.
    """
    # Size gate FIRST — a single stat(), no bytes read — so an oversized file
    # is rejected before any adapter loads it into memory. A missing path
    # raises FileNotFoundError here exactly as the read below would.
    limits.check_file_size(path)
    ctx = _FileCtx(
        path=Path(path),
        language=language,
        profile=profile,
        mathtype_fallback=mathtype_fallback,
        chem_detection=chem_detection,
        limits=limits,
    )
    handler = _SUFFIX_ROUTES.get(ctx.path.suffix.lower(), _route_plain)
    return handler(ctx)
