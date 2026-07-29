"""``auto`` Japanese analyzer: pick the best installed engine.

Tries to construct janome → fugashi → sudachi (in that order — janome is
pure-Python and self-contained, the most reliable when present); the
first that loads wins. Falls back to the dependency-free ``kana``
analyzer when none is installed. Selection happens once, on first use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from brailix.core.context import FrontendContext
from brailix.core.errors import CANDIDATE_UNAVAILABLE_ERRORS

if TYPE_CHECKING:
    from brailix.frontend.ja.analyzer import JapaneseAnalyzer, JapaneseToken

_PREFERENCE = ("janome", "fugashi", "sudachi")


def _pick() -> JapaneseAnalyzer:
    from brailix.frontend.ja.analyzer.registry import analyzer_registry

    for name in _PREFERENCE:
        try:
            return analyzer_registry.get(name)
        except CANDIDATE_UNAVAILABLE_ERRORS:
            # Engine unavailable — not installed, or installed beside a
            # dependency version known to break it. Best-effort probe, try the
            # next. The catch is the shared list
            # (:data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS`) and
            # deliberately nothing wider: an *unexplained* load failure (a
            # corrupt dictionary, a numpy mismatch nobody has characterised)
            # or a programming bug still propagates, rather than silently
            # degrading to kana (汉字 → MISSING_READING, no は→ワ) with no
            # diagnostic. That distinction is the same one
            # IncompatibleDependencyError draws in its own docstring: only a
            # known, deterministic incompatibility is a skip signal.
            continue
    return analyzer_registry.get("kana")


@dataclass(slots=True)
class AutoJapaneseAnalyzer:
    name: str = "auto"
    # init=False/repr=False: the resolved delegate is internal cache state, not
    # a constructor argument (mirrors AutoChineseAnalyzer / AutoPinyinResolver).
    _delegate: JapaneseAnalyzer | None = field(
        default=None, init=False, repr=False
    )

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        if self._delegate is None:
            self._delegate = _pick()
        return self._delegate.analyze(text, ctx)


def _load() -> AutoJapaneseAnalyzer:
    return AutoJapaneseAnalyzer()
