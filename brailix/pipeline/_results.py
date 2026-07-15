"""Public result / value types returned by the pipeline.

These are the data carriers handed back from :class:`brailix.pipeline.Pipeline`
calls — split out from the orchestrator so callers can import the
result shapes without dragging in the whole pipeline module.  Re-exported
from :mod:`brailix.pipeline` so ``brailix.pipeline.TranslationResult`` etc.
keep working.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from brailix.core.defaults import DEFAULT_RENDERER
from brailix.core.errors import Warning, WarningCollector
from brailix.ir.braille import BrailleBlock, BrailleDocument
from brailix.ir.document import Block, DocumentIR
from brailix.ir.tactile import TactileRaster
from brailix.renderer import renderer_registry

# Default tactile-graphics renderer for :meth:`GraphicResult.render` — the
# embossable 8-bit grayscale BMP master (see ``ARCHITECTURE.md``
# §1.1). Like the braille renderers, it lives in ``renderer_registry``.
DEFAULT_TACTILE_RENDERER = "bmp"

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TranslationResult:
    """Output of one :meth:`Pipeline.translate_text` call.

    Holds the parsed :class:`DocumentIR` and the
    :class:`BrailleDocument` produced by the backend. Concrete output
    formats (Unicode braille, BRF, cells, layout, ...) are
    produced by calling :meth:`render` — nothing is pre-rendered, so
    you only pay for the formats you ask for.
    """

    text: str
    ir: DocumentIR
    braille_ir: BrailleDocument
    warnings: WarningCollector = field(default_factory=WarningCollector)
    default_renderer: str = DEFAULT_RENDERER

    def render(self, name: str | None = None) -> Any:
        """Render the braille IR through the named renderer.

        ``name`` defaults to :attr:`default_renderer`. Returns whatever
        the renderer produces — typically ``str`` (Unicode braille) or
        ``bytes`` (BRF); cells / layout renderers may produce other types.

        Raises :class:`KeyError` if no renderer is registered under
        ``name``; :class:`MissingExtraError` if the renderer needs an
        unavailable optional dependency.
        """
        return renderer_registry.get(name or self.default_renderer).render(
            self.braille_ir
        )

    def proofread_json(self) -> dict[str, Any]:
        """A JSON-ready dict mapping source text to braille IR for
        proofreading tools. Does not include any rendered output —
        consumers can render on demand if they need it."""
        return {
            "text": self.text,
            "ir": self.ir.to_dict(),
            "braille_ir": self.braille_ir.to_dict(),
            "warnings": self.warnings.to_list(),
        }


@dataclass(slots=True)
class GraphicResult:
    """Output of one :meth:`Pipeline.translate_graphic` call — the tactile
    vertical's counterpart to :class:`TranslationResult`.

    Holds the :class:`~brailix.ir.tactile.TactileRaster` the tactile backend
    produced and the normalised SVG tree (the graphics IR), exposed so an
    editor can show / edit the object tree. A graphic **always** rasterises to
    something — a malformed or unsupported source soft-fails to a blank raster
    plus warnings, never to ``None`` (the "pipeline never crashes" rule, same
    as braille). Concrete outputs (``.bmp`` / ``.png`` / ``.pdf`` / a U+2800
    braille-display preview) are produced by :meth:`render`, through the
    **same**
    ``renderer_registry`` the braille renderers use: a tactile renderer is just
    another file there, selected by name — there is no parallel renderer
    registry (see ``ARCHITECTURE.md``).
    """

    raster: TactileRaster
    svg_tree: ET.Element | None = None
    warnings: WarningCollector = field(default_factory=WarningCollector)
    default_renderer: str = DEFAULT_TACTILE_RENDERER

    def render(self, name: str | None = None) -> Any:
        """Render the tactile raster through the named renderer.

        ``name`` defaults to :attr:`default_renderer` (``"bmp"``). Returns
        whatever the renderer produces — ``bytes`` for ``bmp`` / ``png`` / ``pdf``,
        a ``str`` for the ``tactile_preview`` U+2800 readback. Raises
        :class:`KeyError` if no renderer is registered under ``name``.
        """
        return renderer_registry.get(name or self.default_renderer).render(
            self.raster
        )


@dataclass(slots=True)
class TactilePageResult:
    """Output of one :meth:`Pipeline.translate_document_to_pages` call — a
    braille document with embedded figures laid onto tactile page rasters
    (``ARCHITECTURE.md`` G3).

    Holds one :class:`~brailix.ir.tactile.TactileRaster` per page: braille text
    stamped as real dots plus the document's figures scaled into the flow
    (output model A — a page *is* a raster; a mixed page does not round-trip to
    BRF). ``warnings`` aggregates the per-block compile diagnostics. Concrete
    bytes come from :meth:`render` / :meth:`render_all`, through the **same**
    ``renderer_registry`` the standalone graphic and braille outputs use — a
    page is just another :class:`TactileRaster`, so ``bmp`` / ``png`` / ``pdf``
    / ``tactile_preview`` all accept it with no new renderer.
    """

    pages: list[TactileRaster] = field(default_factory=list)
    warnings: WarningCollector = field(default_factory=WarningCollector)
    default_renderer: str = DEFAULT_TACTILE_RENDERER

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def render(self, name: str | None = None, *, page: int = 0) -> Any:
        """Render one page (default the first) through the named renderer.

        ``name`` defaults to :attr:`default_renderer` (``"bmp"``). Raises
        :class:`IndexError` if ``page`` is out of range, :class:`KeyError` if
        no renderer is registered under ``name``."""
        return renderer_registry.get(name or self.default_renderer).render(
            self.pages[page]
        )

    def render_all(self, name: str | None = None) -> list[Any]:
        """Render every page through the named renderer, in order — one output
        per page (e.g. a list of ``.bmp`` byte strings ready to write as
        ``page-1.bmp`` … ``page-N.bmp``)."""
        renderer = renderer_registry.get(name or self.default_renderer)
        return [renderer.render(p) for p in self.pages]


# ---------------------------------------------------------------------------
# Shared parsed-tree reuse pool
# ---------------------------------------------------------------------------

# Reuse pool for parsed MathML / MusicXML trees, keyed by
# ``(domain, source, surface)`` where ``domain`` is ``"math"``,
# ``"music"``, or ``"graphic"``.  An incremental recompile passes the
# prior compile's pool
# back in so a node whose source didn't change (e.g. an override edit
# that leaves the formula / score text untouched) reuses the cached tree
# instead of re-parsing it — the dominant cost for large scores.  The
# domain prefix keeps math, music and graphic entries from colliding on a
# shared ``source`` value such as ``"plain"``.
TreeSubcache = dict[tuple[str, str, str], ET.Element]


# ---------------------------------------------------------------------------
# CompiledBlock — block-level cache entry for incremental compilation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CompiledBlock:
    """Block-level incremental compilation result.

    Returned by :meth:`Pipeline.translate_block`. Carries enough state
    for a front-end to cache a block's compilation independently of other
    blocks:

    * ``ir`` — frontend-populated :class:`Block` (children filled).
    * ``braille_blocks`` — backend output. For simple blocks this is
      a 1-element list; composite blocks (List / Table) expand to N
      elements (one per item / row).
    * ``warnings`` — diagnostics emitted while compiling this block.
    * ``tree_subcache`` — parsed MathML / MusicXML / graphic-SVG tree cache
      keyed by ``(domain, source, surface)`` (``domain`` ∈ ``{"math",
      "music", "graphic"}``).
      Populated for every math / music node parsed during this compile;
      reuseable by a future :meth:`Pipeline.translate_block` call (pass
      the dict in via the ``tree_subcache`` parameter) so the same
      formula / score isn't parsed twice when surrounding text — or an
      unrelated override — changes.  Empty when the block has no
      math, music, or graphic.
    * ``source_hash`` — stable digest of ``(block surface, profile,
      structure)`` (see :func:`brailix.pipeline.block_hash`). Safe as a
      cache key on its own: a same-text Heading vs Paragraph, ordered vs
      unordered list, or two differently-shaped Tables hash apart, so a
      cache keyed on it can't serve one block's braille for another. A
      front-end that also wants override-aware caching (a proofreading
      front-end) composes this hash with its own override salt.
    * ``compiled_at`` — when this entry was produced; helpful for
      debugging stale caches.

    Pipeline produces these but does **not** keep a cache itself —
    cache management is the caller's job (the library only exposes a
    block-level primitive).
    """

    block_id: str
    source_hash: str
    ir: Block
    braille_blocks: list[BrailleBlock]
    warnings: list[Warning] = field(default_factory=list)
    tree_subcache: TreeSubcache = field(default_factory=dict)
    compiled_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    # Tactile-graphics inline embedding (ARCHITECTURE.md G1):
    # a :class:`~brailix.ir.document.GraphicBlock` rasterises to a
    # :class:`TactileRaster` through the SAME incremental pipeline that
    # compiles text blocks (no separate ``translate_graphic`` call), and it
    # rides here alongside the (empty) ``braille_blocks`` placeholder that
    # holds the figure's place in the block flow.  ``None`` for every text
    # block — only a figure block carries a raster.
    raster: TactileRaster | None = None
