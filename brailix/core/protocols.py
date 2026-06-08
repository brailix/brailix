"""Plugin contracts for every pluggable subsystem.

Every adapter, analyzer, parser, backend, and renderer in brailix
conforms to one of these Protocols. The library itself depends only on
these contracts — concrete implementations live behind registries (see
the per-subsystem ``adapters/`` packages) and are loaded lazily so a
user without HanLP installed can still run a jieba-only pipeline.

These are :func:`typing.runtime_checkable` Protocols so registries can
validate at registration time. The structural check only verifies method
names, not signatures, so you should also write unit tests for adapter
behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from brailix.core.config import BrailleProfile
    from brailix.core.context import BackendContext, FrontendContext
    from brailix.ir.braille import (
        BrailleCell,
        BrailleDocument,
        BrailleSequence,
    )
    from brailix.ir.document import Block
    from brailix.ir.inline import (
        ChineseToken,
        HanziChar,
        InlineNode,
        Segment,
        Word,
    )

    NormalizedItem = InlineNode | Segment
    BrailleRenderable = BrailleDocument | BrailleSequence


# ---------------------------------------------------------------------------
# Frontend: text segmentation + Chinese pipeline
# ---------------------------------------------------------------------------


@runtime_checkable
class Segmenter(Protocol):
    """Split a block of raw text into typed inline segments (hanzi /
    number / date / math / latin / punct / ...). The segmenter
    decides *what* a region is, not how to translate it.

    ``ctx`` may be ``None`` so callers without a fully-built
    :class:`FrontendContext` (e.g. low-level unit tests or the
    minimal-config code path in :func:`brailix.frontend.segment`)
    can still drive a segmenter.
    """

    name: str

    def segment(
        self, block: Block, ctx: FrontendContext | None
    ) -> list[Segment]: ...


@runtime_checkable
class Normalizer(Protocol):
    """Promote raw :class:`Segment` runs into typed inline nodes where
    possible (numbers, dates, percent, latin words, math_inline).
    Segments the normalizer doesn't recognize pass through untouched
    so the Pipeline's per-type frontend dispatch can take over."""

    name: str

    def normalize(
        self,
        segments: Iterable[Segment],
        ctx: FrontendContext | None = None,
    ) -> list[NormalizedItem]: ...


@runtime_checkable
class ChineseAnalyzer(Protocol):
    """Tokenize a Chinese text region into words with POS tags.

    Implementations wrap external tokenizers (HanLP, jieba, pkuseg, ...)
    and emit the normalized :class:`ChineseToken` shape so downstream
    code never depends on the underlying library.

    ``ctx`` may be ``None`` so callers (notably the ``auto`` delegating
    adapter) can pass through whatever they received without forcing
    a non-None context just to satisfy the type checker.
    """

    name: str

    def analyze(
        self, text: str, ctx: FrontendContext | None
    ) -> list[ChineseToken]: ...


@runtime_checkable
class PinyinResolver(Protocol):
    """Annotate Chinese tokens with pinyin (numeric-tone form).

    The resolver fills the ``pinyin`` field on tokens; it must not
    change token boundaries or types. Low-confidence readings should
    be reported via the context's :class:`WarningCollector`. ``ctx``
    may be ``None`` for the same reason as :class:`ChineseAnalyzer`.
    """

    name: str

    def resolve(
        self, tokens: list[ChineseToken], ctx: FrontendContext | None
    ) -> list[ChineseToken]: ...


# ---------------------------------------------------------------------------
# Math: source-format adapters + IR builder
# ---------------------------------------------------------------------------


@runtime_checkable
class LanguageFrontend(Protocol):
    """Turn a run of one language's prose into inline IR nodes.

    Registered per language (``frontend.language_frontend_registry``);
    the Pipeline picks the implementation whose key matches the active
    profile's ``language`` primary subtag and routes each prose segment
    to it. This is the seam for adding a language (Japanese, Korean,
    ...): implement ``process`` — tokenize → reading → inline IR for that
    language — declare which segment types carry that language's prose,
    and register it; the orchestrator stays language-agnostic.

    ``prose_types`` are the :class:`~brailix.ir.inline.Segment` type
    names this language's prose appears as (Chinese: ``{"hanzi_text"}``;
    a Japanese frontend might consume ``{"hanzi_text", "kana_text"}``).
    The Pipeline routes a segment here when its type is in this set, so
    the segment type stays script-accurate while routing stays
    language-driven. The matching segmenter (selected by the same
    language subtag) is what emits those types.
    """

    prose_types: Collection[str]

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]: ...


@runtime_checkable
class LanguageBackend(Protocol):
    """Translate a language's prose IR nodes (Word / HanziChar) to cells.

    Registered per language (``backend.dispatch.language_backend_registry``);
    the dispatcher routes prose nodes to the one matching the profile's
    language. This is the seam for a new language's braille rules
    (Japanese kana → cells, ...); language-neutral nodes (Number / Punct
    / Latin / Math / Music) stay on the shared dispatch table.
    """

    def translate_word(
        self, node: Word, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]: ...

    def translate_hanzi_char(
        self, node: HanziChar, ctx: BackendContext, profile: BrailleProfile
    ) -> list[BrailleCell]: ...


@runtime_checkable
class MathSourceAdapter(Protocol):
    """Convert a math formula from one source format into MathML.

    MathML is the normalized intermediate format for the math
    subsystem. Adapters never emit braille and never build an IR —
    the MathML tree itself is the IR (see :mod:`brailix.frontend.math`).
    """

    source: str  # latex / omml / asciimath / mathml / plain / ...

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str: ...


# ---------------------------------------------------------------------------
# Music: source-format adapters
# ---------------------------------------------------------------------------


@runtime_checkable
class MusicSourceAdapter(Protocol):
    """Convert score data from one source format into MusicXML.

    MusicXML is the normalized intermediate format for the music
    subsystem. Adapters never emit braille and never build an IR —
    the MusicXML tree itself is the IR (see
    :mod:`brailix.frontend.music` and ``ARCHITECTURE.md``).
    """

    source: str  # musicxml / mxl / midi / abc / plain / ...

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str: ...


# ---------------------------------------------------------------------------
# Backend support seam: inline-text translation
# ---------------------------------------------------------------------------
#
# The one sanctioned backend→frontend dependency (ARCHITECTURE §12). A few
# backend handlers embed natural-language prose — music ``<words>``
# directions, inline lyrics, Chinese chemical-reaction conditions. Rather
# than re-implement the zh / latin text path inside the backend, the
# Pipeline injects a translator implementing this Protocol onto
# ``BackendContext.options`` (read it via
# :meth:`BackendContext.inline_text_translator`). It is dependency
# injection, not an import — the backend never imports the frontend. When
# no translator is wired (a bare backend run, or a unit test), handlers
# fall back to a warning + marker.


@runtime_checkable
class InlineTextTranslator(Protocol):
    """Translate a run of inline prose into braille cells.

    Injected by :class:`~brailix.pipeline.Pipeline` so backend handlers
    that embed natural-language text can render it through the zh / latin
    frontend path without importing the frontend. See ARCHITECTURE §12.
    """

    def __call__(self, text: str) -> list[BrailleCell]: ...


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
#
# Note: there is deliberately no ``Backend`` Protocol. The backend isn't a
# pluggable-by-name adapter — it's a node-type dispatcher (see
# ``backend/dispatch.py`` and ARCHITECTURE §6.1), so it has no registry
# and no name→impl contract to satisfy. New braille standards are added via
# Profile JSON + resources, not by registering a Backend implementation.


@runtime_checkable
class Renderer(Protocol):
    """Encode a braille IR into a concrete output.

    The return type is intentionally :data:`~typing.Any` — concrete
    renderers can produce Unicode braille (``str``), BRF (``bytes``),
    a list of :class:`~brailix.ir.braille.BrailleCell` instances,
    HTML / JSON for proofreading tools, or anything else a downstream
    pipeline cares about. Input is either a :class:`BrailleDocument`
    (block-structured) or a :class:`BrailleSequence` (flat).
    """

    name: str

    def render(self, bir: BrailleRenderable) -> Any: ...


# Forward declarations for context types that are defined in
# ``core.context`` — kept here as TYPE_CHECKING-only imports to avoid
# circular references at runtime.
if TYPE_CHECKING:
    from brailix.core.context import (
        FrontendContext,
        MathContext,
        MusicContext,
    )
