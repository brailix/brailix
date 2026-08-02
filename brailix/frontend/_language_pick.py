"""Pick an adapter by the active language — what the ``auto`` segmenter and
``auto`` normalizer share.

Both answer the same question: *this document is in language L; is there an
adapter registered under L, or do I use the built-in one?* Segmentation and
normalization are two steps of one frontend infrastructure layer rather than
two independently replaceable components, so the answer lives in one place.
(Contrast the language *implementations* — zh and ja — which deliberately do
not share code with each other.)

The language reaches an adapter through ``ctx.options["language"]``, put
there by the orchestrator, which is the layer that knows the active profile.
An adapter is handed the fact rather than resolving it, so nothing below the
orchestrator has to load a profile to find out what it is translating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext
    from brailix.core.registry import Registry

# Where the orchestrator publishes the active profile's language subtag.
LANGUAGE_OPTION = "language"


def pick_by_language(
    registry: Registry[Any], ctx: FrontendContext | None, fallback: str
) -> str:
    """Name of the adapter to use for ``ctx``'s language.

    An adapter registered under the language's primary subtag wins; anything
    else falls back to ``fallback`` (the built-in, language-neutral adapter).
    A context with no language — a direct call in a test, a caller that built
    its own :class:`FrontendContext` — takes the fallback too, which is the
    behaviour every such caller had before languages were pluggable.

    This is *not* the "which engine is installed" kind of automatic that the
    analyzer / resolver chains do: nothing here can fail to load, so there is
    no chain to walk and no exception to step over. Both are ``auto`` to a
    caller for the same reason — "pick the right one for this document, I
    don't want to know the names" — and that is the level the shared name is
    claiming, not a shared mechanism underneath.
    """
    if ctx is None or not ctx.options:
        return fallback
    lang = ctx.options.get(LANGUAGE_OPTION)
    if isinstance(lang, str) and lang and registry.has(lang):
        return lang
    return fallback


__all__ = ("LANGUAGE_OPTION", "pick_by_language")
