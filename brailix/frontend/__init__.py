"""Frontend layer: text → structured IR.

The frontend never emits braille. Its job is to identify *what* each
region of input is (hanzi run, number, date, latin word, math
fragment, ...) and produce a typed
:class:`~brailix.ir.inline` representation. The Backend then
decides how to write each type as braille.

## One public callable per subsystem

Each subsystem under ``frontend/`` exposes **a single high-level
entry point** plus a registry of internal adapter implementations.
Users call the entry point with a :class:`FrontendContext`; which
concrete adapter runs is decided by ``ctx.options[...]`` (or by an
``"auto"`` default that probes what's installed):

==================  ==============================================
Module              Public callable
------------------  ----------------------------------------------
``frontend.segment``  :func:`segment` (selected by ``segmenter``)
``frontend.normalize`` :func:`normalize` (selected by ``normalizer``)
``frontend.zh``       :func:`tokenize` (selected by ``zh_analyzer``)
``frontend.zh.pinyin``   :func:`annotate` (selected by ``pinyin_resolver``)
``frontend.math``     :func:`parse_math_tree` (source via :class:`MathContext`)
==================  ==============================================

Custom adapters register themselves with the corresponding internal
registry (``analyzer_registry`` in :mod:`frontend.zh.analyzer.registry`,
``resolver_registry`` in :mod:`frontend.zh.pinyin.registry`, etc.) and
then become available by name. End users never touch the registries
directly — they set the name via ``ctx.options`` (or the equivalent
:class:`~brailix.Pipeline` constructor argument) and call the
public function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from brailix.core.protocols import LanguageFrontend
from brailix.core.registry import Registry
from brailix.frontend.ja import prose_to_inline as _ja_prose_to_inline
from brailix.frontend.math import parse_math_tree
from brailix.frontend.normalize import normalize
from brailix.frontend.segment import segment
from brailix.frontend.zh import (
    shift_token_spans as _shift_zh_spans,
)
from brailix.frontend.zh import (
    tokenize as tokenize_zh,
)
from brailix.frontend.zh import (
    tokens_to_inline as _zh_to_inline,
)
from brailix.frontend.zh.pinyin import annotate as annotate_pinyin

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext
    from brailix.ir.inline import InlineNode


class _ZhFrontend(LanguageFrontend):
    """Chinese :class:`~brailix.core.protocols.LanguageFrontend`:
    tokenize → pinyin → inline IR.

    Lives here (frontend orchestration level), not inside
    ``frontend.zh.analyzer``, because it chains the analyzer with the
    pinyin resolver — and the analyzer must not import
    ``frontend.zh.pinyin`` (subsystem independence, ARCHITECTURE §7.1).
    """

    # Chinese prose reaches the frontend as ``hanzi_text`` segments (Han
    # ideograph runs from the default segmenter). The Pipeline routes
    # those here via this declaration rather than a hard-coded literal.
    prose_types = frozenset({"hanzi_text"})

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]:
        tokens = tokenize_zh(surface, ctx)
        tokens = _shift_zh_spans(tokens, base)
        tokens = annotate_pinyin(tokens, ctx)
        return _zh_to_inline(tokens)


class _JaFrontend(LanguageFrontend):
    """Japanese :class:`~brailix.core.protocols.LanguageFrontend` (J1).

    Pure-kana: a kana run becomes one :class:`~brailix.ir.inline.Word`
    whose ``reading`` is the kana itself; kanji (``hanzi_text``) degrades
    to ``reading=None`` placeholders until J2 fills readings. The
    per-segment work lives in
    :func:`brailix.frontend.ja.prose_to_inline`; this shell only declares
    the prose types it claims. No analyzer chaining yet (J2) and no
    wakachigaki yet (J3), so ``ctx`` is currently unused.
    """

    prose_types = frozenset({"kana_text", "hanzi_text"})

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]:
        return _ja_prose_to_inline(surface, base)


# Per-language frontend registry — the Pipeline routes each prose
# segment to the implementation matching the profile's language. Adding
# a language = register a LanguageFrontend here (or via entry points).
language_frontend_registry: Registry[LanguageFrontend] = Registry(
    "language_frontend", LanguageFrontend
)
language_frontend_registry.register("zh", _ZhFrontend)
language_frontend_registry.register("ja", _JaFrontend)

__all__ = (
    "segment",
    "normalize",
    "tokenize_zh",
    "annotate_pinyin",
    "parse_math_tree",
    "language_frontend_registry",
)
