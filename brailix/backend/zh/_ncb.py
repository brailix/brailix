"""Where the Chinese backend reads its NCB rules out of a profile.

GF0019-2018 (National Common Braille) ships its standard-specific data — the
tone-omission table, the character-level overrides, the word-level ones — as a
single record loaded from one ``tables.zh.exceptions`` resource. The profile
carries it in the generic per-language spec slot
(:attr:`~brailix.core.config.BrailleProfile.lang_specs`), and this module is
the one place that knows the slot's name and what the record is.

That is the whole point of the indirection. It used to be a
``zh_exceptions: NcbExceptions | None`` field on ``BrailleProfile``: the
profile type every language and every standard compiles through, naming one
concrete Chinese standard and importing its type — while the comment beside
the generic slot said, in as many words, that a new language should arrive
through the slot rather than by growing the shared dataclass. Nothing about
the arrangement was broken, and that was the problem with it: the next
standard's exceptions had an obvious place to go, right next to this one's,
and the fingerprint, the loader and the type annotations would all have grown
a second concrete standard the same way.

So the knowledge sits here, in the backend that acts on it, and the shared
core carries an opaque record it never has to describe.
"""

from __future__ import annotations

from brailix.core.config import BrailleProfile
from brailix.core.config.zh_ncb_tables import NcbExceptions

# The key this standard's record is filed under inside the profile's ``zh``
# spec group. Named once; both readers below go through the accessor.
_NCB_EXCEPTIONS_SPEC = "ncb_exceptions"


def ncb_exceptions(profile: BrailleProfile) -> NcbExceptions | None:
    """The profile's NCB exceptions record, or ``None`` if it declares none.

    ``None`` is the ordinary case, not a failure: ``cn_current`` follows
    Current Chinese Braille and has no NCB resource at all, so every NCB call
    site is written to no-op on it. A profile for another language never has
    the entry either, because :meth:`BrailleProfile.lang_spec` looks under its
    own language subtag.

    The ``isinstance`` is not defensive noise: the slot is typed ``Any``
    precisely so the core need not know this type, which means a hand-built
    profile (or a future loader change) can put anything there, and the call
    sites below read attributes off whatever comes back.
    """
    spec = profile.lang_spec(_NCB_EXCEPTIONS_SPEC)
    return spec if isinstance(spec, NcbExceptions) else None
