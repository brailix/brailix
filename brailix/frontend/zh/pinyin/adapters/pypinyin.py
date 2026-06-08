"""pypinyin-backed pinyin resolver (lightweight fallback).

pypinyin uses dictionary lookup rather than a learned model, so it's
much smaller than g2pW but less accurate on polyphones. Recommended
for environments where the g2pW model can't be downloaded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin.adapters._align import resolve_by_char_alignment
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class PypinyinResolver:
    """Wraps :func:`pypinyin.lazy_pinyin` (with style=Style.TONE3).

    ``converter`` may be injected for testing. It receives a str and
    returns a list[str] of one syllable per character (numeric-tone).
    """

    name: str = "pypinyin"
    converter: Callable[[str], list[str]] = field(default=None)  # type: ignore[assignment]

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        if not tokens:
            return []
        sentence = "".join(t.surface for t in tokens)
        syllables = list(self.converter(sentence))
        return resolve_by_char_alignment(
            tokens,
            syllables,
            ctx,
            source="pinyin.pypinyin",
            engine="pypinyin",
        )


def _load() -> PypinyinResolver:
    import pypinyin  # noqa: WPS433 — lazy

    def converter(text: str) -> list[str]:
        return pypinyin.lazy_pinyin(text, style=pypinyin.Style.TONE3, neutral_tone_with_five=True)

    return PypinyinResolver(converter=converter)
