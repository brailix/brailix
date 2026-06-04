"""Per-element MusicXML handlers + the dispatch table.

Each handler appends :class:`BrailleCell`\\ s to ``cells`` and
optionally mutates ``mctx`` (e.g. ``mctx.prev_pitch`` after a note).
A handler must never raise on unrecognised content — it warns and
falls through, so the surrounding score still produces some output.

The dispatch table ``_DISPATCH`` is consumed lazily by
:mod:`brailix.backend.music.dispatch`. Handlers are organised by
BANA chapter / milestone; each submodule contributes a
``_DISPATCH_PARTIAL`` mapping that is merged here. Submodules that
don't own a top-level MusicXML tag (``notations``, ``lyrics``) have
no ``_DISPATCH_PARTIAL`` — they're called from inside
``notes._emit_note`` instead.

Subpackage map (per BANA / milestone):

* :mod:`.containers` — ``<score-partwise>`` / ``<part>`` / ``<measure>``
* :mod:`.attributes` — M3.1 clef / key / time
* :mod:`.barline`    — M3.3 bar-style / repeats / volta
* :mod:`.harmony`    — S5 ``<sound>`` (D.C./D.S./Coda) + S7 ``<harmony>``
* :mod:`.direction`  — M3.4 dynamics / words / wedge
* :mod:`.notes`      — note + rest + chord interval + accidental + dots
* :mod:`.notations`  — appoggiatura / tuplet / tie / slur / fingering /
  ornaments / tremolo (per-note, no top-level tags)
* :mod:`.lyrics`     — M5 lyric marker (per-note)
* :mod:`.fallback`   — music-error + no-op skip tags + unsupported catch-all
"""

from __future__ import annotations

from brailix.backend.music.handlers.attributes import (
    _DISPATCH_PARTIAL as _attributes,
)
from brailix.backend.music.handlers.barline import (
    _DISPATCH_PARTIAL as _barline,
)
from brailix.backend.music.handlers.containers import (
    _DISPATCH_PARTIAL as _containers,
)
from brailix.backend.music.handlers.direction import (
    _DISPATCH_PARTIAL as _direction,
)
from brailix.backend.music.handlers.fallback import (
    _DISPATCH_PARTIAL as _fallback,
)
from brailix.backend.music.handlers.fallback import _emit_unsupported
from brailix.backend.music.handlers.harmony import (
    _DISPATCH_PARTIAL as _harmony,
)
from brailix.backend.music.handlers.notes import (
    _DISPATCH_PARTIAL as _notes,
)

_DISPATCH: dict = {}
for _partial in (
    _containers, _attributes, _barline, _harmony,
    _direction, _notes, _fallback,
):
    _DISPATCH.update(_partial)

__all__ = ("_DISPATCH", "_emit_unsupported")
