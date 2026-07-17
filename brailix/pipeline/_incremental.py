"""The block-level incremental compile primitive.

The body of :meth:`Pipeline.translate_block`, extracted as a module function
(the same facade-stays-a-delegation shape as :mod:`brailix.pipeline._pages`):
one block runs frontend populate → optional caller IR transform → backend
expand → (for a figure) rasterisation, and comes back as a
:class:`~brailix.pipeline.CompiledBlock` with the ``source_hash`` cache key
and this run's parsed-tree pool. Everything cache-related that the compiler
owns lives here — the reuse-pool threading and the hash salting — while
cache *storage* stays the caller's job (``Pipeline`` keeps no cache).

Not public API: callers go through :meth:`Pipeline.translate_block`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from brailix.backend.block import expand_block
from brailix.core.errors import WarningCollector
from brailix.ir.braille import BrailleCell
from brailix.ir.document import Block, DocumentIR, GraphicBlock
from brailix.ir.inline import GraphicInline
from brailix.ir.tactile import TactileRaster
from brailix.pipeline._helpers import block_hash
from brailix.pipeline._results import CompiledBlock, TreeSubcache
from brailix.pipeline._session import CompilationSession, _InlineTextTranslator

if TYPE_CHECKING:
    from brailix.pipeline import Pipeline

# Tactile profile used when an inline figure block (a GraphicBlock embedded in
# a braille document) is rasterised through the main pipeline
# (ARCHITECTURE.md G1).  ``"generic"`` matches the default a
# standalone ``translate_graphic`` call uses; a document-level / per-block
# tactile profile is a later refinement (G3/G4).
_DEFAULT_INLINE_TACTILE_PROFILE = "generic"


def compile_block(
    pipeline: Pipeline,
    block: Block,
    *,
    ir_transformer: Callable[[DocumentIR], None] | None = None,
    tree_subcache: TreeSubcache | None = None,
) -> CompiledBlock:
    """Compile one block end-to-end for ``pipeline``.

    See :meth:`Pipeline.translate_block` for the full contract — this
    function is its body.
    """
    # One session for this block: fresh collector + matching contexts,
    # with the backend context stamped with this block's type up front —
    # the only difference from the translate_text / translate_document
    # setup — so expand_block sees the right block_type without a rebuild.
    # The session also carries the parsed-tree sub-cache pair:
    # ``tree_in`` is read-only (the caller-provided reuse pool),
    # ``tree_out`` accumulates trees from this compile.  Kept out of
    # :class:`FrontendContext` to avoid polluting the public
    # adapter-facing surface with front-end-specific state.
    session = CompilationSession.begin(
        pipeline, block_type=block.type, tree_subcache=tree_subcache
    )
    pipeline._frontend.populate_block(
        block,
        session.frontend_ctx,
        tree_in=session.tree_in,
        tree_out=session.tree_out,
    )

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
    braille_blocks = expand_block(block, session.backend_ctx, pipeline._profile)

    # Tactile-graphics inline embedding (ARCHITECTURE.md G1):
    # a figure block rasterises to a TactileRaster through THIS same
    # incremental pipeline — no separate ``translate_graphic`` call — so a
    # braille document holding figures compiles down one path.  Labels
    # translate through this pipeline's own text path, so a figure's labels
    # come out in the document's braille standard automatically.
    raster: TactileRaster | None = None
    if isinstance(block, GraphicBlock):
        raster, _tree = rasterize_graphic_block(
            pipeline,
            block,
            session.warnings,
            tactile_profile=_DEFAULT_INLINE_TACTILE_PROFILE,
            # Labels report into this block's own collector — a figure's
            # untranslatable label is a real diagnostic of this compile,
            # not preview noise.
            label_translator=_InlineTextTranslator(
                pipeline, session.warnings, "graphic_label", block.span
            ),
        )

    # Stable cache key: textual surface + resolved profile + structure,
    # salted with this pipeline's compilation fingerprint so a cache
    # shared across differently-configured pipelines (resolver, user
    # dictionary, edited profile content, ...) can never serve the other
    # configuration's braille.  Callers who need override-aware cache
    # keys (a proofreading front-end) compose this hash with their own
    # override-list salt outside the compiler.
    source_hash = block_hash(
        block, pipeline.profile_name, fingerprint=pipeline.fingerprint
    )

    return CompiledBlock(
        block_id=block.id or "",
        source_hash=source_hash,
        ir=block,
        braille_blocks=braille_blocks,
        warnings=list(session.warnings.warnings),
        tree_subcache=session.tree_out,
        raster=raster,
    )


def rasterize_graphic_block(
    pipeline: Pipeline,
    block: Any,
    warns: WarningCollector,
    *,
    tactile_profile: str | Any,
    label_translator: Callable[[str], list[BrailleCell]] | None,
    record_provenance: bool = False,
) -> tuple[TactileRaster, ET.Element]:
    """Rasterise an already-populated :class:`GraphicBlock` into a
    :class:`~brailix.ir.tactile.TactileRaster`.

    The rasterising tail shared by the standalone tactile entry and the
    inline-in-a-braille-document path (:func:`compile_block`,
    ARCHITECTURE.md G1) — one rasteriser, not two. Pulls the
    SVG tree off the block's :class:`GraphicInline` child
    (``_populate_graphic_block`` always lands one — an error-marked SVG on
    soft-failure, never ``None`` — so a figure always rasterises to
    *something*), loads the tactile profile, and rasterises. Returns
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
