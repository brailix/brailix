"""Music score file input adapters.

Read a score file from disk and wrap it as a single-block
:class:`DocumentIR` carrying a :class:`ScoreBlock`. Three entry points,
split by how — and *when* — the source reaches MusicXML (ARCHITECTURE §1,
the input/frontend payload-shape boundary):

:func:`parse_musicxml` — the MusicXML family (no source adapter needed):

* ``.musicxml`` / ``.xml`` → read UTF-8/UTF-16 text, ``source="musicxml"``
* ``.mxl``                → ZIP container (binary), unzipped eagerly via the
  existing frontend
  :class:`~brailix.frontend.music.adapters.mxl.MxlSourceAdapter` to extract
  the inner XML, then ``source="musicxml"`` (the decompressed text is plain
  MusicXML so the backend doesn't need to re-unzip later).

:func:`parse_score_file` — *binary* dialects decoded eagerly at the input
boundary, because the text IR can't carry binary bytes (§1 rule 2, the same
exception ``.mxl`` / MTEF take):

* ``.mid`` / ``.midi`` → MIDI bytes converted through the ``midi`` adapter
  (needs the ``midi`` extra); ``source`` normalised to ``"musicxml"``.

:func:`parse_deferred_score` — *text* dialects kept **raw** and deferred to
the frontend (§1 rule 1), exactly as ``MathBlock(source="latex")`` defers
LaTeX; the input layer imports no frontend for these:

* ``.abc`` → stored verbatim as ``ScoreBlock(source="abc")``;
  ``_populate.populate_music_block`` runs the ``abc`` adapter later,
  where a missing ``abc`` extra soft-fails instead of raising at read time.

Neither opens .sib / .musx / .dorico / .mscz — proprietary formats stay
outside brailix per ``ARCHITECTURE.md``
"""

from __future__ import annotations

import os
from pathlib import Path

from brailix.core.context import MusicContext
from brailix.ir.document import DocumentIR, ScoreBlock

_MUSICXML_TEXT_SUFFIXES = frozenset({".musicxml", ".xml"})
_MXL_SUFFIXES = frozenset({".mxl"})

MUSIC_SUFFIXES = _MUSICXML_TEXT_SUFFIXES | _MXL_SUFFIXES

# Binary score dialects: decoded eagerly at the input boundary because the
# text IR can't carry binary bytes (ARCHITECTURE §1 rule 2 — the same
# exception MTEF and the ``.mxl`` ZIP take). Suffix → music source name;
# kept as data so a new binary score format is one more entry plus its
# registered adapter — no new branch (ARCHITECTURE.md, adapter pattern).
_BINARY_SCORE_SOURCES: dict[str, str] = {
    ".mid": "midi",
    ".midi": "midi",
}
BINARY_SCORE_SUFFIXES = frozenset(_BINARY_SCORE_SOURCES)

# Text score dialects: kept RAW at input and deferred to the frontend
# (ARCHITECTURE §1 rule 1), exactly as ``MathBlock(source="latex")`` defers
# LaTeX. ABC is UTF-8 text, so it fits the text IR and rides the
# defer-to-frontend seam rather than the binary eager path — the input layer
# holds no frontend import for it. Suffix → music source name (the block's
# ``source``, which the frontend later hands to ``music_source_registry``).
_DEFERRED_SCORE_SOURCES: dict[str, str] = {
    ".abc": "abc",
}
DEFERRED_SCORE_SUFFIXES = frozenset(_DEFERRED_SCORE_SOURCES)


def _read_xml_text(p: Path) -> str:
    """Read a MusicXML / XML text file, honouring a UTF-16 BOM.

    XML may legitimately be encoded UTF-16 — Finale and some Windows exporters
    write it with a byte-order mark — and a flat ``utf-8-sig`` read raises
    ``UnicodeDecodeError`` on those valid files. Detect the UTF-16 BOM and
    decode accordingly; otherwise ``utf-8-sig`` (strips a UTF-8 BOM, still
    raises on genuinely invalid UTF-8 — the documented contract).
    """
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    # Normalise line endings the way a text-mode read (universal newlines)
    # does, so a CRLF source reads identically to an LF one downstream.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_musicxml(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
) -> DocumentIR:
    """Read a MusicXML / .mxl file and return a single-block
    :class:`DocumentIR`.

    Suffix dispatch handles ``.musicxml`` / ``.xml`` (UTF-8/UTF-16 text) and
    ``.mxl`` (ZIP container). Both produce a ``ScoreBlock`` whose
    ``text`` is the resolved MusicXML string and ``source`` is
    ``"musicxml"`` — the inner XML carries no compression by the time
    it lands in the block.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for unrecognised suffixes,
    :class:`UnicodeDecodeError` if a ``.musicxml`` file's bytes are
    neither valid UTF-8 nor UTF-16-BOM-prefixed.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _MXL_SUFFIXES:
        text = _unzip_mxl(p.read_bytes(), profile=profile)
    elif suffix in _MUSICXML_TEXT_SUFFIXES:
        # Honour a UTF-16 BOM (Finale / Windows exporters) and strip a UTF-8
        # BOM; a surviving BOM would break the score sniff / XML parse.
        text = _read_xml_text(p)
    else:
        raise ValueError(
            f"unsupported music file extension {suffix!r} "
            f"(expected .musicxml / .xml / .mxl)"
        )

    block = ScoreBlock(text=text, source="musicxml")
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[block],
    )


def parse_score_file(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
) -> DocumentIR:
    """Read a *binary* score file (``.mid`` / ``.midi``) and eagerly decode
    it to MusicXML at the input boundary.

    Binary dialects are the deliberate §1-rule-2 exception: the text IR
    can't carry raw bytes, so the matching music source adapter (the
    ``midi`` adapter, needing the ``midi`` extra) runs here at input time —
    the same strategy :func:`parse_musicxml` uses for ``.mxl``. The result
    is wrapped as a ``ScoreBlock`` whose ``source`` is normalised to
    ``"musicxml"``; by the time the block lands, its ``text`` is plain
    MusicXML, so the rest of the pipeline treats it exactly like a MusicXML
    file. A malformed source comes back as a ``<music-error>`` placeholder
    per the music subsystem's soft-failure contract.

    Text dialects (``.abc``) do **not** come here — they stay raw and defer
    to the frontend via :func:`parse_deferred_score` (ARCHITECTURE §1 rule
    1), so this function imports the music source registry only for the
    binary-decode exception.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for a suffix this function doesn't handle (use
    :func:`parse_deferred_score` for ``.abc``, :func:`parse_musicxml` for
    the MusicXML family), and
    :class:`~brailix.core.errors.MissingExtraError` when the format's
    optional dependency isn't installed — the message names the extra
    (for example ``pip install brailix[midi]``).
    """
    from brailix.frontend.music.registry import music_source_registry

    p = Path(path)
    suffix = p.suffix.lower()
    source = _BINARY_SCORE_SOURCES.get(suffix)
    if source is None:
        raise ValueError(
            f"unsupported binary score extension {suffix!r} "
            f"(expected {sorted(_BINARY_SCORE_SOURCES)}; "
            f"use parse_deferred_score for {sorted(_DEFERRED_SCORE_SOURCES)}, "
            f"parse_musicxml for .musicxml / .xml / .mxl)"
        )
    # registry.get raises MissingExtraError (naming the extra) when the
    # adapter's optional dependency is absent — surfaced loudly here, the
    # same way parse_docx surfaces a missing ``docx`` extra.
    adapter = music_source_registry.get(source)
    musicxml = adapter.to_musicxml(
        p.read_bytes(), MusicContext(source=source, profile=profile)
    )

    block = ScoreBlock(text=musicxml, source="musicxml")
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[block],
    )


def parse_deferred_score(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
) -> DocumentIR:
    """Read a *text-dialect* score file (``.abc``) and store it **raw**,
    deferring conversion to the frontend.

    ABC is UTF-8 text, so — unlike the binary MIDI path — it fits in the
    text IR and follows ARCHITECTURE §1 rule 1 (text dialects are kept raw
    at input and converted in the frontend), exactly as a
    ``MathBlock(source="latex")`` defers LaTeX. The ``ScoreBlock`` carries
    the raw source with ``source`` set to the dialect name (``"abc"``); the
    matching music source adapter runs later in
    ``_populate.populate_music_block``, where a missing ``abc`` extra
    soft-fails to a ``MUSIC_ADAPTER_MISSING`` warning and a malformed source
    to a ``<music-error>`` tree — the pipeline keeps running either way.

    Crucially, this function imports **no** frontend: the input layer keeps
    no math/music frontend for a text dialect (only the binary decoders in
    :func:`parse_score_file` / :func:`parse_musicxml` reach across).
    Conversion, the ``abc`` extra, and its failure modes all live at
    frontend time.

    Raises :class:`FileNotFoundError` if the path is missing and
    :class:`ValueError` for a suffix this function doesn't handle (use
    :func:`parse_score_file` for ``.mid`` / ``.midi``, :func:`parse_musicxml`
    for the MusicXML family). It never raises
    :class:`~brailix.core.errors.MissingExtraError`: no adapter is touched
    here, so reading a ``.abc`` needs no optional dependency installed.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    source = _DEFERRED_SCORE_SOURCES.get(suffix)
    if source is None:
        raise ValueError(
            f"unsupported deferred score extension {suffix!r} "
            f"(expected {sorted(_DEFERRED_SCORE_SOURCES)}; "
            f"use parse_score_file for .mid / .midi, "
            f"parse_musicxml for .musicxml / .xml / .mxl)"
        )
    # BOM-aware text read (UTF-16 / UTF-8), matching parse_musicxml; ABC is
    # plain text, so it lands in the block verbatim — no adapter, no frontend.
    text = _read_xml_text(p)
    block = ScoreBlock(text=text, source=source)
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[block],
    )


def _unzip_mxl(data: bytes, *, profile: str) -> str:
    """Decompress an .mxl payload to its inner MusicXML string.

    Reuses the existing :class:`MxlSourceAdapter` so the
    ``META-INF/container.xml`` → rootfile resolution stays in one
    place (frontend ``adapters/mxl.py``). The adapter's soft-failure
    contract applies: malformed ZIPs come back as
    ``<score-partwise><music-error/></score-partwise>`` placeholder
    XML, and the downstream music backend surfaces it as
    ``MUSIC_PARSE_RECOVERY``.
    """
    from brailix.frontend.music.registry import music_source_registry

    return music_source_registry.get("mxl").to_musicxml(
        data, MusicContext(source="mxl", profile=profile)
    )
