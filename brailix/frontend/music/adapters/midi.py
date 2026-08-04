"""MIDI source adapter — converts MIDI files to MusicXML via partitura.

The pipeline:

1. ``mido.MidiFile(file=...)`` parses the raw MIDI bytes from an
   in-memory ``BytesIO`` buffer into a ``mido.MidiFile`` object.
2. ``partitura.load_score_midi`` turns that into a ``Score``, handling
   quantisation, voice splitting and key inference. (``partitura``'s
   ``load_score`` takes a *filename* — it can't read our in-memory
   payload, so we hand it the parsed ``mido`` object via
   ``load_score_midi`` instead.)
3. ``partitura.save_musicxml`` serialises that ``Score`` back out as
   MusicXML — we stream through an in-memory buffer to avoid temp files.

``partitura`` is an optional extra. The registry registers this adapter
with ``extra="midi"``; a missing package surfaces as
:class:`~brailix.core.errors.MissingExtraError` pointing at
``pip install brailix[midi]``.
"""

from __future__ import annotations

import io as _io
import warnings as _warnings
from dataclasses import dataclass as _dataclass

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import music_error_wrap


@_dataclass(slots=True)
class MidiSourceAdapter:
    """MIDI bytes → MusicXML string via partitura.

    Soft-failure: any conversion exception comes back as a
    ``<music-error>`` MusicXML doc, so a malformed MIDI doesn't break
    a multi-document pipeline.
    """

    source: str = "midi"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, str):
            # MIDI is binary; reject str input loudly with a soft fail.
            return music_error_wrap(
                src,
                reason="midi source requires bytes input, got str",
            )
        if not src:
            return music_error_wrap("", reason="empty midi payload")
        try:
            import mido  # noqa: WPS433 — lazy by design
            import partitura  # noqa: WPS433
        except ImportError:
            # Surfaced by the registry as MissingExtraError when the
            # adapter is requested via music_source_registry.get("midi");
            # this defensive branch covers the rare case of a caller
            # building MidiSourceAdapter directly. (mido ships as a
            # partitura dependency, so the midi extra pulls both.)
            return music_error_wrap(
                "",
                reason="partitura not installed (pip install brailix[midi])",
            )
        try:
            # partitura emits UserWarnings for unsupported MIDI features;
            # they're noise for a library caller, so scope-suppress them.
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                midi = mido.MidiFile(file=_io.BytesIO(src))
                score = partitura.load_score_midi(midi)
                buf = _io.BytesIO()
                partitura.save_musicxml(score, buf)
            return buf.getvalue().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — third-party failures vary
            return music_error_wrap("", reason=f"partitura error: {e}")


def _load() -> MidiSourceAdapter:
    """Lazy-load the adapter. Imports ``partitura`` here so the
    registry's MissingExtraError fires at registration-touch time
    rather than at module import."""
    import partitura  # noqa: F401, WPS433 — registration-time import gate

    return MidiSourceAdapter()
