"""Compilation-configuration fingerprint.

:func:`block_hash` keys a compiled block on ``(surface, profile name,
structure)`` — none of which changes when the *compilation configuration*
does: swapping the pinyin resolver, adding a user-dictionary entry, or
editing a same-named profile JSON on ``extra_profile_paths`` all leave the
digest unchanged while changing the braille. A cache keyed on that digest
alone would then serve output compiled under a configuration the caller no
longer runs — silent wrong braille, the worst failure mode a cache has.

:func:`compilation_fingerprint` closes that hole: a stable digest of every
Pipeline-level input that can change the compiled output for the same
source text. :class:`~brailix.pipeline.Pipeline` computes it once per
instance, folds it into every :attr:`CompiledBlock.source_hash`, and stamps
it onto the blocks whose ``children`` its frontend populates so a later
compile under a *different* configuration re-runs the frontend instead of
reusing semantic IR built under the old one.

Covered (a change flips the fingerprint):

* the **resolved profile content** — every loaded table / feature, so a
  same-named profile edited on ``extra_profile_paths`` fingerprints apart
  from the builtin it shadows;
* the selected **adapter names** (segmenter / normalizer after language
  resolution, analyzer / resolver as configured);
* the **user pinyin dictionary** (order-insensitive);
* the **run mode** — braille output is mode-independent, but recorded
  warnings are not (STRICT raises, LENIENT downgrades), and a cached
  compile replays its warnings;
* the **brailix version** plus a fingerprint schema version, so digests
  never survive an upgrade that may have changed translation rules.

Deliberately NOT covered — the fingerprint identifies *configuration*, not
*environment*: third-party adapter library versions, model files on disk,
and what ``"auto"`` adapter names resolve to at run time are invisible to
it. Within one process those are fixed, so in-memory caches (the intended
consumer) are sound; a cache persisted across machines or installs needs
an environment stamp of its own on top.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brailix.core.config import BrailleProfile

# Schema version of the fingerprint composition. Bump when the set of
# covered inputs or their serialization changes, so a digest computed under
# the old recipe can never equal one computed under the new.
_FINGERPRINT_VERSION = "1"


def _canon(value: Any, out: list[str]) -> None:
    """Append a canonical, process-stable serialization of ``value``.

    ``repr`` alone is not stable enough for a digest: ``frozenset``
    iteration order varies across interpreter runs (hash randomization),
    and two logically-equal dicts may differ in insertion order. Sort
    unordered containers, recurse into dataclasses field-by-field
    (honouring ``compare=False`` — runtime memoization like
    ``BrailleProfile._letter_cache`` must not perturb the digest), and
    fall back to ``repr`` for scalars.
    """
    if isinstance(value, dict):
        out.append("{")
        for key in sorted(value, key=repr):
            out.append(repr(key))
            out.append(":")
            _canon(value[key], out)
            out.append(",")
        out.append("}")
    elif isinstance(value, (set, frozenset)):
        out.append("{")
        out.extend(sorted(repr(v) for v in value))
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for v in value:
            _canon(v, out)
            out.append(",")
        out.append("]")
    elif is_dataclass(value) and not isinstance(value, type):
        out.append(type(value).__name__)
        out.append("(")
        for f in fields(value):
            if not f.compare:
                continue
            out.append(f.name)
            out.append("=")
            _canon(getattr(value, f.name), out)
            out.append(",")
        out.append(")")
    else:
        out.append(repr(value))


def profile_digest(profile: BrailleProfile) -> str:
    """SHA-256 hex digest of a resolved profile's full content.

    Two profiles loaded from identical JSON + tables digest alike; any
    table entry, feature flag, or metadata difference — including a
    user-folder profile shadowing a same-named builtin — digests apart.
    """
    parts: list[str] = []
    _canon(profile, parts)
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def compilation_fingerprint(
    profile: BrailleProfile,
    *,
    mode: str,
    segmenter: str,
    normalizer: str,
    analyzer: str,
    resolver: str,
    user_pinyin_dict: dict[str, str],
) -> str:
    """SHA-256 hex digest of one Pipeline's compilation configuration.

    See the module docstring for exactly what is (and is not) covered.
    ``segmenter`` / ``normalizer`` should be the *resolved* names (after
    per-language selection); ``analyzer`` / ``resolver`` are the configured
    names — ``"auto"`` is a configuration value in its own right.
    """
    from brailix import __version__

    h = hashlib.sha256()

    def put(tag: str, value: str) -> None:
        h.update(tag.encode("utf-8"))
        h.update(b"=")
        h.update(value.encode("utf-8"))
        h.update(b"|")

    put("fp", _FINGERPRINT_VERSION)
    put("brailix", __version__)
    put("profile", profile_digest(profile))
    put("mode", mode)
    put("segmenter", segmenter)
    put("normalizer", normalizer)
    put("analyzer", analyzer)
    put("resolver", resolver)
    for surface in sorted(user_pinyin_dict):
        put(f"dict:{surface}", user_pinyin_dict[surface])
    return h.hexdigest()
