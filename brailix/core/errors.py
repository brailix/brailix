"""Error types, warning records, and the run-mode/collector machinery.

The pipeline never crashes on unknown structures in ``normal`` or ``lenient``
mode — it records a :class:`Warning` and best-effort continues. ``strict``
mode promotes warnings to :class:`StrictModeError`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

from brailix.core.span import Span


class RunMode(str, Enum):  # noqa: UP042 — keep (str, Enum) __str__/serialization semantics
    """How aggressively the pipeline tolerates malformed input."""

    STRICT = "strict"
    NORMAL = "normal"
    LENIENT = "lenient"


def normalize_run_mode(mode: RunMode | str) -> RunMode:
    """Return a canonical :class:`RunMode` for public string inputs."""
    if isinstance(mode, RunMode):
        return mode
    return RunMode(mode.lower())


class WarningLevel(str, Enum):  # noqa: UP042 — keep (str, Enum) __str__/serialization semantics
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BrailixError(Exception):
    """Base class for all brailix exceptions."""


class ParseError(BrailixError):
    """Raised when an input source cannot be parsed at all."""


class ConfigurationError(BrailixError, ValueError):
    """Raised when a profile (or one of its tables) is malformed.

    The message identifies the offending file and key wherever possible
    so the user can jump straight to the bad entry. Subclasses both
    :class:`BrailixError` (so the standard ``except BrailixError``
    blocks catch it) and :class:`ValueError` (so legacy call sites that
    were catching :class:`ValueError` from the loader keep working).
    """


class StrictModeError(BrailixError):
    """Raised when a Warning is emitted while running in STRICT mode."""

    def __init__(self, warning: Warning):
        super().__init__(f"[{warning.code}] {warning.message}")
        self.warning = warning


class MissingExtraError(BrailixError):
    """Raised when an adapter is requested but its optional dependency is
    not installed.

    The message tells the user which ``pip install brailix[<extra>]``
    would fix it.
    """

    def __init__(self, adapter: str, extra: str, hint: str | None = None):
        msg = (
            f"adapter '{adapter}' requires optional dependency group "
            f"'{extra}'. Install it with: pip install brailix[{extra}]"
        )
        if hint:
            msg = f"{msg}\n{hint}"
        super().__init__(msg)
        self.adapter = adapter
        self.extra = extra


class ModelNotInstalledError(BrailixError):
    """Raised when an adapter needs a downloadable model that isn't
    present in the portable ``models/`` directory.

    Only raised under managed download (a front-end opted in via
    :func:`brailix.core.models.set_managed_download`): the adapter checks
    the expected install path and raises this instead of letting its
    backend auto-download, so a front-end's downloader can fetch the
    model under its own control (progress feedback, user consent),
    rendering a "please download" prompt against the ``model_id`` +
    ``install_dir`` fields.  By default adapters auto-download on first
    use and this is never raised.

    Callers without an interactive UI (CLI, scripts) still get a
    meaningful English fallback from ``str(exc)``.
    """

    def __init__(self, model_id: str, install_dir: object):
        super().__init__(
            f"model {model_id!r} is not installed at {install_dir}. "
            f"Install the model files there to enable this adapter."
        )
        self.model_id = model_id
        self.install_dir = install_dir


# ---------------------------------------------------------------------------
# Warning record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Warning:
    """A non-fatal diagnostic recorded during translation."""

    code: str
    message: str
    level: WarningLevel = WarningLevel.WARN
    surface: str | None = None
    span: Span | None = None
    candidates: tuple[str, ...] = ()
    source: str | None = None  # e.g. "zh_analyzer", "math.latex"
    # Optional structural provenance for inputs that have no usable
    # text span — domain-specific string keys.  The music backend fills
    # ``{"part_id": ..., "measure_number": ...}`` (the same labels its
    # ``BrailleCell.source_text`` provenance tags carry) so a frontend
    # can navigate to the score location; normalized MusicXML elements
    # carry no source offsets, which is why ``span`` can't serve here.
    # ``None`` (the default) means "no structural anchor known".
    anchor: dict[str, str] | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "code": self.code,
            "level": self.level.value,
            "message": self.message,
        }
        if self.surface is not None:
            d["surface"] = self.surface
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        if self.candidates:
            d["candidates"] = list(self.candidates)
        if self.source is not None:
            d["source"] = self.source
        if self.anchor:
            d["anchor"] = dict(self.anchor)
        return d


# ---------------------------------------------------------------------------
# WarningCollector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WarningCollector:
    """Accumulates warnings during a pipeline run.

    Behavior depends on :class:`RunMode`:

    * ``STRICT``  — :meth:`emit` raises :class:`StrictModeError`.
    * ``NORMAL``  — warnings are stored and returned at the end.
    * ``LENIENT`` — warnings are stored; ``ERROR``-level entries are
      downgraded to ``WARN``.
    """

    mode: RunMode | str = RunMode.NORMAL
    warnings: list[Warning] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)

    def emit(self, warning: Warning) -> None:
        if self.mode is RunMode.STRICT:
            raise StrictModeError(warning)
        if self.mode is RunMode.LENIENT and warning.level is WarningLevel.ERROR:
            warning = Warning(
                code=warning.code,
                message=warning.message,
                level=WarningLevel.WARN,
                surface=warning.surface,
                span=warning.span,
                candidates=warning.candidates,
                source=warning.source,
                anchor=warning.anchor,
            )
        self.warnings.append(warning)

    def warn(
        self,
        code: str,
        message: str,
        *,
        surface: str | None = None,
        span: Span | None = None,
        candidates: tuple[str, ...] = (),
        source: str | None = None,
        anchor: dict[str, str] | None = None,
    ) -> None:
        """Convenience: emit a WARN-level warning."""
        self.emit(
            Warning(
                code=code,
                message=message,
                level=WarningLevel.WARN,
                surface=surface,
                span=span,
                candidates=candidates,
                source=source,
                anchor=anchor,
            )
        )

    def error(
        self,
        code: str,
        message: str,
        *,
        surface: str | None = None,
        span: Span | None = None,
        candidates: tuple[str, ...] = (),
        source: str | None = None,
        anchor: dict[str, str] | None = None,
    ) -> None:
        """Convenience: emit an ERROR-level warning.

        ``ERROR`` marks an *unrecoverable structure* — the input could not
        be processed at all and only a placeholder / unknown cell stands in
        for it (content is lost), as opposed to :meth:`warn`'s
        recognized-but-degraded diagnostics. This is the level the run
        modes pivot on: ``STRICT`` raises, ``NORMAL`` keeps it as ``ERROR``
        (a front-end can surface it red), and ``LENIENT`` downgrades it to
        ``WARN`` — the experimental "just give me output" mode flags
        nothing as a hard failure.
        """
        self.emit(
            Warning(
                code=code,
                message=message,
                level=WarningLevel.ERROR,
                surface=surface,
                span=span,
                candidates=candidates,
                source=source,
                anchor=anchor,
            )
        )

    def __iter__(self) -> Iterator[Warning]:
        return iter(self.warnings)

    def __len__(self) -> int:
        return len(self.warnings)

    def __bool__(self) -> bool:
        return bool(self.warnings)

    def by_code(self, code: str) -> list[Warning]:
        return [w for w in self.warnings if w.code == code]

    def discard(self, predicate: Callable[[Warning], bool]) -> int:
        """Drop every stored warning matching ``predicate``; return how
        many were removed.

        Lets a later pipeline stage retract a diagnostic an earlier one
        emitted once new information makes it moot.  The pinyin frontend
        uses it to clear ``LOW_CONFIDENCE_PINYIN`` warnings for words the
        user's personal dictionary resolves — the user has already
        pinned that reading globally, so the polyphone nudge is noise.
        """
        before = len(self.warnings)
        self.warnings[:] = [w for w in self.warnings if not predicate(w)]
        return before - len(self.warnings)

    def to_list(self) -> list[dict]:
        return [w.to_dict() for w in self.warnings]
