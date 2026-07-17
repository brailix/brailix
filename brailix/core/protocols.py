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
        HanziMarker,
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

    def translate_date_marker(
        self,
        marker: HanziMarker,
        follows_number: bool,
        ctx: BackendContext,
        profile: BrailleProfile,
    ) -> list[BrailleCell]:
        """Translate a date marker (年/月/日/号/时/分/秒, …) to cells.

        The language owns both the marker's **reading** and the
        orthographic **connector rule** — whether a number→marker joiner
        cell precedes it when ``follows_number`` is true (Chinese exempts
        the year marker 年; other markers take the connector). The
        language-neutral :func:`brailix.backend.number.translate_date`
        skeleton handles the numeric components and delegates each marker
        here, so no date-marker rule lives outside a ``LanguageBackend``.
        """
        ...


@runtime_checkable
class MathSourceAdapter(Protocol):
    """Convert a math formula from one source format into MathML.

    MathML is the normalized intermediate format for the math
    subsystem. Adapters never emit braille and never build an IR —
    the MathML tree itself is the IR (see :mod:`brailix.frontend.math`).
    """

    source: str  # latex / omml / mathml / plain / ...

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
# Graphics: source-format adapters
# ---------------------------------------------------------------------------


@runtime_checkable
class GraphicSourceAdapter(Protocol):
    """Convert a graphic from one source format into SVG.

    SVG is the normalized intermediate format for the tactile-graphics
    subsystem. Adapters never emit a raster and never build an IR — the
    SVG tree itself is the IR (see :mod:`brailix.frontend.graphics` and
    ``ARCHITECTURE.md``), the exact analogue of MathML
    for math and MusicXML for music.
    """

    source: str  # svg / primitives / image / chart / ...

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
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

    The protocol is deliberately just ``(text) -> cells``. Diagnostics are
    the implementation's affair: the Pipeline's translator reports the
    nested run's warnings into the host compile's collector (so strict
    mode fails and normal mode records — embedded text is never silently
    degraded), and additionally offers an OPTIONAL ``bind_domain(domain,
    span)`` method that
    :meth:`~brailix.core.context.BackendContext.inline_text_translator`
    duck-types to attribute those warnings to the embedding construct. A
    plain function satisfies the protocol; it simply won't get domain
    attribution.
    """

    def __call__(self, text: str) -> list[BrailleCell]: ...


@runtime_checkable
class GraphicAssetResolver(Protocol):
    """Resolve a graphic's asset reference to its raw bytes.

    Injected onto :class:`~brailix.core.context.GraphicsContext` so the
    ``image`` source adapter can turn a document-relative asset name
    (``media/image1.png`` — the name :attr:`brailix.ir.document.
    ImageAlt.target` and :attr:`~brailix.ir.document.DocumentIR.assets`
    share) into pixels without knowing *where* the bytes live: an image
    embedded in a ``.docx`` rides in memory, one authored by hand sits
    beside the source file. Returns ``None`` when the name is unknown, so
    the adapter can fall back to reading a filesystem path. This is the
    same inject-a-callable seam as :class:`InlineTextTranslator`
    (ARCHITECTURE §12) — the resolver is handed in, never imported.

    **Caching identity.** What a resolver returns rides into compiled
    output (an ``image`` fence inlines the resolved bytes into the
    graphic tree), so the pipeline folds a resolver identity into its
    compilation fingerprint and its graphic tree-cache keys (see
    :func:`brailix.pipeline._fingerprint.asset_resolver_identity`). By
    default each resolver *instance* is its own identity and is treated
    as an immutable asset set — two instances never share caches. A
    resolver may instead expose ``cache_identity`` (a string attribute,
    or a zero-arg method returning one) for content-addressed identity:
    equal values share caches deliberately, and a resolver whose
    underlying assets can change mid-life must refresh the value when
    they do.
    """

    def __call__(self, name: str) -> bytes | None: ...


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
    """Encode an IR into a concrete output — the dumb-encoder layer.

    The return type is intentionally :data:`~typing.Any` — concrete
    renderers can produce Unicode braille (``str``), BRF (``bytes``),
    a list of :class:`~brailix.ir.braille.BrailleCell` instances,
    HTML / JSON for proofreading tools, BMP / PNG bytes for tactile
    graphics, or anything else a downstream pipeline cares about.

    Input is whatever IR the renderer consumes: a braille IR — a
    :class:`BrailleDocument` (block-structured) or :class:`BrailleSequence`
    (flat) — for the braille renderers (``unicode`` / ``brf`` / ``cells`` /
    ``layout``), or a :class:`~brailix.ir.tactile.TactileRaster` for the
    tactile-graphics renderers (``bmp`` / ``png`` / ``tactile_preview``).
    Both kinds share the one ``renderer_registry`` and this single protocol;
    each result type passes its own IR to the renderer it names (a braille
    :class:`~brailix.pipeline.TranslationResult` to a braille renderer, a
    :class:`~brailix.pipeline.GraphicResult` to a tactile one). A renderer
    may declare the IR it consumes via a ``consumes`` attribute (``"braille"``
    by default; ``"tactile_raster"`` for the graphics renderers) so a
    braille-only front-end (the CLI) can list just the renderers that apply
    to it.

    The ``ir`` parameter is typed :data:`~typing.Any` on purpose: this one
    protocol covers renderers consuming *different* IR types (a braille IR vs
    a tactile raster), and no single non-``Any`` annotation lets all of them
    structurally conform. Each concrete renderer narrows ``ir`` to the type it
    actually accepts, and ``consumes`` records which that is; callers pass the
    matching IR.
    """

    name: str

    def render(self, ir: Any) -> Any: ...


# Forward declarations for context types that are defined in
# ``core.context`` — kept here as TYPE_CHECKING-only imports to avoid
# circular references at runtime.
if TYPE_CHECKING:
    from brailix.core.context import (
        FrontendContext,
        GraphicsContext,
        MathContext,
        MusicContext,
    )
