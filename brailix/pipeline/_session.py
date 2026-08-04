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

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import (
    INLINE_TEXT_TRANSLATOR_KEY,
    BackendContext,
    FrontendContext,
)
from brailix.core.errors import WarningCollector
from brailix.core.span import Span
from brailix.pipeline._fingerprint import registries_generation
from brailix.pipeline._results import TreeSubcache

if _TYPE_CHECKING:
    from brailix.ir.braille import BrailleCell
    from brailix.pipeline import Pipeline


def warn_epoch_changed(warnings: WarningCollector) -> None:
    """Record ``COMPILE_EPOCH_CHANGED`` on ``warnings``.

    One wording for every entry point. The standalone tactile entry has no
    :class:`CompilationSession` to hang the check on (a graphic's compile is
    Pipeline-free), so it snapshots the generation itself and calls this —
    which keeps the diagnostic a caller sees identical whichever path
    produced it.
    """
    warnings.warn(
        code="COMPILE_EPOCH_CHANGED",
        message=(
            "an adapter was registered or unregistered while this compile "
            "was running; its braille may mix the old and new "
            "implementations. Recompile once the registration settles."
        ),
        source="pipeline",
    )


@_dataclass(slots=True)
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
    # The compilation identity this run is pinned to, read ONCE at ``begin``.
    # Everything that has to agree with the braille this run emits — the
    # frontend's stamp, the parsed-tree cache identity, the caller's
    # ``source_hash`` — reads it from here rather than re-reading
    # :attr:`Pipeline.fingerprint`, which moves under a concurrent
    # ``register`` / ``unregister``. Re-reading it at the end of a compile
    # produced a block whose cells came from one epoch and whose cache key
    # named another: two compiles either side of a mid-run registration
    # returned *the same* ``source_hash`` for provably different braille.
    fingerprint: str = ""
    # The registry-generation vector at ``begin``, kept so the end of the run
    # can tell whether the registration surface moved underneath it (see
    # :meth:`epoch_drifted`).
    generation: tuple[int, ...] = ()
    tree_in: TreeSubcache = _field(default_factory=dict)
    tree_out: TreeSubcache = _field(default_factory=dict)

    def epoch_drifted(self) -> bool:
        """True if an adapter registration landed while this run was compiling.

        Pinning the fingerprint keeps the cache key honest about *an* epoch,
        but it cannot make the run itself single-epoch: the frontend resolves
        adapter names on every use, so a registration landing mid-run means
        earlier nodes were translated by the outgoing implementation and later
        ones by its replacement. That block is a blend no fingerprint
        describes, so the run reports it rather than returning it silently.
        """
        return registries_generation() != self.generation

    def report_epoch_drift(self) -> bool:
        """Emit ``COMPILE_EPOCH_CHANGED`` if the epoch moved; return whether it
        did.

        Every ``translate_*`` entry point ends here, so the diagnostic does not
        depend on which one the caller picked. It used to live only in the
        block-level compile, on the reasoning that only that path returns a
        cache key — but the blend is a property of the *run*, not of the result
        shape: a whole-document translation straddling a registration is just
        as much a mix of two implementations, and silently returning it told
        the caller nothing.
        """
        if not self.epoch_drifted():
            return False
        warn_epoch_changed(self.warnings)
        return True

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
        #   runtime re-register would be reused as-is. Read exactly once and
        #   kept on the session: every later consumer in this run takes it
        #   from there, so the run cannot start on one identity and finish on
        #   another.
        # * ``asset_resolver`` — ``Pipeline.asset_resolver`` is a plain
        #   assignable field (a front-end binds its resolver to an
        #   already-built pipeline: ``pipe.asset_resolver = ...``), while
        #   the driver holds its own copy from ``__post_init__``; without
        #   this sync a late-bound resolver would silently never run and
        #   every in-document image would soft-fail to a blank raster.
        generation = registries_generation()
        fingerprint = pipeline.fingerprint
        pipeline._frontend.fingerprint = fingerprint
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
            fingerprint=fingerprint,
            generation=generation,
            tree_in=tree_subcache or {},
        )


# ---------------------------------------------------------------------------
# Inline-text translator binding
# ---------------------------------------------------------------------------


@_dataclass(slots=True, frozen=True)
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
