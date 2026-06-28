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
from brailix.frontend.music.adapters.musicxml import music_error_wrap
from brailix.frontend.music.normalizer import normalize


def parse_music_tree(
    src: str | bytes, ctx: MusicContext
) -> ET.Element | None:
    """Convert one music fragment to a normalised :class:`ET.Element`
    tree (rooted at ``<score-partwise>``).

    Steps: pick the source adapter from ``ctx.source`` → produce a
    MusicXML string → run the normalizer (strip namespace, normalize
    voices, infer missing note types) → return the resulting
    :class:`ET.Element`.

    Returns ``None`` (and records a ``MUSIC_ADAPTER_MISSING`` warning
    via ``ctx.warnings``) when the requested source adapter is absent
    or its optional dependency isn't installed; the pipeline keeps
    running.

    Soft-failure backstop: an adapter (or the normalizer) that raises
    anyway — the registry is open to third-party adapters — degrades to
    the standard ``<music-error>`` tree instead of crashing the caller.
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

    try:
        musicxml = adapter.to_musicxml(src, ctx)
        return normalize(musicxml, ctx)
    except Exception as e:  # noqa: BLE001 — pipeline must never crash
        # Adapters promise soft failure (<music-error> + warning) and
        # the normalizer promises never to raise, but the registry is
        # deliberately open and our own have slipped (a circled-digit
        # <voice> used to raise out of the voice remap).  Degrade to
        # the standard <music-error> tree and keep translating.
        surface = src if isinstance(src, str) else repr(src)
        try:
            return normalize(
                music_error_wrap(
                    surface[:200], reason=f"adapter failure: {e!r}"
                ),
                ctx,
            )
        except Exception:  # pragma: no cover — double fault
            ctx.warnings.warn(
                code="MUSIC_PARSE_RECOVERY",
                message=f"music adapter failure: {e!r}",
                source="frontend.music",
            )
            return None


__all__ = ("parse_music_tree",)
