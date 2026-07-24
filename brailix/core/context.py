"""Per-run context objects threaded through the pipeline.

Each phase (Frontend, Math parsing, Backend) gets its own context type.
They share a :class:`WarningCollector` so diagnostics from any layer
end up in the same final report.

The context types carry the profile name plus mode / options as a
small bundle adapters can inspect. ``profile`` is required on every
context — there is no built-in default braille standard; the caller
(normally :class:`~brailix.Pipeline`) always supplies the chosen one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from brailix.core.errors import RunMode, WarningCollector, normalize_run_mode

if TYPE_CHECKING:
    from brailix.core.protocols import (
        GraphicAssetResolver,
        InlineTextTranslator,
    )
    from brailix.core.span import Span

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrontendContext:
    """Context for the Frontend phase: segmentation, normalization,
    language-specific processing.

    Adapters read ``profile`` and ``options`` to pick behavior; they
    write diagnostics into ``warnings``. The language of any given
    fragment lives on the :class:`~brailix.core.config.BrailleProfile`
    pulled from ``profile`` — the context doesn't duplicate it.
    """

    profile: str
    mode: RunMode | str = RunMode.NORMAL
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)
        # **The context's mode is authoritative.** Bind the shared collector
        # to this mode so adapters that only see the collector still emit
        # under the right policy. :meth:`WarningCollector.bind_mode` adopts the
        # mode of a freshly supplied (still-default) collector but RAISES if the
        # collector was already bound to a *different* mode: sharing one
        # collector across two contexts with different modes used to let the
        # most-recently-constructed context silently win — an order-dependent
        # policy that is now a loud error. In the normal path :class:`Pipeline`
        # creates one collector per run with the matching mode and
        # :meth:`child` inherits the parent's mode, so binding is a no-op.
        self.warnings.bind_mode(self.mode)

    def child(self, **overrides: Any) -> FrontendContext:
        """Create a derived context that shares the same warnings
        collector but overrides specific fields.

        Note: overriding ``mode`` on a child re-binds the shared collector's
        mode (see :meth:`__post_init__`), which now **raises** rather than
        silently switching the parent's collector — a single collector must
        not straddle two run modes. Give the child its own collector if it
        genuinely needs a different mode.
        """
        # Annotated as dict[str, Any] so ``**base`` matches the
        # heterogeneous parameter types of FrontendContext — without
        # the annotation, mypy infers dict[str, object] (invariant)
        # and rejects the spread against str / RunMode / Collector.
        base: dict[str, Any] = {
            "profile": self.profile,
            "mode": self.mode,
            "warnings": self.warnings,
            "options": dict(self.options),
        }
        base.update(overrides)
        return FrontendContext(**base)


# ---------------------------------------------------------------------------
# Math (Frontend sub-phase)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MathContext:
    """Context for the math subsystem (source adapter + IR builder).

    Source-format adapters and the MathIR builder both run inside the
    frontend, but each math fragment gets its own context so per-formula
    state (display vs inline, surrounding text) stays local.
    """

    mode: Literal["inline", "display"] = "inline"
    source: str = "plain"  # latex / omml / mathml / plain
    profile: str = field(kw_only=True)  # required; no built-in default standard
    surrounding_text: tuple[str, str] | None = None  # (before, after)
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Music (Frontend sub-phase)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MusicContext:
    """Context for the music subsystem (source adapter + normalizer).

    Adapters convert any source format (MusicXML, .mxl, MIDI, ABC, ...)
    into a normalised MusicXML tree — that tree itself is the music
    IR (see ``ARCHITECTURE.md``). Each music fragment gets its
    own context so per-fragment state stays local.
    """

    mode: Literal["inline", "block", "score"] = "block"
    source: str = "plain"  # musicxml / mxl / midi / abc / plain
    profile: str = field(kw_only=True)  # required; no built-in default standard
    # No transposition / octave-inference / lyrics knobs live here: those
    # behaviours are driven by profile features (e.g. ``music.octave_rule`` /
    # ``music.show_lyrics``) read by the backend, not by MusicContext. Add a
    # field only when a source adapter actually reads it — and key the tree
    # cache on it — so the context never advertises a setting that does nothing.
    surrounding_text: tuple[str, str] | None = None  # (before, after)
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graphics (tactile-graphics frontend sub-phase)
# ---------------------------------------------------------------------------

# Key under which a caller stashes the graphic-asset resolver on
# ``GraphicsContext.options``. Read it via
# :meth:`GraphicsContext.asset_resolver`, never by the literal string —
# see :class:`brailix.core.protocols.GraphicAssetResolver` and
# ARCHITECTURE §12 (the same inject-a-callable seam as the inline-text
# translator).
GRAPHIC_ASSET_RESOLVER_KEY = "graphic_asset_resolver"


@dataclass(slots=True)
class GraphicsContext:
    """Context for the tactile-graphics subsystem (source → SVG adapter).

    Source-format adapters convert any graphics source (raw SVG, geometry
    primitives, a raster image, a chart spec, ...) into a normalised SVG
    string — that SVG tree itself is the graphics IR (see
    :mod:`brailix.frontend.graphics` and
    ``ARCHITECTURE.md``), exactly as MathML / MusicXML
    are the IR for their verticals. The tactile rendering profile
    (millimetre adaptation params + DPI) is deliberately **not** carried
    here: it is a backend concern, applied at rasterize time
    (:func:`brailix.backend.tactile.rasterize`), so the source adapters
    stay device-independent.
    """

    source: str = "svg"  # svg / primitives / image / chart / ...
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)

    def asset_resolver(self) -> GraphicAssetResolver | None:
        """The caller-injected asset resolver, or ``None``.

        The ``image`` source adapter calls this to turn a document-relative
        asset name (``media/image1.png``) into raw bytes — an image embedded
        in a ``.docx`` lives only in memory, so there is no path to read.
        ``None`` in a bare run (a hand-authored figure that references a
        real file, a unit test), where the adapter falls back to resolving
        the reference as a filesystem path. The sanctioned injection seam
        (ARCHITECTURE §12) — the callable is handed in, never imported."""
        return self.options.get(GRAPHIC_ASSET_RESOLVER_KEY)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

# Key under which the Pipeline stashes the inline-text translator on
# ``BackendContext.options``. Read it via
# :meth:`BackendContext.inline_text_translator`, never by the literal
# string — see :class:`brailix.core.protocols.InlineTextTranslator` and
# ARCHITECTURE §12.
INLINE_TEXT_TRANSLATOR_KEY = "inline_text_translator"


@dataclass(slots=True)
class BackendContext:
    """Context for the Backend phase: translates IR to BrailleIR.

    Carries the profile name, run mode, current block type, and the
    shared warning collector. Context-sensitive *braille* state (the
    number-sign latch, math nesting depth, ...) deliberately lives **not**
    here but on the per-subsystem state machines that own it — e.g.
    :class:`~brailix.backend.math.context.MathBrailleContext`. A single
    shared bag of those flags on the context was never read by the
    dispatcher and only invited silently-ignored writes, so it was removed.
    """

    profile: str
    mode: RunMode | str = RunMode.NORMAL
    block_type: str = "paragraph"
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)
        # See FrontendContext.__post_init__ for the rationale: the context's
        # mode is authoritative; the shared collector is bound to follow it,
        # and re-binding a collector already bound to a different mode raises.
        self.warnings.bind_mode(self.mode)

    def inline_text_translator(
        self, domain: str | None = None, span: Span | None = None
    ) -> InlineTextTranslator | None:
        """The Pipeline-injected inline-text translator, or ``None``.

        Backend handlers that embed prose (music ``<words>`` / lyrics,
        chem reaction conditions, math ``\\text{...}``) call this to render
        text through the zh / latin path. ``None`` in a bare backend run or
        a unit test, so callers fall back to a warning + marker. This is the
        sanctioned backend→frontend seam (ARCHITECTURE §12) — the
        callable is injected, never imported.

        ``domain`` / ``span`` attribute the embedded run's diagnostics: the
        nested translation's warnings surface in the host compile's report
        tagged with the embedding construct (``"music_words"`` /
        ``"math_text"`` / ...) and anchored to the embedding node's span.
        The attribution is duck-typed via an optional ``bind_domain``
        method on the injected translator (the Pipeline's implementation
        has one); a plain-callable third-party translator is returned
        unchanged, keeping the ``(text) -> cells`` protocol minimal.
        """
        translator = self.options.get(INLINE_TEXT_TRANSLATOR_KEY)
        if translator is None or domain is None:
            return translator
        bind = getattr(translator, "bind_domain", None)
        if bind is None:
            return translator
        return bind(domain, span)
