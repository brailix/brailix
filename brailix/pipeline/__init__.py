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
  :func:`_ensure_block_span`, :func:`_block_surface`, :func:`block_hash`,
  :func:`cache_lookup`, :func:`cache_record`.
* :mod:`brailix.pipeline.frontend_driver` — the :class:`FrontendDriver`
  collaborator (segment → normalize → per-segment routing → inline-math
  attach → block populate). Its math / music / graphic tree parsers are
  injected there, so a test simulates an adapter failure by replacing
  ``pipeline._frontend._parse_math_tree`` (etc.) on the instance rather
  than monkeypatching a ``brailix.pipeline.*`` name.

The cohesive :class:`Pipeline` orchestrator stays here. The module still
re-exports :data:`_frontend_parse_math_tree` /
:data:`_frontend_parse_music_tree` / :data:`_frontend_parse_graphic_tree`
(the real frontend entry points, used by :meth:`Pipeline.translate_math_inline`
and :func:`translate_graphic`) for backward compatibility.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from brailix.backend.block import expand_block, translate_document
from brailix.core.config import BrailleProfile, load_profile
from brailix.core.context import (
    GRAPHIC_ASSET_RESOLVER_KEY,
    INLINE_TEXT_TRANSLATOR_KEY,
    BackendContext,
    FrontendContext,
    GraphicsContext,
    MathContext,
)
from brailix.core.defaults import (
    DEFAULT_NORMALIZER,
    DEFAULT_PINYIN_RESOLVER,
    DEFAULT_RENDERER,
    DEFAULT_SEGMENTER,
    DEFAULT_ZH_ANALYZER,
)
from brailix.core.errors import (
    RunMode,
    WarningCollector,
    normalize_run_mode,
)
from brailix.core.span import Span
from brailix.frontend import parse_math_tree as _frontend_parse_math_tree
from brailix.frontend.graphics import (
    parse_graphic_tree as _frontend_parse_graphic_tree,
)
from brailix.frontend.music import parse_music_tree as _frontend_parse_music_tree
from brailix.input import DEFAULT_INPUT_LIMITS, InputLimits
from brailix.input import parse_file as _parse_file
from brailix.input import parse_markdown as _parse_markdown
from brailix.input import parse_plain as _parse_plain
from brailix.ir.braille import BrailleCell
from brailix.ir.document import Block, DocumentIR, Paragraph
from brailix.ir.inline import (
    GraphicInline,
    MathInline,
)
from brailix.ir.tactile import TactileRaster
from brailix.pipeline._helpers import (
    _all_prose_types,
    _block_surface,
    _ensure_block_span,
    _resolve_language_adapter,
    block_hash,
)
from brailix.pipeline._results import (
    CompiledBlock,
    GraphicResult,
    TactilePageResult,
    TranslationResult,
    TreeSubcache,
)
from brailix.pipeline.frontend_driver import FrontendDriver

if TYPE_CHECKING:
    from brailix.core.protocols import GraphicAssetResolver

# Note: brailix is the pure compiler — it knows nothing about front-end
# concepts like Override / WarningCase / Identity.  Callers that want
# to mutate the IR between frontend and backend (a proofreading front-end adjusting
# pinyin / splitting / merging tokens) pass an ``ir_transformer``
# callable to :meth:`Pipeline.translate_block`; the compiler runs it
# blindly without caring what semantics the caller attaches to it.

__all__ = [
    "Pipeline",
    "FrontendDriver",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
    "_resolve_language_adapter",
    "_all_prose_types",
    "_ensure_block_span",
    "_block_surface",
    "_frontend_parse_math_tree",
    "_frontend_parse_music_tree",
    "_frontend_parse_graphic_tree",
]


# ---------------------------------------------------------------------------
# Inline tactile-graphics default
# ---------------------------------------------------------------------------

# Tactile profile used when an inline figure block (a GraphicBlock embedded in
# a braille document) is rasterised through the main pipeline
# (ARCHITECTURE.md G1).  ``"generic"`` matches the default a
# standalone ``translate_graphic`` call uses; a document-level / per-block
# tactile profile is a later refinement (G3/G4).
_DEFAULT_INLINE_TACTILE_PROFILE = "generic"


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

    * ``profile`` — **required**; JSON profile name under
      :mod:`brailix.profiles` (the braille standard, e.g. ``cn_current``
      / ``cn_ncb`` / ``ja_current``). Drives table selection and runtime
      features. There is no built-in default — the caller always chooses.
    * ``mode`` — diagnostic policy (see :class:`RunMode`).
    * ``segmenter`` / ``normalizer`` — segmenter and normalizer adapter
      names.
    * ``analyzer`` — Chinese tokenizer name (``auto`` / ``char`` /
      ``thulac`` / ``jieba`` / ``hanlp``).
    * ``resolver`` — pinyin resolver name (``auto`` / ``null`` /
      ``pypinyin`` / ``g2pm`` / ``g2pw``).
    * ``user_pinyin_dict`` — optional ``surface → reading`` overrides
      layered on top of ``resolver`` (a proofreading front-end's personal
      dictionary). Multi-char surfaces only; empty = no-op.
    * ``default_renderer`` — forwarded to every
      :class:`TranslationResult` so :meth:`TranslationResult.render`
      knows what to use when called without arguments.

    :meth:`translate_text` is the simplest entry point; the rest of the
    public surface is :meth:`translate_document` / :meth:`translate_file`
    / :meth:`translate_block` / :meth:`translate_math_inline` /
    :meth:`translate_graphic` / :meth:`parse_text` / :meth:`parse_file`.
    To plug in a new adapter, register it with the matching internal
    registry under a name of your choice, then construct a Pipeline with
    that name — no Pipeline code changes needed.
    """

    profile: str
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
    # Resolves a graphic's asset reference (``media/image1.png``) to raw
    # bytes when the referenced image lives in the document rather than on
    # disk — an image imported from a ``.docx`` rides in memory. Injected onto
    # every ``GraphicsContext`` the pipeline builds (inline ``graphic-image``
    # fences and standalone ``translate_graphic`` alike), so the ``image``
    # source adapter can inline it as a ``data:`` URI. ``None`` (the default)
    # leaves the adapter to read the reference as a filesystem path — the bare
    # library and every test that omits it are unaffected. See
    # :class:`~brailix.core.protocols.GraphicAssetResolver` and §2.2 of
    # ``ARCHITECTURE.md``.
    asset_resolver: GraphicAssetResolver | None = None
    _profile: BrailleProfile = field(init=False, default=None)  # type: ignore[assignment]
    _frontend: FrontendDriver = field(init=False, default=None)  # type: ignore[assignment]

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
        self._frontend = FrontendDriver(
            profile=self.profile,
            profile_obj=self._profile,
            segmenter=self.segmenter,
            normalizer=self.normalizer,
            analyzer=self.analyzer,
            resolver=self.resolver,
            user_pinyin_dict=self.user_pinyin_dict,
            asset_resolver=self.asset_resolver,
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
    # The public surface is the ``translate_*`` / ``parse_*`` methods and
    # the returned :class:`TranslationResult`. Internal stages are
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
        children = self._frontend.run_frontend(text, ctx)
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
        # NORMAL regardless of the pipeline's own mode: a strict-mode
        # collector RAISES on the first warning, which would turn the
        # documented "failures surface as soft-failure cells" preview
        # contract into a StrictModeError crash for any formula with an
        # unknown symbol.
        silent = WarningCollector(mode=RunMode.NORMAL)
        math_ctx = MathContext(
            source=source, mode="inline", profile=self.profile, warnings=silent
        )
        tree = _frontend_parse_math_tree(surface, math_ctx)
        if tree is None:
            return ""
        node = MathInline(surface=surface, source=source, math=tree)
        backend_ctx = BackendContext(
            profile=self.profile,
            # NORMAL, not self.mode — the context's __post_init__
            # re-stamps its mode onto the shared collector, which would
            # silently undo the NORMAL collector above.
            mode=RunMode.NORMAL,
            warnings=silent,
            # Inject the inline-text translator so embedded text — a
            # \text{...} / <mtext> run, esp. Chinese — renders through the
            # zh / latin path instead of failing per-char. Without it a
            # live preview drops to blank cells + warnings for any text.
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        cells = _math_translate(node, backend_ctx, self._profile)
        return "".join(cell_to_char(c) for c in cells)

    def translate_graphic(
        self,
        source: str | bytes,
        *,
        source_format: str = "svg",
        tactile_profile: str | Any = "generic",
        braille_profile: str | None = None,
        label_translator: Callable[[str], list[BrailleCell]] | None = None,
        record_provenance: bool = False,
        warnings: WarningCollector | None = None,
    ) -> GraphicResult:
        """Compile a tactile graphic into a :class:`GraphicResult`.

        Convenience delegation to the module-level
        :func:`translate_graphic` — a graphic's own compile needs no
        braille standard (its product is a raster, not cells), so the
        real entry is Pipeline-free. Going through a Pipeline buys one
        thing: when ``braille_profile`` matches this pipeline's own
        profile, ``<text>`` labels translate through **this** pipeline's
        text path instead of spinning up a second one.

        See :func:`translate_graphic` for the parameter contract.
        """
        translator = label_translator
        if translator is None and braille_profile is not None:
            translator = self._graphic_label_translator(braille_profile)
        return translate_graphic(
            source,
            source_format=source_format,
            tactile_profile=tactile_profile,
            label_translator=translator,
            record_provenance=record_provenance,
            warnings=(
                warnings
                if warnings is not None
                else WarningCollector(mode=self.mode)
            ),
            mode=self.mode,
            # An image graphic's reference resolves against this pipeline's
            # document assets (a figure edited in isolation still shows its
            # embedded picture in the live preview).
            asset_resolver=self.asset_resolver,
        )

    def _rasterize_graphic(
        self,
        block: Any,
        warns: WarningCollector,
        *,
        tactile_profile: str | Any,
        label_translator: Callable[[str], list[BrailleCell]] | None,
        record_provenance: bool = False,
    ) -> tuple[TactileRaster, ET.Element]:
        """Rasterise an already-populated :class:`GraphicBlock` into a
        :class:`~brailix.ir.tactile.TactileRaster`.

        The shared tail of :meth:`translate_graphic` (the standalone tactile
        entry) and :meth:`translate_block` (the inline-in-a-braille-document
        path, ARCHITECTURE.md G1) — one rasteriser, not two.
        Pulls the SVG tree off the block's :class:`GraphicInline` child
        (:meth:`_populate_graphic_block` always lands one — an error-marked
        SVG on soft-failure, never ``None`` — so a figure always rasterises to
        *something*), loads the tactile profile, and rasterises.  Returns
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

    def _graphic_label_translator(
        self, braille_profile: str
    ) -> Callable[[str], list[BrailleCell]]:
        """A ``text → braille cells`` translator for graphic labels, backed by
        the requested braille standard.

        Reuses this pipeline's own text path when ``braille_profile`` matches
        :attr:`profile` (no second Pipeline); otherwise spins a sub-pipeline on
        that standard. Either way it routes through ``_translate_inline_text``,
        whose throwaway NORMAL collector keeps a label's per-char warnings out
        of the graphic's own report.

        The sub-pipeline is derived with :func:`dataclasses.replace` so it
        **inherits every configured adapter** — segmenter, normalizer,
        analyzer, resolver, the user's pinyin dictionary, extra profile
        search paths, asset resolver — and only the braille standard differs.
        Building a bare ``Pipeline(profile=braille_profile)`` here instead
        would silently drop that config, so a label in a non-document braille
        standard would tokenize / read differently from the surrounding body
        (a user pinyin-dictionary entry would go missing, a custom profile on
        an ``extra_profile_paths`` drop would fail to load) — the
        "Profile-driven, adapter-replaceable" contract requires the whole
        parent configuration to ride along, not just the default path."""
        if braille_profile == self.profile:
            return self._translate_inline_text
        return replace(self, profile=braille_profile)._translate_inline_text

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
        # Stamp the pipeline's identity onto the (possibly hand-built) doc
        # so the result is self-describing the same way translate_text /
        # parse_* leave it.  The backend reads ``self._profile`` directly,
        # so this is for the IR metadata's consumers, not the translation;
        # other caller metadata keys are preserved.
        doc.metadata["language"] = self._profile.language
        doc.metadata["profile"] = self.profile
        for block in doc.blocks:
            self._frontend.populate_block(block, ctx)
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

    def translate_document_to_pages(
        self,
        doc: DocumentIR,
        *,
        tactile_profile: str | Any = _DEFAULT_INLINE_TACTILE_PROFILE,
        margin_mm: float | None = None,
        item_gap_mm: float | None = None,
    ) -> TactilePageResult:
        """Lay a braille document with embedded figures onto tactile pages.

        The mixed-layout output (``ARCHITECTURE.md`` G3): each
        block is compiled through the **same** incremental pipeline every other
        path uses (:meth:`translate_block`) — a text block yields braille
        cells, a :class:`~brailix.ir.document.GraphicBlock` yields a
        :class:`~brailix.ir.tactile.TactileRaster` (G1). Text cells are wrapped
        to the page's cell width by the layout renderer, then the tactile
        backend's page compositor stamps them as **real braille dots** and
        blits the figures into the flow, paginating onto one raster per page
        (output model A — the page *is* a raster; there is no BRF for a mixed
        page). The result exports through the existing tactile renderers.

        ``tactile_profile`` names a tactile profile (page size + DPI + braille
        dot geometry + interline pitch) or is an already-loaded
        :class:`~brailix.backend.tactile.profile.TactileProfile`; it drives the
        page geometry. Figures are rasterised at the G1 default (``"generic"``)
        and placed at their true millimetre size, so they land correctly
        whatever page profile is chosen — a document-/block-level figure
        profile is a later refinement (plan §3). ``margin_mm`` and
        ``item_gap_mm`` default to one cell advance and one interline pitch.

        Braille state does not leak across blocks (ARCHITECTURE §12), so
        compiling block-by-block is sound. The document is **mutated in place**
        (children filled where missing), like :meth:`translate_document`.
        """
        from brailix.backend.tactile.page import (
            PageFigure,
            PageItem,
            PageText,
            compose_pages,
            line_width_cells,
        )
        from brailix.backend.tactile.profile import load_tactile_profile
        from brailix.ir.document import GraphicBlock
        from brailix.renderer.layout import LayoutOptions, LayoutRenderer

        tprof = (
            load_tactile_profile(tactile_profile)
            if isinstance(tactile_profile, str)
            else tactile_profile
        )
        # Wrap width = the one shared cells-per-line rule (``line_width_cells``);
        # the compositor stamps at the same cell advance, so the wrap width and
        # the stamp geometry agree.
        layout = LayoutRenderer(
            options=LayoutOptions(
                line_width=line_width_cells(tprof, margin_mm=margin_mm),
                page_height=None,
            )
        )

        warnings = WarningCollector(mode=self.mode)
        items: list[PageItem] = []
        for block in doc.blocks:
            compiled = self.translate_block(block)
            # Aggregate each block's diagnostics without re-running the
            # collector's mode logic (they are already final): append directly.
            warnings.warnings.extend(compiled.warnings)
            if isinstance(block, GraphicBlock):
                if compiled.raster is not None:
                    items.append(PageFigure(raster=compiled.raster))
                continue
            for bblock in compiled.braille_blocks:
                lines = layout.lay_out_block(bblock)
                if lines:
                    items.append(PageText(lines=lines))

        pages = compose_pages(
            items,
            tprof,
            margin_mm=margin_mm,
            item_gap_mm=item_gap_mm,
            warnings=warnings,
        )
        return TactilePageResult(pages=pages, warnings=warnings)

    def translate_file(
        self,
        path: str | os.PathLike[str],
        *,
        limits: InputLimits = DEFAULT_INPUT_LIMITS,
    ) -> TranslationResult:
        """Read ``path`` and translate end-to-end.

        Convenience wrapper over :func:`brailix.input.parse_file` +
        :meth:`translate_document`. The input is dispatched by suffix
        (Markdown, Word ``.docx`` / ``.doc``, MusicXML / ``.mxl`` /
        score MIDI / ABC, else plain text — see :meth:`parse_file` and
        :func:`brailix.input.parse_file` for the full table). The
        Pipeline's own ``profile`` and language are baked into the
        resulting :class:`DocumentIR`'s metadata so the
        :class:`TranslationResult` is indistinguishable from one
        produced by :meth:`translate_text` on the same source.

        ``limits`` bounds the input file size (see :meth:`parse_file`).

        IO errors propagate as-is (:class:`FileNotFoundError`,
        :class:`~brailix.input.InputTooLargeError`,
        :class:`UnicodeDecodeError`, ``PermissionError``); pre-parse
        the file yourself and call :meth:`translate_document` if you
        need to catch them at a different layer.
        """
        doc = self.parse_file(path, limits=limits)
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
        *,
        limits: InputLimits = DEFAULT_INPUT_LIMITS,
    ) -> DocumentIR:
        """Read ``path`` as UTF-8 / bytes and parse to :class:`DocumentIR`.

        Suffix dispatch matches :func:`brailix.input.parse_file`:
        ``.md`` / ``.markdown`` → Markdown adapter; ``.docx`` /
        ``.docm`` → :func:`brailix.input.parse_docx`; ``.doc`` →
        :func:`brailix.input.parse_doc`; ``.musicxml`` / ``.mxl`` (and
        a ``.xml`` that sniffs as a score) → :func:`brailix.input.parse_musicxml`;
        ``.mid`` / ``.midi`` → :func:`brailix.input.parse_score_file`;
        ``.abc`` → :func:`brailix.input.parse_deferred_score`;
        everything else (including ``.txt`` and no suffix) → plain. The
        Pipeline's ``profile`` and ``language`` are baked into the IR
        metadata.

        ``limits`` bounds the input file size (see
        :class:`brailix.input.InputLimits`): the default is generous, a
        service handling untrusted uploads tightens it, and
        :meth:`~brailix.input.InputLimits.unlimited` opts out.

        Use this when you need the unpopulated :class:`DocumentIR`
        from a file for incremental compilation (the incremental-compilation
        pattern a front-end uses). For the end-to-end one-shot, call
        :meth:`translate_file`.
        """
        return _parse_file(
            path,
            language=self._profile.language,
            profile=self.profile,
            mathtype_fallback=self._profile.feature(
                "input.docx.mathtype_fallback", "off"
            ),
            chem_detection=self._profile.feature(
                "input.docx.detect_chemistry", False
            ),
            limits=limits,
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
        math, music and graphic frontends: keys are ``(domain, source,
        surface)`` (``domain`` ∈ ``{"math", "music", "graphic"}``), values are
        the normalised
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

        Pipeline keeps **no cache of its own** — the caller consults its
        own block cache via ``source_hash`` before calling this method. The
        hash covers ``(block surface, profile)`` only; callers that want
        override-aware cache keys should compose ``source_hash`` with their
        own override-list salt at the caller layer.

        Pass a **fresh, unpopulated** block each call. The backend always
        re-runs, but the frontend does not: a block that already has
        ``children`` is treated as already frontend-processed, and
        :meth:`FrontendDriver.populate_block` short-circuits over it (the
        parsed math / music tree can't be rebuilt from flattened children).
        So mutating ``block.text`` on a block whose ``children`` are already
        filled and re-compiling reuses the STALE children — and ``source_hash``,
        keyed on the reconstructed child surface, may not change either. An
        editing front-end avoids this by re-parsing the source into fresh
        blocks on every compile; a caller that holds IR blocks across edits
        must rebuild (or clear ``children`` on) any block whose text changed.
        """
        # One fresh collector + matching contexts for this block.  The
        # backend context is stamped with this block's type up front — the
        # only difference from the translate_text / translate_document
        # setup — so expand_block sees the right block_type without a rebuild.
        warnings, ctx, backend_ctx = self._fresh_contexts(block_type=block.type)

        # Parsed-tree sub-cache is threaded through the populate path as
        # a mutable pair: ``tree_in`` is read-only (caller-provided
        # reuse pool), ``tree_out`` accumulates trees from this
        # compile.  Kept out of :class:`FrontendContext` to avoid
        # polluting the public adapter-facing surface with front-end-
        # specific state.
        tree_in = tree_subcache or {}
        tree_out: TreeSubcache = {}
        self._frontend.populate_block(block, ctx, tree_in=tree_in, tree_out=tree_out)

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
        braille_blocks = expand_block(block, backend_ctx, self._profile)

        # Tactile-graphics inline embedding (ARCHITECTURE.md G1):
        # a figure block rasterises to a TactileRaster through THIS same
        # incremental pipeline — no separate ``translate_graphic`` call — so a
        # braille document holding figures compiles down one path.  Labels
        # translate through this pipeline's own text path, so a figure's labels
        # come out in the document's braille standard automatically.
        from brailix.ir.document import GraphicBlock

        raster: TactileRaster | None = None
        if isinstance(block, GraphicBlock):
            raster, _tree = self._rasterize_graphic(
                block,
                warnings,
                tactile_profile=_DEFAULT_INLINE_TACTILE_PROFILE,
                label_translator=self._translate_inline_text,
            )

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
            raster=raster,
        )

    # --- Internal: shared per-translate setup -----------------------

    def _fresh_contexts(
        self, *, block_type: str = "paragraph"
    ) -> tuple[WarningCollector, FrontendContext, BackendContext]:
        warnings = WarningCollector(mode=self.mode)
        ctx = FrontendContext(
            profile=self.profile,
            mode=self.mode,
            warnings=warnings,
            options=self._frontend.frontend_options(),
        )
        backend_ctx = BackendContext(
            profile=self.profile,
            mode=self.mode,
            block_type=block_type,
            warnings=warnings,
            options={INLINE_TEXT_TRANSLATOR_KEY: self._translate_inline_text},
        )
        return warnings, ctx, backend_ctx

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
        # NORMAL regardless of the pipeline's own mode: the docstring
        # promises this private collector never pollutes the caller's
        # diagnostics, but a strict-mode collector raises on the first
        # warning — a stray untranslatable char inside a <words> /
        # \text{...} run would abort the whole score's translation from
        # deep inside a backend handler.
        warnings = WarningCollector(mode=RunMode.NORMAL)
        # NORMAL on the contexts too, not just the collector — their
        # __post_init__ re-stamps the context mode onto the shared
        # collector, which would silently undo the line above.
        ctx = FrontendContext(
            profile=self.profile,
            mode=RunMode.NORMAL,
            warnings=warnings,
            options=self._frontend.frontend_options(),
        )
        children = self._frontend.run_frontend(text, ctx)
        paragraph = Paragraph(children=children, span=Span(0, len(text)))
        doc = DocumentIR(blocks=[paragraph])
        backend_ctx = BackendContext(
            profile=self.profile, mode=RunMode.NORMAL, warnings=warnings
        )
        braille_doc = translate_document(doc, backend_ctx, self._profile)
        return braille_doc.all_cells()


# ---------------------------------------------------------------------------
# Module-level tactile-graphics entry
# ---------------------------------------------------------------------------


def translate_graphic(
    source: str | bytes,
    *,
    source_format: str = "svg",
    tactile_profile: str | Any = "generic",
    braille_profile: str | None = None,
    label_translator: Callable[[str], list[BrailleCell]] | None = None,
    record_provenance: bool = False,
    warnings: WarningCollector | None = None,
    mode: RunMode | str = RunMode.NORMAL,
    asset_resolver: GraphicAssetResolver | None = None,
) -> GraphicResult:
    """Compile a tactile graphic into a :class:`GraphicResult`.

    The tactile vertical's standalone entry: the **graphics frontend**
    (:data:`_frontend_parse_graphic_tree`, the same single-callable shape as
    math / music) normalises the source (``source_format`` ∈ ``svg`` /
    ``primitives`` / ``figure`` / ``image``) into the SVG-tree IR, and the
    **tactile backend** rasterises that tree into a
    :class:`~brailix.ir.tactile.TactileRaster`. Concrete bytes (``.bmp`` /
    ``.png`` / ``.pdf`` / U+2800 preview) come from
    :meth:`GraphicResult.render` through the shared ``renderer_registry``.

    A module-level function, not a :class:`Pipeline` method, because a
    graphic's own compile needs **no braille standard** — its product is a
    raster, not cells (math / music, whose product *is* braille, keep their
    Pipeline entries). Only ``<text>`` label translation touches braille:
    pass ``braille_profile`` (a braille standard; a label pipeline is built
    on it) or a ready ``label_translator`` callable — with neither, labels
    are warned (``GRAPHICS_LABEL_NO_PROFILE``) and skipped.
    :meth:`Pipeline.translate_graphic` delegates here, wiring its own text
    path as the label translator when the standards match.

    ``tactile_profile`` names a tactile profile (mm adaptation params + DPI)
    or is an already-loaded ``TactileProfile``. ``record_provenance``
    records which pixels each SVG element drew for an editor's cross-pane
    highlight (off by default — export pays nothing). Bytes input goes to
    the source adapters as-is; they own the decode and its soft-failure.
    ``asset_resolver`` resolves an ``image`` source's reference to
    in-document bytes (see :class:`~brailix.core.protocols.
    GraphicAssetResolver`); ``None`` leaves it to read a filesystem path.
    """
    from brailix.backend.tactile import rasterize
    from brailix.backend.tactile.profile import load_tactile_profile

    warns = (
        warnings
        if warnings is not None
        else WarningCollector(mode=normalize_run_mode(mode))
    )
    gctx = GraphicsContext(
        source=source_format,
        warnings=warns,
        options=(
            {GRAPHIC_ASSET_RESOLVER_KEY: asset_resolver}
            if asset_resolver is not None
            else {}
        ),
    )
    tree = _frontend_parse_graphic_tree(source, gctx)
    if tree is None:  # a monkeypatched / fake frontend may return None
        tree = ET.Element("svg", {"data-bk-error": "no graphic tree"})

    translator = label_translator
    if translator is None and braille_profile is not None:
        translator = Pipeline(
            profile=braille_profile, mode=mode
        )._translate_inline_text

    prof = (
        load_tactile_profile(tactile_profile)
        if isinstance(tactile_profile, str)
        else tactile_profile
    )
    raster = rasterize(
        tree, prof, warns, translator, record_provenance=record_provenance
    )
    return GraphicResult(raster=raster, svg_tree=tree, warnings=warns)
