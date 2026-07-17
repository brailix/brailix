"""Run-scoped compilation state — one ``translate_*`` call's working set.

A :class:`~brailix.pipeline.Pipeline` is long-lived **configuration**:
profile, adapter names, user dictionary, fingerprint. Everything scoped to a
*single* translate run lives here instead:

* :class:`CompilationSession` — the fresh :class:`WarningCollector` plus the
  frontend / backend contexts bound to it, and the parsed-tree reuse pool an
  incremental compile threads through (``tree_in`` / ``tree_out``). One
  object instead of a growing tuple, so future per-run state (a per-run
  cache, a scoped asset resolver) lands here without widening every call
  site — the first step of keeping ``Pipeline`` a facade while its
  internals grow named services.
* :class:`_InlineTextTranslator` — the run-scoped binding that fixes where
  embedded-text diagnostics go (the session's collector, or nowhere for the
  preview contract) and how they are attributed.

Nothing here is public API: construct sessions through
:meth:`CompilationSession.begin` from ``Pipeline`` methods only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from brailix.core.context import (
    INLINE_TEXT_TRANSLATOR_KEY,
    BackendContext,
    FrontendContext,
)
from brailix.core.errors import WarningCollector
from brailix.core.span import Span
from brailix.pipeline._results import TreeSubcache

if TYPE_CHECKING:
    from brailix.ir.braille import BrailleCell
    from brailix.pipeline import Pipeline


@dataclass(slots=True)
class CompilationSession:
    """The state of one translate run.

    ``warnings`` is this run's fresh collector; ``frontend_ctx`` /
    ``backend_ctx`` are both bound to it, so every diagnostic the run emits
    lands in one place under one mode policy. ``tree_in`` is the
    caller-provided parsed-tree reuse pool (read-only — see
    :data:`~brailix.pipeline.TreeSubcache`'s immutability contract) and
    ``tree_out`` accumulates what this run actually parsed; both stay empty
    dicts on the non-incremental paths that don't thread a pool.
    """

    warnings: WarningCollector
    frontend_ctx: FrontendContext
    backend_ctx: BackendContext
    tree_in: TreeSubcache = field(default_factory=dict)
    tree_out: TreeSubcache = field(default_factory=dict)

    @classmethod
    def begin(
        cls,
        pipeline: Pipeline,
        *,
        block_type: str = "paragraph",
        tree_subcache: TreeSubcache | None = None,
    ) -> CompilationSession:
        """Open a session for one run of ``pipeline``.

        ``block_type`` stamps the backend context up front (the block-level
        compile passes the real type; whole-document paths keep the
        ``"paragraph"`` default and the backend re-stamps per block).
        ``tree_subcache`` becomes ``tree_in`` — the incremental path's reuse
        pool; ``None`` reads as an always-miss empty pool.
        """
        # Refresh the frontend's run-scoped snapshots at run start:
        #
        # * ``fingerprint`` — the pipeline's fingerprint moves when a
        #   registry registration (or the asset resolver) changes (see
        #   :attr:`Pipeline.fingerprint`), and the stale-children check
        #   compares block stamps against the driver's copy — a run must
        #   compare against the CURRENT identity, or IR populated before a
        #   runtime re-register would be reused as-is.
        # * ``asset_resolver`` — ``Pipeline.asset_resolver`` is a plain
        #   assignable field (a front-end binds its resolver to an
        #   already-built pipeline: ``pipe.asset_resolver = ...``), while
        #   the driver holds its own copy from ``__post_init__``; without
        #   this sync a late-bound resolver would silently never run and
        #   every in-document image would soft-fail to a blank raster.
        pipeline._frontend.fingerprint = pipeline.fingerprint
        pipeline._frontend.asset_resolver = pipeline.asset_resolver
        warnings = WarningCollector(mode=pipeline.mode)
        frontend_ctx = FrontendContext(
            profile=pipeline.profile,
            mode=pipeline.mode,
            warnings=warnings,
            options=pipeline._frontend.frontend_options(),
        )
        backend_ctx = BackendContext(
            profile=pipeline.profile,
            mode=pipeline.mode,
            block_type=block_type,
            warnings=warnings,
            # The inline-text translator is bound to THIS run's collector:
            # embedded prose (music <words> / lyrics, math \text{...}, chem
            # conditions) reports into the same diagnostics as everything
            # else, under the same mode policy — strict fails, normal
            # records. Only the explicit preview APIs discard.
            options={
                INLINE_TEXT_TRANSLATOR_KEY: _InlineTextTranslator(
                    pipeline, warnings
                )
            },
        )
        return cls(
            warnings=warnings,
            frontend_ctx=frontend_ctx,
            backend_ctx=backend_ctx,
            tree_in=tree_subcache or {},
        )


# ---------------------------------------------------------------------------
# Inline-text translator binding
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class _InlineTextTranslator:
    """The Pipeline-built :class:`~brailix.core.protocols.InlineTextTranslator`.

    A tiny binding object around :meth:`Pipeline._translate_inline_text`:
    it fixes WHERE the nested run's diagnostics go (``host_warnings`` — the
    host compile's collector, or ``None`` for the discard-everything preview
    contract) and, optionally, HOW they are attributed (``domain`` +
    ``host_span``). Call sites deep in the backend re-tag it through
    :meth:`bind_domain` — surfaced via
    :meth:`brailix.core.context.BackendContext.inline_text_translator`'s
    ``domain`` / ``span`` arguments — so a warning inside a music
    ``<words>`` run reads differently from one inside a math
    ``\\text{...}`` run. The protocol itself stays a bare
    ``(text) -> cells`` callable; ``bind_domain`` is an optional extension
    the accessor duck-types, so a third-party translator that is a plain
    function keeps working (it just doesn't get domain attribution).
    """

    pipeline: Pipeline
    host_warnings: WarningCollector | None = None
    domain: str | None = None
    host_span: Span | None = None

    def __call__(self, text: str) -> list[BrailleCell]:
        return self.pipeline._translate_inline_text(
            text,
            host_warnings=self.host_warnings,
            domain=self.domain,
            host_span=self.host_span,
        )

    def bind_domain(
        self, domain: str, span: Span | None = None
    ) -> _InlineTextTranslator:
        """A copy of this translator attributing its warnings to ``domain``
        (and anchoring them to ``span``, the embedding node's span)."""
        return _InlineTextTranslator(
            self.pipeline, self.host_warnings, domain, span
        )
