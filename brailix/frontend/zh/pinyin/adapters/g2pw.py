"""g2pW-backed pinyin resolver.

g2pW is the deep-learning polyphone disambiguator from Yang et al.
We import it lazily inside :func:`_load`. The wrapper accepts an
injected predictor for testability.

Low-confidence readings emit a ``LOW_CONFIDENCE_PINYIN`` warning so
human proofreaders can review them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin.adapters._align import resolve_by_char_alignment
from brailix.ir.inline import ChineseToken

LOW_CONFIDENCE_THRESHOLD = 0.75


@dataclass(slots=True)
class G2pwPinyinResolver:
    """Wraps a g2pW predictor.

    ``predictor`` is a callable that accepts a single Chinese sentence
    string and returns a tuple ``(pinyins, confidences)`` where
    ``pinyins`` is a list[str] of numeric-tone syllables (one per
    *character* in the input) and ``confidences`` is an optional
    list[float] aligned with ``pinyins``.
    """

    name: str = "g2pw"
    predictor: Any = field(default=None)
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        if not tokens:
            return []
        sentence = "".join(t.surface for t in tokens)
        pinyins, confidences = _normalize_predictor_output(self.predictor(sentence))
        return resolve_by_char_alignment(
            tokens,
            pinyins,
            ctx,
            source="pinyin.g2pw",
            engine="g2pW",
            confidences=confidences,
            low_confidence_threshold=self.low_confidence_threshold,
        )


def _normalize_predictor_output(value: Any) -> tuple[list[str], list[float] | None]:
    """Accept either ``(pinyins, confidences)`` or just ``pinyins``."""
    if isinstance(value, tuple) and len(value) == 2:
        pys, confs = value
        return list(pys), list(confs) if confs is not None else None
    return list(value), None


def _load() -> G2pwPinyinResolver:
    import g2pw  # noqa: WPS433 — lazy by design

    predictor = g2pw.G2PWConverter()
    return G2pwPinyinResolver(predictor=predictor)
