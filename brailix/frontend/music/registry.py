"""Registry for music source-format adapters.

Adapters convert score data from a specific source format (MusicXML,
.mxl, MIDI, ABC, ...) into a normalised MusicXML string. The music
backend then walks the MusicXML element tree directly — there is no
separate IR-builder layer. Adding a new source format means adding
exactly one adapter; the backend doesn't change.
"""

from __future__ import annotations

from brailix.core.protocols import MusicSourceAdapter
from brailix.core.registry import Registry

music_source_registry: Registry[MusicSourceAdapter] = Registry(
    "music.source", protocol=MusicSourceAdapter
)


def _register_builtin() -> None:
    from brailix.frontend.music.adapters import (  # noqa: F401
        abc,
        midi,
        musicxml,
        mxl,
        plain,
    )

    music_source_registry.register("musicxml", musicxml._load)
    music_source_registry.register("mxl", mxl._load)
    music_source_registry.register("plain", plain._load)
    # MIDI / ABC adapters are optional — they only work when their
    # respective pip extras are installed. Registering with ``extra=``
    # lets the registry turn a missing import into a friendly
    # MissingExtraError pointing at the right install command.
    music_source_registry.register("midi", midi._load, extra="midi")
    music_source_registry.register("abc", abc._load, extra="abc")


_register_builtin()
