"""Plugin contracts for every pluggable subsystem.

Every adapter, analyzer, parser, backend, and renderer in brailix
conforms to one of these Protocols. The library itself depends only on
these contracts — concrete implementations live behind registries (see
the per-subsystem ``adapters/`` packages) and are loaded lazily so a
user without HanLP installed can still run a jieba-only pipeline.

These are :func:`typing.runtime_checkable` Protocols so a registry can
validate the adapters it hands out. The check happens at **load time**, not
registration time: :meth:`brailix.core.registry.Registry.register` only
stores the loader (that is what keeps the heavy import lazy), and the
``isinstance`` check runs in :meth:`~brailix.core.registry.Registry.get`,
once the loader has produced an instance. So a non-conforming adapter
surfaces on first use, not at import of the module that registered it. The
structural check only verifies method names, not signatures, so you should
also write unit tests for adapter behaviour.
"""

from __future__ import annotations

from collections.abc import Collection as _Collection
from collections.abc import Iterable as _Iterable
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any
from typing import Protocol as _Protocol
from typing import runtime_checkable as _runtime_checkable

from brailix.core.config import BrailleProfile as _BrailleProfile

# The two families below are the ONLY annotations in the package that a
# runtime introspector cannot read back, and both are deferred by a rule that
# outranks introspection:
#
# * ``brailix.ir`` — ``brailix.core`` may *annotate* against the IR but must
#   never import it at runtime, because IR imports core and the reverse edge
#   would close a cycle and turn ``import brailix.ir`` into "load the whole
#   compiler" (``tests/test_core_layering.py::
#   test_core_does_not_import_ir_at_runtime``);
# * ``brailix.core.context`` — it imports *this* module at runtime for its own
#   accessor annotations, so binding it back here is the same cycle one layer
#   in.
#
# Everything else this module annotates against is bound above, aliased, so
# ``typing.get_type_hints`` reads it. The exemption is registered and its
# extent checked in ``tests/test_public_api.py`` — a *new* deferred name that
# is not from one of those two modules fails there.
if _TYPE_CHECKING:
    from brailix.core.context import BackendContext, FrontendContext
    from brailix.ir.braille import (
        BrailleCell,
        BrailleDocument,
        BrailleSequence,
    )
    from brailix.ir.document import Block
    from brailix.ir.inline import (
        HanziMarker,
        InlineNode,
        Segment,
        Word,
    )

    NormalizedItem = InlineNode | Segment
    BrailleRenderable = BrailleDocument | BrailleSequence


# ---------------------------------------------------------------------------
# Frontend: text segmentation + normalization
#
# The per-language analyzer / reading-resolver protocols are NOT here: they
# live with their language (``frontend.zh.analyzer.ChineseAnalyzer``,
# ``frontend.ja.analyzer.JapaneseAnalyzer``), because a protocol naming one
# language's types is that language's contract, not the core's. What stays
# is what every language routes through.
# ---------------------------------------------------------------------------


@_runtime_checkable
class Segmenter(_Protocol):
    """Split a block of raw text into typed inline segments (hanzi /
    number / date / math / latin / punct / ...). The segmenter
    decides *what* a region is, not how to translate it.

    ``ctx`` may be ``None`` so callers without a fully-built
    :class:`FrontendContext` (e.g. low-level unit tests or the
    minimal-config code path in :func:`brailix.frontend.segmentation`)
    can still drive a segmenter.
    """

    name: str

    def segment(
        self, block: Block, ctx: FrontendContext | None
    ) -> list[Segment]: ...


@_runtime_checkable
class Normalizer(_Protocol):
    """Promote raw :class:`Segment` runs into typed inline nodes where
    possible (numbers, dates, percent, latin words, math_inline).
    Segments the normalizer doesn't recognize pass through untouched
    so the Pipeline's per-type frontend dispatch can take over."""

    name: str

    def normalize(
        self,
        segments: _Iterable[Segment],
        ctx: FrontendContext | None = None,
    ) -> list[NormalizedItem]: ...


# ---------------------------------------------------------------------------
# Math: source-format adapters + IR builder
# ---------------------------------------------------------------------------


@_runtime_checkable
class LanguageFrontend(_Protocol):
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

    Two **optional** declarations let a front-end offer this language's
    pluggable parts without knowing the language exists. Both are read with a
    fallback, so an implementation that omits them stays valid (they are
    deliberately not required members: this Protocol is runtime-checked, and
    adding one would reject every implementation written before it) —

    * ``adapters``: ``{family: () -> list[str]}``, the registered adapter names
      this language offers per family (``"analyzer"``, ``"resolver"``). Read
      through :func:`brailix.frontend.list_language_adapters`; a language that
      declares none simply has nothing to pick from.
    * ``display_name``: the English name to show in a listing
      (``"Chinese"``). Read through
      :func:`brailix.frontend.language_display_name`, which falls back to the
      subtag.
    """

    prose_types: _Collection[str]

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]: ...


@_runtime_checkable
class LanguageBackend(_Protocol):
    """Translate a language's prose IR nodes to cells.

    Two node kinds, one required method each: :class:`Word` (a language's
    prose word, of any length) and :class:`HanziMarker` (the date markers,
    whose reading *and* number-joiner rule are the language's own — see
    :meth:`translate_date_marker`). Both are required; the registry runs a
    runtime protocol check on first resolution, so an implementation missing
    one is rejected at ``get()``.

    There used to be a third, ``translate_hanzi_char``, for a separate
    single-character node type. A single character is now simply a
    one-character :class:`Word` (see that class), so the method is gone —
    an adapter that still writes one satisfies this protocol, and is never
    called.

    Registered per language (``backend.dispatch.language_backend_registry``);
    the dispatcher routes prose nodes to the one matching the profile's
    language. This is the seam for a new language's braille rules
    (Japanese kana → cells, ...); language-neutral nodes (Number / Punct
    / Latin / Math / Music) stay on the shared dispatch table.
    """

    def translate_word(
        self, node: Word, ctx: BackendContext, profile: _BrailleProfile
    ) -> list[BrailleCell]: ...

    def translate_date_marker(
        self,
        marker: HanziMarker,
        follows_number: bool,
        ctx: BackendContext,
        profile: _BrailleProfile,
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


@_runtime_checkable
class MathSourceAdapter(_Protocol):
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


@_runtime_checkable
class MusicSourceAdapter(_Protocol):
    """Convert score data from one source format into MusicXML.

    MusicXML is the normalized intermediate format for the music
    subsystem. Adapters never emit braille and never build an IR —
    the MusicXML tree itself is the IR; see
    :mod:`brailix.frontend.music`.
    """

    source: str  # musicxml / mxl / midi / abc / plain / ...

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str: ...


# ---------------------------------------------------------------------------
# Graphics: source-format adapters
# ---------------------------------------------------------------------------


@_runtime_checkable
class GraphicSourceAdapter(_Protocol):
    """Convert a graphic from one source format into SVG.

    SVG is the normalized intermediate format for the tactile-graphics
    subsystem. Adapters never emit a raster and never build an IR — the
    SVG tree itself is the IR (see :mod:`brailix.frontend.graphics`), the
    exact analogue of MathML for math and MusicXML for music.
    """

    source: str  # svg / primitives / image / chart / ...

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
    ) -> str: ...


# ---------------------------------------------------------------------------
# Backend support seam: inline-text translation
# ---------------------------------------------------------------------------
#
# The one sanctioned backend→frontend dependency (ARCHITECTURE#arch-boundaries). A few
# backend handlers embed natural-language prose — music ``<words>``
# directions, inline lyrics, Chinese chemical-reaction conditions. Rather
# than re-implement the zh / latin text path inside the backend, the
# Pipeline injects a translator implementing this Protocol onto
# ``BackendContext.options`` (read it via
# :meth:`BackendContext.inline_text_translator`). It is dependency
# injection, not an import — the backend never imports the frontend. When
# no translator is wired (a bare backend run, or a unit test), handlers
# fall back to a warning + marker.


@_runtime_checkable
class InlineTextTranslator(_Protocol):
    """Translate a run of inline prose into braille cells.

    Injected by :class:`~brailix.pipeline.Pipeline` so backend handlers
    that embed natural-language text can render it through the zh / latin
    frontend path without importing the frontend. See ARCHITECTURE#arch-boundaries.

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


@_runtime_checkable
class GraphicAssetResolver(_Protocol):
    """Resolve a graphic's asset reference to its raw bytes.

    Injected onto :class:`~brailix.core.context.GraphicsContext` so the
    ``image`` source adapter can turn a document-relative asset name
    (``media/image1.png`` — the name
    :attr:`brailix.ir.document.ImageAlt.target` and
    :attr:`~brailix.ir.document.DocumentIR.assets`
    share) into pixels without knowing *where* the bytes live: an image
    embedded in a ``.docx`` rides in memory, one authored by hand sits
    beside the source file. Returns ``None`` when the name is unknown, so
    the adapter can fall back to reading a filesystem path. This is the
    same inject-a-callable seam as :class:`InlineTextTranslator`
    (ARCHITECTURE#arch-boundaries) — the resolver is handed in, never imported.

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
# ``backend/dispatch.py`` and ARCHITECTURE#arch-dispatch), so it has no registry
# and no name→impl contract to satisfy. New braille standards are added via
# Profile JSON + resources, not by registering a Backend implementation.


@_runtime_checkable
class Renderer(_Protocol):
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
    tactile-graphics renderers (``bmp`` / ``png`` / ``pdf`` /
    ``tactile_preview``).
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

    def render(self, ir: _Any) -> _Any: ...


# Forward declarations for context types that are defined in
# ``core.context`` — kept here as TYPE_CHECKING-only imports to avoid
# circular references at runtime.
if _TYPE_CHECKING:
    from brailix.core.context import (
        FrontendContext,
        GraphicsContext,
        MathContext,
        MusicContext,
    )


# This module is on the **extension surface** (see :mod:`brailix`): the
# contracts an adapter author implements, promised at this path and pinned by
# ``tests/test_public_api.py``. Every module on that surface says what it
# publishes, for the same reason the end-user facades do — without an
# ``__all__`` there is no promise to check, and the check that exists to say
# "publishes no more than it promises" had nothing to compare against.
__all__ = (
    "Segmenter",
    "Normalizer",
    "LanguageFrontend",
    "LanguageBackend",
    "MathSourceAdapter",
    "MusicSourceAdapter",
    "GraphicSourceAdapter",
    "InlineTextTranslator",
    "GraphicAssetResolver",
    "Renderer",
)
