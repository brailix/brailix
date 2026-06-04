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

        # g2pW promises one pinyin per character; any length divergence
        # means it skipped (or duplicated) a position. Slicing past that
        # point would misalign every later token, so bail out cleanly.
        if len(pinyins) != len(sentence) or (
            confidences is not None and len(confidences) != len(sentence)
        ):
            if ctx is not None:
                ctx.warnings.warn(
                    code="PINYIN_LENGTH_MISMATCH",
                    message=(
                        f"g2pW returned {len(pinyins)} pinyins for "
                        f"{len(sentence)}-char input; dropping pinyin"
                    ),
                    surface=sentence,
                    source="pinyin.g2pw",
                )
            return [
                ChineseToken(
                    surface=tok.surface,
                    pos=tok.pos,
                    span=tok.span,
                    pinyin=None,
                    confidence=None,
                )
                for tok in tokens
            ]

        out: list[ChineseToken] = []
        char_cursor = 0
        for tok in tokens:
            length = len(tok.surface)
            token_pinyins = pinyins[char_cursor : char_cursor + length]
            token_confs = (
                confidences[char_cursor : char_cursor + length]
                if confidences is not None
                else None
            )
            pinyin_str = " ".join(p for p in token_pinyins if p)
            new = ChineseToken(
                surface=tok.surface,
                pos=tok.pos,
                span=tok.span,
                pinyin=pinyin_str or None,
                confidence=(
                    min(token_confs) if token_confs else None
                ),
            )
            if (
                ctx is not None
                and token_confs
                and min(token_confs) < self.low_confidence_threshold
            ):
                ctx.warnings.warn(
                    code="LOW_CONFIDENCE_PINYIN",
                    message=f"polyphone reading has low confidence: {tok.surface}",
                    surface=tok.surface,
                    span=tok.span,
                    source="pinyin.g2pw",
                )
            out.append(new)
            char_cursor += length
        return out


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
