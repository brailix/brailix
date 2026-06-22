"""Core types and infrastructure shared across all layers.

This package ``__init__`` re-exports the cross-layer core types (span,
errors, per-run contexts, default names) as the stable, **shallow**
public surface. Downstream consumers import from ``brailix.core``
rather than the concrete modules (``brailix.core.span`` /
``.errors`` / ``.context`` / ``.defaults``) so the library can
reorganise them freely.

Sub-packages keep their own surface: profile/config loading via
``brailix.core.config``; model-asset infrastructure via
``brailix.core.models``.
"""

from __future__ import annotations

from brailix.core.context import (
    BackendContext,
    FrontendContext,
    MathContext,
    MusicContext,
)
from brailix.core.defaults import (
    DEFAULT_NORMALIZER,
    DEFAULT_PINYIN_RESOLVER,
    DEFAULT_RENDERER,
    DEFAULT_SEGMENTER,
    DEFAULT_ZH_ANALYZER,
)
from brailix.core.errors import (
    BrailixError,
    ConfigurationError,
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
    # contexts
    "BackendContext",
    "FrontendContext",
    "MathContext",
    "MusicContext",
    # default names
    "DEFAULT_NORMALIZER",
    "DEFAULT_PINYIN_RESOLVER",
    "DEFAULT_RENDERER",
    "DEFAULT_SEGMENTER",
    "DEFAULT_ZH_ANALYZER",
    # errors + warnings
    "BrailixError",
    "ConfigurationError",
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
