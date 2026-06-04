"""End-to-end brailix pipeline.

Wires together segmentation, normalization, language-specific
processing (Chinese tokenize + pinyin), math parsing, and the Backend
dispatcher into one :meth:`Pipeline.translate_text` call. Each
frontend subsystem has its own single-callable public interface
(see :mod:`brailix.frontend`); this module is just orchestration
plus the optional name-override knobs.

Rendering is **deferred**: :meth:`translate_text` returns a
:class:`TranslationResult` carrying the parsed IR and the braille IR,
but does not run a renderer. Ask for a concrete output by calling
:meth:`TranslationResult.render`.

Typical usage::

    from brailix import Pipeline

    pipe = Pipeline(profile="cn_current")
    result = pipe.translate_text("我在重庆。")
    print(result.render())          # default Unicode braille string
    print(result.render("unicode"))

Package layout
--------------

This is a subpackage; the separable pieces live in sibling modules and
are re-exported here so ``brailix.pipeline.<name>`` keeps resolving:

* :mod:`brailix.pipeline._results` — the public result / value types
  :class:`TranslationResult`, :class:`CompiledBlock`,
  :data:`TreeSubcache`.
* :mod:`brailix.pipeline._helpers` — the module-level standalone helpers
  :func:`_resolve_language_adapter`, :func:`_all_prose_types`,
  :func:`_ensure_block_span`, :func:`_block_surface`, :func:`block_hash`.

The cohesive :class:`Pipeline` orchestrator stays here, together with
:data:`_frontend_parse_math_tree` / :data:`_frontend_parse_music_tree`,
which tests monkeypatch via ``brailix.pipeline.*`` — a patch only
affects callers that look the name up in *this* namespace, and
:meth:`Pipeline._populate_block` / :meth:`Pipeline._attach_math` are
those callers, so both the names and the class must live together here.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brailix.backend.block import expand_block, translate_document
from brailix.core.config import BrailleProfile, load_profile
from brailix.core.context import (
    INLINE_TEXT_TRANSLATOR_KEY,
    BackendContext,
    FrontendContext,
    MathContext,
    MusicContext,
)
from brailix.core.defaults import (
    DEFAULT_NORMALIZER,
    DEFAULT_PINYIN_RESOLVER,
    DEFAULT_PROFILE,
    DEFAULT_RENDERER,
    DEFAULT_SEGMENTER,
    DEFAULT_ZH_ANALYZER,
)
from brailix.core.errors import RunMode, WarningCollector, normalize_run_mode
from brailix.core.span import Span
from brailix.frontend import language_frontend_registry
from brailix.frontend import normalize as _frontend_normalize
from brailix.frontend import parse_math_tree as _frontend_parse_math_tree
from brailix.frontend import segment as _frontend_segment
from brailix.frontend.music import parse_music_tree as _frontend_parse_music_tree
from brailix.frontend.normalize import normalizer_registry
from brailix.frontend.segment import segmenter_registry
from brailix.frontend.zh import (
    insert_cross_kind_boundary_spaces as _frontend_zh_boundary_spaces,
)
from brailix.input import parse_file as _parse_file
from brailix.input import parse_markdown as _parse_markdown
from brailix.input import parse_plain as _parse_plain
from brailix.ir.braille import BrailleCell
from brailix.ir.document import Block, DocumentIR, Paragraph
from brailix.ir.inline import (
    CodeInline,
    InlineNode,
    MathInline,
    MusicInline,
    Segment,
    Unknown,
)
from brailix.pipeline._helpers import (
    _all_prose_types,
    _block_surface,
    _ensure_block_span,
    _resolve_language_adapter,
    block_hash,
)
from brailix.pipeline._results import (
    CompiledBlock,
    TranslationResult,
    TreeSubcache,
)

# Note: brailix is the pure compiler — it knows nothing about front-end
# concepts like Override / WarningCase / Identity.  Callers that want
# to mutate the IR between frontend and backend (a proofreading front-end adjusting
# pinyin / splitting / merging tokens) pass an ``ir_transformer``
# callable to :meth:`Pipeline.translate_block`; the compiler runs it
# blindly without caring what semantics the caller attaches to it.

__all__ = [
    "Pipeline",
    "TranslationResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
    "_resolve_language_adapter",
    "_all_prose_types",
    "_ensure_block_span",
    "_block_surface",
    "_frontend_parse_math_tree",
    "_frontend_parse_music_tree",
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Pipeline:
    """Convenience wrapper for the default end-to-end flow.

    Configuration is **all by name**. Every adapter family is selected
    by a string that resolves through the corresponding internal
    registry; default ``"auto"`` lets the system pick the best
    installed candidate without user intervention.

    Field meaning:

    * ``profile`` — JSON profile under :mod:`brailix.profiles`.
      Drives table selection and runtime features.
    * ``mode`` — diagnostic policy (see :class:`RunMode`).
    * ``segmenter`` / ``normalizer`` — segmenter and normalizer adapter
      names.
    * ``analyzer`` — Chinese tokenizer name (``auto`` / ``char`` /
      ``jieba`` / ``hanlp``).
    * ``resolver`` — pinyin resolver name (``auto`` / ``null`` /
      ``pypinyin`` / ``g2pw``).
    * ``user_pinyin_dict`` — optional ``surface → reading`` overrides
      layered on top of ``resolver`` (a proofreading front-end's personal
      dictionary). Multi-char surfaces only; empty = no-op.
    * ``default_renderer`` — forwarded to every
      :class:`TranslationResult` so :meth:`TranslationResult.render`
      knows what to use when called without arguments.

    The only public method is :meth:`translate_text`. To plug in a new
    adapter, register it with the matching internal registry under a
    name of your choice, then construct a Pipeline with that name —
    no Pipeline code changes needed.
    """

    profile: str = DEFAULT_PROFILE
    mode: RunMode | str = RunMode.NORMAL
    segmenter: str = DEFAULT_SEGMENTER
    normalizer: str = DEFAULT_NORMALIZER
    analyzer: str = DEFAULT_ZH_ANALYZER
    resolver: str = DEFAULT_PINYIN_RESOLVER
    # Personal pinyin dictionary (user-authored surface→reading map),
    # layered on top of whichever resolver runs: the zh frontend applies
    # it as a post-pass so the user's explicit reading wins for every
    # document.  Multi-char keys only (single-char readings are too
    # context-dependent to force globally).  Empty by default → pure no-op,
    # so the bare library and every test that omits it are unaffected.
    user_pinyin_dict: dict[str, str] = field(default_factory=dict)
    default_renderer: str = DEFAULT_RENDERER
    # User-folder profile directories injected by the caller so a portable
    # build can ship with its own profile drops.
    # ``load_profile`` searches these first; same-named user profile
    # shadows the builtin.  Kept as a tuple so the dataclass stays
    # hashable / frozen-friendly even though :class:`Pipeline` itself
    # is mutable.
    extra_profile_paths: tuple[str, ...] = ()
    _profile: BrailleProfile = field(init=False, default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)
        # Accept ``Path`` objects too — keeping the dataclass field type
        # as ``tuple[str, ...]`` simplifies serialization, but the caller
        # naturally passes :class:`pathlib.Path`.
        if self.extra_profile_paths:
            self.extra_profile_paths = tuple(
                str(p) for p in self.extra_profile_paths
            )
        self._profile = load_profile(
            self.profile,
            extra_search_paths=[Path(p) for p in self.extra_profile_paths]
            or None,
        )

    @property
    def profile_name(self) -> str:
        """The resolved profile's name (``BrailleProfile.name``).

        The :attr:`profile` field holds the *requested* profile name;
        this returns the loaded profile's own ``name``, which is the
        authoritative identity to persist. Exposed so a front-end never
        has to reach into the private ``_profile``.
        """
        return self._profile.name

    @property
    def profile_language(self) -> str:
        """The resolved profile's language tag (e.g. ``"zh-CN"``).

        Exposed so a front-end can record a document's language without
        touching the private ``_profile`` — the public-API boundary.
        """
        return self._profile.language

    # --- Public API ---------------------------------------------------
    #
    # The only public surface is :meth:`translate_text` and the
    # returned :class:`TranslationResult`. Internal stages are
    # deliberately private: users compose by registering new adapter
    # names through the internal registries and pointing Pipeline
    # constructor arguments at those names.

    def translate_text(self, text: str) -> TranslationResult:
        """Translate one paragraph of text into a :class:`TranslationResult`.

        The input is wrapped as a single :class:`Paragraph` block. For
        multi-block documents (headings + lists + tables...) build a
        :class:`DocumentIR` yourself or parse Markdown via
        :func:`brailix.input.markdown.parse_markdown` and call
        :meth:`translate_document`.

        To mutate the IR between frontend and backend (e.g. a proofreading front-end
        applying user overrides), use :meth:`translate_block` and pass
        an ``ir_transformer`` — Pipeline keeps no override / workflow
        concept, that lives in the front-end layer.
        """
        warnings, ctx, backend_ctx = self._fresh_contexts()
        children = self._run_frontend(text, ctx)
        paragraph = Paragraph(
            children=children, span=Span(0, len(text)) if text else None
        )
        doc = DocumentIR(
            metadata={"language": self._profile.language, "profile": self.profile},
            blocks=[paragraph],
        )
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        return TranslationResult(
            text=text,
            ir=doc,
            braille_ir=braille_doc,
            warnings=warnings,
            default_renderer=self.default_renderer,
        )

    def translate_math_inline(self, surface: str, source: str) -> str:
        """Translate a single inline math formula to a Unicode-braille string.

        Convenience for one-off / live-preview callers (a CLI, or a proofreading
        front-end's math editor) that hold a raw formula plus its source format
        (``"latex"`` / ``"mathml"`` / ``"asciimath"`` / ...) and want braille
        without reassembling the math frontend + backend + renderer by hand —
        keeping them off ``brailix.backend`` internals.

        Inline mode (matches how an inline :class:`MathInline` renders). Parse
        / adapter failures and unsupported constructs surface as the usual
        soft-failure cells; warnings go to a throwaway collector so a preview
        never pollutes the caller's diagnostics. Returns ``""`` when the
        formula doesn't parse into a tree at all.
        """
        from brailix.backend.math import translate as _math_translate
        from brailix.renderer.unicode_braille import cell_to_char

        surface = surface.strip()
        if not surface:
            return ""
        silent = WarningCollector(mode=self.mode)
        math_ctx = MathContext(
            source=source, mode="inline", profile=self.profile, warnings=silent
        )
        tree = _frontend_parse_math_tree(surface, math_ctx)
        if tree is None:
            return ""
        node = MathInline(surface=surface, source=source, math=tree)
        backend_ctx = BackendContext(
            profile=self.profile, mode=self.mode, warnings=silent
        )
        cells = _math_translate(node, backend_ctx, self._profile)
        return "".join(cell_to_char(c) for c in cells)

    def translate_document(self, doc: DocumentIR) -> TranslationResult:
        """Translate a pre-built :class:`DocumentIR` end-to-end.

        Each block is walked: if it carries raw ``text`` and no
        ``children``, the frontend runs over its text to populate
        ``children``; if it already has ``children`` they're used as-is
        (so callers can hand-build IR for tests). Composite containers
        (List, Table) recurse into their ``items`` / ``rows`` /
        ``cells`` for the same treatment.

        Returns a :class:`TranslationResult` with the populated IR and
        the rendered :class:`BrailleDocument`. The original ``doc`` is
        **mutated in place** — children are filled where they were
        missing — so subsequent re-translations skip the frontend
        cost.
        """
        warnings, ctx, backend_ctx = self._fresh_contexts()
        for block in doc.blocks:
            self._populate_block(block, ctx)
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        # The surface for a multi-block document is the concatenation
        # of every block's text — useful for proofread output but not
        # always semantically meaningful (no separator between blocks).
        rebuilt_text = "\n".join(
            _block_surface(b) for b in doc.blocks
        )
        return TranslationResult(
            text=rebuilt_text,
            ir=doc,
            braille_ir=braille_doc,
            warnings=warnings,
            default_renderer=self.default_renderer,
        )

    def translate_file(
        self,
        path: str | os.PathLike[str],
    ) -> TranslationResult:
        """Read ``path`` as UTF-8 and translate end-to-end.

        Convenience wrapper over :func:`brailix.input.parse_file` +
        :meth:`translate_document`: ``.md`` / ``.markdown`` go through
        the Markdown adapter, everything else through the plain-text
        wrapper. The Pipeline's own ``profile`` and language are baked
        into the resulting :class:`DocumentIR`'s metadata so the
        :class:`TranslationResult` is indistinguishable from one
        produced by :meth:`translate_text` on the same source.

        IO errors propagate as-is (:class:`FileNotFoundError`,
        :class:`UnicodeDecodeError`, ``PermissionError``); pre-parse
        the file yourself and call :meth:`translate_document` if you
        need to catch them at a different layer.
        """
        doc = self.parse_file(path)
        return self.translate_document(doc)

    def parse_text(
        self,
        text: str,
        *,
        format: str = "plain",
    ) -> DocumentIR:
        """Parse ``text`` into a :class:`DocumentIR` without translating.

        ``format`` selects the adapter: ``"plain"`` (one paragraph) or
        ``"markdown"`` (the Markdown subset described under
        :func:`brailix.input.markdown.parse_markdown` — headings, lists,
        quotes, code blocks, ``$$...$$`` math, tables). The Pipeline's
        ``profile`` and ``language`` are baked into the resulting IR
        metadata so a follow-up :meth:`translate_document` matches what
        :meth:`translate_text` / :meth:`translate_file` would have
        produced on the same input.

        Use this when you need the unpopulated :class:`DocumentIR` to
        drive incremental compilation block-by-block (the incremental-compilation
        pattern a front-end uses) instead of running frontend + backend in one
        shot. Pair with :meth:`translate_block` for the per-block
        compile step.
        """
        if format == "markdown":
            return _parse_markdown(
                text,
                language=self._profile.language,
                profile=self.profile,
            )
        if format == "plain":
            return _parse_plain(
                text,
                language=self._profile.language,
                profile=self.profile,
            )
        if format == "musicxml":
            # Wrap raw MusicXML text as a single ScoreBlock so any
            # caller using parse_text can route .musicxml
            # through the same block-level compile path it uses for
            # markdown / plain. ``_populate_music_block`` will parse
            # the inner XML into a MusicInline tree at compile time.
            from brailix.ir.document import ScoreBlock

            return DocumentIR(
                metadata={
                    "language": self._profile.language,
                    "profile": self.profile,
                },
                blocks=[ScoreBlock(text=text, source="musicxml")],
            )
        raise ValueError(
            f"unknown parse format: {format!r} "
            "(expected 'plain' / 'markdown' / 'musicxml')"
        )

    def parse_file(
        self,
        path: str | os.PathLike[str],
    ) -> DocumentIR:
        """Read ``path`` as UTF-8 / bytes and parse to :class:`DocumentIR`.

        Suffix dispatch matches :func:`brailix.input.parse_file`:
        ``.md`` / ``.markdown`` → Markdown adapter, ``.docx`` /
        ``.docm`` → :func:`brailix.input.parse_docx`, ``.doc`` →
        :func:`brailix.input.parse_doc`, everything else (including
        ``.txt`` and no suffix) → plain. The Pipeline's ``profile``
        and ``language`` are baked into the IR metadata.

        Use this when you need the unpopulated :class:`DocumentIR`
        from a file for incremental compilation (the incremental-compilation
        pattern a front-end uses). For the end-to-end one-shot, call
        :meth:`translate_file`.
        """
        return _parse_file(
            path,
            language=self._profile.language,
            profile=self.profile,
        )

    def translate_block(
        self,
        block: Block,
        *,
        ir_transformer: Callable[[DocumentIR], None] | None = None,
        tree_subcache: TreeSubcache | None = None,
    ) -> CompiledBlock:
        """Translate a single :class:`Block` end-to-end (frontend +
        backend) and return a :class:`CompiledBlock`.

        The **incremental compilation primitive** a front-end uses:
        re-compile one paragraph / heading /
        list-item without touching the rest of the document. The
        returned :class:`CompiledBlock` carries the populated IR,
        braille output, warnings, and a stable ``source_hash`` for
        cache keying.

        Block-level translation is sound because braille state
        (number_sign, capital indicator, math state machine) **does
        not leak** across block boundaries — see ARCHITECTURE.md §12.

        ``ir_transformer`` is an optional in-place mutation hook that
        runs between frontend and backend.  The compiler doesn't care
        what semantics the caller attaches to it: a proofreading
        front-end wraps its override-application pass here; a different
        front-end could plug in glossary rewrites or auto-fix passes.
        The transformer receives a singleton :class:`DocumentIR`
        wrapping ``block`` so it can use absolute ``block_path`` like
        ``(0, child_idx, ...)``.

        ``tree_subcache`` is an optional parsed-tree cache shared by the
        math and music frontends: keys are ``(domain, source, surface)``
        (``domain`` ∈ ``{"math", "music"}``), values are the normalised
        MathML / MusicXML :class:`ET.Element` trees from a previous
        compile.  When the frontend encounters a math / music node whose
        key matches an entry, it reuses the cached tree instead of
        re-running the adapter — typically the caller passes the prior
        :class:`CompiledBlock`\\ 's ``tree_subcache`` so an edit that
        leaves a formula / score unchanged (e.g. an override) doesn't
        trigger a re-parse (block-level cache covers the whole-block
        case; this covers the "block changed but the embedded tree
        didn't" case — decisive for large scores, which are one block
        whose 4MB tree would otherwise re-parse on every override edit).
        The returned :class:`CompiledBlock.tree_subcache` always reflects
        what was actually parsed during this compile (a superset / equal
        subset of the input, never empty when math or music exists).

        Pipeline is **stateless** with respect to caching — every
        call re-runs frontend + backend.  The caller is responsible
        for consulting its own block cache via ``source_hash`` before
        calling this method.  The hash covers ``(block surface,
        profile)`` only; callers that want override-aware cache keys
        should compose ``source_hash`` with their own override-list
        salt at the caller layer.
        """
        warnings, _ctx, _backend_ctx = self._fresh_contexts()

        # Frontend: populate inline children if needed.
        ctx = FrontendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options=self._frontend_options(),
        )
        # Parsed-tree sub-cache is threaded through the populate path as
        # a mutable pair: ``tree_in`` is read-only (caller-provided
        # reuse pool), ``tree_out`` accumulates trees from this
        # compile.  Kept out of :class:`FrontendContext` to avoid
        # polluting the public adapter-facing surface with front-end-
        # specific state.
        tree_in = tree_subcache or {}
        tree_out: TreeSubcache = {}
        self._populate_block(block, ctx, tree_in=tree_in, tree_out=tree_out)

        # Run the optional caller-supplied IR transformer.  We wrap
        # the block in a singleton doc so the transformer can index
        # children with absolute ``block_path = (0, ...)`` (same
        # convention a front-end's override-application pass uses).
        if ir_transformer is not None:
            singleton = DocumentIR(blocks=[block])
            ir_transformer(singleton)

        # Backend: expand into one or more BrailleBlocks (composites
        # like List / Table expand to N elements; simple blocks to 1).
        backend_ctx = BackendContext(
            profile=self.profile,
            mode=self.mode,
            block_type=block.type,
            warnings=warnings,
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        braille_blocks = expand_block(block, backend_ctx, self._profile)

        # Stable cache key: textual surface + profile.  Callers who
        # need override-aware cache keys (a proofreading front-end) compose this
        # hash with their own override-list salt outside the
        # compiler.
        source_hash = block_hash(block, self.profile)

        return CompiledBlock(
            block_id=block.id or "",
            source_hash=source_hash,
            ir=block,
            braille_blocks=braille_blocks,
            warnings=list(warnings.warnings),
            tree_subcache=tree_out,
        )

    # --- Internal: shared per-translate setup -----------------------

    def _fresh_contexts(self) -> tuple[WarningCollector, FrontendContext, BackendContext]:
        warnings = WarningCollector(mode=self.mode)
        ctx = FrontendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options=self._frontend_options(),
        )
        backend_ctx = BackendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        return warnings, ctx, backend_ctx

    def _populate_block(
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

        ``tree_in`` / ``tree_out`` are the parsed-tree reuse / record
        pools — see :meth:`translate_block`.  Threaded as keyword
        arguments rather than baked into :class:`FrontendContext` so
        the public adapter-facing surface stays free of front-end caching
        concerns; when both are ``None`` math / music parses run as before.
        """
        # Import lazily to avoid circular dependency at module load.
        from brailix.ir.document import (
            CodeBlock,
            MathBlock,
            MusicBlock,
            ScoreBlock,
            Table,
        )
        from brailix.ir.document import (
            List as ListBlock,
        )

        if isinstance(block, ListBlock):
            for item in block.items:
                self._populate_block(item, ctx, tree_in=tree_in, tree_out=tree_out)
            return
        if isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    self._populate_block(
                        cell, ctx, tree_in=tree_in, tree_out=tree_out
                    )
            return
        if isinstance(block, MathBlock):
            if block.text and not block.children:
                self._populate_math_block(
                    block, ctx, tree_in=tree_in, tree_out=tree_out
                )
            elif block.span is None and block.text:
                block.span = Span(0, len(block.text))
            return
        if isinstance(block, (ScoreBlock, MusicBlock)):
            if block.text and not block.children:
                self._populate_music_block(
                    block, ctx, tree_in=tree_in, tree_out=tree_out
                )
            elif block.span is None and block.text:
                block.span = Span(0, len(block.text))
            return
        if isinstance(block, CodeBlock):
            if block.text and not block.children:
                if block.span is None:
                    block.span = Span(0, len(block.text))
                block.children = [
                    CodeInline(surface=block.text, span=block.span)
                ]
            elif block.span is None and block.text:
                block.span = Span(0, len(block.text))
            return
        text = getattr(block, "text", None)
        if text and not block.children:
            block.children = self._run_frontend(
                text, ctx, tree_in=tree_in, tree_out=tree_out
            )
            if block.span is None:
                block.span = Span(0, len(text))

    def _populate_music_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Parse a :class:`ScoreBlock` / :class:`MusicBlock`'s raw
        ``text`` via the music frontend and populate ``children`` with
        a single :class:`MusicInline` carrying the MusicXML tree.

        Mirrors :meth:`_populate_math_block` for the music subsystem
        (see ``ARCHITECTURE.md``): the block holds only
        ``source``; the parsed tree lives on a child ``MusicInline``,
        so the backend dispatcher can route it like any other inline
        node.

        Soft-failure: if the adapter is missing the frontend returns
        ``None`` (a ``MUSIC_ADAPTER_MISSING`` warning is already
        recorded by then). Adapter parse errors land in a
        ``<music-error>`` tree that backend handlers will surface as
        ``MUSIC_PARSE_RECOVERY``. Either way ``block.children`` ends
        up populated and the pipeline keeps running.

        ``tree_in`` / ``tree_out`` are the shared parsed-tree reuse /
        record pools (see :meth:`translate_block`): on a key hit the
        whole MusicXML parse + normalise is skipped — the decisive win
        for proofreading, where the score source never changes between override
        edits.
        """
        text, span, _had_span = _ensure_block_span(block)

        cache_key = ("music", block.source, text)
        cached_tree = tree_in.get(cache_key) if tree_in is not None else None
        if cached_tree is not None:
            tree: ET.Element | None = cached_tree
        else:
            music_ctx = MusicContext(
                source=block.source,
                mode="score",
                profile=self.profile,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            )
            try:
                tree = _frontend_parse_music_tree(text, music_ctx)
            except Exception as exc:  # noqa: BLE001 — adapter failures are wide
                ctx.warnings.warn(
                    code="MUSIC_BLOCK_PARSE_FAILED",
                    message=f"music block parse failed: {exc!r}",
                    surface=text,
                    span=span,
                    source="pipeline",
                )
                tree = None

        if tree is not None and tree_out is not None:
            tree_out[cache_key] = tree

        block.children = [
            MusicInline(
                surface=text,
                span=span,
                source=block.source,
                score=tree,
            )
        ]

    def _populate_math_block(
        self,
        block: Any,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        """Parse a :class:`MathBlock`'s raw ``text`` via the math
        frontend and populate ``block.children``.

        On adapter exceptions (deliberately wide ``except`` — adapter
        failure modes vary): record a ``MATH_BLOCK_PARSE_FAILED``
        warning and fall back to one :class:`Unknown` per source
        character so layout still occupies real estate. The per-char
        :class:`Unknown` will trigger ``UNKNOWN_NODE`` warnings via
        the dispatcher when backend renders them — that's expected
        and slightly more precise than the legacy single-warning
        behavior (each char is genuinely an unknown to the backend).

        Lazy import of ``parse_math_tree`` so monkeypatching
        ``brailix.frontend.parse_math_tree`` in tests is observed at
        each call (necessary for fault injection).
        """
        from brailix.frontend import parse_math_tree

        # Remember whether the caller-supplied block had a span. The
        # per-char Unknown fallback below matches the legacy behavior
        # in backend.block._unknown_cells_for: if the source block has
        # no span, the fallback cells also have no span — the caller
        # then knows it can't anchor them.
        text, span, had_original_span = _ensure_block_span(block)

        cache_key = ("math", block.source, text)
        cached_tree: ET.Element | None = (
            tree_in.get(cache_key) if tree_in is not None else None
        )
        if cached_tree is not None:
            tree: ET.Element | None = cached_tree
        else:
            math_ctx = MathContext(
                source=block.source,
                mode="display",
                profile=self.profile,
                warnings=ctx.warnings,
                options=dict(ctx.options),
            )
            try:
                tree = parse_math_tree(text, math_ctx)
            except Exception as exc:  # noqa: BLE001 — adapter errors are wide
                ctx.warnings.warn(
                    code="MATH_BLOCK_PARSE_FAILED",
                    message=f"math block parse failed: {exc!r}",
                    surface=text,
                    span=span,
                    source="pipeline",
                )
                base = span.start
                block.children = [
                    Unknown(
                        surface=ch,
                        span=Span(base + i, base + i + 1)
                        if had_original_span
                        else None,
                    )
                    for i, ch in enumerate(text)
                ]
                return

        if tree is not None and tree_out is not None:
            tree_out[cache_key] = tree

        block.children = [
            MathInline(
                surface=text,
                span=span,
                source=block.source,
                math=tree,
            )
        ]

    # --- Frontend orchestration --------------------------------------
    #
    # All frontend stages live in :mod:`brailix.frontend`. Pipeline
    # only orchestrates: segment → normalize → per-segment routing →
    # math attachment. The routing is language-agnostic — segmenter,
    # normalizer and the prose frontend are each selected by the active
    # profile's language (see :meth:`_frontend_options` /
    # :meth:`_process_segment`), so adding a language is registration,
    # not a change here. See ARCHITECTURE §7.6.

    def _frontend_options(self) -> dict[str, Any]:
        lang = self._profile.language.split("-")[0]
        return {
            "segmenter": _resolve_language_adapter(
                segmenter_registry, self.segmenter, DEFAULT_SEGMENTER, lang
            ),
            "normalizer": _resolve_language_adapter(
                normalizer_registry, self.normalizer, DEFAULT_NORMALIZER, lang
            ),
            "zh_analyzer": self.analyzer,
            "pinyin_resolver": self.resolver,
            "user_pinyin_dict": self.user_pinyin_dict,
        }

    def _run_frontend(
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
                self._attach_math(item, ctx, tree_in=tree_in, tree_out=tree_out)
                out.append(item)
            else:
                out.append(item)
        return _frontend_zh_boundary_spaces(out, self._profile.zh_compounds)

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
        elif segment.type in _all_prose_types():
            # A prose segment, but the active language has no frontend.
            ctx.warnings.warn(
                code="NO_LANGUAGE_FRONTEND",
                message=f"no frontend registered for language {lang!r}",
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

    def _attach_math(
        self,
        node: MathInline,
        ctx: FrontendContext,
        *,
        tree_in: TreeSubcache | None = None,
        tree_out: TreeSubcache | None = None,
    ) -> None:
        if node.math is not None:
            # Already parsed (frontend ran twice, or caller pre-populated).
            # Still record in tree_out so the caller's per-block cache
            # snapshot is complete — otherwise a re-parse that hits this
            # short-circuit path would silently drop the formula from
            # the next compile's reuse pool.
            if tree_out is not None and node.math is not None:
                tree_out[("math", node.source, node.surface)] = node.math
            return
        cache_key = ("math", node.source, node.surface)
        if tree_in is not None:
            cached = tree_in.get(cache_key)
            if cached is not None:
                node.math = cached
                if tree_out is not None:
                    tree_out[cache_key] = cached
                return
        math_ctx = MathContext(
            source=node.source,
            mode="inline",
            profile=self.profile,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        )
        tree = _frontend_parse_math_tree(node.surface, math_ctx)
        node.math = tree
        if tree is not None and tree_out is not None:
            tree_out[cache_key] = tree

    def _translate_inline_text(self, text: str) -> list[BrailleCell]:
        """Translate a run of text to braille cells via the zh / latin
        text path — injected on ``BackendContext.options`` so the music
        backend can render ``<words>`` directions (expression text,
        teaching notes) instead of deferring them to a warning.

        Runs a throwaway frontend + backend over a one-paragraph doc.
        The inner :class:`BackendContext` deliberately omits the
        translator, so a (text-only) run can't recurse back into music;
        its warnings go to a private collector — ``<words>`` rendering is
        best-effort, a stray untranslatable char shouldn't spam the
        score's report.
        """
        if not text.strip():
            return []
        warnings = WarningCollector(mode=self.mode)
        ctx = FrontendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options=self._frontend_options(),
        )
        children = self._run_frontend(text, ctx)
        paragraph = Paragraph(children=children, span=Span(0, len(text)))
        doc = DocumentIR(blocks=[paragraph])
        backend_ctx = BackendContext(
            profile=self.profile, mode=self.mode, warnings=warnings
        )
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        return braille_doc.all_cells()
