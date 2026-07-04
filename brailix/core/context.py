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
    from brailix.core.protocols import InlineTextTranslator

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
        # **The context's mode is authoritative.** We force the collector
        # to match so adapters that only see the collector still emit
        # under the right policy. This is a one-way write: if you share
        # the same collector across two contexts with different modes,
        # the most-recently-constructed context wins. In practice
        # collectors aren't shared across modes — :class:`Pipeline`
        # creates one collector per run with the matching mode and
        # :meth:`child` inherits the parent's mode by default.
        self.warnings.mode = self.mode

    def child(self, **overrides: Any) -> FrontendContext:
        """Create a derived context that shares the same warnings
        collector but overrides specific fields.

        Note: overriding ``mode`` on a child re-writes the shared
        collector's mode (see :meth:`__post_init__`). Don't do that
        unless you want the parent's collector to switch modes too.
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
    # NOTE: transposition / octave_inference / show_lyrics are not yet
    # consumed — these behaviours are currently driven by profile features
    # (e.g. ``music.octave_rule`` / ``music.show_lyrics``) read by the
    # backend's MusicBrailleContext, not by these fields.
    transposition: int = 0           # semitones; -12 = octave down (reserved)
    octave_inference: bool = True    # BANA Par. 3.2.2 (reserved)
    show_lyrics: bool = True         # (reserved)
    surrounding_text: tuple[str, str] | None = None  # (before, after)
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Graphics (tactile-graphics frontend sub-phase)
# ---------------------------------------------------------------------------


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
        # See FrontendContext.__post_init__ for the rationale: the
        # context's mode is authoritative; the shared collector follows.
        self.warnings.mode = self.mode

    def inline_text_translator(self) -> InlineTextTranslator | None:
        """The Pipeline-injected inline-text translator, or ``None``.

        Backend handlers that embed prose (music ``<words>`` / lyrics,
        chem reaction conditions) call this to render text through the
        zh / latin path. ``None`` in a bare backend run or a unit test,
        so callers fall back to a warning + marker. This is the
        sanctioned backend→frontend seam (ARCHITECTURE §12) — the
        callable is injected, never imported.
        """
        return self.options.get(INLINE_TEXT_TRANSLATOR_KEY)
