"""Music score file input adapters.

Read a score file from disk and wrap it as a single-block
:class:`DocumentIR` carrying a :class:`ScoreBlock`. Three entry points,
split by how — and *when* — the source reaches MusicXML (ARCHITECTURE#arch-layers,
the input/frontend payload-shape boundary):

:func:`parse_musicxml` — the MusicXML family. Bare MusicXML needs no source
adapter; its ZIP container does, and that difference is the whole boundary
rule, not a footnote to it:

* ``.musicxml`` / ``.xml`` → read UTF-8/UTF-16 text, ``source="musicxml"``.
  Text in, text out: no adapter, no frontend import.
* ``.mxl``                → ZIP container (binary), so it takes the same
  eager-decode exception ``.mid`` does: unzipped at input through the frontend
  :class:`~brailix.frontend.music.adapters.mxl.MxlSourceAdapter` to extract
  the inner XML, then ``source="musicxml"`` (the decompressed text is plain
  MusicXML so the backend doesn't need to re-unzip later).

:func:`parse_score_file` — *binary* dialects decoded eagerly at the input
boundary, because the text IR can't carry binary bytes
(ARCHITECTURE#arch-layers rule 2, the same
exception ``.mxl`` / MTEF take):

* ``.mid`` / ``.midi`` → MIDI bytes converted through the ``midi`` adapter
  (needs the ``midi`` extra); ``source`` normalised to ``"musicxml"``.

:func:`parse_deferred_score` — *text* dialects kept **raw** and deferred to
the frontend (ARCHITECTURE#arch-layers rule 1), exactly as
``MathBlock(source="latex")`` defers
LaTeX; the input layer imports no frontend for these:

* ``.abc`` → stored verbatim as ``ScoreBlock(source="abc")``;
  ``_populate.populate_music_block`` runs the ``abc`` adapter later,
  where a missing ``abc`` extra soft-fails instead of raising at read time.

Neither opens .sib / .musx / .dorico / .mscz — proprietary formats stay
outside brailix per ``ARCHITECTURE.md``.

All three take an :class:`~brailix.input.limits.InputLimits` and apply its
decoded-character ceiling the moment the score text exists — before it is
wrapped in a block. The check lives *here*, in the adapter that materialises
the text, rather than in :func:`brailix.input.parse_file`'s routing, so a
caller reaching for one of these directly (the documented way to bypass
suffix dispatch) keeps its policy instead of silently losing it — and so a
score suffix can never become a hole in a service's character budget.
"""

from __future__ import annotations

import os
from pathlib import Path

from brailix.core.context import MusicContext
from brailix.input.limits import DEFAULT_INPUT_LIMITS, InputLimits
from brailix.ir.document import DocumentIR, ScoreBlock

_MUSICXML_TEXT_SUFFIXES = frozenset({".musicxml", ".xml"})
_MXL_SUFFIXES = frozenset({".mxl"})

MUSIC_SUFFIXES = _MUSICXML_TEXT_SUFFIXES | _MXL_SUFFIXES

# Binary score dialects: decoded eagerly at the input boundary because the
# text IR can't carry binary bytes (ARCHITECTURE#arch-layers rule 2 — the same
# exception MTEF and the ``.mxl`` ZIP take). Suffix → music source name;
# kept as data so a new binary score format is one more entry plus its
# registered adapter — no new branch (ARCHITECTURE#arch-adapters).
_BINARY_SCORE_SOURCES: dict[str, str] = {
    ".mid": "midi",
    ".midi": "midi",
}
BINARY_SCORE_SUFFIXES = frozenset(_BINARY_SCORE_SOURCES)

# Text score dialects: kept RAW at input and deferred to the frontend
# (ARCHITECTURE#arch-layers rule 1), exactly as ``MathBlock(source="latex")`` defers
# LaTeX. ABC is UTF-8 text, so it fits the text IR and rides the
# defer-to-frontend seam rather than the binary eager path — the input layer
# holds no frontend import for it. Suffix → music source name (the block's
# ``source``, which the frontend later hands to ``music_source_registry``).
_DEFERRED_SCORE_SOURCES: dict[str, str] = {
    ".abc": "abc",
}
DEFERRED_SCORE_SUFFIXES = frozenset(_DEFERRED_SCORE_SOURCES)


def parse_musicxml(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    limits: InputLimits = DEFAULT_INPUT_LIMITS,
) -> DocumentIR:
    """Read a MusicXML / .mxl file and return a single-block
    :class:`DocumentIR`.

    Suffix dispatch handles ``.musicxml`` / ``.xml`` (UTF-8/UTF-16 text) and
    ``.mxl`` (ZIP container). Both produce a ``ScoreBlock`` whose
    ``text`` is the resolved MusicXML string and ``source`` is
    ``"musicxml"`` — the inner XML carries no compression by the time
    it lands in the block.

    ``limits`` bounds the resolved MusicXML *text*: the ceiling is applied
    after the read / decompression, since that string is what the music
    frontend then walks. (The whole-file byte gate is
    :meth:`InputLimits.check_file_size`, run by
    :func:`brailix.input.parse_file`
    before any read; a ``.mxl``'s archive-internal budget is the
    ``mxl`` adapter's own, always on.)

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for unrecognised suffixes,
    :class:`UnicodeDecodeError` if a ``.musicxml`` file's bytes are
    neither valid UTF-8 nor UTF-16-BOM-prefixed, and
    :class:`~brailix.input.limits.InputTooLargeError` when the resolved text
    exceeds ``limits``.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _MXL_SUFFIXES:
        text = _unzip_mxl(limits.read_bounded(p), profile=profile)
    elif suffix in _MUSICXML_TEXT_SUFFIXES:
        # Honour a UTF-16 BOM (Finale / Windows exporters) and strip a UTF-8
        # BOM; a surviving BOM would break the score sniff / XML parse.
        text = limits.read_bounded_text(p, normalize_newlines=True)
    else:
        raise ValueError(
            f"unsupported music file extension {suffix!r} "
            f"(expected .musicxml / .xml / .mxl)"
        )
    limits.check_text_length(text)
    return _score_document(text, language=language, profile=profile)


def _score_document(text: str, *, language: str, profile: str) -> DocumentIR:
    """Wrap resolved MusicXML ``text`` as the single-block ``DocumentIR`` every
    score route returns.

    Split out so a caller that has *already read* the text can build the same
    document without reading the file again. The generic ``.xml`` route needs
    exactly that: it reads the file to sniff the root element, and used to then
    hand :func:`parse_musicxml` the *path*, which opened and decoded it a
    second time. Two reads of one path are two different files whenever
    something replaces it in between — the sniff would classify one document
    and the parse consume another — which is the window the ``.docx`` route was
    already closed against. (The wasted second decode of a large score is the
    lesser half.)

    Not public: callers go through :func:`parse_musicxml` or, inside the input
    layer, through this.
    """
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[ScoreBlock(text=text, source="musicxml")],
    )


def parse_score_file(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    limits: InputLimits = DEFAULT_INPUT_LIMITS,
) -> DocumentIR:
    """Read a *binary* score file (``.mid`` / ``.midi``) and eagerly decode
    it to MusicXML at the input boundary.

    Binary dialects are the deliberate ARCHITECTURE#arch-layers rule-2
        exception: the text IR
    can't carry raw bytes, so the matching music source adapter (the
    ``midi`` adapter, needing the ``midi`` extra) runs here at input time —
    the same strategy :func:`parse_musicxml` uses for ``.mxl``. The result
    is wrapped as a ``ScoreBlock`` whose ``source`` is normalised to
    ``"musicxml"``; by the time the block lands, its ``text`` is plain
    MusicXML, so the rest of the pipeline treats it exactly like a MusicXML
    file. A malformed source comes back as a ``<music-error>`` placeholder
    per the music subsystem's soft-failure contract.

    Text dialects (``.abc``) do **not** come here — they stay raw and defer
    to the frontend via :func:`parse_deferred_score` (ARCHITECTURE#arch-layers rule
    1), so this function imports the music source registry only for the
    binary-decode exception.

    ``limits`` bounds the *decoded* MusicXML the adapter produces — the
    string the music frontend then walks — the same ceiling
    :func:`parse_musicxml` applies to the text it reads.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for a suffix this function doesn't handle (use
    :func:`parse_deferred_score` for ``.abc``, :func:`parse_musicxml` for
    the MusicXML family),
    :class:`~brailix.core.errors.MissingExtraError` when the format's
    optional dependency isn't installed — the message names the extra
    (for example ``pip install brailix[midi]``) — and
    :class:`~brailix.input.limits.InputTooLargeError` when the decoded
    MusicXML exceeds ``limits``.
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
        limits.read_bounded(p), MusicContext(source=source, profile=profile)
    )
    limits.check_text_length(musicxml)
    return _score_document(musicxml, language=language, profile=profile)


def parse_deferred_score(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    limits: InputLimits = DEFAULT_INPUT_LIMITS,
) -> DocumentIR:
    """Read a *text-dialect* score file (``.abc``) and store it **raw**,
    deferring conversion to the frontend.

    ABC is UTF-8 text, so — unlike the binary MIDI path — it fits in the
    text IR and follows ARCHITECTURE#arch-layers rule 1 (text dialects are kept raw
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

    ``limits`` bounds the decoded source text. It matters *more* here than
    on the eager paths, not less: the text is stored verbatim and every
    character of it is what the frontend later converts, so a text dialect
    behind a score suffix must not buy a way past a service's character
    ceiling.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for a suffix this function doesn't handle (use
    :func:`parse_score_file` for ``.mid`` / ``.midi``, :func:`parse_musicxml`
    for the MusicXML family), and
    :class:`~brailix.input.limits.InputTooLargeError` when the decoded text
    exceeds ``limits``. It never raises
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
    # Read through the CALLER's limits: this used to call a reader whose
    # ``limits`` parameter was defaulted, so an ``.abc`` file replaced after
    # ``parse_file``'s stat gate was consumed up to the *default* ceiling
    # rather than the tightened one the caller asked for.
    text = limits.read_bounded_text(p, normalize_newlines=True)
    limits.check_text_length(text)
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
