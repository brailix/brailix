"""Pinyin frontend subsystem — one public entry point: :func:`annotate`.

Internally backed by a registry of pluggable resolvers
(``null`` / ``pypinyin`` / ``g2pw`` / ``auto``). Callers go through
:func:`annotate`; the right adapter is picked based on
``ctx.options["pinyin_resolver"]`` (defaults to ``"auto"`` which
lazily prefers ``g2pw`` → ``pypinyin`` → ``null``).
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.ir.inline import ChineseToken

_DEFAULT_RESOLVER: str = "auto"


def annotate(
    tokens: list[ChineseToken], ctx: FrontendContext | None = None
) -> list[ChineseToken]:
    """Fill ``ChineseToken.pinyin`` for every token, returning the new list.

    Resolver selection comes from ``ctx.options["pinyin_resolver"]``;
    default ``"auto"``.
    """
    name = _DEFAULT_RESOLVER
    user_dict: dict[str, str] | None = None
    if ctx is not None and ctx.options:
        name = ctx.options.get("pinyin_resolver", _DEFAULT_RESOLVER)
        # Personal pinyin dictionary, injected by a front-end (a
        # proofreading front-end) as plain data on the options bag.  Absent /
        # empty for the
        # bare library and every test that doesn't opt in.
        user_dict = ctx.options.get("user_pinyin_dict") or None

    from brailix.frontend.zh.pinyin.registry import resolver_registry

    resolved = resolver_registry.get(name).resolve(tokens, ctx)
    if user_dict:
        _apply_user_dict(resolved, user_dict)
        if ctx is not None:
            _suppress_low_confidence(ctx, user_dict)
    return resolved


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
    the dictionary never stores them and we double-guard here.  Mutates
    ``tokens`` in place; :class:`ChineseToken` is a mutable slots
    dataclass and the resolver contract only forbids changing
    surface / span, which we don't touch.
    """
    for tok in tokens:
        if len(tok.surface) <= 1:
            continue
        reading = user_dict.get(tok.surface)
        if reading:
            tok.pinyin = reading


__all__ = ("annotate", "list_resolvers")
