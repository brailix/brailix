"""``plain`` music adapter — last-resort fallback.

When the pipeline lands a music fragment with ``source="plain"`` (the
default for fragments lacking a specific source label), this adapter
runs. It does **not** try to parse anything heuristically — that would
silently produce wrong cells. Instead it wraps the input in a
``<music-error>`` so the backend emits a fallback cell sequence and
the warning collector records ``MUSIC_PARSE_RECOVERY``.

This keeps a clean two-tier contract for music input:

* declared sources (``musicxml`` / ``mxl`` / ``midi`` / ``abc``) get
  parsed properly;
* anything else surfaces as an obvious failure for proofread UIs to
  flag — no guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import music_error_wrap


@dataclass(slots=True)
class PlainMusicSourceAdapter:
    """Surface any plain-text music input as a soft failure."""

    source: str = "plain"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — defensive
                src = ""
        return music_error_wrap(
            src,
            reason="plain music source unsupported -- declare a real source",
        )


def _load() -> PlainMusicSourceAdapter:
    return PlainMusicSourceAdapter()
