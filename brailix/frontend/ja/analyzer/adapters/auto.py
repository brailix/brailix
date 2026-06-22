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
from brailix.core.errors import MissingExtraError

if TYPE_CHECKING:
    from brailix.frontend.ja.analyzer import JapaneseAnalyzer, JapaneseToken

_PREFERENCE = ("janome", "fugashi", "sudachi")


def _pick() -> JapaneseAnalyzer:
    from brailix.frontend.ja.analyzer.registry import analyzer_registry

    for name in _PREFERENCE:
        try:
            return analyzer_registry.get(name)
        except MissingExtraError:
            # Engine simply not installed — best-effort probe, try the next.
            # A genuine load failure (corrupt dictionary, version mismatch) or
            # a programming bug is deliberately NOT swallowed here: it
            # propagates instead of silently degrading to kana (汉字 →
            # MISSING_READING, no は→ワ) with no diagnostic, mirroring the zh
            # auto chain's narrow catch.
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
