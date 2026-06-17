"""Tone-emission strategy registry — ARCHITECTURE.md §7.3 form.

A profile's ``features.zh.tone_strategy`` is a strategy *name*; the
backend looks it up in :data:`_REGISTRY` and calls the registered
builder.  Adding a new tone standard (e.g. UEB-Chinese, Hong Kong
braille) means:

1. Write a sibling module under :mod:`brailix.backend.zh.tone` that
   defines a :class:`TonePolicy` implementation.
2. End the module with ``register("<name>", builder)``.
3. Reference ``"<name>"`` from a profile's ``features.zh.tone_strategy``.

Neither :mod:`brailix.backend.zh` nor this package's own dispatch
code changes for new standards.  Out-of-tree plugins can register
their own strategies the same way before any ``tone_policy_for``
call.

Two builtins ship:

* :mod:`.basic` — registers ``"basic"``.  Current Chinese Braille /
  cn_current behavior: emit a tone cell whenever ``features.zh.tone``
  is on and the tone is non-neutral.
* :mod:`.ncb_omission` — registers ``"ncb_omission"``.  National Common
  Braille (NCB, GF0019-2018): tone omission grouped by initial +
  zero-initial default + cross-syllable boundary rule; reads the
  table from :attr:`BrailleProfile.zh_tone_omission`.

Both ship under :mod:`brailix.backend.zh.tone` so they're discovered
in lockstep with the registry module — the lazy import in
:func:`_ensure_builtins_registered` runs on the first
:func:`tone_policy_for` call, after which the registry is stable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from brailix.backend.zh.pinyin_parser import ParsedPinyin
from brailix.core.config import BrailleProfile
from brailix.core.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TonePolicy(Protocol):
    """Decide whether a syllable's tone cell should be emitted.

    Implementations are pure functions of the (syllable, parsed,
    next_syllable, next_parsed) tuple — no other state.  ``next_*``
    is None at word boundaries (or whenever the caller can't easily
    peek).  Policies must tolerate ``next_*=None`` by treating it as
    "no successor info available" — typically equivalent to applying
    the base rule without the boundary override.
    """

    def should_emit_tone(
        self,
        *,
        syllable: str,
        parsed: ParsedPinyin,
        next_syllable: str | None = None,
        next_parsed: ParsedPinyin | None = None,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, Callable[[BrailleProfile], TonePolicy]] = {}


def register(
    name: str,
    builder: Callable[[BrailleProfile], TonePolicy],
) -> None:
    """Register a tone strategy under ``name``.

    ``builder`` is a callable that takes a :class:`BrailleProfile`
    and returns a :class:`TonePolicy`.  The factory shape (rather
    than a class) lets a strategy require profile-level resources
    (e.g. ``NcbOmissionPolicy`` needs ``profile.zh_tone_omission``)
    and raise :class:`ConfigurationError` at build time if those
    are missing — instead of crashing on the first decision call.

    First registration wins: re-registering the same name is a harmless
    no-op (so test fixtures re-importing strategy modules need no
    teardown).  Overriding an already-registered name — including a
    builtin — is therefore NOT supported and is silently ignored; tone
    strategies are a closed builtin set, not a plugin seam.
    """
    if name in _REGISTRY:
        return
    _REGISTRY[name] = builder


def registered_names() -> list[str]:
    """Return the names of every currently-registered strategy.

    Used by error messages and test introspection.  Triggers the
    lazy builtin registration so callers always see at least
    ``["basic", "ncb_omission"]``.
    """
    _ensure_builtins_registered()
    return sorted(_REGISTRY)


def tone_policy_for(profile: BrailleProfile) -> TonePolicy:
    """Return the tone policy a profile selects.

    Reads ``features.zh.tone_strategy`` — defaults to ``"basic"`` so
    profiles that don't mention it (cn_current) keep the legacy
    behavior — and looks up the matching builder in :data:`_REGISTRY`.

    Raises :class:`ConfigurationError` when:

    * the strategy name isn't registered (lists known names so the
      user can fix the typo), or
    * the builder itself raises (missing required profile table,
      malformed table, ...).
    """
    _ensure_builtins_registered()
    strategy = profile.feature("zh.tone_strategy", "basic")
    builder = _REGISTRY.get(strategy)
    if builder is None:
        raise ConfigurationError(
            f"profile {profile.name!r}: unknown tone_strategy "
            f"{strategy!r}; registered: {sorted(_REGISTRY)}"
        )
    return builder(profile)


# Alias kept for older call sites — identical semantics.
build_tone_policy = tone_policy_for


# ---------------------------------------------------------------------------
# Builtin discovery
# ---------------------------------------------------------------------------
#
# Import the builtin strategy modules on first lookup so their
# module-level :func:`register` calls populate :data:`_REGISTRY`.
# Lazy (not at package import time) so that third-party callers can
# import :mod:`brailix.backend.zh.tone` to call :func:`register`
# without dragging in builtin classes first.  In practice we only
# care that the registry is populated before the first
# :func:`tone_policy_for` call.


_builtins_loaded = False


def _ensure_builtins_registered() -> None:
    """Idempotently import :mod:`.basic` and :mod:`.ncb_omission`
    so their ``register(...)`` calls fire.

    Tests that snapshot / restore :data:`_REGISTRY` can rely on the
    builtins being present after this is called — useful when
    asserting third-party registrations don't clobber them.
    """
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    # Side-effect imports: each module ends with ``register(...)``.
    from brailix.backend.zh.tone import basic, ncb_omission  # noqa: F401


__all__ = (
    "TonePolicy",
    "build_tone_policy",
    "register",
    "registered_names",
    "tone_policy_for",
)
