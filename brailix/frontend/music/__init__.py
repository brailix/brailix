"""Music frontend subsystem — one public entry point:
:func:`parse_music_tree`.

Source adapters (``musicxml`` / ``mxl`` / ``midi`` / ``abc`` /
``plain``) live in ``adapters/`` and are picked from an internal
registry based on :class:`~brailix.core.context.MusicContext`. The
MusicXML tree returned by an adapter, after normalisation, is the
music IR itself — there is no separate IR-builder layer (see
``ARCHITECTURE.md``).

Callers only need :func:`parse_music_tree`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core.context import MusicContext
from brailix.core.errors import MissingExtraError
from brailix.frontend.music.normalizer import normalize


def parse_music_tree(
    src: str | bytes, ctx: MusicContext
) -> ET.Element | None:
    """Convert one music fragment to a normalised :class:`ET.Element`
    tree (rooted at ``<score-partwise>``).

    Steps: pick the source adapter from ``ctx.source`` → produce a
    MusicXML string → run the normalizer (strip namespace, collapse
    score-timewise to score-partwise) → return the resulting
    :class:`ET.Element`.

    Returns ``None`` (and records a ``MUSIC_ADAPTER_MISSING`` warning
    via ``ctx.warnings``) when the requested source adapter is absent
    or its optional dependency isn't installed; the pipeline keeps
    running.
    """
    from brailix.frontend.music.registry import music_source_registry

    try:
        adapter = music_source_registry.get(ctx.source)
    except MissingExtraError as e:
        ctx.warnings.warn(
            code="MUSIC_ADAPTER_MISSING",
            message=str(e),
            source="frontend.music",
        )
        return None
    except KeyError as e:
        ctx.warnings.warn(
            code="MUSIC_ADAPTER_MISSING",
            message=str(e),
            surface=src if isinstance(src, str) else None,
            candidates=tuple(music_source_registry.names()),
            source="frontend.music",
        )
        return None

    musicxml = adapter.to_musicxml(src, ctx)
    return normalize(musicxml, ctx)


__all__ = ("parse_music_tree",)
