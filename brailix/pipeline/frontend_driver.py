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
    MathContext,
)
from brailix.core.defaults import DEFAULT_NORMALIZER, DEFAULT_SEGMENTER
from brailix.core.errors import PROGRAMMING_ERRORS, StrictModeError
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
from brailix.ir.inline import InlineNode, MathInline, Segment
from brailix.pipeline._helpers import (
    _all_prose_types,
    _block_surface,
    _resolve_language_adapter,
    cache_lookup,
    cache_record,
)
from brailix.pipeline._populate import populate_leaf
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
        from brailix.ir.document import List as ListBlock
        from brailix.ir.document import Table

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
                    # Heal HERE, before reading ``already_populated``: a stale
                    # cell (edited text / other configuration) is dropped and
                    # rebuilt inside the recursive call below, and the rebuilt
                    # children need the span rebase exactly like a fresh
                    # populate — reading the pre-heal children count would
                    # skip it and highlight the wrong column.
                    self._heal_stale_children(cell)
                    already_populated = bool(cell.children)
                    self.populate_block(
                        cell, ctx, tree_in=tree_in, tree_out=tree_out
                    )
                    if cell_offset and not already_populated:
                        _shift_node_spans(cell, cell_offset)
                    cell_offset += _table_cell_source_len(cell) + _TABLE_CELL_GAP
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

    def _heal_stale_children(self, block: Any) -> None:
        """Drop ``children`` that no longer describe ``block.text`` — the
        stale-re-entry self-heal the populate paths rely on.

        Two ways a populated block goes stale (both would otherwise be
        silently reused, emitting braille that doesn't match the input):

        * **Edited text** (the P1-2 footgun): the caller mutated
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
        """
        if not block.children:
            return
        if block.text is not None and _block_surface(block) != block.text:
            block.children = []
            # The stamp describes children that no longer exist; clear it so
            # an emptied block (``text == ""``, never re-populated) doesn't
            # keep advertising the configuration that built the old ones.
            block.frontend_fingerprint = None
            return
        if not block.text:
            return
        stamp = getattr(block, "frontend_fingerprint", None)
        if (
            stamp is not None
            and self.fingerprint is not None
            and stamp != self.fingerprint
        ):
            block.children = []

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
        cache_key = ("math", node.source, node.surface, "")
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
            # See _populate.populate_music_block: keep the code, don't rewrap.
            raise
        except PROGRAMMING_ERRORS:
            # A code defect is never a "bad formula" — surface it rather than
            # degrade the inline node to None. See brailix.core.errors.
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
