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
from brailix.frontend.ja import analyze as _ja_analyze
from brailix.frontend.ja import tokens_to_inline as _ja_tokens_to_inline
from brailix.frontend.math import parse_math_tree
from brailix.frontend.normalize import normalize
from brailix.frontend.segment import segment
from brailix.frontend.zh import (
    insert_cross_kind_boundary_spaces as _zh_boundary_spaces,
)
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
    from collections.abc import Callable

    from brailix.core.config import BrailleProfile
    from brailix.core.context import FrontendContext
    from brailix.ir.inline import InlineNode

    BoundaryHandler = Callable[
        [list[InlineNode], BrailleProfile], list[InlineNode]
    ]


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
    """Japanese :class:`~brailix.core.protocols.LanguageFrontend`.

    Chains the morphological analyzer (selected by
    ``ctx.options["ja_analyzer"]``, default ``auto``) with
    ``tokens_to_inline``: a ``ja_text`` run (kana + kanji) is analyzed
    into tokens carrying katakana pronunciation-form readings, then turned
    into :class:`~brailix.ir.inline.Word` nodes. Pure kana works with no
    analyzer installed (the ``kana`` fallback); kanji readings need
    janome / fugashi / sudachi. Word-boundary spacing (文節分かち書き) is
    inserted by ``tokens_to_inline`` from the analyzer's POS — only when a
    real analyzer is present; the ``kana`` fallback (no POS) keeps the
    source's own spaces.
    """

    prose_types = frozenset({"ja_text"})

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]:
        return _ja_tokens_to_inline(_ja_analyze(surface, ctx), base)


# Per-language frontend registry — the Pipeline routes each prose
# segment to the implementation matching the profile's language. Adding
# a language = register a LanguageFrontend here (or via entry points).
language_frontend_registry: Registry[LanguageFrontend] = Registry(
    "language_frontend", LanguageFrontend
)
language_frontend_registry.register("zh", _ZhFrontend)
language_frontend_registry.register("ja", _JaFrontend)


# Per-language boundary pass — the post-frontend step that inserts
# cross-kind / word-boundary separators on the assembled inline stream
# (the orchestrator runs it once after concatenating per-segment outputs).
# Chinese inserts spaces / connectors at hanzi↔latin / number / math
# boundaries; a language with no handler passes through unchanged — its
# within-segment spacing already ran in its frontend (e.g. Japanese
# wakachigaki in ``tokens_to_inline``). Keyed by the language subtag,
# mirroring the §7.6 registries, so the orchestrator stays language-blind.
boundary_registry: dict[str, BoundaryHandler] = {}


def _zh_boundary(
    nodes: list[InlineNode], profile: BrailleProfile
) -> list[InlineNode]:
    return _zh_boundary_spaces(nodes, profile.zh_compounds)


boundary_registry["zh"] = _zh_boundary


def apply_boundary(
    nodes: list[InlineNode], lang: str, profile: BrailleProfile
) -> list[InlineNode]:
    """Run the boundary pass registered for ``lang`` on the assembled
    inline stream; a language with no registered handler passes through
    unchanged."""
    handler = boundary_registry.get(lang)
    return handler(nodes, profile) if handler else nodes


__all__ = (
    "segment",
    "normalize",
    "tokenize_zh",
    "annotate_pinyin",
    "parse_math_tree",
    "language_frontend_registry",
    "apply_boundary",
)
