"""MusicXML / .mxl file input adapter.

Reads a score file from disk and wraps it as a single-block
:class:`DocumentIR` carrying a :class:`ScoreBlock`. Suffix dispatch:

* ``.musicxml`` / ``.xml`` → read UTF-8 text, ``source="musicxml"``
* ``.mxl``                → ZIP container, unzipped via the existing
  frontend :class:`~brailix.frontend.music.adapters.mxl.MxlSourceAdapter`
  to extract the inner XML, then ``source="musicxml"`` (the
  decompressed text is plain MusicXML so the backend doesn't need to
  re-unzip later).

The block's ``text`` is the resulting MusicXML string;
``Pipeline._populate_music_block`` parses it through the music
frontend → MusicInline + ET.Element tree.

This adapter does **not** open .sib / .musx / .dorico / .mscz —
proprietary formats stay outside brailix per ``ARCHITECTURE.md``
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
