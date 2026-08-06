"""Core types and infrastructure shared across all layers.

This package ``__init__`` re-exports the cross-layer core types (span,
errors, per-run contexts) as the stable, **shallow** public surface.
Downstream consumers import from ``brailix.core`` rather than the concrete
modules (``brailix.core.span`` / ``.errors`` / ``.context``) so the library
can reorganise them freely.

There are no *default names* here. Every subsystem default is a field default
on :class:`brailix.Pipeline`, written where a caller reads it — in the
signature — and low-level components that sit below the orchestrator and
cannot import it keep their own local statement of the same value, which
``tests/frontend/test_default_adapter_names.py`` holds equal. The
``brailix.core.defaults`` module those constants used to live in was an
indirection whose whole job was keeping the copies in step, and it did not:
each subsystem kept a same-valued private copy anyway, so moving a default
there left the subsystem applying the old one.

Sub-packages keep their own surface: profile/config loading via
``brailix.core.config``; model-asset infrastructure via
``brailix.core.models``.

``BrailleProfile`` is therefore imported from ``brailix.core.config``, not
from here. That is a supported path, not an internal one — it is named in the
extension surface (see the top-level :mod:`brailix` docstring), because every
``LanguageBackend`` method takes a profile and an implementer has to be able
to annotate it. Re-exporting it here instead would put the whole profile /
table loader behind every ``import brailix.core``, and so behind
:mod:`brailix.ir`, which promises to load carrying core primitives alone.
"""

from __future__ import annotations

from brailix.core.context import (
    BackendContext,
    FrontendContext,
    GraphicsContext,
    MathContext,
    MusicContext,
)
from brailix.core.errors import (
    BackendContractError,
    BrailixError,
    ConfigurationError,
    FrontendContractError,
    IncompatibleDependencyError,
    IncompatibleRendererError,
    MissingExtraError,
    ModelNotInstalledError,
    ParseError,
    RunMode,
    StrictModeError,
    UnknownAdapterError,
    Warning,
    WarningCollector,
    WarningLevel,
    normalize_run_mode,
)
from brailix.core.span import Span, merge_spans

__all__ = (
    # span
    "Span",
    "merge_spans",
    # contexts — one per adapter family, so every Protocol an extender
    # implements can be annotated from this shallow surface
    "BackendContext",
    "FrontendContext",
    "GraphicsContext",
    "MathContext",
    "MusicContext",
    # errors + warnings — every exception a public entry point can raise, so
    # ``except`` can name the case instead of widening to ``BrailixError`` and
    # catching every other compile failure with it
    "BackendContractError",
    "BrailixError",
    "ConfigurationError",
    "FrontendContractError",
    "IncompatibleDependencyError",
    "IncompatibleRendererError",
    "MissingExtraError",
    "ModelNotInstalledError",
    "ParseError",
    "RunMode",
    "StrictModeError",
    "UnknownAdapterError",
    "Warning",
    "WarningCollector",
    "WarningLevel",
    "normalize_run_mode",
)
