"""Pinyin frontend subsystem — two subsystem entry points: :func:`annotate`
and :func:`list_resolvers` (registry enumeration for CLI / editor pickers).

Internally backed by a registry of pluggable resolvers
(``null`` / ``pypinyin`` / ``g2pm`` / ``g2pw`` / ``auto``). Callers go
through :func:`annotate`; the right adapter is picked based on
``ctx.options["pinyin_resolver"]`` (defaults to ``"auto"`` which
lazily prefers ``g2pm`` → ``g2pw`` → ``pypinyin`` → ``null``).
"""

from __future__ import annotations

import math as _math
from dataclasses import replace as _replace
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Protocol as _Protocol
from typing import runtime_checkable as _runtime_checkable

from brailix.core.context import FrontendContext
from brailix.core.errors import FrontendContractError
from brailix.frontend.zh.tokens import ChineseToken

if _TYPE_CHECKING:
    from brailix.core.span import Span

# The ``auto`` adapter's registered name — what a caller who names
# nothing gets. Matches the corresponding :class:`brailix.Pipeline`
# field default, which is the library-wide declaration; not imported
# from there because the orchestrator sits ABOVE this layer. Pinned
# equal by ``tests/frontend/test_default_adapter_names.py``.
_AUTO = "auto"


@_runtime_checkable
class PinyinResolver(_Protocol):
    """Annotate Chinese tokens with pinyin (numeric-tone form).

    The resolver fills the ``pinyin`` field on tokens; it must not
    change token boundaries or types. Low-confidence readings should
    be reported via the context's
    :class:`~brailix.core.errors.WarningCollector`. ``ctx`` may be ``None``
    so callers (notably the ``auto`` delegating adapter) can pass through
    whatever they received.

    "Must not change boundaries or types" is enforced, not merely stated:
    :func:`annotate` compares what comes back against what went in and raises
    :class:`~brailix.core.errors.FrontendContractError` if the count, order,
    surfaces, spans or POS moved (see :func:`_check_resolver_output`).
    ``pinyin`` and ``confidence`` are the two fields a resolver owns.

    Lives here rather than in :mod:`brailix.core.protocols`: a reading engine
    is a Chinese concern — the language declares what can be chosen for it —
    and a protocol whose signature names Chinese types belongs with the
    language, not on a layer every language shares. Japanese has no
    counterpart at all: its readings come out of its analyzer.
    """

    name: str

    def resolve(
        self, tokens: list[ChineseToken], ctx: FrontendContext | None
    ) -> list[ChineseToken]: ...


def annotate(
    tokens: list[ChineseToken], ctx: FrontendContext | None = None
) -> list[ChineseToken]:
    """Fill ``ChineseToken.pinyin`` for every token, returning the new list.

    Resolver selection comes from ``ctx.options["pinyin_resolver"]``;
    default ``"auto"``.
    """
    name = _AUTO
    user_dict: dict[str, str] | None = None
    if ctx is not None and ctx.options:
        name = ctx.options.get("pinyin_resolver", _AUTO)
        # Personal pinyin dictionary, injected by a front-end (a
        # proofreading front-end) as plain data on the options bag.  Absent /
        # empty for the
        # bare library and every test that doesn't opt in.
        user_dict = ctx.options.get("user_pinyin_dict") or None

    from brailix.frontend.zh.pinyin.registry import resolver_registry

    # Snapshotted BEFORE the call, not compared against ``tokens`` after it:
    # the ``null`` resolver hands back the caller's own objects, so a resolver
    # that rewrote a surface in place would be comparing each token with
    # itself and every check would pass.
    identity = [(t.surface, t.span, t.pos) for t in tokens]
    resolved = resolver_registry.get(name).resolve(tokens, ctx)
    _check_resolver_output(identity, resolved, name)
    if user_dict:
        _apply_user_dict(resolved, user_dict)
        if ctx is not None:
            _suppress_low_confidence(ctx, user_dict)
    return resolved


def _check_resolver_output(
    before: list[tuple[str, Span | None, str | None]],
    after: object,
    adapter: str,
) -> None:
    """Verify a resolver annotated the tokens rather than rewriting them.

    :class:`PinyinResolver` says in as many words that a resolver "must not
    change token boundaries or types" — a rule that was stated in three
    docstrings and checked nowhere, while :func:`annotate` handed the
    adapter's return value straight back to the orchestrator. The registry is
    open, so the tokens a third party returns become the document: the
    surfaces get written as braille, the spans become every resulting cell's
    source coordinates, and the POS rides into the IR.

    A resolver fills in readings. It may therefore change ``pinyin`` and
    ``confidence`` and nothing else — not the number of tokens, not their
    order, not a surface, span or POS. Each of those is checked against the
    list that went in, so a resolver that silently re-segments (or drops a
    token it could not read) is named here instead of surfacing later as
    braille that does not match the source.

    **The two fields it may change are checked too**, which is the half that
    was missing: everything a resolver is forbidden to touch was compared, and
    the only two it is *allowed* to touch went through unread.
    ``ChineseToken`` declares ``pinyin: str | None`` and
    ``confidence: float | None``, and a resolver returning ``pinyin=123``
    reached the backend's syllable parser, which called ``.strip()`` on it —
    an ``AttributeError`` from a module several layers from the adapter that
    caused it, past every caller catching
    :class:`~brailix.core.errors.BrailixError`. A boundary that names the
    adapter and the token index is exactly what this function is for.

    ``confidence`` is a probability: the g2pW adapter compares it against a
    0.75 threshold to decide whether to warn, so a value outside ``[0, 1]``
    (or a ``NaN``, which fails every comparison silently) is not a confidence,
    it is a number that will read as one. An ``int`` is accepted and stored as
    the ``float`` the field declares — ``1`` is a legitimate certainty, and a
    field typed ``float`` holding an ``int`` is the kind of thing that reaches
    JSON before anyone notices.

    Identity is deliberately NOT required: the ``null`` resolver returns the
    caller's own token objects while the others build fresh ones with
    :func:`dataclasses.replace`, and both are correct.
    """
    if not isinstance(after, list):
        raise FrontendContractError(
            f"pinyin resolver {adapter!r} returned {type(after).__name__}, not "
            f"a list of ChineseToken"
        )
    if len(after) != len(before):
        raise FrontendContractError(
            f"pinyin resolver {adapter!r} returned {len(after)} tokens for "
            f"{len(before)} given; a resolver fills in readings, it does not "
            f"re-segment"
        )
    for i, (was, now) in enumerate(zip(before, after, strict=True)):
        if not isinstance(now, ChineseToken):
            raise FrontendContractError(
                f"pinyin resolver {adapter!r} returned {type(now).__name__} at "
                f"index {i}, not a ChineseToken"
            )
        if (now.surface, now.span, now.pos) != was:
            raise FrontendContractError(
                f"pinyin resolver {adapter!r} changed token {i} from "
                f"surface={was[0]!r} span={was[1]} pos={was[2]!r} to "
                f"surface={now.surface!r} span={now.span} pos={now.pos!r}; "
                f"only pinyin and confidence may change"
            )
        if now.pinyin is not None and not isinstance(now.pinyin, str):
            raise FrontendContractError(
                f"pinyin resolver {adapter!r} gave token {i} "
                f"({now.surface!r}) pinyin {now.pinyin!r} of type "
                f"{type(now.pinyin).__name__}; ChineseToken declares "
                f"pinyin: str | None"
            )
        if now.confidence is not None:
            now.confidence = _checked_confidence(now, i, adapter)


def _checked_confidence(
    token: ChineseToken, index: int, adapter: str
) -> float:
    """``token.confidence`` as a probability, or raise naming the adapter."""
    value = token.confidence
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrontendContractError(
            f"pinyin resolver {adapter!r} gave token {index} "
            f"({token.surface!r}) confidence {value!r} of type "
            f"{type(value).__name__}; ChineseToken declares "
            f"confidence: float | None"
        )
    number = float(value)
    if not _math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise FrontendContractError(
            f"pinyin resolver {adapter!r} gave token {index} "
            f"({token.surface!r}) confidence {number}; a confidence is a "
            f"probability in [0, 1] — it is compared against a threshold to "
            f"decide whether to warn about the reading"
        )
    return number


def list_resolvers() -> list[str]:
    """Return the names of every registered pinyin-resolver adapter.

    Sorted, and independent of installed extras: registration records a
    lazy loader, so ``"g2pw"`` is listed even before its wheel is
    present (selecting it raises
    :class:`~brailix.core.errors.MissingExtraError` only on load).
    Front-ends build a resolver picker from this rather than a
    duplicated whitelist.
    """
    from brailix.frontend.zh.pinyin.registry import resolver_registry

    return resolver_registry.names()


def available_resolvers() -> list[str]:
    """:func:`list_resolvers` filtered to the ones installed right now.

    See :func:`~brailix.frontend.zh.analyzer.available_analyzers` for why a
    picker wants this rather than the full roster. It matters more here than
    it does for segmentation: a segmentation engine that cannot load leaves
    the ``char`` fallback, which still produces braille, while a pinyin
    engine that cannot load leaves ``null`` — and a Chinese word with no
    reading produces no cells at all.
    """
    from brailix.frontend.zh.pinyin.registry import resolver_registry

    return resolver_registry.available_names()


def _suppress_low_confidence(
    ctx: FrontendContext, user_dict: dict[str, str]
) -> None:
    """Retract ``LOW_CONFIDENCE_PINYIN`` warnings for dictionary words.

    The resolver emits its polyphone-uncertainty warnings *before* the
    dictionary post-pass runs, so a word the user has pinned in their
    personal dictionary still carries a stale "is this reading right?"
    nudge.  Once the reading is dictionary-resolved it's no longer in
    question, so drop that one diagnostic.  Other warnings (length
    mismatch, unknown character) are about different problems and stay.
    """
    ctx.warnings.discard(
        lambda w: w.code == "LOW_CONFIDENCE_PINYIN" and w.surface in user_dict
    )


def _apply_user_dict(
    tokens: list[ChineseToken], user_dict: dict[str, str]
) -> None:
    """Override readings from the user's personal pinyin dictionary.

    Applied as a post-pass *after* the configured resolver, so the
    user's explicit, persisted choice wins over the automatic reading
    for every document.  The dictionary is not an alternative
    resolver — it's a thin override layer on top of whichever resolver
    ran — which is why it lives here rather than in the registry.

    Multi-character surfaces only: single characters are too
    context-dependent to force globally (the polyphone trap — the same
    character legitimately reads differently sentence to sentence), so
    the dictionary never stores them and we double-guard here.

    Replaces overridden entries in ``tokens`` with fresh
    :class:`ChineseToken` objects rather than mutating in place: the
    ``null`` resolver returns the caller's own token objects (only the
    list is copied), so an in-place write would leak back into the
    caller's input. We touch only ``pinyin`` — surface / span are
    preserved by :func:`dataclasses.replace`.
    """
    for i, tok in enumerate(tokens):
        if len(tok.surface) <= 1:
            continue
        reading = user_dict.get(tok.surface)
        if reading and reading != tok.pinyin:
            # The校对员 pinned this reading in their personal dict, so it's
            # certain — clear the resolver's stale confidence so a low value
            # doesn't serialize onto a now-definite reading (matches the
            # withdrawn LOW_CONFIDENCE_PINYIN warning's semantics).
            tokens[i] = _replace(tok, pinyin=reading, confidence=None)


# This subsystem publishes its own contract and its own two entry points.
# ``ChineseToken`` is imported above because :class:`PinyinResolver`'s
# signature names it, but it is NOT published here: the token is the mediator
# the analyzer and this resolver hand between them, and belongs to neither end
# (:mod:`brailix.frontend.zh.tokens`, which is where the extension manifest and
# the extension guide both point). Publishing it from one end as well would put
# the shared format's compatibility promise at an address that is free to be
# replaced along with that end.
__all__ = (
    "PinyinResolver",
    "annotate",
    "available_resolvers",
    "list_resolvers",
)
