"""Error types, warning records, and the run-mode/collector machinery.

The pipeline never crashes on unknown structures in ``normal`` or ``lenient``
mode — it records a :class:`Warning` and best-effort continues. ``strict``
mode promotes warnings to :class:`StrictModeError`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
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


class BackendContractError(BrailixError):
    """A backend implementation violated an output contract the rest of the
    system builds on — e.g. emitting a :class:`~brailix.ir.braille.BrailleCell`
    without a ``source_span`` for an IR node that carries one, which breaks
    the "every cell maps to a source span" traceability invariant
    (ARCHITECTURE §3) that proofreading navigation depends on.

    This is a *programming* error in the backend (built-in or plugin), never
    a property of the user's input, so it is raised unconditionally — no run
    mode downgrades or swallows it (the same philosophy as
    :data:`PROGRAMMING_ERRORS`: a loud, locatable failure beats silently
    wrong output).
    """


class MissingExtraError(BrailixError):
    """Raised when an adapter is requested but its optional dependency is
    not installed.

    The message tells the user which ``pip install brailix[<extra>]``
    would fix it.
    """

    def __init__(
        self,
        adapter: str,
        extra: str,
        hint: str | None = None,
        *,
        missing_module: str | None = None,
    ):
        msg = (
            f"adapter '{adapter}' requires optional dependency group "
            f"'{extra}'. Install it with: pip install brailix[{extra}]"
        )
        if missing_module:
            # The concrete import that failed — usually the extra's own
            # top-level package, but sometimes a transitive dependency the
            # extra pulls in (e.g. g2pM importing numpy). Surfacing it turns
            # a "which package is actually missing?" diagnosis from a guess
            # into a fact.
            msg = f"{msg}\n(the missing import was: {missing_module})"
        if hint:
            msg = f"{msg}\n{hint}"
        super().__init__(msg)
        self.adapter = adapter
        self.extra = extra
        self.missing_module = missing_module


class UnknownAdapterError(BrailixError, KeyError):
    """Raised when a registry is asked for an adapter / analyzer / resolver /
    renderer name it doesn't know (and no optional extra would supply it).

    Subclasses both :class:`BrailixError` — so an ``except BrailixError`` block
    (e.g. the CLI's top-level handler) catches it WITHOUT also swallowing every
    unrelated internal :class:`KeyError` as a clean user error — and
    :class:`KeyError`, so the many call sites and tests that catch the
    registry's idiomatic "key not found" keep working unchanged. Mirrors
    :class:`ConfigurationError`'s dual-base rationale.
    """


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
# Programming-error classification (soft-failure boundaries)
# ---------------------------------------------------------------------------

# Exception types that signal a *code defect*, never a legitimate response to
# bad input, so a soft-failure boundary must let them PROPAGATE rather than
# disguise them as a recoverable "bad input" warning.
#
# Brailix's design deliberately soft-fails on malformed input (a broken formula
# / score degrades to a placeholder + warning so one bad element can't fail a
# whole document — the "pipeline never crashes" rule). The hazard of that
# pattern is a broad ``except Exception`` at the boundary swallowing a regression
# (``AttributeError`` on a ``None``, a fired ``assert``, a typo'd name) and
# reporting it as "unreadable input" — a green pipeline silently hiding a
# maintainer's bug, which is worse than a loud, locatable crash.
#
# Only the *unambiguous* code-defect types are listed. ``TypeError`` /
# ``ValueError`` / ``KeyError`` are deliberately EXCLUDED: the adapter
# registries are open (third-party math / music parsers, latex2mathml, …) and
# those libraries legitimately raise them on malformed input, where a
# soft-failure — not a crash — is the correct behaviour the design intends. An
# adapter that finds its dependency raising an ``AttributeError`` on bad input
# should catch that *locally* with an explicit reason, not rely on the global
# backstop to paper over every defect.
PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    AttributeError,
    NameError,
    AssertionError,
)


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

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
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
            # Drop ERROR to WARN, preserving every other field. Use
            # dataclasses.replace, not a hand-listed rebuild: the old
            # field-by-field copy silently dropped any field added to Warning
            # later (surface / span / candidates / source / anchor each had to
            # be remembered here), losing diagnostics in LENIENT mode.
            warning = replace(warning, level=WarningLevel.WARN)
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

    def to_list(self) -> list[dict[str, object]]:
        return [w.to_dict() for w in self.warnings]
