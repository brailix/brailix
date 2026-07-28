"""How a leaf block's ``children`` get filled, one handler per block kind.

:meth:`brailix.pipeline.frontend_driver.FrontendDriver.populate_block` owns *whether* to
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
from brailix.pipeline._helpers import (
    _ensure_block_span,
    cache_lookup,
    cache_record,
    tree_cache_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from brailix.core.span import Span as _Span
    from brailix.pipeline._results import TreeSubcache

    # Imported for typing only: the driver imports THIS module at runtime, so a
    # runtime import back would close a cycle.
    from brailix.pipeline.frontend_driver import FrontendDriver


# ---------------------------------------------------------------------------
# The shared vertical skeleton
# ---------------------------------------------------------------------------


def parse_cached_tree(
    ctx: FrontendContext,
    *,
    domain: str,
    source: str,
    text: str,
    span: _Span,
    salt: str = "",
    identity: str,
    parser: Callable[[str, Any], ET.Element | None],
    context_factory: Callable[[], Any],
    code: str,
    label: str,
    tree_in: TreeSubcache | None,
    tree_out: TreeSubcache | None,
) -> tuple[ET.Element | None, Exception | None]:
    """Reuse-or-parse one vertical's tree, and classify what went wrong.

    Everything the math / music / graphic populate paths do *identically*
    lives here — the cache key, the lookup, the context construction, the
    parse call, the exception ladder, the warning, and recording a successful
    parse. What they do *differently* — how each recovers from a failed parse
    — deliberately does not: this returns ``(tree, error)`` and the caller
    decides, because the recoveries are genuinely unlike each other (music
    keeps a carrier with no tree, math abandons the carrier for one
    :class:`Unknown` per character, graphics substitutes an error-marked SVG).

    The exception ladder is the part worth having in one place:

    * :class:`StrictModeError` propagates unchanged — the frontend's own
      ``warn`` already raised it carrying its real code, and re-wrapping would
      relabel, say, a missing adapter as a parse failure;
    * a :data:`PROGRAMMING_ERRORS` (AttributeError / NameError / ...) is a
      code defect, never a "bad formula", so it surfaces instead of being
      buried in a soft-failure warning;
    * anything else is an adapter failure — the ``except`` is deliberately
      wide because a registered adapter's failure modes are open — and is
      warned as ``code`` against ``span``, with ``label`` naming the
      construct in the message.

    ``code`` keeps that name (rather than something like ``failure_code``)
    on purpose. A warning code is user-visible text downstream — a front-end
    looks up a per-code description in the reader's language — and the way
    the set of emitted codes is discovered is by scanning source for a
    ``code=`` keyword followed by a string literal. Spelling the keyword
    differently makes the three codes below invisible to that scan, which is
    how they would quietly lose their descriptions.

    ``identity`` is the parse identity every vertical keys on — the driver's
    compilation fingerprint — so a pool threaded in from another pipeline (or
    surviving a runtime adapter re-registration) can't hand back a tree parsed
    under a configuration this compile no longer runs. See
    :func:`~brailix.pipeline._helpers.tree_cache_key`.

    ``context_factory`` is called only on a cache miss, so a hit costs no
    context construction. A successful parse is recorded into ``tree_out``;
    a failure is not (``cache_record`` would refuse a ``None`` anyway), which
    leaves a caller that recovers to a *substitute* tree free to record that
    substitute itself if it wants the recovery reused.
    """
    key = tree_cache_key(domain, source, text, salt, identity=identity)
    cached = cache_lookup(tree_in, key)
    if cached is not None:
        cache_record(tree_out, key, cached)
        return cached, None
    try:
        tree = parser(text, context_factory())
    except StrictModeError:
        raise
    except PROGRAMMING_ERRORS:
        raise
    except Exception as exc:  # noqa: BLE001 — adapter failures are wide
        ctx.warnings.error(
            code=code,
            message=f"{label} parse failed: {exc!r}",
            surface=text,
            span=span,
            source="pipeline",
        )
        return None, exc
    cache_record(tree_out, key, tree)
    return tree, None


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
    :meth:`~brailix.pipeline.frontend_driver.FrontendDriver.populate_block` owns the recursion,
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
    # it becomes the tree-cache salt so two blocks with identical source +
    # text but different modes can't share one cached tree.
    mode: Literal["block", "score"] = (
        "score" if isinstance(block, ScoreBlock) else "block"
    )
    # Recovery: keep the carrier with no tree. The backend's MUSIC_NO_IR path
    # turns that into a warning rather than a crash, so the document still
    # compiles around the unreadable score.
    tree, _error = parse_cached_tree(
        ctx,
        domain="music",
        source=block.source,
        text=text,
        span=span,
        salt=mode,
        identity=driver.parse_identity,
        parser=driver._parse_music_tree,
        context_factory=lambda: MusicContext(
            source=block.source,
            mode=mode,
            profile=driver.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        ),
        code="MUSIC_BLOCK_PARSE_FAILED",
        label="music block",
        tree_in=tree_in,
        tree_out=tree_out,
    )

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
    parser inline math (:meth:`~brailix.pipeline.frontend_driver.FrontendDriver.attach_math`)
    uses — so a test injects a fault by replacing that attribute on the driver.
    """
    # Remember whether the caller-supplied block had a span. The
    # per-char Unknown fallback below matches the legacy behavior
    # in backend.block._unknown_cells_for: if the source block has
    # no span, the fallback cells also have no span — the caller
    # then knows it can't anchor them.
    text, span, had_original_span = _ensure_block_span(block)

    tree, error = parse_cached_tree(
        ctx,
        domain="math",
        source=block.source,
        text=text,
        span=span,
        # Nothing beyond (source, surface) feeds a math parse, so no salt.
        identity=driver.parse_identity,
        parser=driver._parse_math_tree,
        context_factory=lambda: MathContext(
            source=block.source,
            mode="display",
            profile=driver.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        ),
        code="MATH_BLOCK_PARSE_FAILED",
        label="math block",
        tree_in=tree_in,
        tree_out=tree_out,
    )
    if error is not None:
        # Recovery: one Unknown per source character, so layout still occupies
        # the real estate the formula would have. Each will trigger its own
        # UNKNOWN_NODE warning from the dispatcher — expected, and slightly
        # more precise than one warning for the whole block.
        base = span.start
        block.children = [
            Unknown(
                surface=ch,
                span=Span(base + i, base + i + 1) if had_original_span else None,
            )
            for i, ch in enumerate(text)
        ]
        return

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

    # The parse result embeds what the asset resolver returned (an ``image``
    # fence inlines the resolved bytes as a data: URI), so the resolver's
    # identity is the salt: two documents referencing the same
    # ``media/image1.png`` name through different resolvers must not share a
    # cached tree.
    #
    # The failure path here is a backstop rather than the normal soft-fail:
    # ``parse_graphic_tree`` already degrades a missing adapter / bad source
    # to an error-marked SVG itself, so this catches only a frontend that
    # raises anyway (the registry is open; a test may inject a raising fake).
    # One read of the resolver identity, used for both the parse key and the
    # recovery record below: reading it twice could mint two different tokens
    # for a resolver that declares none (see asset_resolver_identity).
    salt = asset_resolver_identity(driver.asset_resolver)
    identity = driver.parse_identity
    tree, error = parse_cached_tree(
        ctx,
        domain="graphic",
        source=block.source,
        text=text,
        span=span,
        salt=salt,
        identity=identity,
        parser=driver._parse_graphic_tree,
        # The tactile profile (mm + DPI) is a backend concern applied at
        # rasterize time, never at the frontend — the context carries only
        # source / warnings / options.
        context_factory=lambda: GraphicsContext(
            source=block.source,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        ),
        code="GRAPHICS_BLOCK_PARSE_FAILED",
        label="graphic block",
        tree_in=tree_in,
        tree_out=tree_out,
    )
    if error is not None:
        # Recovery: an error-marked SVG, never None — the tactile backend
        # turns it into a blank raster + GRAPHICS_SOFT_FAIL, so a graphic
        # always rasterises to *something*. Recorded like a successful parse
        # (unlike math / music, whose recoveries are not cached): the parse is
        # deterministic in (source, surface, resolver), so a re-compile of the
        # same figure would only fail again.
        tree = ET.Element("svg", {"data-bk-error": repr(error)})
        cache_record(
            tree_out,
            tree_cache_key(
                "graphic", block.source, text, salt, identity=identity
            ),
            tree,
        )

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
