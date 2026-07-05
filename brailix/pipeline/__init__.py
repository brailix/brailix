"""End-to-end brailix pipeline.

Wires together segmentation, normalization, language-specific
processing (Chinese tokenize + pinyin), math parsing, and the Backend
dispatcher into one :meth:`Pipeline.translate_text` call. Each
frontend subsystem has its own single-callable public interface
(see :mod:`brailix.frontend`); this module is just orchestration
plus the optional name-override knobs.

Rendering is **deferred**: :meth:`translate_text` returns a
:class:`TranslationResult` carrying the parsed IR and the braille IR,
but does not run a renderer. Ask for a concrete output by calling
:meth:`TranslationResult.render`.

Typical usage::

    from brailix import Pipeline

    pipe = Pipeline(profile="cn_current")
    result = pipe.translate_text("我在重庆。")
    print(result.render())          # default Unicode braille string
    print(result.render("unicode"))

Package layout
--------------

This is a subpackage; the separable pieces live in sibling modules and
are re-exported here so ``brailix.pipeline.<name>`` keeps resolving:

* :mod:`brailix.pipeline._results` — the public result / value types
  :class:`TranslationResult`, :class:`CompiledBlock`,
  :data:`TreeSubcache`.
* :mod:`brailix.pipeline._helpers` — the module-level standalone helpers
  :func:`_resolve_language_adapter`, :func:`_all_prose_types`,
  :func:`_ensure_block_span`, :func:`_block_surface`, :func:`block_hash`,
  :func:`cache_lookup`, :func:`cache_record`.

The cohesive :class:`Pipeline` orchestrator stays here, with its
:class:`FrontendDriver` collaborator and
:data:`_frontend_parse_math_tree` / :data:`_frontend_parse_music_tree` /
:data:`_frontend_parse_graphic_tree`, which tests monkeypatch via
``brailix.pipeline.*`` — a patch only affects callers that look the name
up in *this* namespace, and :meth:`FrontendDriver.populate_block` /
:meth:`FrontendDriver.attach_math` / :func:`translate_graphic` are those
callers, so both the names and the classes must live together here.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from brailix.backend.block import expand_block, translate_document
from brailix.core.config import BrailleProfile, load_profile
from brailix.core.context import (
    GRAPHIC_ASSET_RESOLVER_KEY,
    INLINE_TEXT_TRANSLATOR_KEY,
    BackendContext,
    FrontendContext,
    GraphicsContext,
    MathContext,
    MusicContext,
)
from brailix.core.defaults import (
    DEFAULT_NORMALIZER,
    DEFAULT_PINYIN_RESOLVER,
    DEFAULT_RENDERER,
    DEFAULT_SEGMENTER,
    DEFAULT_ZH_ANALYZER,
)
from brailix.core.errors import (
    RunMode,
    StrictModeError,
    WarningCollector,
    normalize_run_mode,
)
from brailix.core.span import Span
from brailix.frontend import apply_boundary as _apply_boundary
from brailix.frontend import language_frontend_registry
from brailix.frontend import normalize as _frontend_normalize
from brailix.frontend import parse_math_tree as _frontend_parse_math_tree
from brailix.frontend import segment as _frontend_segment
from brailix.frontend.graphics import (
    parse_graphic_tree as _frontend_parse_graphic_tree,
)
from brailix.frontend.music import parse_music_tree as _frontend_parse_music_tree
from brailix.frontend.normalize import normalizer_registry
from brailix.frontend.segment import segmenter_registry
from brailix.input import parse_file as _parse_file
from brailix.input import parse_markdown as _parse_markdown
from brailix.input import parse_plain as _parse_plain
from brailix.ir.braille import BrailleCell
from brailix.ir.document import Block, DocumentIR, Paragraph
from brailix.ir.inline import (
    CodeInline,
    GraphicInline,
    InlineNode,
    MathInline,
    MusicInline,
    Segment,
    Unknown,
)
from brailix.ir.tactile import TactileRaster
from brailix.pipeline._helpers import (
    _all_prose_types,
    _block_surface,
    _ensure_block_span,
    _resolve_language_adapter,
    block_hash,
    cache_lookup,
    cache_record,
)
from brailix.pipeline._results import (
    CompiledBlock,
    GraphicResult,
    TactilePageResult,
    TranslationResult,
    TreeSubcache,
)

if TYPE_CHECKING:
    from brailix.core.protocols import GraphicAssetResolver

# Note: brailix is the pure compiler — it knows nothing about front-end
# concepts like Override / WarningCase / Identity.  Callers that want
# to mutate the IR between frontend and backend (a proofreading front-end adjusting
# pinyin / splitting / merging tokens) pass an ``ir_transformer``
# callable to :meth:`Pipeline.translate_block`; the compiler runs it
# blindly without caring what semantics the caller attaches to it.

__all__ = [
    "Pipeline",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
    "_resolve_language_adapter",
    "_all_prose_types",
    "_ensure_block_span",
    "_block_surface",
    "_frontend_parse_math_tree",
    "_frontend_parse_music_tree",
    "_frontend_parse_graphic_tree",
]


# ---------------------------------------------------------------------------
# Table-cell span rebasing
# ---------------------------------------------------------------------------

# Source-text gap between table cells: a row's display text joins its cells
# with two spaces (and the backend separates them with two blank cells), so a
# cell's source spans are offset by the prior cells' lengths plus this gap.
_TABLE_CELL_GAP = 2

# Tactile profile used when an inline figure block (a GraphicBlock embedded in
# a braille document) is rasterised through the main pipeline
# (ARCHITECTURE.md G1).  ``"generic"`` matches the default a
# standalone ``translate_graphic`` call uses; a document-level / per-block
# tactile profile is a later refinement (G3/G4).
_DEFAULT_INLINE_TACTILE_PROFILE = "generic"


def _shift_node_spans(node: Any, delta: int) -> None:
    """Recursively shift ``node``'s ``span`` and every descendant's by ``delta``.

    Inline nodes / blocks are mutable (``frozen=False`` slots dataclasses) and
    ``Span`` is immutable, so each shift assigns a fresh ``Span``.  Nodes
    without provenance (``span is None``) are left untouched."""
    span = getattr(node, "span", None)
    if span is not None:
        node.span = span.shift(delta)
    for child in getattr(node, "children", ()) or ():
        _shift_node_spans(child, delta)


def _table_cell_source_len(cell: Any) -> int:
    """Source-text length of a table cell — what a row's display text joins.

    A cell's source length is its own ``text`` when present, else the total of
    its children's surfaces, so the rebase offset matches the row's joined
    source string.  Uses the raw text, never the cell's span (which this pass
    shifts), so re-translating an already-populated table stays idempotent."""
    if cell.text:
        return len(cell.text)
    return sum(len(getattr(child, "surface", "")) for child in cell.children)


# ---------------------------------------------------------------------------
# Frontend driver
# ---------------------------------------------------------------------------


class FrontendDriver:
    """Runs the frontend half of a :class:`Pipeline`: segmentation,
    normalization, per-segment language routing, inline-math attachment,
    and block population (math / music / graphic parse plus tree caching).

    A collaborator :class:`Pipeline` builds once in ``__post_init__`` with
    its own copy of the frontend adapter selection, so the frontend stages
    can be constructed and exercised without a full Pipeline. Backend and
    rendering stay on :class:`Pipeline`; its two bridge methods
    (:meth:`Pipeline._fresh_contexts` / :meth:`Pipeline._translate_inline_text`)
    call back into :meth:`frontend_options` / :meth:`run_frontend` here.

    It lives in this module (not a submodule) on purpose: the block-populate
    and math-attach methods resolve ``_frontend_parse_math_tree`` /
    ``_frontend_parse_music_tree`` / ``_frontend_parse_graphic_tree`` in
    *this* namespace, and tests inject faults by monkeypatching those
    ``brailix.pipeline.*`` names (see the module docstring).
    """

    __slots__ = (
        "profile",
        "_profile",
        "segmenter",
        "normalizer",
        "analyzer",
        "resolver",
        "user_pinyin_dict",
        "asset_resolver",
    )

    def __init__(
        self,
        *,
        profile: str,
        profile_obj: BrailleProfile,
        segmenter: str,
        normalizer: str,
        analyzer: str,
        resolver: str,
        user_pinyin_dict: dict[str, str],
        asset_resolver: GraphicAssetResolver | None,
    ) -> None:
        self.profile = profile
        self._profile = profile_obj
        self.segmenter = segmenter
        self.normalizer = normalizer
        self.analyzer = analyzer
        self.resolver = resolver
        self.user_pinyin_dict = user_pinyin_dict
        self.asset_resolver = asset_resolver

    def populate_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Run the frontend over any block that still has raw ``text``
        and no ``children`` yet. Recurses into composite containers.

        :class:`MathBlock` deliberately bypasses the Chinese frontend
        (the tokenizer would mangle LaTeX) and instead drives the
        **math frontend** here; on parse failure we emit warnings
        plus per-char :class:`Unknown` nodes so layout stays stable.

        :class:`CodeBlock` similarly bypasses the Chinese frontend and
        wraps its raw text as a single :class:`CodeInline` — the
        backend's punct path then emits one cell per source character.

        Both keep the Frontend → IR → Backend layering pure: this
        method is the one place that runs frontend, and the backend
        only ever sees populated children.

        Every text-bearing block also lands a ``span``: the math / music
        populate helpers set theirs, and a shared tail synthesises one
        from the text length for the remaining kinds — including a
        pre-populated block that arrives with ``text`` but no span (all
        kinds handled the same way, no per-kind drift).

        ``tree_in`` / ``tree_out`` are the parsed-tree reuse / record
        pools — see :meth:`translate_block`.  Threaded as keyword
        arguments rather than baked into :class:`FrontendContext` so
        the public adapter-facing surface stays free of front-end caching
        concerns; when both are ``None`` math / music parses run as before.
        """
        # Import lazily to avoid circular dependency at module load.
        from brailix.ir.document import (
            CodeBlock,
            GraphicBlock,
            MathBlock,
            MusicBlock,
            ScoreBlock,
            Table,
        )
        from brailix.ir.document import (
            List as ListBlock,
        )

        if isinstance(block, ListBlock):
            for item in block.items:
                self.populate_block(item, ctx, tree_in=tree_in, tree_out=tree_out)
            return
        if isinstance(block, Table):
            for row in block.rows:
                # Each cell is tokenised in isolation, so its inline spans are
                # local to the cell's own text. A row's display text is its
                # cells joined by two spaces (matching the backend's two-blank
                # column separator), so rebase each cell's spans by its offset
                # in that joined string — otherwise a non-first cell's inline
                # node / braille cell highlights the wrong column.
                cell_offset = 0
                for cell in row.cells:
                    already_populated = bool(cell.children)
                    self.populate_block(
                        cell, ctx, tree_in=tree_in, tree_out=tree_out
                    )
                    if cell_offset and not already_populated:
                        _shift_node_spans(cell, cell_offset)
                    cell_offset += _table_cell_source_len(cell) + _TABLE_CELL_GAP
            return
        # Leaf block.  Populate children from raw ``text`` only when it's
        # present and nothing has filled them yet; the per-kind branches
        # below differ only in *how* they populate.
        if block.text and not block.children:
            if isinstance(block, MathBlock):
                self._populate_math_block(
                    block, ctx, tree_in=tree_in, tree_out=tree_out
                )
                return
            if isinstance(block, (ScoreBlock, MusicBlock)):
                self._populate_music_block(
                    block, ctx, tree_in=tree_in, tree_out=tree_out
                )
                return
            if isinstance(block, GraphicBlock):
                self._populate_graphic_block(
                    block, ctx, tree_in=tree_in, tree_out=tree_out
                )
                return
            if isinstance(block, CodeBlock):
                # No language frontend — wrap the verbatim text as one
                # CodeInline so the backend's punct path emits one cell
                # per source char.
                text, span, _ = _ensure_block_span(block)
                block.children = [CodeInline(surface=text, span=span)]
                return
            text, span, _ = _ensure_block_span(block)
            block.children = self.run_frontend(
                text, ctx, tree_in=tree_in, tree_out=tree_out
            )
            return

        # Already populated (or no text): a text-bearing block still lands
        # a span.  Single rule for every block kind — math / score / code /
        # prose alike — so the pre-populated "text + children, no span"
        # case can't drift per kind.
        #
        # Contract note: a MathBlock/ScoreBlock/MusicBlock handed in already-
        # filled (children present) does NOT get its parse tree recorded into
        # ``tree_out`` here — the ET tree isn't reconstructable from the
        # flattened children without re-parsing, which would defeat the cache.
        # This is safe today because callers parse fresh, unfilled blocks each
        # run and so hit the populate path above; a future caller that reuses
        # pre-filled IR blocks must thread the tree via ``tree_in`` rather than
        # rely on this method to re-record it.
        if block.span is None and block.text:
            block.span = Span(0, len(block.text))

    def _populate_music_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Parse a :class:`ScoreBlock` / :class:`MusicBlock`'s raw
        ``text`` via the music frontend and populate ``children`` with
        a single :class:`MusicInline` carrying the MusicXML tree.

        Mirrors :meth:`_populate_math_block` for the music subsystem
        (see ``ARCHITECTURE.md``): the block holds only
        ``source``; the parsed tree lives on a child ``MusicInline``,
        so the backend dispatcher can route it like any other inline
        node.

        Soft-failure: if the adapter is missing the frontend returns
        ``None`` (a ``MUSIC_ADAPTER_MISSING`` warning is already
        recorded by then). Adapter parse errors land in a
        ``<music-error>`` tree that backend handlers will surface as
        ``MUSIC_PARSE_RECOVERY``. Either way ``block.children`` ends
        up populated and the pipeline keeps running.

        ``tree_in`` / ``tree_out`` are the shared parsed-tree reuse /
        record pools (see :meth:`translate_block`): on a key hit the
        whole MusicXML parse + normalise is skipped — the decisive win
        for proofreading, where the score source never changes between override
        edits.
        """
        text, span, _had_span = _ensure_block_span(block)

        cache_key = ("music", block.source, text)
        cached_tree = cache_lookup(tree_in, cache_key)
        if cached_tree is not None:
            tree: ET.Element | None = cached_tree
        else:
            music_ctx = MusicContext(
                source=block.source,
                mode="score",
                profile=self.profile,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            )
            try:
                tree = _frontend_parse_music_tree(text, music_ctx)
            except StrictModeError:
                # STRICT mode: the frontend's own warn (e.g. adapter missing)
                # already raised this carrying its real code; don't reclassify
                # it as *_PARSE_FAILED — let it propagate unchanged.
                raise
            except Exception as exc:  # noqa: BLE001 — adapter failures are wide
                ctx.warnings.error(
                    code="MUSIC_BLOCK_PARSE_FAILED",
                    message=f"music block parse failed: {exc!r}",
                    surface=text,
                    span=span,
                    source="pipeline",
                )
                tree = None

        cache_record(tree_out, cache_key, tree)

        block.children = [
            MusicInline(
                surface=text,
                span=span,
                source=block.source,
                score=tree,
            )
        ]

    def _populate_math_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Parse a :class:`MathBlock`'s raw ``text`` via the math
        frontend and populate ``block.children``.

        On adapter exceptions (deliberately wide ``except`` — adapter
        failure modes vary): record a ``MATH_BLOCK_PARSE_FAILED``
        warning and fall back to one :class:`Unknown` per source
        character so layout still occupies real estate. The per-char
        :class:`Unknown` will trigger ``UNKNOWN_NODE`` warnings via
        the dispatcher when backend renders them — that's expected
        and slightly more precise than the legacy single-warning
        behavior (each char is genuinely an unknown to the backend).

        Parsing goes through the module-level ``_frontend_parse_math_tree``
        alias — the same call site inline math (:meth:`attach_math`) and
        music (:meth:`_populate_music_block`) use — so a test injects a
        fault by monkeypatching ``brailix.pipeline._frontend_parse_math_tree``.
        """
        # Remember whether the caller-supplied block had a span. The
        # per-char Unknown fallback below matches the legacy behavior
        # in backend.block._unknown_cells_for: if the source block has
        # no span, the fallback cells also have no span — the caller
        # then knows it can't anchor them.
        text, span, had_original_span = _ensure_block_span(block)

        cache_key = ("math", block.source, text)
        cached_tree = cache_lookup(tree_in, cache_key)
        if cached_tree is not None:
            tree: ET.Element | None = cached_tree
        else:
            math_ctx = MathContext(
                source=block.source,
                mode="display",
                profile=self.profile,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            )
            try:
                tree = _frontend_parse_math_tree(text, math_ctx)
            except StrictModeError:
                # See _populate_music_block: keep the real code, don't rewrap.
                raise
            except Exception as exc:  # noqa: BLE001 — adapter errors are wide
                ctx.warnings.error(
                    code="MATH_BLOCK_PARSE_FAILED",
                    message=f"math block parse failed: {exc!r}",
                    surface=text,
                    span=span,
                    source="pipeline",
                )
                base = span.start
                block.children = [
                    Unknown(
                        surface=ch,
                        span=Span(base + i, base + i + 1)
                        if had_original_span
                        else None,
                    )
                    for i, ch in enumerate(text)
                ]
                return

        cache_record(tree_out, cache_key, tree)

        block.children = [
            MathInline(
                surface=text,
                span=span,
                source=block.source,
                math=tree,
            )
        ]

    def _populate_graphic_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Parse a :class:`~brailix.ir.document.GraphicBlock`'s raw ``text``
        via the graphics frontend and populate ``block.children`` with a
        single :class:`~brailix.ir.inline.GraphicInline` carrying the SVG tree.

        Mirrors :meth:`_populate_math_block` / :meth:`_populate_music_block`
        for the tactile-graphics subsystem (``ARCHITECTURE.md``): the block holds only ``source``; the parsed SVG tree lives on
        the child carrier. Parsing goes through the module-level
        ``_frontend_parse_graphic_tree`` alias — the graphics frontend's
        single public entry, same shape as math / music — which never
        raises: a missing adapter or adapter failure degrades to an SVG
        bearing a ``data-bk-error`` marker, so the tactile backend can
        surface ``GRAPHICS_SOFT_FAIL`` — ``block.children`` always ends up
        populated and the pipeline keeps running. Shares the
        ``("graphic", …)`` tree sub-cache domain alongside math / music.
        """
        text, span, _had_span = _ensure_block_span(block)

        cache_key = ("graphic", block.source, text)
        cached_tree = cache_lookup(tree_in, cache_key)
        if cached_tree is not None:
            tree: ET.Element | None = cached_tree
        else:
            # The tactile profile (mm + DPI) is a backend concern applied
            # at rasterize time, never at the frontend — the context
            # carries only source / warnings / options.
            gctx = GraphicsContext(
                source=block.source,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            )
            try:
                tree = _frontend_parse_graphic_tree(text, gctx)
            except StrictModeError:
                # See _populate_music_block: keep the real code, don't rewrap.
                raise
            except Exception as exc:  # noqa: BLE001 — adapter errors are wide
                # Backstop for a frontend that raises anyway (the registry is
                # open; a test may monkeypatch the alias with a raising fake).
                ctx.warnings.error(
                    code="GRAPHICS_BLOCK_PARSE_FAILED",
                    message=f"graphic block parse failed: {exc!r}",
                    surface=text,
                    span=span,
                    source="pipeline",
                )
                # Soft-fail to an error-marked SVG (never None): the tactile
                # backend turns this into a blank raster + GRAPHICS_SOFT_FAIL,
                # so a graphic always rasterises to *something*.
                tree = ET.Element("svg", {"data-bk-error": repr(exc)})

        cache_record(tree_out, cache_key, tree)

        block.children = [
            GraphicInline(
                surface=text,
                span=span,
                source=block.source,
                svg=tree,
            )
        ]

    # --- Frontend orchestration --------------------------------------
    #
    # All frontend stages live in :mod:`brailix.frontend`. Pipeline
    # only orchestrates: segment → normalize → per-segment routing →
    # math attachment. The routing is language-agnostic — segmenter,
    # normalizer and the prose frontend are each selected by the active
    # profile's language (see :meth:`frontend_options` /
    # :meth:`_process_segment`), so adding a language is registration,
    # not a change here. See ARCHITECTURE §7.6.

    def frontend_options(self) -> dict[str, Any]:
        lang = self._profile.language.split("-")[0]
        return {
            "segmenter": _resolve_language_adapter(
                segmenter_registry, self.segmenter, DEFAULT_SEGMENTER, lang
            ),
            "normalizer": _resolve_language_adapter(
                normalizer_registry, self.normalizer, DEFAULT_NORMALIZER, lang
            ),
            # Analyzer is selected per language: each LanguageFrontend reads
            # ``ctx.options["{lang}_analyzer"]`` (zh reads ``zh_analyzer``, ja
            # reads ``ja_analyzer``). Key off the active profile's language
            # primary subtag — the same ``lang`` the segmenter / normalizer
            # use above — instead of hard-coding one option key per language,
            # so a new prose language is registration, not a change here.
            # ``_process_segment`` routes a run to the frontend matching this
            # same ``lang``, so only the current language's analyzer key is
            # ever read; a missing key falls back to the frontend's default
            # (``auto``).
            f"{lang}_analyzer": self.analyzer,
            "pinyin_resolver": self.resolver,
            "user_pinyin_dict": self.user_pinyin_dict,
            # Forwarded onto the GraphicsContext (built from a copy of these
            # options in _populate_graphic_block) so a graphic-image fence's
            # image reference resolves to in-document bytes. Omitted when
            # None so a bare run carries no spurious key.
            **(
                {GRAPHIC_ASSET_RESOLVER_KEY: self.asset_resolver}
                if self.asset_resolver is not None
                else {}
            ),
        }

    def run_frontend(
        self,
        text: str,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> list[InlineNode]:
        block = Paragraph(text=text)
        segments = _frontend_segment(block, ctx)
        normalized = _frontend_normalize(segments, ctx)

        out: list[InlineNode] = []
        for item in normalized:
            if isinstance(item, Segment):
                out.extend(self._process_segment(item, ctx))
            elif isinstance(item, MathInline):
                self.attach_math(item, ctx, tree_in=tree_in, tree_out=tree_out)
                out.append(item)
            else:
                out.append(item)
        lang = self._profile.language.split("-")[0]
        return _apply_boundary(out, lang, self._profile)

    def _process_segment(
        self, segment: Segment, ctx: FrontendContext
    ) -> list[InlineNode]:
        # Prose runs route to the language frontend selected by the active
        # profile's language primary subtag; the frontend declares which
        # segment types are its prose (``prose_types``), so this
        # orchestrator never hard-codes a script. Adding a language means
        # registering a LanguageFrontend (plus a matching segmenter for
        # its script) — no change here. See ARCHITECTURE §7.6.
        lang = self._profile.language.split("-")[0]
        if language_frontend_registry.has(lang):
            frontend = language_frontend_registry.get(lang)
            if segment.type in frontend.prose_types:
                base = segment.span.start if segment.span else 0
                return frontend.process(segment.surface, base, ctx)
        # Independent `if` (not `elif`): a prose segment can reach here either
        # because the active language has no frontend, OR because its frontend
        # doesn't claim this segment's type (some other language's prose). Both
        # mean "no frontend for this prose" — NO_LANGUAGE_FRONTEND — not the
        # misleading UNHANDLED_SEGMENT_TYPE the old `elif` fell through to.
        if segment.type in _all_prose_types():
            # Same code (NO_LANGUAGE_FRONTEND) for both arrival reasons, but an
            # accurate message: the language may have no frontend at all, or
            # have one that simply doesn't claim this prose segment type.
            if language_frontend_registry.has(lang):
                message = (
                    f"language {lang!r} frontend does not handle prose "
                    f"segment type {segment.type!r}"
                )
            else:
                message = f"no frontend registered for language {lang!r}"
            ctx.warnings.warn(
                code="NO_LANGUAGE_FRONTEND",
                message=message,
                surface=segment.surface,
                span=segment.span,
                source="pipeline",
            )
            return []
        ctx.warnings.warn(
            code="UNHANDLED_SEGMENT_TYPE",
            message=f"no frontend handler for segment type {segment.type!r}",
            surface=segment.surface,
            span=segment.span,
            source="pipeline",
        )
        return []

    def attach_math(
        self,
        node: MathInline,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        cache_key = ("math", node.source, node.surface)
        if node.math is not None:
            # Already parsed (frontend ran twice, or caller pre-populated).
            # Still record in tree_out so the caller's per-block cache
            # snapshot is complete — otherwise a re-parse that hits this
            # short-circuit path would silently drop the formula from
            # the next compile's reuse pool.
            cache_record(tree_out, cache_key, node.math)
            return
        cached = cache_lookup(tree_in, cache_key)
        if cached is not None:
            node.math = cached
            cache_record(tree_out, cache_key, cached)
            return
        math_ctx = MathContext(
            source=node.source,
            mode="inline",
            profile=self.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        )
        # The MathSourceAdapter registry is open, so a non-conforming
        # adapter can raise; mirror _populate_block's display-math guard so
        # an inline formula can never crash the whole document translate
        # (the backend's MATH_NO_IR path degrades a None tree to a warning).
        try:
            tree = _frontend_parse_math_tree(node.surface, math_ctx)
        except StrictModeError:
            # See _populate_music_block: keep the real code, don't rewrap.
            raise
        except Exception as exc:  # noqa: BLE001 — adapter errors are wide
            ctx.warnings.error(
                code="MATH_INLINE_PARSE_FAILED",
                message=f"inline math parse failed: {exc!r}",
                surface=node.surface,
                span=node.span,
                source="pipeline",
            )
            node.math = None
            return
        node.math = tree
        cache_record(tree_out, cache_key, tree)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Pipeline:
    """Convenience wrapper for the default end-to-end flow.

    Configuration is **all by name**. Every adapter family is selected
    by a string that resolves through the corresponding internal
    registry; default ``"auto"`` lets the system pick the best
    installed candidate without user intervention.

    Field meaning:

    * ``profile`` — **required**; JSON profile name under
      :mod:`brailix.profiles` (the braille standard, e.g. ``cn_current``
      / ``cn_ncb`` / ``ja_current``). Drives table selection and runtime
      features. There is no built-in default — the caller always chooses.
    * ``mode`` — diagnostic policy (see :class:`RunMode`).
    * ``segmenter`` / ``normalizer`` — segmenter and normalizer adapter
      names.
    * ``analyzer`` — Chinese tokenizer name (``auto`` / ``char`` /
      ``thulac`` / ``jieba`` / ``hanlp``).
    * ``resolver`` — pinyin resolver name (``auto`` / ``null`` /
      ``pypinyin`` / ``g2pm`` / ``g2pw``).
    * ``user_pinyin_dict`` — optional ``surface → reading`` overrides
      layered on top of ``resolver`` (a proofreading front-end's personal
      dictionary). Multi-char surfaces only; empty = no-op.
    * ``default_renderer`` — forwarded to every
      :class:`TranslationResult` so :meth:`TranslationResult.render`
      knows what to use when called without arguments.

    :meth:`translate_text` is the simplest entry point; the rest of the
    public surface is :meth:`translate_document` / :meth:`translate_file`
    / :meth:`translate_block` / :meth:`translate_math_inline` /
    :meth:`translate_graphic` / :meth:`parse_text` / :meth:`parse_file`.
    To plug in a new adapter, register it with the matching internal
    registry under a name of your choice, then construct a Pipeline with
    that name — no Pipeline code changes needed.
    """

    profile: str
    mode: RunMode | str = RunMode.NORMAL
    segmenter: str = DEFAULT_SEGMENTER
    normalizer: str = DEFAULT_NORMALIZER
    analyzer: str = DEFAULT_ZH_ANALYZER
    resolver: str = DEFAULT_PINYIN_RESOLVER
    # Personal pinyin dictionary (user-authored surface→reading map),
    # layered on top of whichever resolver runs: the zh frontend applies
    # it as a post-pass so the user's explicit reading wins for every
    # document.  Multi-char keys only (single-char readings are too
    # context-dependent to force globally).  Empty by default → pure no-op,
    # so the bare library and every test that omits it are unaffected.
    user_pinyin_dict: dict[str, str] = field(default_factory=dict)
    default_renderer: str = DEFAULT_RENDERER
    # User-folder profile directories injected by the caller so a portable
    # build can ship with its own profile drops.
    # ``load_profile`` searches these first; same-named user profile
    # shadows the builtin.  Kept as a tuple so the dataclass stays
    # hashable / frozen-friendly even though :class:`Pipeline` itself
    # is mutable.
    extra_profile_paths: tuple[str, ...] = ()
    # Resolves a graphic's asset reference (``media/image1.png``) to raw
    # bytes when the referenced image lives in the document rather than on
    # disk — an image imported from a ``.docx`` rides in memory. Injected onto
    # every ``GraphicsContext`` the pipeline builds (inline ``graphic-image``
    # fences and standalone ``translate_graphic`` alike), so the ``image``
    # source adapter can inline it as a ``data:`` URI. ``None`` (the default)
    # leaves the adapter to read the reference as a filesystem path — the bare
    # library and every test that omits it are unaffected. See
    # :class:`~brailix.core.protocols.GraphicAssetResolver` and §2.2 of
    # ``ARCHITECTURE.md``.
    asset_resolver: GraphicAssetResolver | None = None
    _profile: BrailleProfile = field(init=False, default=None)  # type: ignore[assignment]
    _frontend: FrontendDriver = field(init=False, default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)
        # Accept ``Path`` objects too — keeping the dataclass field type
        # as ``tuple[str, ...]`` simplifies serialization, but the caller
        # naturally passes :class:`pathlib.Path`.
        if self.extra_profile_paths:
            self.extra_profile_paths = tuple(
                str(p) for p in self.extra_profile_paths
            )
        self._profile = load_profile(
            self.profile,
            extra_search_paths=[Path(p) for p in self.extra_profile_paths]
            or None,
        )
        self._frontend = FrontendDriver(
            profile=self.profile,
            profile_obj=self._profile,
            segmenter=self.segmenter,
            normalizer=self.normalizer,
            analyzer=self.analyzer,
            resolver=self.resolver,
            user_pinyin_dict=self.user_pinyin_dict,
            asset_resolver=self.asset_resolver,
        )

    @property
    def profile_name(self) -> str:
        """The resolved profile's name (``BrailleProfile.name``).

        The :attr:`profile` field holds the *requested* profile name;
        this returns the loaded profile's own ``name``, which is the
        authoritative identity to persist. Exposed so a front-end never
        has to reach into the private ``_profile``.
        """
        return self._profile.name

    @property
    def profile_language(self) -> str:
        """The resolved profile's language tag (e.g. ``"zh-CN"``).

        Exposed so a front-end can record a document's language without
        touching the private ``_profile`` — the public-API boundary.
        """
        return self._profile.language

    # --- Public API ---------------------------------------------------
    #
    # The public surface is the ``translate_*`` / ``parse_*`` methods and
    # the returned :class:`TranslationResult`. Internal stages are
    # deliberately private: users compose by registering new adapter
    # names through the internal registries and pointing Pipeline
    # constructor arguments at those names.

    def translate_text(self, text: str) -> TranslationResult:
        """Translate one paragraph of text into a :class:`TranslationResult`.

        The input is wrapped as a single :class:`Paragraph` block. For
        multi-block documents (headings + lists + tables...) build a
        :class:`DocumentIR` yourself or parse Markdown via
        :func:`brailix.input.markdown.parse_markdown` and call
        :meth:`translate_document`.

        To mutate the IR between frontend and backend (e.g. a proofreading front-end
        applying user overrides), use :meth:`translate_block` and pass
        an ``ir_transformer`` — Pipeline keeps no override / workflow
        concept, that lives in the front-end layer.
        """
        warnings, ctx, backend_ctx = self._fresh_contexts()
        children = self._frontend.run_frontend(text, ctx)
        paragraph = Paragraph(
            children=children, span=Span(0, len(text)) if text else None
        )
        doc = DocumentIR(
            metadata={"language": self._profile.language, "profile": self.profile},
            blocks=[paragraph],
        )
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        return TranslationResult(
            text=text,
            ir=doc,
            braille_ir=braille_doc,
            warnings=warnings,
            default_renderer=self.default_renderer,
        )

    def translate_math_inline(self, surface: str, source: str) -> str:
        """Translate a single inline math formula to a Unicode-braille string.

        Convenience for one-off / live-preview callers (a CLI, or a proofreading
        front-end's math editor) that hold a raw formula plus its source format
        (``"latex"`` / ``"mathml"`` / ``"asciimath"`` / ...) and want braille
        without reassembling the math frontend + backend + renderer by hand —
        keeping them off ``brailix.backend`` internals.

        Inline mode (matches how an inline :class:`MathInline` renders). Parse
        / adapter failures and unsupported constructs surface as the usual
        soft-failure cells; warnings go to a throwaway collector so a preview
        never pollutes the caller's diagnostics. Returns ``""`` when the
        formula doesn't parse into a tree at all.
        """
        from brailix.backend.math import translate as _math_translate
        from brailix.renderer.unicode_braille import cell_to_char

        surface = surface.strip()
        if not surface:
            return ""
        # NORMAL regardless of the pipeline's own mode: a strict-mode
        # collector RAISES on the first warning, which would turn the
        # documented "failures surface as soft-failure cells" preview
        # contract into a StrictModeError crash for any formula with an
        # unknown symbol.
        silent = WarningCollector(mode=RunMode.NORMAL)
        math_ctx = MathContext(
            source=source, mode="inline", profile=self.profile, warnings=silent
        )
        tree = _frontend_parse_math_tree(surface, math_ctx)
        if tree is None:
            return ""
        node = MathInline(surface=surface, source=source, math=tree)
        backend_ctx = BackendContext(
            profile=self.profile,
            # NORMAL, not self.mode — the context's __post_init__
            # re-stamps its mode onto the shared collector, which would
            # silently undo the NORMAL collector above.
            mode=RunMode.NORMAL,
            warnings=silent,
            # Inject the inline-text translator so embedded text — a
            # \text{...} / <mtext> run, esp. Chinese — renders through the
            # zh / latin path instead of failing per-char. Without it a
            # live preview drops to blank cells + warnings for any text.
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        cells = _math_translate(node, backend_ctx, self._profile)
        return "".join(cell_to_char(c) for c in cells)

    def translate_graphic(
        self,
        source: str | bytes,
        *,
        source_format: str = "svg",
        tactile_profile: str | Any = "generic",
        braille_profile: str | None = None,
        label_translator: Callable[[str], list[BrailleCell]] | None = None,
        record_provenance: bool = False,
        warnings: WarningCollector | None = None,
    ) -> GraphicResult:
        """Compile a tactile graphic into a :class:`GraphicResult`.

        Convenience delegation to the module-level
        :func:`translate_graphic` — a graphic's own compile needs no
        braille standard (its product is a raster, not cells), so the
        real entry is Pipeline-free. Going through a Pipeline buys one
        thing: when ``braille_profile`` matches this pipeline's own
        profile, ``<text>`` labels translate through **this** pipeline's
        text path instead of spinning up a second one.

        See :func:`translate_graphic` for the parameter contract.
        """
        translator = label_translator
        if translator is None and braille_profile is not None:
            translator = self._graphic_label_translator(braille_profile)
        return translate_graphic(
            source,
            source_format=source_format,
            tactile_profile=tactile_profile,
            label_translator=translator,
            record_provenance=record_provenance,
            warnings=(
                warnings
                if warnings is not None
                else WarningCollector(mode=self.mode)
            ),
            mode=self.mode,
            # An image graphic's reference resolves against this pipeline's
            # document assets (a figure edited in isolation still shows its
            # embedded picture in the live preview).
            asset_resolver=self.asset_resolver,
        )

    def _rasterize_graphic(
        self,
        block: Any,
        warns: WarningCollector,
        *,
        tactile_profile: str | Any,
        label_translator: Callable[[str], list[BrailleCell]] | None,
        record_provenance: bool = False,
    ) -> tuple[TactileRaster, ET.Element]:
        """Rasterise an already-populated :class:`GraphicBlock` into a
        :class:`~brailix.ir.tactile.TactileRaster`.

        The shared tail of :meth:`translate_graphic` (the standalone tactile
        entry) and :meth:`translate_block` (the inline-in-a-braille-document
        path, ARCHITECTURE.md G1) — one rasteriser, not two.
        Pulls the SVG tree off the block's :class:`GraphicInline` child
        (:meth:`_populate_graphic_block` always lands one — an error-marked
        SVG on soft-failure, never ``None`` — so a figure always rasterises to
        *something*), loads the tactile profile, and rasterises.  Returns
        ``(raster, tree)``.
        """
        from brailix.backend.tactile import rasterize
        from brailix.backend.tactile.profile import load_tactile_profile

        child = block.children[0] if block.children else None
        tree = child.svg if isinstance(child, GraphicInline) else None
        if tree is None:  # defensive — populate guarantees a GraphicInline tree
            tree = ET.Element("svg", {"data-bk-error": "no graphic tree"})
        prof = (
            load_tactile_profile(tactile_profile)
            if isinstance(tactile_profile, str)
            else tactile_profile
        )
        raster = rasterize(
            tree, prof, warns, label_translator, record_provenance=record_provenance
        )
        return raster, tree

    def _graphic_label_translator(
        self, braille_profile: str
    ) -> Callable[[str], list[BrailleCell]]:
        """A ``text → braille cells`` translator for graphic labels, backed by
        the requested braille standard.

        Reuses this pipeline's own text path when ``braille_profile`` matches
        :attr:`profile` (no second Pipeline); otherwise spins a sub-pipeline on
        that standard. Either way it routes through ``_translate_inline_text``,
        whose throwaway NORMAL collector keeps a label's per-char warnings out
        of the graphic's own report."""
        if braille_profile == self.profile:
            return self._translate_inline_text
        return Pipeline(
            profile=braille_profile, mode=self.mode
        )._translate_inline_text

    def translate_document(self, doc: DocumentIR) -> TranslationResult:
        """Translate a pre-built :class:`DocumentIR` end-to-end.

        Each block is walked: if it carries raw ``text`` and no
        ``children``, the frontend runs over its text to populate
        ``children``; if it already has ``children`` they're used as-is
        (so callers can hand-build IR for tests). Composite containers
        (List, Table) recurse into their ``items`` / ``rows`` /
        ``cells`` for the same treatment.

        Returns a :class:`TranslationResult` with the populated IR and
        the rendered :class:`BrailleDocument`. The original ``doc`` is
        **mutated in place** — children are filled where they were
        missing — so subsequent re-translations skip the frontend
        cost.
        """
        warnings, ctx, backend_ctx = self._fresh_contexts()
        # Stamp the pipeline's identity onto the (possibly hand-built) doc
        # so the result is self-describing the same way translate_text /
        # parse_* leave it.  The backend reads ``self._profile`` directly,
        # so this is for the IR metadata's consumers, not the translation;
        # other caller metadata keys are preserved.
        doc.metadata["language"] = self._profile.language
        doc.metadata["profile"] = self.profile
        for block in doc.blocks:
            self._frontend.populate_block(block, ctx)
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        # The surface for a multi-block document is the concatenation
        # of every block's text — useful for proofread output but not
        # always semantically meaningful (no separator between blocks).
        rebuilt_text = "\n".join(
            _block_surface(b) for b in doc.blocks
        )
        return TranslationResult(
            text=rebuilt_text,
            ir=doc,
            braille_ir=braille_doc,
            warnings=warnings,
            default_renderer=self.default_renderer,
        )

    def translate_document_to_pages(
        self,
        doc: DocumentIR,
        *,
        tactile_profile: str | Any = _DEFAULT_INLINE_TACTILE_PROFILE,
        margin_mm: float | None = None,
        item_gap_mm: float | None = None,
    ) -> TactilePageResult:
        """Lay a braille document with embedded figures onto tactile pages.

        The mixed-layout output (``ARCHITECTURE.md`` G3): each
        block is compiled through the **same** incremental pipeline every other
        path uses (:meth:`translate_block`) — a text block yields braille
        cells, a :class:`~brailix.ir.document.GraphicBlock` yields a
        :class:`~brailix.ir.tactile.TactileRaster` (G1). Text cells are wrapped
        to the page's cell width by the layout renderer, then the tactile
        backend's page compositor stamps them as **real braille dots** and
        blits the figures into the flow, paginating onto one raster per page
        (output model A — the page *is* a raster; there is no BRF for a mixed
        page). The result exports through the existing tactile renderers.

        ``tactile_profile`` names a tactile profile (page size + DPI + braille
        dot geometry + interline pitch) or is an already-loaded
        :class:`~brailix.backend.tactile.profile.TactileProfile`; it drives the
        page geometry. Figures are rasterised at the G1 default (``"generic"``)
        and placed at their true millimetre size, so they land correctly
        whatever page profile is chosen — a document-/block-level figure
        profile is a later refinement (plan §3). ``margin_mm`` and
        ``item_gap_mm`` default to one cell advance and one interline pitch.

        Braille state does not leak across blocks (ARCHITECTURE §12), so
        compiling block-by-block is sound. The document is **mutated in place**
        (children filled where missing), like :meth:`translate_document`.
        """
        from brailix.backend.tactile.page import (
            PageFigure,
            PageItem,
            PageText,
            compose_pages,
            line_width_cells,
        )
        from brailix.backend.tactile.profile import load_tactile_profile
        from brailix.ir.document import GraphicBlock
        from brailix.renderer.layout import LayoutOptions, LayoutRenderer

        tprof = (
            load_tactile_profile(tactile_profile)
            if isinstance(tactile_profile, str)
            else tactile_profile
        )
        # Wrap width = the one shared cells-per-line rule (``line_width_cells``);
        # the compositor stamps at the same cell advance, so the wrap width and
        # the stamp geometry agree.
        layout = LayoutRenderer(
            options=LayoutOptions(
                line_width=line_width_cells(tprof, margin_mm=margin_mm),
                page_height=None,
            )
        )

        warnings = WarningCollector(mode=self.mode)
        items: list[PageItem] = []
        for block in doc.blocks:
            compiled = self.translate_block(block)
            # Aggregate each block's diagnostics without re-running the
            # collector's mode logic (they are already final): append directly.
            warnings.warnings.extend(compiled.warnings)
            if isinstance(block, GraphicBlock):
                if compiled.raster is not None:
                    items.append(PageFigure(raster=compiled.raster))
                continue
            for bblock in compiled.braille_blocks:
                lines = layout.lay_out_block(bblock)
                if lines:
                    items.append(PageText(lines=lines))

        pages = compose_pages(
            items,
            tprof,
            margin_mm=margin_mm,
            item_gap_mm=item_gap_mm,
            warnings=warnings,
        )
        return TactilePageResult(pages=pages, warnings=warnings)

    def translate_file(
        self,
        path: str | os.PathLike[str],
    ) -> TranslationResult:
        """Read ``path`` and translate end-to-end.

        Convenience wrapper over :func:`brailix.input.parse_file` +
        :meth:`translate_document`. The input is dispatched by suffix
        (Markdown, Word ``.docx`` / ``.doc``, MusicXML / ``.mxl`` /
        score MIDI / ABC, else plain text — see :meth:`parse_file` and
        :func:`brailix.input.parse_file` for the full table). The
        Pipeline's own ``profile`` and language are baked into the
        resulting :class:`DocumentIR`'s metadata so the
        :class:`TranslationResult` is indistinguishable from one
        produced by :meth:`translate_text` on the same source.

        IO errors propagate as-is (:class:`FileNotFoundError`,
        :class:`UnicodeDecodeError`, ``PermissionError``); pre-parse
        the file yourself and call :meth:`translate_document` if you
        need to catch them at a different layer.
        """
        doc = self.parse_file(path)
        return self.translate_document(doc)

    def parse_text(
        self,
        text: str,
        *,
        format: str = "plain",
    ) -> DocumentIR:
        """Parse ``text`` into a :class:`DocumentIR` without translating.

        ``format`` selects the adapter: ``"plain"`` (one paragraph) or
        ``"markdown"`` (the Markdown subset described under
        :func:`brailix.input.markdown.parse_markdown` — headings, lists,
        quotes, code blocks, ``$$...$$`` math, tables). The Pipeline's
        ``profile`` and ``language`` are baked into the resulting IR
        metadata so a follow-up :meth:`translate_document` matches what
        :meth:`translate_text` / :meth:`translate_file` would have
        produced on the same input.

        Use this when you need the unpopulated :class:`DocumentIR` to
        drive incremental compilation block-by-block (the incremental-compilation
        pattern a front-end uses) instead of running frontend + backend in one
        shot. Pair with :meth:`translate_block` for the per-block
        compile step.
        """
        if format == "markdown":
            return _parse_markdown(
                text,
                language=self._profile.language,
                profile=self.profile,
            )
        if format == "plain":
            return _parse_plain(
                text,
                language=self._profile.language,
                profile=self.profile,
            )
        if format == "musicxml":
            # Wrap raw MusicXML text as a single ScoreBlock so any
            # caller using parse_text can route .musicxml
            # through the same block-level compile path it uses for
            # markdown / plain. ``_populate_music_block`` will parse
            # the inner XML into a MusicInline tree at compile time.
            from brailix.ir.document import ScoreBlock

            return DocumentIR(
                metadata={
                    "language": self._profile.language,
                    "profile": self.profile,
                },
                blocks=[ScoreBlock(text=text, source="musicxml")],
            )
        raise ValueError(
            f"unknown parse format: {format!r} "
            "(expected 'plain' / 'markdown' / 'musicxml')"
        )

    def parse_file(
        self,
        path: str | os.PathLike[str],
    ) -> DocumentIR:
        """Read ``path`` as UTF-8 / bytes and parse to :class:`DocumentIR`.

        Suffix dispatch matches :func:`brailix.input.parse_file`:
        ``.md`` / ``.markdown`` → Markdown adapter; ``.docx`` /
        ``.docm`` → :func:`brailix.input.parse_docx`; ``.doc`` →
        :func:`brailix.input.parse_doc`; ``.musicxml`` / ``.mxl`` (and
        a ``.xml`` that sniffs as a score) → :func:`brailix.input.parse_musicxml`;
        ``.mid`` / ``.midi`` / ``.abc`` → :func:`brailix.input.parse_score_file`;
        everything else (including ``.txt`` and no suffix) → plain. The
        Pipeline's ``profile`` and ``language`` are baked into the IR
        metadata.

        Use this when you need the unpopulated :class:`DocumentIR`
        from a file for incremental compilation (the incremental-compilation
        pattern a front-end uses). For the end-to-end one-shot, call
        :meth:`translate_file`.
        """
        return _parse_file(
            path,
            language=self._profile.language,
            profile=self.profile,
            mathtype_fallback=self._profile.feature(
                "input.docx.mathtype_fallback", "off"
            ),
            chem_detection=self._profile.feature(
                "input.docx.detect_chemistry", False
            ),
        )

    def translate_block(
        self,
        block: Block,
        *,
        ir_transformer: Callable[[DocumentIR], None] | None = None,
        tree_subcache: TreeSubcache | None = None,
    ) -> CompiledBlock:
        """Translate a single :class:`Block` end-to-end (frontend +
        backend) and return a :class:`CompiledBlock`.

        The **incremental compilation primitive** a front-end uses:
        re-compile one paragraph / heading /
        list-item without touching the rest of the document. The
        returned :class:`CompiledBlock` carries the populated IR,
        braille output, warnings, and a stable ``source_hash`` for
        cache keying.

        Block-level translation is sound because braille state
        (number_sign, capital indicator, math state machine) **does
        not leak** across block boundaries — see ARCHITECTURE.md §12.

        ``ir_transformer`` is an optional in-place mutation hook that
        runs between frontend and backend.  The compiler doesn't care
        what semantics the caller attaches to it: a proofreading
        front-end wraps its override-application pass here; a different
        front-end could plug in glossary rewrites or auto-fix passes.
        The transformer receives a singleton :class:`DocumentIR`
        wrapping ``block`` so it can use absolute ``block_path`` like
        ``(0, child_idx, ...)``.

        ``tree_subcache`` is an optional parsed-tree cache shared by the
        math, music and graphic frontends: keys are ``(domain, source,
        surface)`` (``domain`` ∈ ``{"math", "music", "graphic"}``), values are
        the normalised
        MathML / MusicXML :class:`ET.Element` trees from a previous
        compile.  When the frontend encounters a math / music node whose
        key matches an entry, it reuses the cached tree instead of
        re-running the adapter — typically the caller passes the prior
        :class:`CompiledBlock`\\ 's ``tree_subcache`` so an edit that
        leaves a formula / score unchanged (e.g. an override) doesn't
        trigger a re-parse (block-level cache covers the whole-block
        case; this covers the "block changed but the embedded tree
        didn't" case — decisive for large scores, which are one block
        whose 4MB tree would otherwise re-parse on every override edit).
        The returned :class:`CompiledBlock.tree_subcache` always reflects
        what was actually parsed during this compile (a superset / equal
        subset of the input, never empty when math or music exists).

        Pipeline is **stateless** with respect to caching — every
        call re-runs frontend + backend.  The caller is responsible
        for consulting its own block cache via ``source_hash`` before
        calling this method.  The hash covers ``(block surface,
        profile)`` only; callers that want override-aware cache keys
        should compose ``source_hash`` with their own override-list
        salt at the caller layer.
        """
        # One fresh collector + matching contexts for this block.  The
        # backend context is stamped with this block's type up front — the
        # only difference from the translate_text / translate_document
        # setup — so expand_block sees the right block_type without a rebuild.
        warnings, ctx, backend_ctx = self._fresh_contexts(block_type=block.type)

        # Parsed-tree sub-cache is threaded through the populate path as
        # a mutable pair: ``tree_in`` is read-only (caller-provided
        # reuse pool), ``tree_out`` accumulates trees from this
        # compile.  Kept out of :class:`FrontendContext` to avoid
        # polluting the public adapter-facing surface with front-end-
        # specific state.
        tree_in = tree_subcache or {}
        tree_out: TreeSubcache = {}
        self._frontend.populate_block(block, ctx, tree_in=tree_in, tree_out=tree_out)

        # Run the optional caller-supplied IR transformer.  We wrap
        # the block in a singleton doc so the transformer can index
        # children with absolute ``block_path = (0, ...)`` (same
        # convention a front-end's override-application pass uses).
        if ir_transformer is not None:
            singleton = DocumentIR(blocks=[block])
            ir_transformer(singleton)

        # Backend: expand into one or more BrailleBlocks (composites
        # like List / Table expand to N elements; simple blocks to 1;
        # a GraphicBlock to one empty "graphic" placeholder — its dots ride
        # on ``raster`` below, not in cells).
        braille_blocks = expand_block(block, backend_ctx, self._profile)

        # Tactile-graphics inline embedding (ARCHITECTURE.md G1):
        # a figure block rasterises to a TactileRaster through THIS same
        # incremental pipeline — no separate ``translate_graphic`` call — so a
        # braille document holding figures compiles down one path.  Labels
        # translate through this pipeline's own text path, so a figure's labels
        # come out in the document's braille standard automatically.
        from brailix.ir.document import GraphicBlock

        raster: TactileRaster | None = None
        if isinstance(block, GraphicBlock):
            raster, _tree = self._rasterize_graphic(
                block,
                warnings,
                tactile_profile=_DEFAULT_INLINE_TACTILE_PROFILE,
                label_translator=self._translate_inline_text,
            )

        # Stable cache key: textual surface + profile.  Callers who
        # need override-aware cache keys (a proofreading front-end) compose this
        # hash with their own override-list salt outside the
        # compiler.
        source_hash = block_hash(block, self.profile)

        return CompiledBlock(
            block_id=block.id or "",
            source_hash=source_hash,
            ir=block,
            braille_blocks=braille_blocks,
            warnings=list(warnings.warnings),
            tree_subcache=tree_out,
            raster=raster,
        )

    # --- Internal: shared per-translate setup -----------------------

    def _fresh_contexts(
        self, *, block_type: str = "paragraph"
    ) -> tuple[WarningCollector, FrontendContext, BackendContext]:
        warnings = WarningCollector(mode=self.mode)
        ctx = FrontendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options=self._frontend.frontend_options(),
        )
        backend_ctx = BackendContext(
            profile=self.profile,
            mode=self.mode,
            block_type=block_type,
            warnings=warnings,
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        return warnings, ctx, backend_ctx

    def _translate_inline_text(self, text: str) -> list[BrailleCell]:
        """Translate a run of text to braille cells via the zh / latin
        text path — injected on ``BackendContext.options`` so the music
        backend can render ``<words>`` directions (expression text,
        teaching notes) instead of deferring them to a warning.

        Runs a throwaway frontend + backend over a one-paragraph doc.
        The inner :class:`BackendContext` deliberately omits the
        translator, so a (text-only) run can't recurse back into music;
        its warnings go to a private collector — ``<words>`` rendering is
        best-effort, a stray untranslatable char shouldn't spam the
        score's report.
        """
        if not text.strip():
            return []
        # NORMAL regardless of the pipeline's own mode: the docstring
        # promises this private collector never pollutes the caller's
        # diagnostics, but a strict-mode collector raises on the first
        # warning — a stray untranslatable char inside a <words> /
        # \text{...} run would abort the whole score's translation from
        # deep inside a backend handler.
        warnings = WarningCollector(mode=RunMode.NORMAL)
        # NORMAL on the contexts too, not just the collector — their
        # __post_init__ re-stamps the context mode onto the shared
        # collector, which would silently undo the line above.
        ctx = FrontendContext(
            profile=self.profile,
            mode=RunMode.NORMAL,
            warnings=warnings,
            options=self._frontend.frontend_options(),
        )
        children = self._frontend.run_frontend(text, ctx)
        paragraph = Paragraph(children=children, span=Span(0, len(text)))
        doc = DocumentIR(blocks=[paragraph])
        backend_ctx = BackendContext(
            profile=self.profile, mode=RunMode.NORMAL, warnings=warnings
        )
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        return braille_doc.all_cells()


# ---------------------------------------------------------------------------
# Module-level tactile-graphics entry
# ---------------------------------------------------------------------------


def translate_graphic(
    source: str | bytes,
    *,
    source_format: str = "svg",
    tactile_profile: str | Any = "generic",
    braille_profile: str | None = None,
    label_translator: Callable[[str], list[BrailleCell]] | None = None,
    record_provenance: bool = False,
    warnings: WarningCollector | None = None,
    mode: RunMode | str = RunMode.NORMAL,
    asset_resolver: GraphicAssetResolver | None = None,
) -> GraphicResult:
    """Compile a tactile graphic into a :class:`GraphicResult`.

    The tactile vertical's standalone entry: the **graphics frontend**
    (:data:`_frontend_parse_graphic_tree`, the same single-callable shape as
    math / music) normalises the source (``source_format`` ∈ ``svg`` /
    ``primitives`` / ``figure`` / ``image``) into the SVG-tree IR, and the
    **tactile backend** rasterises that tree into a
    :class:`~brailix.ir.tactile.TactileRaster`. Concrete bytes (``.bmp`` /
    ``.png`` / ``.pdf`` / U+2800 preview) come from
    :meth:`GraphicResult.render` through the shared ``renderer_registry``.

    A module-level function, not a :class:`Pipeline` method, because a
    graphic's own compile needs **no braille standard** — its product is a
    raster, not cells (math / music, whose product *is* braille, keep their
    Pipeline entries). Only ``<text>`` label translation touches braille:
    pass ``braille_profile`` (a braille standard; a label pipeline is built
    on it) or a ready ``label_translator`` callable — with neither, labels
    are warned (``GRAPHICS_LABEL_NO_PROFILE``) and skipped.
    :meth:`Pipeline.translate_graphic` delegates here, wiring its own text
    path as the label translator when the standards match.

    ``tactile_profile`` names a tactile profile (mm adaptation params + DPI)
    or is an already-loaded ``TactileProfile``. ``record_provenance``
    records which pixels each SVG element drew for an editor's cross-pane
    highlight (off by default — export pays nothing). Bytes input goes to
    the source adapters as-is; they own the decode and its soft-failure.
    ``asset_resolver`` resolves an ``image`` source's reference to
    in-document bytes (see :class:`~brailix.core.protocols.
    GraphicAssetResolver`); ``None`` leaves it to read a filesystem path.
    """
    from brailix.backend.tactile import rasterize
    from brailix.backend.tactile.profile import load_tactile_profile

    warns = (
        warnings
        if warnings is not None
        else WarningCollector(mode=normalize_run_mode(mode))
    )
    gctx = GraphicsContext(
        source=source_format,
        warnings=warns,
        options=(
            {GRAPHIC_ASSET_RESOLVER_KEY: asset_resolver}
            if asset_resolver is not None
            else {}
        ),
    )
    tree = _frontend_parse_graphic_tree(source, gctx)
    if tree is None:  # a monkeypatched / fake frontend may return None
        tree = ET.Element("svg", {"data-bk-error": "no graphic tree"})

    translator = label_translator
    if translator is None and braille_profile is not None:
        translator = Pipeline(
            profile=braille_profile, mode=mode
        )._translate_inline_text

    prof = (
        load_tactile_profile(tactile_profile)
        if isinstance(tactile_profile, str)
        else tactile_profile
    )
    raster = rasterize(
        tree, prof, warns, translator, record_provenance=record_provenance
    )
    return GraphicResult(raster=raster, svg_tree=tree, warnings=warns)
