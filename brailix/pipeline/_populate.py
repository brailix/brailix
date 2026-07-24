"""How a leaf block's ``children`` get filled, one handler per block kind.

:meth:`brailix.pipeline.FrontendDriver.populate_block` owns *whether* to
populate (structural recursion, the stale-heal, the fingerprint stamp); this
module owns *how*, per kind: a math / music / graphic block parses through its
vertical's frontend into a single carrier inline node, a code block is wrapped
verbatim, and everything else is prose and runs the language frontend.

Split out of :mod:`brailix.pipeline.frontend_driver` so the driver stays the
orchestration stage — the same extraction the pipeline package already applies
with ``_session`` / ``_incremental`` / ``_pages``: a free function taking the
orchestrator object, not a method, so the family can grow without growing the
driver.

Dispatch goes through :data:`BLOCK_POPULATORS`, keyed on the block's **exact**
type. Adding a content vertical is one handler plus one table entry here — it
does not touch the driver at all.

These handlers are *not* the frontend. The analysis itself lives in
:mod:`brailix.frontend` (segmentation, normalization, the math / music /
graphics parsers, all independently usable); what happens here is the
compiler-side concern of running that analysis and reusing its result through
the shared parsed-tree pools. That is why this module sits in ``pipeline`` and
may import ``pipeline`` internals, while ``frontend`` may never import either
(ARCHITECTURE §1 / §12, pinned by ``tests/test_core_layering.py``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any, Literal

from brailix.core.context import (
    FrontendContext,
    GraphicsContext,
    MathContext,
    MusicContext,
)
from brailix.core.errors import PROGRAMMING_ERRORS, StrictModeError
from brailix.core.span import Span
from brailix.ir.document import (
    Block,
    CodeBlock,
    GraphicBlock,
    MathBlock,
    MusicBlock,
    ScoreBlock,
)
from brailix.ir.inline import (
    CodeInline,
    GraphicInline,
    MathInline,
    MusicInline,
    Unknown,
)
from brailix.pipeline._fingerprint import asset_resolver_identity
from brailix.pipeline._helpers import _ensure_block_span, cache_lookup, cache_record

if TYPE_CHECKING:
    from collections.abc import Callable

    from brailix.pipeline._results import TreeSubcache

    # Imported for typing only: the driver imports THIS module at runtime, so a
    # runtime import back would close a cycle.
    from brailix.pipeline.frontend_driver import FrontendDriver


def populate_leaf(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Fill one leaf block's ``children`` from its raw ``text``.

    Dispatches on the block's exact type through :data:`BLOCK_POPULATORS`; a
    block type absent from the table is prose and runs the language frontend.
    :meth:`~brailix.pipeline.FrontendDriver.populate_block` owns the recursion,
    the stale-heal and the fingerprint stamp and calls this for each leaf.
    """
    populate = BLOCK_POPULATORS.get(type(block), populate_prose_block)
    populate(driver, block, ctx, tree_in=tree_in, tree_out=tree_out)


def populate_prose_block(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Default populate: run the language frontend over the block's text.

    Every text-bearing block that is not one of the special verticals lands
    here — Paragraph, Heading, Quote, Footnote, ImageAlt alt text, ListItem
    and TableCell alike — which is why the table holds only the exceptions and
    this stays the fallback.
    """
    text, _span, _ = _ensure_block_span(block)
    block.children = driver.run_frontend(
        text, ctx, tree_in=tree_in, tree_out=tree_out
    )


def populate_code_block(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Wrap a :class:`~brailix.ir.document.CodeBlock`'s verbatim text as a
    single :class:`CodeInline`.

    No language frontend runs and nothing is parsed, so ``driver``, ``ctx`` and
    the two tree pools go unused — they are carried only to keep every entry in
    :data:`BLOCK_POPULATORS` callable through one uniform signature. The
    backend's punct path emits one cell per source character, keeping code
    byte-exact.
    """
    text, span, _ = _ensure_block_span(block)
    block.children = [CodeInline(surface=text, span=span)]


def populate_music_block(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Parse a :class:`ScoreBlock` / :class:`MusicBlock`'s raw ``text`` via the
    music frontend and populate ``children`` with a single :class:`MusicInline`
    carrying the MusicXML tree.

    Mirrors :func:`populate_math_block` for the music subsystem (see
    ``ARCHITECTURE.md``): the block holds only ``source``; the
    parsed tree lives on a child ``MusicInline``, so the backend dispatcher can
    route it like any other inline node.

    Soft-failure: if the adapter is missing the frontend returns ``None`` (a
    ``MUSIC_ADAPTER_MISSING`` warning is already recorded by then). Adapter
    parse errors land in a ``<music-error>`` tree that backend handlers will
    surface as ``MUSIC_PARSE_RECOVERY``. Either way ``block.children`` ends up
    populated and the pipeline keeps running.

    ``tree_in`` / ``tree_out`` are the shared parsed-tree reuse / record pools
    (see :meth:`Pipeline.translate_block`): on a key hit the whole MusicXML
    parse + normalise is skipped — the decisive win for proofreading, where the
    score source never changes between override edits.
    """
    text, span, _had_span = _ensure_block_span(block)

    # A full :class:`ScoreBlock` runs in ``"score"`` mode; a single-passage
    # :class:`MusicBlock` in ``"block"`` mode. Previously both were forced
    # to ``"score"``, so a MusicBlock never received its declared mode — a
    # third-party adapter that honours the public MusicContext contract
    # would have been misinformed. Since ``mode`` is now a real parse input,
    # it becomes part of the tree-cache salt so two blocks with identical
    # source + text but different modes can't share one cached tree.
    mode: Literal["block", "score"] = (
        "score" if isinstance(block, ScoreBlock) else "block"
    )
    cache_key = ("music", block.source, text, mode)
    cached_tree = cache_lookup(tree_in, cache_key)
    if cached_tree is not None:
        tree: ET.Element | None = cached_tree
    else:
        music_ctx = MusicContext(
            source=block.source,
            mode=mode,
            profile=driver.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        )
        try:
            tree = driver._parse_music_tree(text, music_ctx)
        except StrictModeError:
            # STRICT mode: the frontend's own warn (e.g. adapter missing)
            # already raised this carrying its real code; don't reclassify
            # it as *_PARSE_FAILED — let it propagate unchanged.
            raise
        except PROGRAMMING_ERRORS:
            # A code defect (AttributeError / NameError / AssertionError) is
            # never a "bad score" — surface it instead of burying it in a
            # MUSIC_BLOCK_PARSE_FAILED warning. See brailix.core.errors.
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


def populate_math_block(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Parse a :class:`MathBlock`'s raw ``text`` via the math frontend and
    populate ``block.children``.

    On adapter exceptions (deliberately wide ``except`` — adapter failure modes
    vary): record a ``MATH_BLOCK_PARSE_FAILED`` warning and fall back to one
    :class:`Unknown` per source character so layout still occupies real estate.
    The per-char :class:`Unknown` will trigger ``UNKNOWN_NODE`` warnings via the
    dispatcher when backend renders them — that's expected and slightly more
    precise than the legacy single-warning behavior (each char is genuinely an
    unknown to the backend).

    Parsing goes through the injected ``driver._parse_math_tree`` — the same
    parser inline math (:meth:`~brailix.pipeline.FrontendDriver.attach_math`)
    uses — so a test injects a fault by replacing that attribute on the driver.
    """
    # Remember whether the caller-supplied block had a span. The
    # per-char Unknown fallback below matches the legacy behavior
    # in backend.block._unknown_cells_for: if the source block has
    # no span, the fallback cells also have no span — the caller
    # then knows it can't anchor them.
    text, span, had_original_span = _ensure_block_span(block)

    cache_key = ("math", block.source, text, "")
    cached_tree = cache_lookup(tree_in, cache_key)
    if cached_tree is not None:
        tree: ET.Element | None = cached_tree
    else:
        math_ctx = MathContext(
            source=block.source,
            mode="display",
            profile=driver.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        )
        try:
            tree = driver._parse_math_tree(text, math_ctx)
        except StrictModeError:
            # See populate_music_block: keep the real code, don't rewrap.
            raise
        except PROGRAMMING_ERRORS:
            # A code defect is never a "bad formula" — surface it rather
            # than degrade to per-char Unknown. See brailix.core.errors.
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


def populate_graphic_block(
    driver: FrontendDriver,
    block: Any,
    ctx: FrontendContext,
    *,
    tree_in: TreeSubcache | None = None,
    tree_out: TreeSubcache | None = None,
) -> None:
    """Parse a :class:`~brailix.ir.document.GraphicBlock`'s raw ``text`` via the
    graphics frontend and populate ``block.children`` with a single
    :class:`~brailix.ir.inline.GraphicInline` carrying the SVG tree.

    Mirrors :func:`populate_math_block` / :func:`populate_music_block` for the
    tactile-graphics subsystem (``ARCHITECTURE.md``): the
    block holds only ``source``; the parsed SVG tree lives on the child carrier.
    Parsing goes through the injected ``driver._parse_graphic_tree`` — the
    graphics frontend's single public entry, same shape as math / music — which
    never raises: a missing adapter or adapter failure degrades to an SVG
    bearing a ``data-bk-error`` marker, so the tactile backend can surface
    ``GRAPHICS_SOFT_FAIL`` — ``block.children`` always ends up populated and the
    pipeline keeps running. Shares the ``("graphic", …)`` tree sub-cache domain
    alongside math / music.
    """
    text, span, _had_span = _ensure_block_span(block)

    # The parse result embeds what the asset resolver returned (an
    # ``image`` fence inlines the resolved bytes as a data: URI), so the
    # resolver's identity is part of the key: two documents referencing
    # the same ``media/image1.png`` name through different resolvers
    # must not share a cached tree. Math parses consume nothing beyond
    # (source, surface) — its salt slot stays ""; music carries its mode.
    cache_key = (
        "graphic",
        block.source,
        text,
        asset_resolver_identity(driver.asset_resolver),
    )
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
            tree = driver._parse_graphic_tree(text, gctx)
        except StrictModeError:
            # See populate_music_block: keep the real code, don't rewrap.
            raise
        except PROGRAMMING_ERRORS:
            # A code defect is never a "bad graphic" — surface it rather
            # than degrade to an error-marked SVG. See brailix.core.errors.
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


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# Block type -> the handler that fills that block's ``children``.
#
# Keyed on the block's EXACT type, mirroring the inline dispatcher's
# :data:`brailix.backend.dispatch._DISPATCH`: the IR block set is a closed, flat
# set of direct :class:`~brailix.ir.document.Block` dataclasses (ARCHITECTURE
# §7.5 — the adapter layer is the open extension surface, the IR type set is
# not), so an O(1) table is both correct and cheaper than an isinstance ladder,
# and a new content vertical costs one entry here instead of another branch.
#
# A block type ABSENT from the table is prose: Paragraph / Heading / Quote /
# Footnote / ImageAlt / ListItem / TableCell all fall through to
# :func:`populate_prose_block` and the language frontend.
#
# This is a private table, not an open registry — third parties extend brailix
# by registering *adapters* (a new math / music / graphic source behind an
# existing protocol), never by adding IR block types.
BLOCK_POPULATORS: dict[type[Block], Callable[..., None]] = {
    MathBlock: populate_math_block,
    ScoreBlock: populate_music_block,
    MusicBlock: populate_music_block,
    GraphicBlock: populate_graphic_block,
    CodeBlock: populate_code_block,
}
