"""Music score file input adapters.

Read a score file from disk and wrap it as a single-block
:class:`DocumentIR` carrying a :class:`ScoreBlock`. Two entry points,
split by how the source reaches MusicXML:

:func:`parse_musicxml` — the MusicXML family (no adapter needed):

* ``.musicxml`` / ``.xml`` → read UTF-8 text, ``source="musicxml"``
* ``.mxl``                → ZIP container, unzipped via the existing
  frontend :class:`~brailix.frontend.music.adapters.mxl.MxlSourceAdapter`
  to extract the inner XML, then ``source="musicxml"`` (the
  decompressed text is plain MusicXML so the backend doesn't need to
  re-unzip later).

:func:`parse_score_file` — formats that need a source adapter to reach
MusicXML:

* ``.mid`` / ``.midi`` → MIDI bytes converted via the ``midi`` adapter
  (needs the ``midi`` extra)
* ``.abc``            → ABC text converted via the ``abc`` adapter
  (needs the ``abc`` extra)

Both store the resulting MusicXML string as the block's ``text`` with
``source="musicxml"`` (conversion is eager, at input time, exactly as
``.mxl`` is); ``Pipeline._populate_music_block`` then parses that text
through the music frontend → MusicInline + ET.Element tree.

Neither opens .sib / .musx / .dorico / .mscz — proprietary formats stay
outside brailix per ``ARCHITECTURE.md``
"""

from __future__ import annotations

import os
from pathlib import Path

from brailix.core.context import MusicContext
from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.ir.document import DocumentIR, ScoreBlock

_MUSICXML_TEXT_SUFFIXES = frozenset({".musicxml", ".xml"})
_MXL_SUFFIXES = frozenset({".mxl"})

MUSIC_SUFFIXES = _MUSICXML_TEXT_SUFFIXES | _MXL_SUFFIXES

# Score formats that aren't MusicXML text and need a source adapter to
# get there. Suffix → music source name; binary suffixes are read as
# bytes (MIDI), the rest as UTF-8 text (ABC). Kept as data so a new
# score format is one more entry plus its registered adapter — no new
# branch (ARCHITECTURE.md, adapter pattern).
_ADAPTER_SCORE_SOURCES: dict[str, str] = {
    ".mid": "midi",
    ".midi": "midi",
    ".abc": "abc",
}
_BINARY_SCORE_SUFFIXES = frozenset({".mid", ".midi"})

ADAPTER_SCORE_SUFFIXES = frozenset(_ADAPTER_SCORE_SOURCES)


def parse_musicxml(
    path: str | os.PathLike[str],
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Read a MusicXML / .mxl file and return a single-block
    :class:`DocumentIR`.

    Suffix dispatch handles ``.musicxml`` / ``.xml`` (UTF-8 text) and
    ``.mxl`` (ZIP container). Both produce a ``ScoreBlock`` whose
    ``text`` is the resolved MusicXML string and ``source`` is
    ``"musicxml"`` — the inner XML carries no compression by the time
    it lands in the block.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for unrecognised suffixes,
    :class:`UnicodeDecodeError` if a ``.musicxml`` file's bytes
    aren't valid UTF-8.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _MXL_SUFFIXES:
        text = _unzip_mxl(p.read_bytes())
    elif suffix in _MUSICXML_TEXT_SUFFIXES:
        text = p.read_text(encoding="utf-8")
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
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Read a non-MusicXML score file (``.mid`` / ``.midi`` / ``.abc``)
    and return a single-block :class:`DocumentIR`.

    The matching music source adapter converts the raw source to a
    MusicXML string at input time (eager, the same strategy
    :func:`parse_musicxml` uses for ``.mxl``): MIDI is read as bytes and
    run through the ``midi`` adapter, ABC as UTF-8 text through the
    ``abc`` adapter. The result is wrapped as a ``ScoreBlock`` whose
    ``source`` is normalised to ``"musicxml"`` — by the time the block
    lands, its ``text`` is plain MusicXML, so the rest of the pipeline
    treats it exactly like a MusicXML file. A malformed source comes
    back as a ``<music-error>`` placeholder per the music subsystem's
    soft-failure contract.

    Raises :class:`FileNotFoundError` if the path is missing,
    :class:`ValueError` for a suffix this function doesn't handle (use
    :func:`parse_musicxml` for the MusicXML family), and
    :class:`~brailix.core.errors.MissingExtraError` when the format's
    optional dependency isn't installed — the message names the extra
    (for example ``pip install brailix[midi]``).
    """
    from brailix.frontend.music.registry import music_source_registry

    p = Path(path)
    suffix = p.suffix.lower()
    source = _ADAPTER_SCORE_SOURCES.get(suffix)
    if source is None:
        raise ValueError(
            f"unsupported score file extension {suffix!r} "
            f"(expected {sorted(_ADAPTER_SCORE_SOURCES)}; "
            f"use parse_musicxml for .musicxml / .xml / .mxl)"
        )
    payload: str | bytes = (
        p.read_bytes()
        if suffix in _BINARY_SCORE_SUFFIXES
        else p.read_text(encoding="utf-8")
    )
    # registry.get raises MissingExtraError (naming the extra) when the
    # adapter's optional dependency is absent — surfaced loudly here, the
    # same way parse_docx surfaces a missing ``docx`` extra.
    adapter = music_source_registry.get(source)
    musicxml = adapter.to_musicxml(payload, MusicContext(source=source))

    block = ScoreBlock(text=musicxml, source="musicxml")
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[block],
    )


def _unzip_mxl(data: bytes) -> str:
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
        data, MusicContext(source="mxl")
    )
