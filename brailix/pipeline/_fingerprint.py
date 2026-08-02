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
* the selected **adapter names** (analyzer / resolver as configured);
  segmenter / normalizer are absent because they are not configuration —
  which one runs follows from the profile's language, and the profile's
  full content is hashed above;
* the **user pinyin dictionary** and the **user segmentation dictionary**
  (both order-insensitive);
* the **run mode** — braille output is mode-independent, but recorded
  warnings are not (STRICT raises, LENIENT downgrades), and a cached
  compile replays its warnings;
* the **brailix version** plus a fingerprint schema version, so digests
  never survive an upgrade that may have changed translation rules.

One in-process mutable input IS covered, at the Pipeline layer rather than
here: **what a registered adapter name resolves to**. The registries allow
re-registering an implementation under a live name
(:meth:`brailix.core.registry.Registry.register`), and the frontend
re-resolves names on every run — so a configuration digest alone would let
a cache keep serving output compiled by the replaced implementation.
:attr:`Pipeline.fingerprint` therefore folds every compilation-relevant
registry's ``generation`` counter (:func:`registries_generation`) into the
digest it exposes: any runtime ``register`` / ``unregister`` /
``clear_cache`` advances the fingerprint of every live Pipeline
(deliberately coarse — per-registry, not per-name — because which names a
compile touches isn't statically known; over-invalidation is the safe
direction). ``clear_cache`` counts because dropping a cached ``auto``
adapter lets the replacement re-probe the environment and pick a different
delegate.

Deliberately NOT covered — the fingerprint identifies *configuration*, not
*environment*: third-party adapter library versions, model files on disk,
and what an ``"auto"`` name probes to on its **first** resolution are
invisible to it. Once resolved, that choice is memoised (on the adapter
instance, which the registry caches), and every way of discarding it moves
a generation — so within one process a name that has already compiled
something keeps compiling it the same way, which is what makes in-memory
caches (the intended consumer) sound. An adapter whose own behaviour
changes underneath it (a model downloaded mid-process) is still outside
the coverage, as is a cache persisted across machines or installs: that
needs an environment stamp of its own on top.
"""

from __future__ import annotations

import hashlib
import itertools
import weakref
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from brailix.core.config import BrailleProfile

# Schema version of the fingerprint composition. Bump when the set of
# covered inputs or their serialization changes, so a digest computed under
# the old recipe can never equal one computed under the new.
#
# 3: the composition moved from a hand-rolled ``<tag>=<value>|`` record
#    stream to :func:`_canon` over one nested structure. The old scheme was
#    collidable — see :func:`compilation_fingerprint`.
_FINGERPRINT_VERSION = "3"


def _canon(value: Any, out: list[str]) -> None:
    """Append a canonical, process-stable serialization of ``value``.

    ``repr`` alone is not stable enough for a digest: ``frozenset``
    iteration order varies across interpreter runs (hash randomization),
    and two logically-equal dicts may differ in insertion order. Sort
    unordered containers, recurse into dataclasses field-by-field
    (honouring ``compare=False`` — runtime memoization like
    ``BrailleProfile._letter_cache`` must not perturb the digest), and
    fall back to ``repr`` for scalars.

    Every mapping, not only ``dict``: :class:`~types.MappingProxyType` is
    not a ``dict`` subclass, so a read-only view (which is exactly the shape
    :class:`~brailix.pipeline.Pipeline` holds its user dictionaries in) would
    otherwise fall through to ``repr`` — insertion-ordered, and so no longer
    order-insensitive.
    """
    if isinstance(value, Mapping):
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
    analyzer: str,
    resolver: str,
    user_pinyin_dict: Mapping[str, str],
    user_seg_dict: Mapping[str, Sequence[str]] = MappingProxyType({}),
) -> str:
    """SHA-256 hex digest of one Pipeline's compilation configuration.

    See the module docstring for exactly what is (and is not) covered.
    ``analyzer`` / ``resolver`` are the configured names — ``"auto"`` is a
    configuration value in its own right.

    The inputs are hashed as **one nested structure** through
    :func:`_canon`, not as a stream of ``<tag>=<value>`` records. A
    delimiter protocol over unescaped user-supplied text is collidable, and
    this one demonstrably was: a pinyin reading of
    ``"chong2 qing4|segdict:国家=国家"`` serialized byte-for-byte like a
    plain ``"chong2 qing4"`` reading beside a segmentation entry folding
    国家 — two configurations that compile the same text differently,
    sharing a fingerprint, hence a ``source_hash``, hence a cache entry.
    ``_canon`` cannot be spoofed that way: a value is ``repr``\\ ed (quoted
    and escaped, so no user text can imitate a structural token) and the
    structure it sits in is written around it, so "a string containing a
    separator" and "the structure that separator would have introduced"
    encode differently by construction.

    Two consequences worth stating, both deliberate:

    * The pieces of a segmentation entry keep their boundaries —
      ``("甲", "乙丙")`` and ``("甲乙", "丙")`` are different divisions of
      one surface and must not share a digest — without relying on any
      character being absent from a piece.
    * A ``list`` and a ``tuple`` of the same pieces digest alike, because
      ``_canon`` writes both as sequences. That is the intent: they are the
      same division, and a caller's choice of container is not
      configuration.
    """
    from brailix import __version__

    parts: list[str] = []
    _canon(
        (
            ("fp", _FINGERPRINT_VERSION),
            ("brailix", __version__),
            ("profile", profile_digest(profile)),
            ("mode", mode),
            ("analyzer", analyzer),
            ("resolver", resolver),
            ("user_pinyin_dict", dict(user_pinyin_dict)),
            # Word division changes the braille as surely as a reading does
            # (Chinese braille writes a word together and separates words
            # with a blank cell), so the segmentation dictionary belongs
            # here too.
            ("user_seg_dict", dict(user_seg_dict)),
        ),
        parts,
    )
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Registry-generation folding (the Pipeline.fingerprint layer)
# ---------------------------------------------------------------------------

# The compilation-relevant registries, resolved lazily (importing them at
# module load would cycle back into brailix.pipeline). Everything whose
# resolution can change what a compile emits is on the list; the renderer
# registry is deliberately absent — rendering runs after the compiled
# braille a cache stores, so swapping a renderer can't stale it.
#
# The membership rule is "does replacing an entry change the braille", not
# "is it a Registry instance". ``boundary_registry`` is a dict subclass and was
# left out on the strength of its type, which is how a documented extension
# point ended up outside the fingerprint entirely
# (``tests/integration/test_compilation_fingerprint.py`` now pins the rule
# against the extension surface rather than against a hand-list).
_COMPILATION_REGISTRIES: tuple[Any, ...] | None = None


def _compilation_registries() -> tuple[Any, ...]:
    global _COMPILATION_REGISTRIES
    if _COMPILATION_REGISTRIES is None:
        from brailix.backend.dispatch import language_backend_registry
        from brailix.frontend import boundary_registry, language_frontend_registry
        from brailix.frontend.graphics.registry import graphic_source_registry
        from brailix.frontend.ja.analyzer.registry import (
            analyzer_registry as ja_analyzer_registry,
        )
        from brailix.frontend.math.registry import math_source_registry
        from brailix.frontend.music.registry import music_source_registry
        from brailix.frontend.normalize import normalizer_registry
        from brailix.frontend.segment import segmenter_registry
        from brailix.frontend.zh.analyzer.registry import (
            analyzer_registry as zh_analyzer_registry,
        )
        from brailix.frontend.zh.pinyin.registry import resolver_registry

        _COMPILATION_REGISTRIES = (
            segmenter_registry,
            normalizer_registry,
            language_frontend_registry,
            # The boundary pass is a documented extension point whose handler
            # inserts the Space / Connector nodes between a hanzi run and a
            # Latin word or a number — it changes the braille, so it belongs
            # here. It was missing, and nothing else covered it: the nodes it
            # inserts carry ``surface=""``, so the stale-children check saw no
            # change either, and two compiles either side of a handler swap
            # came back with one ``source_hash`` and different cells.
            boundary_registry,
            language_backend_registry,
            zh_analyzer_registry,
            ja_analyzer_registry,
            resolver_registry,
            math_source_registry,
            music_source_registry,
            graphic_source_registry,
        )
    return _COMPILATION_REGISTRIES


def registries_generation() -> tuple[int, ...]:
    """Current generation of every compilation-relevant registry.

    A cheap (ten atomic int reads), position-stable snapshot:
    :attr:`Pipeline.fingerprint` compares it against the one its cached
    digest was folded with and re-folds only when they differ, so a
    runtime ``register`` / ``unregister`` advances every live Pipeline's
    fingerprint while the steady state costs no hashing at all.
    """
    return tuple(r.generation for r in _compilation_registries())


def fold_runtime_identity(
    base: str, generations: tuple[int, ...], asset_resolver_id: str
) -> str:
    """Combine a configuration fingerprint with the in-process runtime
    identities — the registry-generation snapshot and the graphic asset
    resolver's identity — into the digest :attr:`Pipeline.fingerprint`
    exposes.

    Same-configuration pipelines in the same registry state with the same
    (or no) asset resolver agree; any registration-surface change or a
    different resolver perturbs the result. The generation vector is
    positional (fixed registry order above), so equal vectors mean equal
    fold input.
    """
    h = hashlib.sha256()
    h.update(base.encode("utf-8"))
    h.update(b"|gen|")
    h.update(",".join(str(g) for g in generations).encode("utf-8"))
    h.update(b"|asset|")
    h.update(asset_resolver_id.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Graphic asset-resolver identity
# ---------------------------------------------------------------------------

# Per-instance tokens for resolvers that declare no identity of their own,
# keyed by ``id()`` so a resolver with custom ``__eq__`` can't alias another
# one's token. A ``weakref.finalize`` retires the entry when the resolver is
# collected, so a recycled ``id()`` can never inherit the dead resolver's
# token (the classic id-reuse pitfall). Thread note: two threads racing the
# FIRST identity read of one resolver may mint two tokens and keep the last
# write — one spurious cache invalidation, never a false hit.
_RESOLVER_TOKENS: dict[int, str] = {}
_RESOLVER_COUNTER = itertools.count(1)


def asset_resolver_identity(resolver: Any) -> str:
    """A process-stable identity string for a graphic asset resolver.

    What resolved asset bytes ride into a compiled graphic is part of the
    compilation's identity: two pipelines whose resolvers return different
    bytes for the same reference must not share fingerprints or graphic
    tree-cache entries. Three tiers:

    * ``None`` → ``""`` — no resolver, the shared steady state.
    * A resolver exposing ``cache_identity`` (an attribute or zero-arg
      callable returning a string) → ``declared:<value>``. The opt-in for
      content-addressed identity: equal values deliberately share caches
      (two pipelines over the same document's assets), and a resolver
      whose *content* can change mid-life must refresh the value, because
      the instance tier below treats a resolver as an immutable asset set.
    * Anything else → a per-instance token, minted once per object. Two
      distinct resolver instances never share caches (conservative: equal
      content still fingerprints apart), and a token is retired with its
      instance so a recycled ``id()`` can't revive it. An object that
      cannot be weak-referenced gets a fresh token per read — such a
      resolver simply never caches, which is safe.
    """
    if resolver is None:
        return ""
    declared = getattr(resolver, "cache_identity", None)
    if declared is not None:
        value = declared() if callable(declared) else declared
        return f"declared:{value}"
    key = id(resolver)
    token = _RESOLVER_TOKENS.get(key)
    if token is None:
        token = f"instance:{next(_RESOLVER_COUNTER)}"
        try:
            weakref.finalize(resolver, _RESOLVER_TOKENS.pop, key, None)
        except TypeError:
            # Not weak-referenceable (e.g. a __slots__ instance without
            # __weakref__): don't record a token we could never retire —
            # a fresh identity per read means "never cache", never "wrong
            # cache".
            return f"volatile:{next(_RESOLVER_COUNTER)}"
        _RESOLVER_TOKENS[key] = token
    return token
