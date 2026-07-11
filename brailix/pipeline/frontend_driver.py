"""The frontend half of :class:`brailix.pipeline.Pipeline`.

Segmentation, normalization, per-segment language routing, inline-math
attachment, and block population (math / music / graphic parse plus tree
caching) — everything between a raw ``Block.text`` and populated
``children``.

Split out of :mod:`brailix.pipeline` so the orchestrator module stays
focused on :class:`Pipeline`; re-exported there so
``brailix.pipeline.FrontendDriver`` keeps resolving.

The math / music / graphic tree parsers are **injected** (constructor
arguments defaulting to the real :mod:`brailix.frontend` entry points)
rather than resolved from the module namespace. A test injects a fault by
replacing the ``_parse_math_tree`` / ``_parse_music_tree`` /
``_parse_graphic_tree`` attribute on the FrontendDriver instance (reachable
as ``pipeline._frontend``) — no ``brailix.pipeline.*`` monkeypatch, and no
forced co-location of this class with the parse-function aliases.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from brailix.core.config import BrailleProfile
from brailix.core.context import (
    GRAPHIC_ASSET_RESOLVER_KEY,
    FrontendContext,
    GraphicsContext,
    MathContext,
    MusicContext,
)
from brailix.core.defaults import DEFAULT_NORMALIZER, DEFAULT_SEGMENTER
from brailix.core.errors import StrictModeError
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
from brailix.ir.document import Paragraph
from brailix.ir.inline import (
    CodeInline,
    GraphicInline,
    InlineNode,
    MathInline,
    MusicInline,
    Segment,
    Unknown,
)
from brailix.pipeline._helpers import (
    _all_prose_types,
    _ensure_block_span,
    _resolve_language_adapter,
    cache_lookup,
    cache_record,
)
from brailix.pipeline._results import TreeSubcache

if TYPE_CHECKING:
    from collections.abc import Callable

    from brailix.core.protocols import GraphicAssetResolver

    TreeParser = Callable[[str, Any], ET.Element | None]


# ---------------------------------------------------------------------------
# Table-cell span rebasing
# ---------------------------------------------------------------------------

# Source-text gap between table cells: a row's display text joins its cells
# with two spaces (and the backend separates them with two blank cells), so a
# cell's source spans are offset by the prior cells' lengths plus this gap.
_TABLE_CELL_GAP = 2


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

    The math / music / graphic tree parsers are injected as
    ``_parse_math_tree`` / ``_parse_music_tree`` / ``_parse_graphic_tree``
    (defaulting to the real :mod:`brailix.frontend` entry points). Tests
    inject a fault by replacing one of those attributes on the instance —
    that is why the driver need not live in the ``brailix.pipeline`` module
    namespace alongside the parse-function aliases.
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
        "_parse_math_tree",
        "_parse_music_tree",
        "_parse_graphic_tree",
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
        parse_math_tree: TreeParser = _frontend_parse_math_tree,
        parse_music_tree: TreeParser = _frontend_parse_music_tree,
        parse_graphic_tree: TreeParser = _frontend_parse_graphic_tree,
    ) -> None:
        self.profile = profile
        self._profile = profile_obj
        self.segmenter = segmenter
        self.normalizer = normalizer
        self.analyzer = analyzer
        self.resolver = resolver
        self.user_pinyin_dict = user_pinyin_dict
        self.asset_resolver = asset_resolver
        # Injected tree parsers (see the class docstring): defaults are the
        # real frontend entry points; a test replaces one on the instance to
        # simulate an adapter failure.
        self._parse_math_tree = parse_math_tree
        self._parse_music_tree = parse_music_tree
        self._parse_graphic_tree = parse_graphic_tree

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
        pools — see :meth:`Pipeline.translate_block`.  Threaded as keyword
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
        record pools (see :meth:`Pipeline.translate_block`): on a key hit the
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
                tree = self._parse_music_tree(text, music_ctx)
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

        Parsing goes through the injected ``self._parse_math_tree`` — the
        same parser inline math (:meth:`attach_math`) uses — so a test
        injects a fault by replacing that attribute on the instance.
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
                tree = self._parse_math_tree(text, math_ctx)
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
        the child carrier. Parsing goes through the injected
        ``self._parse_graphic_tree`` — the graphics frontend's single public
        entry, same shape as math / music — which never raises: a missing
        adapter or adapter failure degrades to an SVG bearing a
        ``data-bk-error`` marker, so the tactile backend can surface
        ``GRAPHICS_SOFT_FAIL`` — ``block.children`` always ends up populated
        and the pipeline keeps running. Shares the ``("graphic", …)`` tree
        sub-cache domain alongside math / music.
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
                tree = self._parse_graphic_tree(text, gctx)
            except StrictModeError:
                # See _populate_music_block: keep the real code, don't rewrap.
                raise
            except Exception as exc:  # noqa: BLE001 — adapter errors are wide
                # Backstop for a frontend that raises anyway (the registry is
                # open; a test may inject a raising fake parser).
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
            tree = self._parse_math_tree(node.surface, math_ctx)
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
