"""Route inline IR nodes to the correct translator.

The dispatcher is the only piece of the Backend that knows the full
inline-type-to-translator map. Each translator submodule (zh, ja, number,
punct, latin, phonetic, math, music) exposes pure functions; the dispatcher
composes them via a ``type -> translator`` table.

Block-level translation (``translate_block`` / ``translate_document``
/ ``expand_block``) lives in :mod:`brailix.backend.block`, which
imports ``translate_node`` from here for inline children — a clean
one-way dependency.

Richer Latin translators replace the V1 fallback in ``latin``
without touching the dispatcher.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
from brailix.core.protocols import LanguageBackend
from brailix.core.registry import Registry
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Date,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    PhoneticInline,
    Punct,
    Quantity,
    Space,
    Unknown,
    Word,
)

_Translator = Callable[[Any, BackendContext, BrailleProfile], list[BrailleCell]]

# Inline node type -> translator.  Dispatch is by exact ``type(node)``:
# the IR inline set is closed — every node is a direct dataclass leaf of
# :class:`InlineNode` with no subclass hierarchy to resolve — so an O(1)
# table is both correct and faster than an isinstance ladder, and adding
# a node type is a one-line entry rather than a new branch.  ``LatinWord``
# and ``LatinAcronym`` share the single Latin translator.
_DISPATCH: dict[type[InlineNode], _Translator] = {
    Number: number_backend.translate_number,
    Date: number_backend.translate_date,
    Percent: number_backend.translate_percent,
    Quantity: number_backend.translate_quantity,
    Punct: punct_backend.translate_punct,
    Space: punct_backend.translate_space,
    Connector: punct_backend.translate_connector,
    LatinWord: latin_backend.translate_latin,
    LatinAcronym: latin_backend.translate_latin,
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

    def translate_hanzi_char(
        self, node: HanziChar, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]:
        return zh_backend.translate_hanzi_char(node, ctx, profile)

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

    def translate_hanzi_char(
        self, node: HanziChar, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]:
        return ja_backend.translate_hanzi_char(node, ctx, profile)

    def translate_date_marker(
        self,
        marker: HanziMarker,
        follows_number: bool,
        ctx: BackendContext,
        profile: BrailleProfile,
    ) -> list[BrailleCell]:
        return ja_backend.translate_date_marker(marker, follows_number, ctx, profile)


# Per-language backend registry — the dispatcher routes prose nodes
# (Word / HanziChar) to the implementation matching the profile's
# language. Language-neutral nodes (Number / Punct / Latin / Math /
# Music) stay on ``_DISPATCH``. Adding a language = register here.
language_backend_registry: Registry[LanguageBackend] = Registry(
    "language_backend", LanguageBackend
)
language_backend_registry.register("zh", _ZhBackend)
language_backend_registry.register("ja", _JaBackend)

# Prose node types routed by the profile's language rather than the
# static dispatch table.
_LANGUAGE_NODE_TYPES = (Word, HanziChar)


def translate_node(
    node: InlineNode, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    """Dispatch a single InlineNode to its translator.

    Prose nodes (Word / HanziChar) route to the profile language's
    registered :class:`LanguageBackend`; every other (language-neutral)
    node goes through the shared ``_DISPATCH`` table.
    """
    if isinstance(node, _LANGUAGE_NODE_TYPES):
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
        if isinstance(node, Word):
            return backend.translate_word(node, ctx, profile)
        return backend.translate_hanzi_char(node, ctx, profile)

    handler = _DISPATCH.get(type(node))
    if handler is not None:
        return handler(node, ctx, profile)

    ctx.warnings.warn(
        code="UNHANDLED_NODE_TYPE",
        message=f"no translator for {type(node).__name__}",
        surface=getattr(node, "surface", ""),
        span=getattr(node, "span", None),
        source="backend.dispatch",
    )
    return []
