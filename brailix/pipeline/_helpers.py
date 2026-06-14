"""Module-level standalone helpers used by the pipeline orchestrator.

Pure functions split out of :mod:`brailix.pipeline` so the orchestrator
module stays focused on the :class:`Pipeline` class.  Re-exported from
:mod:`brailix.pipeline` so ``brailix.pipeline._resolve_language_adapter``,
``brailix.pipeline.block_hash`` etc. keep resolving.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from brailix.core.registry import Registry
from brailix.core.span import Span
from brailix.frontend import language_frontend_registry
from brailix.ir.document import Block

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET

    from brailix.pipeline._results import TreeSubcache


def _resolve_language_adapter(
    registry: Registry[Any], configured: str, default_name: str, lang: str
) -> str:
    """Pick a segmenter / normalizer name for the active language.

    Precedence: an explicit, non-default Pipeline override wins; else an
    adapter registered under the language subtag (so a new language ships
    its own script-aware segmenter / structural normalizer); else the
    built-in default. Chinese registers neither, so it resolves to
    ``"default"`` — the Han-aware segmenter and the date-marker
    normalizer that ship with the library.
    """
    if configured != default_name:
        return configured
    if registry.has(lang):
        return lang
    return default_name


def _all_prose_types() -> frozenset[str]:
    """Union of every registered language frontend's ``prose_types``.

    Used only to distinguish a prose segment that *some* language would
    handle (the active profile just has no matching frontend → warn
    ``NO_LANGUAGE_FRONTEND``) from a genuinely unknown segment type
    (warn ``UNHANDLED_SEGMENT_TYPE``).
    """
    return frozenset(
        t
        for name in language_frontend_registry.names()
        for t in language_frontend_registry.get(name).prose_types
    )


def _ensure_block_span(block: Any) -> tuple[str, Span, bool]:
    """Read ``block.text`` and guarantee ``block.span`` is non-None.

    Returns ``(text, span, had_original_span)``:

    * ``text``  — ``block.text`` coerced to "" when missing.
    * ``span``  — ``block.span`` after the call (never None).
    * ``had_original_span`` — True iff the caller-supplied block already
      had a span; lets fallback paths decide whether per-char synthesised
      cells inherit a span or stay un-anchored.

    Mutates ``block.span`` when it was None (single source of truth for
    "every populated block ends up with a span"). Shared by
    :meth:`Pipeline._populate_math_block` and
    :meth:`Pipeline._populate_music_block` — see those for context.
    """
    text = block.text or ""
    had_span = block.span is not None
    if not had_span:
        block.span = Span(0, len(text))
    return text, block.span, had_span


def _block_surface(block: Any) -> str:
    """Reconstruct a human-readable surface for a block.

    Used by :meth:`Pipeline.translate_document` so the resulting
    :class:`TranslationResult` has a meaningful ``text`` value for
    proofread tooling. Falls back to the original raw ``text`` if
    children haven't been populated; otherwise joins child surfaces.
    Composite containers recurse into their ``items`` / ``rows`` /
    ``cells``.
    """
    from brailix.ir.document import List as ListBlock
    from brailix.ir.document import Table

    if isinstance(block, ListBlock):
        return "\n".join(_block_surface(it) for it in block.items)
    if isinstance(block, Table):
        return "\n".join(
            " | ".join(_block_surface(c) for c in row.cells)
            for row in block.rows
        )
    if block.children:
        return "".join(child.surface for child in block.children)
    return block.text or ""


def block_hash(block: Block, profile_name: str) -> str:
    """SHA-256 hex digest of ``(block textual surface, profile)``.

    Used as a stable cache key by :class:`CompiledBlock`. Same hash =
    identical inputs = cached compilation result is reusable. A
    change in source text or selected profile flips the hash.

    Callers that need override-aware (or any other dimension) cache
    keys should compose this digest with their own salt at the caller
    layer — the compiler doesn't know about overrides. A proofreading
    front-end, for example, composes ``block_hash + "|" +
    "|".join(sorted_override_ids)`` for its block cache.
    """
    h = hashlib.sha256()
    h.update(_block_surface(block).encode("utf-8"))
    h.update(b"|")
    h.update(profile_name.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Parsed-tree reuse pool (shared math / music incremental cache)
# ---------------------------------------------------------------------------


def cache_lookup(
    tree_in: TreeSubcache | None, key: tuple[str, str, str]
) -> ET.Element | None:
    """Return the cached parsed tree for ``key``, or ``None`` on a miss.

    A ``None`` reuse pool — the non-incremental call paths that pass no
    ``tree_subcache`` — reads as a miss. Shared by the math / music
    populate paths and :meth:`Pipeline._attach_math` so all three keep
    identical lookup semantics; see :meth:`Pipeline.translate_block` for
    the pool contract.
    """
    return tree_in.get(key) if tree_in is not None else None


def cache_record(
    tree_out: TreeSubcache | None,
    key: tuple[str, str, str],
    tree: ET.Element | None,
) -> None:
    """Record ``tree`` under ``key`` in the output reuse pool.

    No-op when there is no output pool, or when nothing parsed
    (``tree is None``) — so a failed parse never poisons the pool with a
    ``None`` a later compile would mistake for a hit. The single writer
    behind all three tree-caching call sites.
    """
    if tree is not None and tree_out is not None:
        tree_out[key] = tree
