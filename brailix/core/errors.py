"""Error types, warning records, and the run-mode/collector machinery.

The pipeline never crashes on unknown structures in ``normal`` or ``lenient``
mode — it records a :class:`Warning` and best-effort continues. ``strict``
mode promotes warnings to :class:`StrictModeError`.
"""

from __future__ import annotations

import zlib as _zlib
from collections.abc import Callable as _Callable
from collections.abc import Iterator as _Iterator
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from dataclasses import replace as _replace
from enum import Enum as _Enum
from typing import NoReturn as _NoReturn

from brailix.core.span import Span


class RunMode(str, _Enum):  # noqa: UP042 — keep (str, Enum) __str__/serialization semantics
    """How aggressively the pipeline tolerates malformed input."""

    STRICT = "strict"
    NORMAL = "normal"
    LENIENT = "lenient"


def normalize_run_mode(mode: RunMode | str) -> RunMode:
    """Return a canonical :class:`RunMode` for public string inputs."""
    if isinstance(mode, RunMode):
        return mode
    return RunMode(mode.lower())


class WarningLevel(str, _Enum):  # noqa: UP042 — keep (str, Enum) __str__/serialization semantics
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
    (ARCHITECTURE#arch-traceability) that proofreading navigation depends on.

    This is a *programming* error in the backend (built-in or plugin), never
    a property of the user's input, so it is raised unconditionally — no run
    mode downgrades or swallows it (the same philosophy as
    :data:`PROGRAMMING_ERRORS`: a loud, locatable failure beats silently
    wrong output).
    """


class FrontendContractError(BrailixError):
    """A frontend adapter returned tokens the rest of the compiler cannot
    build on — the input-side counterpart of :class:`BackendContractError`.

    A ``Protocol`` proves an object *has* an ``analyze`` / ``resolve`` method;
    it cannot prove what comes back. The analyzer and resolver registries are
    open, so this is checked where a third party's output crosses into the
    library: a token that is not the language's token type, a ``span`` that is
    not a :class:`~brailix.core.span.Span`, a span reaching past the end of the
    analyzed text, spans that overlap or run backwards, a resolver that
    changed the number, order, surface or span of the tokens it was handed.

    Every one of those is a *programming* error in the adapter, never a
    property of the user's document, so — like
    :class:`BackendContractError` — it is raised unconditionally and no run
    mode downgrades it. That matters more here than almost anywhere else: token
    spans are the source coordinates every braille cell inherits, so a wrong
    one does not crash, it produces a document whose proofreading jumps land on
    the wrong characters and whose word spacing is decided from coordinates
    that describe nothing.

    Deliberately *not* raised for a surface that does not match the source text
    it claims (``text[start:end] != surface``). An analyzer that normalises its
    input legitimately produces one — the shipped THULAC and HanLP adapters do,
    and warn about it (see
    :func:`brailix.frontend.zh.analyzer.adapters._spans.recover_spans_by_cursor`)
    — so that stays a warning about unreliable coordinates rather than a
    failed compile.
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


class IncompatibleDependencyError(BrailixError):
    """Raised when an adapter's optional dependency IS installed, but at a
    version known to break the adapter at runtime.

    Deliberately *not* a :class:`MissingExtraError` subclass: that error's
    "pip install brailix[<extra>]" advice is exactly wrong here — everything
    is installed, one package is just too new (or too old) — so this error
    carries its own remedy (``pip install "<dependency><requirement>"``).
    The ``auto`` selection chains treat it like any other
    "candidate unavailable" signal (:class:`ModelNotInstalledError`,
    :class:`MissingExtraError`) and fall through to the next engine, while an
    explicitly requested adapter surfaces the message as-is.

    Raise it only for *known, deterministic* incompatibilities (a removed
    API, a published upstream bound) — an unexplained load failure should
    propagate instead, so real bugs aren't silently reclassified.
    """

    def __init__(
        self,
        adapter: str,
        *,
        dependency: str,
        installed: str,
        requirement: str,
        reason: str,
    ):
        super().__init__(
            f"adapter {adapter!r} is installed but its dependency "
            f"{dependency!r} {installed} is known to be incompatible: "
            f"{reason}. Install a compatible version: "
            f'pip install "{dependency}{requirement}"'
        )
        self.adapter = adapter
        self.dependency = dependency
        self.installed = installed
        self.requirement = requirement


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


class IncompatibleRendererError(BrailixError):
    """Raised when a renderer is asked to consume an IR domain it doesn't
    handle — e.g. handing a braille :class:`~brailix.ir.braille.BrailleDocument`
    to a tactile-raster renderer (``bmp`` / ``png`` / ``pdf`` /
    ``tactile_preview``), or a :class:`~brailix.ir.tactile.TactileRaster` to a
    braille renderer (``unicode`` / ``brf`` / ``layout`` / ``cells``).

    A renderer self-describes the IR it consumes via a ``consumes`` attribute
    (``"braille"`` by default, ``"tactile_raster"`` for the tactile renderers).
    The result types (:class:`~brailix.pipeline.TranslationResult`,
    :class:`~brailix.pipeline.GraphicResult`,
    :class:`~brailix.pipeline.TactilePageResult`) validate it before rendering
    so a mismatched ``render(name)`` fails loudly and locatably here instead of
    crashing deep inside the renderer with an opaque ``AttributeError`` on a
    wrong-typed IR (the two Registry share one namespace on purpose — the
    Protocol is deliberately wide — so the check lives at the call boundary).
    """

    def __init__(self, renderer_name: str, consumes: str, expected: str):
        super().__init__(
            f"renderer {renderer_name!r} consumes {consumes!r} IR, but this "
            f"result provides {expected!r} IR. Pick a renderer that consumes "
            f"{expected!r}."
        )
        self.renderer_name = renderer_name
        self.consumes = consumes
        self.expected = expected


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
# Candidate-unavailable classification (``auto`` selection chains)
# ---------------------------------------------------------------------------

# Exception types that mean "this engine cannot be used here", so an ``auto``
# chain should move on to the next candidate instead of failing the compile.
# The mirror image of PROGRAMMING_ERRORS above: that tuple lists what a wide
# boundary must never swallow, this one lists what a narrow one must catch.
#
# It lives here rather than in any one chain because there are three of them —
# zh analyzer, zh pinyin, ja analyzer — and the *fact* of which errors mean
# "unavailable" is one fact with one owner. Written out per chain it drifted
# immediately: the zh analyzer caught all four, the pinyin resolver two, the ja
# analyzer one, while ``IncompatibleDependencyError`` went on documenting
# itself as a signal "the ``auto`` selection chains" honour — true of exactly
# one of them. The failure that shape produces is quiet: an adapter starts
# raising a version-compatibility error correctly, its language's chain doesn't
# list that type, and what used to degrade now crashes the translation.
#
# Only the *tuple* is shared. Each chain keeps its own loop, its own preference
# order and its own cache: zh and ja are independently replaceable language
# components (ARCHITECTURE#arch-layers), so a common exception list is a shared
# fact, while a common ``_pick_delegate`` helper would be a shared code path
# welding them together.
#
# Deliberately NOT here: ``OSError``. It does not *mean* "unavailable" — it is
# one chain's known failure mode (a loader creating its model directory under a
# read-only root), and the zh analyzer adds it locally with that reason
# written down. Widening it to every chain would silently swallow a corrupt
# dictionary read as "engine not installed".
CANDIDATE_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    # Nothing is registered under that name at all.
    KeyError,
    # Registered, but its optional dependency isn't installed.
    MissingExtraError,
    # Installed, but its downloadable model isn't present yet (managed
    # download: a front-end fetches it under its own control).
    ModelNotInstalledError,
    # Installed, but alongside a dependency version known to break it at
    # runtime — selecting it would crash on first use, so skip it up front.
    IncompatibleDependencyError,
)


# ---------------------------------------------------------------------------
# Unreadable-archive classification (ZIP container boundaries)
# ---------------------------------------------------------------------------

# What ``zipfile`` raises *besides* ``BadZipFile`` when the archive opens but a
# member cannot be read. ``BadZipFile`` covers "this is not a zip / the
# directory is broken"; these cover "the directory is fine, this member is
# not", and every one of them is a property of the **input**, so a container
# adapter owes its caller its own error (a ``ParseError``, a soft failure) for
# each rather than letting a standard-library type escape.
#
# It lives here for the same reason :data:`CANDIDATE_UNAVAILABLE_ERRORS` does:
# there is more than one ZIP container in the tree — ``.docx`` (input layer)
# and ``.mxl`` (music frontend) — and *which exceptions mean "unreadable
# member"* is one fact about ``zipfile``, not a per-adapter opinion. Written
# out twice it drifted immediately: ``.mxl`` classified all five while the
# ``.docx`` preflight caught only ``BadZipFile``, so an encrypted or
# exotically-compressed ``.docx`` leaked a raw ``RuntimeError`` /
# ``NotImplementedError`` / ``zlib.error`` past ``parse_docx``'s documented
# "malformed OOXML → ParseError" surface.
#
# Only the *tuple* is shared. Each adapter keeps its own read loop, its own
# size budget, its own rootfile logic and its own failure mode (``.docx``
# raises, ``.mxl`` soft-fails into a ``<music-error>``) — a common "generic
# unzipper" would weld two formats with genuinely different policies together.
#
# Deliberately NOT ``Exception``: an ``AttributeError`` / ``KeyError`` from a
# regression inside the adapter itself must stay a loud crash, not be
# relabelled "unreadable archive" (:data:`PROGRAMMING_ERRORS`).
UNREADABLE_ZIP_MEMBER_ERRORS: tuple[type[Exception], ...] = (
    # A corrupt deflate stream.
    _zlib.error,
    # An encrypted member, with no password supplied.
    RuntimeError,
    # A compression method zipfile does not implement.
    NotImplementedError,
    # A truncated or otherwise malformed member stream.
    EOFError,
    ValueError,
)


# ---------------------------------------------------------------------------
# Warning record
# ---------------------------------------------------------------------------


class _FrozenAnchor(dict):  # type: ignore[type-arg]
    """The read-only mapping :attr:`Warning.anchor` holds.

    :class:`Warning` is a frozen value object and every field of it is
    immutable — except that one, whose ``dict`` stayed writable from both
    sides. Mutating the dict you *passed* rewrote a diagnostic that had
    already been recorded::

        anchor = {"measure": "1"}
        collector.warn(..., anchor=anchor)
        anchor["measure"] = "99"        # the stored warning now says 99

    and ``warning.anchor["measure"] = "99"`` rewrote it directly. Neither
    failed; both silently changed a record that a block cache, the editor's
    navigation and a test comparison all read as fixed.

    A ``dict`` **subclass** rather than :class:`types.MappingProxyType`,
    deliberately: a proxy cannot be JSON-encoded, deep-copied or pickled, and
    this field exists to be read out — by a front-end, a log, a serialized
    report. Immutability that costs ``json.dumps`` is a bad trade for a
    diagnostic. This stays a real ``dict`` to every reader (equality with a
    plain dict, ``.get``, ``dict(...)``, ``json.dumps``), so the declared field
    type is still ``dict[str, str] | None`` and no consumer annotation changes;
    only the writes are gone.

    ``dict``'s mutators are C-level and do not route through one another —
    overriding ``__setitem__`` does nothing for ``update`` or ``|=`` — so every
    one is overridden by hand, the lesson ``_BoundaryRegistry`` in the frontend
    learned by missing exactly one of them. ``__reduce__`` belongs to that
    list: the pickle / deepcopy protocol rebuilds a ``dict`` subclass by
    *setting its items*, so without it the copying this class exists to keep
    possible would be refused by its own guard.

    ``__init__`` belongs to it as well, and was the one left off. It is how the
    mapping is filled, so it cannot simply refuse — but it is also an ordinary
    public method of the object, and ``dict.__init__`` fills an *existing*
    mapping in C without passing through ``__setitem__``. So
    ``warning.anchor.__init__({"measure": "99"})`` rewrote a recorded
    diagnostic through the one door left open, no base-class trickery needed.
    Sealing after the first call closes it: construction goes through, a second
    call is refused like any other write. (``dict.__setitem__(anchor, k, v)``
    still reaches past every override, as it does past any Python-level guard
    on a C type; that is an explicit base-class call, not something the object
    itself offers.)
    """

    __slots__ = ("_sealed",)

    _MESSAGE = (
        "Warning.anchor is read-only — a Warning is a frozen record. Build "
        "the mapping before you construct the Warning, or take a writable "
        "copy with dict(anchor)."
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError(self._MESSAGE)
        super().__init__(*args, **kwargs)
        self._sealed = True

    def __setitem__(self, key: str, value: str) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def __delitem__(self, key: str) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    # ``object`` rather than dict's own narrower parameter, so no spelling of
    # ``anchor |= …`` slips past the guard; mypy checks an in-place operator
    # against its binary sibling ``__or__`` and reads the widening as an
    # incompatible override, the same waiver ``_BoundaryRegistry.__ior__``
    # takes for the same reason. The body never returns either way.
    def __ior__(self, other: object) -> _FrozenAnchor:  # type: ignore[misc]
        raise TypeError(self._MESSAGE)

    def clear(self) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def pop(self, *args: object, **kwargs: object) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def popitem(self) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def setdefault(self, *args: object, **kwargs: object) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def update(self, *args: object, **kwargs: object) -> _NoReturn:
        raise TypeError(self._MESSAGE)

    def __reduce__(self) -> tuple[object, ...]:
        # Rebuild through the constructor, which fills the mapping in C. The
        # default protocol for a dict subclass replays the items with
        # ``obj[k] = v`` instead, which the guard above would refuse — pickle
        # and deepcopy would both fail on a perfectly ordinary Warning.
        return (self.__class__, (dict(self),))


@_dataclass(frozen=True, slots=True)
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
    # ``None`` (the default) means "no structural anchor known". Stored as a
    # private read-only copy (:class:`_FrozenAnchor`), so neither the caller's
    # dict nor the field itself can rewrite a recorded diagnostic afterwards.
    anchor: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.anchor is not None and not isinstance(self.anchor, _FrozenAnchor):
            # ``object.__setattr__``: the dataclass is frozen, and this runs
            # during its own construction. Copying is half the point — the
            # caller keeps a writable dict of their own, and this record stops
            # tracking it.
            object.__setattr__(self, "anchor", _FrozenAnchor(self.anchor))

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


@_dataclass(slots=True)
class WarningCollector:
    """Accumulates warnings during a pipeline run.

    Behavior depends on :class:`RunMode`:

    * ``STRICT``  — :meth:`emit` raises :class:`StrictModeError`.
    * ``NORMAL``  — warnings are stored and returned at the end.
    * ``LENIENT`` — warnings are stored; ``ERROR``-level entries are
      downgraded to ``WARN``.
    """

    mode: RunMode | str = RunMode.NORMAL
    warnings: list[Warning] = _field(default_factory=list)
    # Set once a context has adopted this collector via :meth:`bind_mode`.
    # Kept off ``__eq__`` / ``__repr__`` so it stays an internal latch and two
    # collectors with the same warnings compare equal regardless of binding.
    _mode_bound: bool = _field(default=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.mode = normalize_run_mode(self.mode)

    def bind_mode(self, mode: RunMode | str) -> None:
        """Adopt ``mode`` as this collector's run-mode policy.

        Idempotent for the same mode. Raises :class:`ValueError` if a
        *different* mode was already bound: :meth:`emit` reads a single
        ``self.mode``, so a collector shared by two contexts with different
        modes would have whichever context was constructed last silently
        decide the policy for **every** warning — including those logically
        belonging to the other context. Making that a loud error is the whole
        point of routing a context's mode through here instead of assigning
        ``collector.mode`` directly.

        A freshly constructed collector is *unbound* even if it was created
        with an explicit ``mode=``, so the first context to adopt it may
        harmonize it to the context's own mode (the documented
        "context mode is authoritative" behaviour); only a *second, different*
        mode conflicts.
        """
        new_mode = normalize_run_mode(mode)
        current = normalize_run_mode(self.mode)
        if self._mode_bound and current is not new_mode:
            raise ValueError(
                f"WarningCollector already bound to run mode "
                f"{current.value!r}; cannot rebind to {new_mode.value!r}. A "
                f"single collector must not be shared across contexts with "
                f"different run modes — give each mode its own collector, or "
                f"construct both contexts with the same mode."
            )
        self.mode = new_mode
        self._mode_bound = True

    def emit(self, warning: Warning) -> None:
        if self.mode is RunMode.STRICT:
            raise StrictModeError(warning)
        if self.mode is RunMode.LENIENT and warning.level is WarningLevel.ERROR:
            # Drop ERROR to WARN, preserving every other field. Use
            # dataclasses.replace, not a hand-listed rebuild: the old
            # field-by-field copy silently dropped any field added to Warning
            # later (surface / span / candidates / source / anchor each had to
            # be remembered here), losing diagnostics in LENIENT mode.
            warning = _replace(warning, level=WarningLevel.WARN)
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

    def __iter__(self) -> _Iterator[Warning]:
        return iter(self.warnings)

    def __len__(self) -> int:
        return len(self.warnings)

    def __bool__(self) -> bool:
        return bool(self.warnings)

    def by_code(self, code: str) -> list[Warning]:
        return [w for w in self.warnings if w.code == code]

    def discard(self, predicate: _Callable[[Warning], bool]) -> int:
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
