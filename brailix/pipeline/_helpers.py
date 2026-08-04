"""Module-level standalone helpers used by the pipeline orchestrator.

Pure functions split out of :mod:`brailix.pipeline` so the orchestrator
module stays focused on the :class:`Pipeline` class.  Re-exported from
:mod:`brailix.pipeline` so ``brailix.pipeline.block_hash`` etc. keep
resolving.
"""

from __future__ import annotations

import hashlib as _hashlib
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.span import Span
from brailix.frontend import language_frontend_registry
from brailix.ir.document import Block

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET
    from typing import Any

    from brailix.pipeline._results import TreeSubcache


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


def _ensure_block_span(block: Any) -> tuple[str, Span, Span]:
    """Read ``block.text`` and guarantee ``block.span`` is non-None.

    Returns ``(text, document_span, leaf_span)`` — the two coordinate
    systems a populated leaf block lives in
    (ARCHITECTURE#arch-spans), handed back separately so a call site has
    to *name* which one it means:

    * ``text``          — ``block.text`` coerced to "" when missing.
    * ``document_span`` — ``block.span`` after the call (never None): where
      the block sits in the **source document**.
    * ``leaf_span``     — ``Span(0, len(text))``: the whole of ``text`` in
      **leaf-local** coordinates, which is the system every
      :class:`~brailix.ir.inline.InlineNode` span and every cell
      ``source_span`` under this block is in.

    Handing back one span for both was the bug this shape exists to
    prevent: the specialised populates below wrote the *document* span
    onto the carrier inline node they built, so a consumer that follows
    the documented contract and adds ``block.span.start`` to a leaf-local
    offset landed at twice the block's offset — every math / code / music
    / graphic block past the first one in a document.

    Mutates ``block.span`` when it was None (single source of truth for
    "every populated block ends up with a span"), which is also why the
    fallback paths no longer ask whether the caller supplied one: after
    this call the leaf coordinates are well-defined either way.
    """
    text = block.text or ""
    if block.span is None:
        block.span = Span(0, len(text))
    return text, block.span, Span(0, len(text))


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


# Bump when the composition of :func:`block_hash` changes, so any cache that
# outlives one recipe (a front-end that ever persists digests across sessions)
# can't serve an entry computed under the old composition against the new one.
# Folded INTO the digest below, not merely prepended, so it perturbs the hash.
_BLOCK_HASH_VERSION = "3"


def block_hash(
    block: Block, profile_name: str, *, fingerprint: str | None = None
) -> str:
    """SHA-256 hex digest of ``(block textual surface, profile, structure)``,
    optionally salted with a compilation-configuration ``fingerprint``.

    The default cache key for a compiled block. A change in source text,
    profile name, or structural shape flips the hash — and, when
    ``fingerprint`` is supplied (:attr:`Pipeline.fingerprint`), so does any
    change in the compilation configuration: the resolved profile's actual
    content, the selected segmenter / normalizer / analyzer / resolver, the
    user pinyin and segmentation dictionaries, the run mode, or the brailix
    version (see
    :func:`brailix.pipeline._fingerprint.compilation_fingerprint` for the exact coverage
    and its limits). :meth:`Pipeline.translate_block` always passes its own
    fingerprint, so :attr:`CompiledBlock.source_hash` is safe to cache on
    within one process without ever serving output compiled under a
    configuration the caller no longer runs.

    **Without** ``fingerprint`` the digest covers only what its three inputs
    say: two calls agree whenever surface, profile *name* and structure agree,
    even if the pipelines behind them would compile the block differently
    (different resolver, different user dictionary, a same-named profile with
    edited content). Equal hash ⟹ equal braille holds only under a fixed
    compilation configuration — a cache shared across differently-configured
    pipelines must use the salted form.

    Folding in :meth:`Block.structure_key` is what covers structural identity.
    The textual surface can't tell a :class:`~brailix.ir.document.Heading` from
    a same-text :class:`~brailix.ir.document.Paragraph`, an ordered from an
    unordered :class:`~brailix.ir.document.List`, or two
    :class:`~brailix.ir.document.Table`\\ s of different shape — all of which
    render under different layout rules. Keying on the surface alone let a block
    cache hand back the wrong block's braille (silent wrong output, not a
    crash); ``structure_key`` supplies exactly that structural identity, derived
    generically from the IR so a new structural field is covered automatically.

    Callers that need an EXTRA dimension in the key — override-aware caching in
    a proofreading front-end — compose this digest with their own salt at the
    caller layer (the compiler doesn't know about overrides), e.g.
    ``block_hash(block, profile) + "|" + "|".join(override_ids)``. They no
    longer fold ``structure_key`` themselves; it is already accounted for here.
    """
    h = _hashlib.sha256()
    h.update(_BLOCK_HASH_VERSION.encode("utf-8"))
    h.update(b"|")
    h.update(_block_surface(block).encode("utf-8"))
    h.update(b"|")
    h.update(profile_name.encode("utf-8"))
    h.update(b"|")
    h.update(block.structure_key().encode("utf-8"))
    if fingerprint is not None:
        # Tagged separator: a crafted structure_key ending in the same bytes
        # can't collide an unsalted digest with a salted one.
        h.update(b"|fp|")
        h.update(fingerprint.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Parsed-tree reuse pool (shared math / music incremental cache)
# ---------------------------------------------------------------------------


def tree_cache_key(
    domain: str,
    source: str,
    surface: str,
    salt: str = "",
    *,
    identity: str,
) -> tuple[str, str, str, str, str]:
    """Build a parsed-tree cache key — the one place its shape is decided.

    ``identity`` is the *parse identity*: the compilation configuration the
    tree was parsed under, i.e. the producing pipeline's
    :attr:`~brailix.pipeline.Pipeline.fingerprint` (``""`` for a bare
    :class:`~brailix.pipeline.frontend_driver.FrontendDriver` that has none). Keyword-only and
    required, because forgetting it is exactly the failure this slot exists to
    prevent: a source adapter receives the whole
    :class:`~brailix.core.context.MathContext` / ``MusicContext`` /
    ``GraphicsContext`` — profile, options, run mode — and the registry behind
    ``source`` is open, so "same ``(domain, source, surface)``" does **not**
    imply "same tree". Without this slot a pool threaded from one pipeline
    into another (``translate_block(tree_subcache=...)``), or reused across a
    runtime re-``register`` of the adapter that built it, kept handing back the
    OTHER configuration's tree while the block's ``source_hash`` was minted
    under the current one — silently wrong braille wearing a fresh cache key.
    The fingerprint already covers profile content, adapter selection, run
    mode, the user dictionary and every compilation registry's generation
    (see :func:`brailix.pipeline._fingerprint.compilation_fingerprint`), so folding it in
    is deliberately coarse in the safe direction: a same-configuration
    re-compile — the proofreading case the pool exists for — still hits.

    ``salt`` carries whatever else the parse result depended on *within* one
    configuration: the music mode, a graphic's asset-resolver identity. It
    defaults to ``""`` for a parse that consumes nothing more (math). Routing
    every key through here is deliberate: the shape drifted once already —
    music blocks and score blocks parse in different modes, but the mode
    wasn't in the key, so two blocks with identical source text shared one
    cached tree. A key built by hand at each call site is exactly where that
    recurs.
    """
    return (domain, identity, source, surface, salt)


def cache_lookup(
    tree_in: TreeSubcache | None, key: tuple[str, str, str, str, str]
) -> ET.Element | None:
    """Return the cached parsed tree for ``key``, or ``None`` on a miss.

    A ``None`` reuse pool — the non-incremental call paths that pass no
    ``tree_subcache`` — reads as a miss. Shared by the math / music / graphic
    populate paths and :meth:`FrontendDriver.attach_math` so they all keep
    identical lookup semantics; see :meth:`Pipeline.translate_block` for
    the pool contract.
    """
    return tree_in.get(key) if tree_in is not None else None


def cache_record(
    tree_out: TreeSubcache | None,
    key: tuple[str, str, str, str, str],
    tree: ET.Element | None,
) -> None:
    """Record ``tree`` under ``key`` in the output reuse pool.

    No-op when there is no output pool, or when nothing parsed
    (``tree is None``) — so a failed parse never poisons the pool with a
    ``None`` a later compile would mistake for a hit. The single writer
    behind all the tree-caching call sites.
    """
    if tree is not None and tree_out is not None:
        tree_out[key] = tree
