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
from brailix.renderer import renderer_registry

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


# ---------------------------------------------------------------------------
# Shared parsed-tree reuse pool
# ---------------------------------------------------------------------------

# Reuse pool for parsed MathML / MusicXML trees, keyed by
# ``(domain, source, surface)`` where ``domain`` is ``"math"`` or
# ``"music"``.  An incremental recompile passes the prior compile's pool
# back in so a node whose source didn't change (e.g. an override edit
# that leaves the formula / score text untouched) reuses the cached tree
# instead of re-parsing it — the dominant cost for large scores.  The
# domain prefix keeps math and music entries from colliding on a shared
# ``source`` value such as ``"plain"``.
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
    * ``tree_subcache`` — parsed MathML / MusicXML tree cache keyed by
      ``(domain, source, surface)`` (``domain`` ∈ ``{"math", "music"}``).
      Populated for every math / music node parsed during this compile;
      reuseable by a future :meth:`Pipeline.translate_block` call (pass
      the dict in via the ``tree_subcache`` parameter) so the same
      formula / score isn't parsed twice when surrounding text — or an
      unrelated override — changes.  Empty when the block has no
      math or music.
    * ``source_hash`` — stable digest of ``(block text, profile name)``.
      Front-ends use this as the base of their cache key; if a
      front-end wants override-aware caching (a proofreading front-end), it composes
      this hash with its own salt.
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
