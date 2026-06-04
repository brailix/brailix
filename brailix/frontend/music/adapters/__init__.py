"""Music source-format adapters. Each module exposes a ``_load``
factory returning a :class:`~brailix.core.protocols.MusicSourceAdapter`.
The sibling :mod:`brailix.frontend.music.registry` module holds the
registry and self-registers everyone at import time.
"""
