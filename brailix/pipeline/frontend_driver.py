"""The frontend half of :class:`brailix.pipeline.Pipeline`.

Segmentation, normalization, per-segment language routing, inline-math
attachment, and the block-population *lifecycle* — structural recursion,
the stale-heal, the fingerprint stamp — everything between a raw
``Block.text`` and populated ``children``.

*How* each block kind is populated lives one module over, in
:mod:`brailix.pipeline._populate`: this driver decides **whether** to
populate a leaf and then hands it to
:func:`~brailix.pipeline._populate.populate_leaf`, which dispatches on the
block's type. That keeps the per-vertical parse handlers out of the
orchestration stage, so a new content vertical grows the table there rather
than this class.

Split out of :mod:`brailix.pipeline` so the orchestrator module stays
focused on :class:`Pipeline`. This module is where the driver is imported
from: the orchestrator takes it under an underscore alias, so it is not
re-exported as ``brailix.pipeline.FrontendDriver`` — a collaborator the
Pipeline constructs for itself should not sit in the package namespace
looking like API.

The math / music / graphic tree parsers are **injected** (constructor
arguments defaulting to the real :mod:`brailix.frontend` entry points)
rather than resolved from the module namespace. A test injects a fault by
replacing the ``_parse_math_tree`` / ``_parse_music_tree`` /
``_parse_graphic_tree`` attribute on the FrontendDriver instance (reachable
as ``pipeline._frontend``) — no ``brailix.pipeline.*`` monkeypatch, and no
forced co-location of this class with the parse-function aliases.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

from brailix.core.config import BrailleProfile
from brailix.core.context import (
    GRAPHIC_ASSET_RESOLVER_KEY,
    FrontendContext,
    MathContext,
)
from brailix.core.span import Span
from brailix.frontend import _apply_boundary, language_frontend_registry
from brailix.frontend import normalize as _frontend_normalize
from brailix.frontend import parse_math_tree as _frontend_parse_math_tree
from brailix.frontend import segment as _frontend_segment
from brailix.frontend._language_pick import LANGUAGE_OPTION
from brailix.frontend.graphics import (
    parse_graphic_tree as _frontend_parse_graphic_tree,
)
from brailix.frontend.music import parse_music_tree as _frontend_parse_music_tree
from brailix.ir.document import Paragraph
from brailix.ir.inline import InlineNode, MathInline, Segment
from brailix.pipeline._helpers import (
    _all_prose_types,
    _block_surface,
    cache_record,
    tree_cache_key,
)
from brailix.pipeline._populate import parse_cached_tree, populate_leaf
from brailix.pipeline._results import TreeSubcache

if _TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from brailix.core.protocols import GraphicAssetResolver

    TreeParser = Callable[[str, _Any], _ET.Element | None]


# ---------------------------------------------------------------------------
# Table-cell span rebasing
# ---------------------------------------------------------------------------

# Source-text gap between table cells: a row's display text joins its cells
# with two spaces (and the backend separates them with two blank cells), so a
# cell's source spans are offset by the prior cells' lengths plus this gap.
_TABLE_CELL_GAP = 2


def _shift_node_spans(node: _Any, delta: int) -> None:
    """Recursively shift ``node``'s ``span`` and every descendant's by ``delta``.

    Inline nodes / blocks are mutable (``frozen=False`` slots dataclasses) and
    ``Span`` is immutable, so each shift assigns a fresh ``Span``.  Nodes
    without provenance (``span is None``) are left untouched."""
    span = getattr(node, "span", None)
    if span is not None:
        node.span = span.shift(delta)
    for child in getattr(node, "children", ()) or ():
        _shift_node_spans(child, delta)


def _table_cell_source_len(cell: _Any) -> int:
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
    rendering stay on :class:`Pipeline`, and two seams reach back in here:
    :meth:`CompilationSession.begin
    <brailix.pipeline._session.CompilationSession.begin>` builds a run's
    contexts from :meth:`frontend_options`, and
    :meth:`Pipeline._translate_inline_text` translates embedded prose through
    :meth:`run_frontend`.

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
        "analyzer",
        "resolver",
        "user_pinyin_dict",
        "user_seg_dict",
        "asset_resolver",
        "fingerprint",
        "_parse_math_tree",
        "_parse_music_tree",
        "_parse_graphic_tree",
    )

    def __init__(
        self,
        *,
        profile: str,
        profile_obj: BrailleProfile,
        analyzer: str,
        resolver: str,
        user_pinyin_dict: Mapping[str, str],
        user_seg_dict: Mapping[str, Sequence[str]],
        asset_resolver: GraphicAssetResolver | None,
        parse_math_tree: TreeParser = _frontend_parse_math_tree,
        parse_music_tree: TreeParser = _frontend_parse_music_tree,
        parse_graphic_tree: TreeParser = _frontend_parse_graphic_tree,
    ) -> None:
        self.profile = profile
        self._profile = profile_obj
        self.analyzer = analyzer
        self.resolver = resolver
        self.user_pinyin_dict = user_pinyin_dict
        self.user_seg_dict = user_seg_dict
        self.asset_resolver = asset_resolver
        # The owning Pipeline's compilation fingerprint
        # (:attr:`brailix.pipeline.Pipeline.fingerprint`), assigned by
        # ``Pipeline.__post_init__`` right after construction (it is derived
        # from this driver's own resolved adapter names, so it can't be a
        # constructor argument).  Stamped onto every block this driver
        # populates and compared on re-entry so children built under another
        # configuration are rebuilt, not reused.  ``None`` (a bare driver in
        # a unit test) disables both the stamping and the comparison.
        self.fingerprint: str | None = None
        # Injected tree parsers (see the class docstring): defaults are the
        # real frontend entry points; a test replaces one on the instance to
        # simulate an adapter failure.
        self._parse_math_tree = parse_math_tree
        self._parse_music_tree = parse_music_tree
        self._parse_graphic_tree = parse_graphic_tree

    @property
    def parse_identity(self) -> str:
        """The identity every parsed-tree cache key carries — this driver's
        compilation fingerprint, or ``""`` when it has none.

        One place owns that normalisation. A driver with no fingerprint is a
        bare unit-test construction: it stamps nothing and invalidates nothing
        (see :meth:`_heal_stale_children`), so its cache entries key on the
        empty identity rather than on a configuration nobody declared.
        """
        return self.fingerprint or ""

    def populate_block(
        self,
        block: _Any,
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
        from brailix.ir.document import List as ListBlock
        from brailix.ir.document import Table

        if isinstance(block, ListBlock):
            for item in block.items:
                self.populate_block(item, ctx, tree_in=tree_in, tree_out=tree_out)
            return
        if isinstance(block, Table):
            for row in block.rows:
                self._populate_row(row, ctx, tree_in=tree_in, tree_out=tree_out)
            return
        self._heal_stale_children(block)

        # Leaf block.  Populate children from raw ``text`` only when it's
        # present and nothing has filled them yet; the per-kind handlers in
        # :mod:`brailix.pipeline._populate` differ only in *how* they populate.
        if block.text and not block.children:
            populate_leaf(self, block, ctx, tree_in=tree_in, tree_out=tree_out)
            # Stamp the configuration that built these children so a later
            # populate under a different configuration rebuilds them (see
            # :meth:`_heal_stale_children`).  After the populate, so a
            # strict-mode abort can't leave a stamped-but-empty block.
            block.frontend_fingerprint = self.fingerprint
            return

        # Already populated (or no text): a text-bearing block still lands
        # a span.  Single rule for every block kind — math / score / code /
        # prose alike — so the pre-populated "text + children, no span"
        # case can't drift per kind.
        #
        # Contract note: a MathBlock/ScoreBlock/MusicBlock that arrives already
        # filled AND whose children still match its text — the consistent
        # re-translate case; a STALE edit (text changed after population) is
        # self-healed above by dropping the children so the populate path
        # re-parses — does NOT get its parse tree re-recorded into ``tree_out``
        # here: the ET tree isn't reconstructable from the flattened children
        # without re-parsing, which would defeat the cache.  A caller that
        # reuses such consistent pre-filled IR blocks and needs the tree in the
        # next compile's reuse pool must thread it via ``tree_in`` rather than
        # rely on this method to re-record it.
        if block.span is None and block.text:
            block.span = Span(0, len(block.text))

    def _populate_row(
        self,
        row: _Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Populate one table row's cells and rebase their spans to the row.

        Each cell is tokenised in isolation, so its inline spans come out
        local to the cell's own text.  A row's display text is its cells
        joined by two spaces (matching the backend's two-blank column
        separator), so every cell's spans are shifted into that row
        coordinate — otherwise a non-first cell's inline node / braille cell
        highlights the wrong column.

        The rebase is stated as an **invariant, not a one-off shift**: after
        this pass a cell with provenance satisfies ``cell.span.start ==
        cell_offset``, and its descendants sit at ``cell_offset + cell-local``.
        Re-establishing it on every pass (rather than shifting once, at the
        moment a cell is first populated) is what makes an *edited* table
        re-compile correctly, because a cell's offset depends on the cells
        **before** it while staleness is judged per cell:

        * Widening column 0 moves column 1 even though column 1's own text —
          and therefore its children — is untouched and reused.  The applied
          offset is read back from ``cell.span.start``, so the cell is shifted
          by the *difference*, never re-shifted from scratch.
        * A cell whose own text changed has its children dropped by the
          stale-heal; the span it still carries describes the OLD text at the
          OLD offset, so it is cleared first and rebuilt cell-local from the
          current text.  (Reusing it would both keep the old length and get
          shifted a second time, landing the cell past the end of the row.)

        A cell that ends up with no span at all — hand-built children with no
        ``text`` to synthesise one from — is left alone, per the hand-built-IR
        "used as-is" contract: there is no anchor to rebase against.
        """
        cell_offset = 0
        for cell in row.cells:
            # Heal BEFORE reading the children: a stale cell (edited text /
            # other configuration) is dropped here and rebuilt by the
            # recursive call below, and the rebuilt children need the same
            # rebase a fresh populate gets.
            self._heal_stale_children(cell)
            if cell.text is not None and not cell.children:
                # About to (re)populate from ``text``: any span still on the
                # cell describes a previous compile's text at a previous
                # offset.  Drop it so ``_ensure_block_span`` rebuilds a
                # cell-local one from the current text.
                cell.span = None
            self.populate_block(cell, ctx, tree_in=tree_in, tree_out=tree_out)
            applied = cell.span.start if cell.span is not None else None
            if applied is not None and applied != cell_offset:
                _shift_node_spans(cell, cell_offset - applied)
            cell_offset += _table_cell_source_len(cell) + _TABLE_CELL_GAP

    def _heal_stale_children(self, block: _Any) -> None:
        """Drop ``children`` that no longer describe ``block.text`` — the
        stale-re-entry self-heal the populate paths rely on.

        Two ways a populated block goes stale (both would otherwise be
        silently reused, emitting braille that doesn't match the input):

        * **Edited text**: the caller mutated
          ``block.text`` after population.  Detected with the SAME surface
          the cache key uses (:func:`_block_surface`) — when the
          reconstructed child surface no longer equals the raw text, drop
          the children so the populate path rebuilds them from the
          authoritative ``block.text``.  ``text`` is authoritative whenever
          it is a string — **including the empty string**: editing a
          populated block to ``""`` clears its children (and the block
          compiles to nothing), it does not keep emitting the old
          content's braille.  Only ``text is None`` — the hand-built-IR
          shape, where there is no raw source to compare against — keeps
          the documented "children used as-is" contract.
        * **Changed configuration**: the children were populated by a
          pipeline whose compilation fingerprint differs from this one's —
          a different resolver / analyzer / user dictionary / profile
          content would produce different semantic IR from the very same
          text, so text equality proves nothing.  Detected via the
          ``frontend_fingerprint`` stamp populate leaves behind.  A block
          with **no** stamp is left alone: hand-built children keep the
          documented "used as-is" contract, and a driver with no
          fingerprint (bare unit-test construction) never invalidates.

        A block whose children still reflect its text and configuration —
        the normal re-translate case — is untouched, preserving the
        "re-translation skips the frontend cost" optimization
        (:meth:`Pipeline.translate_document`).

        Both invalidation paths clear the ``frontend_fingerprint`` stamp along
        with the children, through :meth:`_invalidate`: the stamp's meaning is
        "this is the configuration that built the children currently on this
        block", so a stamp outliving them is already false. It stays false
        whenever the rebuild doesn't complete — a strict-mode abort or an
        adapter exception between the drop and the re-populate leaves a block
        with no children still advertising the *old* configuration, which is
        the state the populate path's "no stamp before children exist" rule
        exists to keep out of the IR.
        """
        if not block.children:
            return
        if block.text is not None and _block_surface(block) != block.text:
            self._invalidate(block)
            return
        if not block.text:
            return
        stamp = getattr(block, "frontend_fingerprint", None)
        if (
            stamp is not None
            and self.fingerprint is not None
            and stamp != self.fingerprint
        ):
            self._invalidate(block)

    @staticmethod
    def _invalidate(block: _Any) -> None:
        """Drop a block's populated ``children`` and the stamp describing them.

        The single way children are invalidated, so the pair can't come apart:
        a stamp is only ever true of children that exist (see
        :meth:`_heal_stale_children`).
        """
        block.children = []
        block.frontend_fingerprint = None

    # --- Frontend orchestration --------------------------------------
    #
    # All frontend stages live in :mod:`brailix.frontend`. Pipeline
    # only orchestrates: segment → normalize → per-segment routing →
    # math attachment. The routing is language-agnostic — segmenter,
    # normalizer and the prose frontend are each selected by the active
    # profile's language (see :meth:`frontend_options` /
    # :meth:`_process_segment`), so adding a language is registration,
    # not a change here. See ARCHITECTURE#arch-language-slots.

    def frontend_options(self) -> dict[str, _Any]:
        lang = self._profile.language.split("-")[0]
        return {
            # The active language, published for whichever adapter needs it.
            # The ``auto`` segmenter / normalizer read it to pick a
            # language-specific implementation; the orchestrator resolves
            # nothing on their behalf, so "which segmenter runs" is decided
            # in one place — the adapter — the same way the analyzer /
            # resolver chains decide their own.
            LANGUAGE_OPTION: lang,
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
            # Read by the zh tokenizer post-pass. Keyed unconditionally (like
            # the pinyin dictionary) rather than per-language: a language whose
            # frontend doesn't look for it simply never reads the key.
            "user_seg_dict": self.user_seg_dict,
            # Forwarded onto the GraphicsContext (built from a copy of these
            # options in _populate.populate_graphic_block) so a graphic-image fence's
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
        # its script) — no change here. See ARCHITECTURE#arch-language-slots.
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
        # misleading UNHANDLED_SEGMENT_TYPE an `elif` would fall through to.
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
        # Already parsed (the frontend ran twice, or the caller pre-populated
        # the node). Still record it in ``tree_out`` so the caller's per-block
        # snapshot is complete — otherwise a re-parse taking this
        # short-circuit would silently drop the formula from the next
        # compile's reuse pool. This is the one step display math has no
        # equivalent of, which is why it sits here rather than in the shared
        # helper.
        if node.math is not None:
            cache_record(
                tree_out,
                # Same key shape (and therefore the same pool) as a display
                # MathBlock: an identical formula parses to the same tree
                # inline or displayed.
                tree_cache_key(
                    "math", node.source, node.surface, identity=self.parse_identity
                ),
                node.math,
            )
            return

        # Everything else is the shared vertical skeleton: cache key, lookup,
        # context construction, the parse, the exception ladder, the warning,
        # and recording a success. Two copies of that drift exactly as one
        # would expect: a repair to the display path's exception ladder does
        # not reach inline math's. What stays local is the recovery, genuinely
        # different: display math falls back to one Unknown per character,
        # inline math keeps the node with no tree (the backend's MATH_NO_IR
        # path degrades that to a warning).
        tree, _error = parse_cached_tree(
            ctx,
            domain="math",
            source=node.source,
            text=node.surface,
            span=node.span,
            # Nothing beyond (source, surface) feeds a math parse, so no salt.
            identity=self.parse_identity,
            parser=self._parse_math_tree,
            context_factory=lambda: MathContext(
                source=node.source,
                mode="inline",
                profile=self.profile,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            ),
            code="MATH_INLINE_PARSE_FAILED",
            label="inline math",
            tree_in=tree_in,
            tree_out=tree_out,
        )
        # ``None`` on failure — the documented soft-fail for an inline formula.
        node.math = tree
