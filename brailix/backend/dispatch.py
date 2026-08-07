"""Route inline IR nodes to the correct translator.

The dispatcher is the only piece of the Backend that knows the full
inline-type-to-translator map. Each translator submodule (zh, ja, number,
punct, latin, phonetic, math, music) exposes pure functions; the dispatcher
composes them via a ``type -> translator`` table.

Block-level translation (``translate_document`` / ``expand_block``)
lives in :mod:`brailix.backend.block`, which
imports ``translate_node`` from here for inline children — a clean
one-way dependency.

Richer Latin translators replace the V1 fallback in ``latin``
without touching the dispatcher.
"""

from __future__ import annotations

from collections.abc import Callable as _Callable
from typing import Any as _Any

from brailix.backend import ja as ja_backend
from brailix.backend import latin as latin_backend
from brailix.backend import math as math_backend
from brailix.backend import music as music_backend
from brailix.backend import number as number_backend
from brailix.backend import phonetic as phonetic_backend
from brailix.backend import punct as punct_backend
from brailix.backend import zh as zh_backend
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.errors import BackendContractError
from brailix.core.protocols import LanguageBackend
from brailix.core.registry import Registry
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Date,
    HanziMarker,
    InlineNode,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    PhoneticInline,
    Punct,
    Space,
    Unknown,
    Word,
)

_Translator = _Callable[[_Any, BackendContext, BrailleProfile], list[BrailleCell]]

# Inline node type -> translator.  Dispatch is by exact ``type(node)``:
# the IR inline set is closed — every node is a direct dataclass leaf of
# :class:`InlineNode` with no subclass hierarchy to resolve — so an O(1)
# table is both correct and faster than an isinstance ladder, and adding
# a node type is a one-line entry rather than a new branch.  ``LatinWord``
# routes to the single Latin translator.
_DISPATCH: dict[type[InlineNode], _Translator] = {
    Number: number_backend.translate_number,
    Date: number_backend.translate_date,
    Percent: number_backend.translate_percent,
    Punct: punct_backend.translate_punct,
    Space: punct_backend.translate_space,
    Connector: punct_backend.translate_connector,
    LatinWord: latin_backend.translate_latin,
    CodeInline: punct_backend.translate_code_inline,
    PhoneticInline: phonetic_backend.translate_phonetic,
    MathInline: math_backend.translate,
    MusicInline: music_backend.translate,
    Unknown: punct_backend.translate_unknown,
}


class _ZhBackend:
    """Chinese :class:`~brailix.core.protocols.LanguageBackend`: the
    prose-node translators from :mod:`brailix.backend.zh`."""

    def translate_word(
        self, node: Word, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]:
        return zh_backend.translate_word(node, ctx, profile)

    def translate_date_marker(
        self,
        marker: HanziMarker,
        follows_number: bool,
        ctx: BackendContext,
        profile: BrailleProfile,
    ) -> list[BrailleCell]:
        return zh_backend.translate_date_marker(marker, follows_number, ctx, profile)


class _JaBackend:
    """Japanese :class:`~brailix.core.protocols.LanguageBackend`: the
    prose-node translators from :mod:`brailix.backend.ja`."""

    def translate_word(
        self, node: Word, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]:
        return ja_backend.translate_word(node, ctx, profile)

    def translate_date_marker(
        self,
        marker: HanziMarker,
        follows_number: bool,
        ctx: BackendContext,
        profile: BrailleProfile,
    ) -> list[BrailleCell]:
        return ja_backend.translate_date_marker(marker, follows_number, ctx, profile)


# Per-language backend registry — the dispatcher routes prose nodes
# (Word) to the implementation matching the profile's
# language. Language-neutral nodes (Number / Punct / Latin / Math /
# Music) stay on ``_DISPATCH``. Adding a language = register here.
language_backend_registry: Registry[LanguageBackend] = Registry(
    "language_backend", LanguageBackend
)
language_backend_registry.register("zh", _ZhBackend)
language_backend_registry.register("ja", _JaBackend)

# The prose node type routed by the profile's language rather than by the
# static dispatch table. One type, one :class:`LanguageBackend` method
# (``translate_word``) — it was a tuple of two while single characters had
# their own node type, and the branch picking between the two methods
# outlived that type as an ``else`` nothing could reach.
_LANGUAGE_NODE_TYPE = Word


def _enforce_source_spans(
    cells: list[BrailleCell], node: InlineNode, origin: str
) -> list[BrailleCell]:
    """Post-condition at a translator boundary: a node that carries a
    ``span`` must come back as cells that ALL carry a ``source_span``.

    "Every cell maps to a source span" (ARCHITECTURE#arch-traceability) is what
    proofreading navigation is built on, and the backend upholds it via the
    span-carrying factories (:func:`brailix.ir.braille.blank_cell` & co).
    The dispatcher is where third-party code enters — an open
    :data:`language_backend_registry` implementation may return the
    span-less :data:`~brailix.ir.braille.BLANK_CELL` sentinel — so the
    invariant is checked HERE, naming the offending translator, instead of
    surfacing later as a proofread jump to nowhere. A node with **no** span
    (hand-built IR) promises nothing, so its cells are exempt — that is the
    documented soft spot of hand-built documents, not a backend defect.

    Applied at **every** boundary a ``LanguageBackend`` is called across, not
    just :func:`translate_node`: the second one is
    :func:`brailix.backend.number.translate_date`, which resolves the same
    registry to translate a date's markers. Calling straight through there
    would let a plugin whose ``translate_word`` is checked return span-less
    cells from ``translate_date_marker`` and break traceability with every
    contract test still green. A new call site that resolves the registry
    must come through here too.

    Raises :class:`BackendContractError` unconditionally — this is a code
    defect, not user input, so no run mode may swallow it.
    """
    if getattr(node, "span", None) is None:
        return cells
    for i, cell in enumerate(cells):
        if cell.source_span is None:
            raise BackendContractError(
                f"{origin} returned cell {i} (role={cell.role!r}) without a "
                f"source_span for {type(node).__name__} "
                f"{getattr(node, 'surface', '')!r}, which carries span "
                f"{node.span}; every cell emitted for a span-carrying node "
                f"must be traceable (ARCHITECTURE#arch-traceability) — use the "
                f"span-carrying factories in brailix.ir.braille instead of "
                f"the span-less sentinels"
            )
    return cells


def translate_node(
    node: InlineNode, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Dispatch a single InlineNode to its translator.

    Prose nodes (Word) route to the profile language's
    registered :class:`LanguageBackend`; every other (language-neutral)
    node goes through the shared ``_DISPATCH`` table.

    Every dispatch enforces the traceability post-condition (see
    :func:`_enforce_source_spans`): span-carrying nodes must translate to
    span-carrying cells, or the offending backend is named in a
    :class:`BackendContractError` at the exact boundary it violated.
    """
    if isinstance(node, _LANGUAGE_NODE_TYPE):
        lang = profile.language.split("-")[0]
        if not language_backend_registry.has(lang):
            ctx.warnings.warn(
                code="NO_LANGUAGE_BACKEND",
                message=f"no backend registered for language {lang!r}",
                surface=getattr(node, "surface", ""),
                span=getattr(node, "span", None),
                source="backend.dispatch",
            )
            return []
        backend = language_backend_registry.get(lang)
        return _enforce_source_spans(
            backend.translate_word(node, ctx, profile),
            node,
            f"language backend {lang!r}",
        )

    handler = _DISPATCH.get(type(node))
    if handler is not None:
        return _enforce_source_spans(
            handler(node, ctx, profile),
            node,
            f"translator {getattr(handler, '__qualname__', handler)!r}",
        )

    ctx.warnings.warn(
        code="UNHANDLED_NODE_TYPE",
        message=f"no translator for {type(node).__name__}",
        surface=getattr(node, "surface", ""),
        span=getattr(node, "span", None),
        source="backend.dispatch",
    )
    return []


# One name, and it is the registry: this module is on the **extension
# surface** (see :mod:`brailix`) as the address a third-party language backend
# registers with, and that is the whole of what it promises. ``translate_node``
# is the module's own entry point, called by :mod:`brailix.backend.block` and
# :mod:`brailix.backend.number` — an explicit ``from ... import translate_node``
# never consulted ``__all__``, so those keep working while the compatibility
# promise stays exactly the one the manifest states. Without this list,
# ``import *`` here also published two dozen IR node types and the whole
# language-backend plumbing.
__all__ = ("language_backend_registry",)
