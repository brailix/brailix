"""pypinyin-backed pinyin resolver (lightweight fallback).

pypinyin uses dictionary lookup rather than a learned model, so it's
much smaller than g2pW but less accurate on polyphones. Recommended
for environments where the g2pW model can't be downloaded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
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

        # pypinyin returns one syllable per character; mismatch means it
        # silently dropped (or merged) something — without this guard the
        # by-cursor slicing below would smear every subsequent token's
        # pinyin one position over.
        if len(syllables) != len(sentence):
            if ctx is not None:
                ctx.warnings.warn(
                    code="PINYIN_LENGTH_MISMATCH",
                    message=(
                        f"pypinyin returned {len(syllables)} syllables for "
                        f"{len(sentence)}-char input; dropping pinyin"
                    ),
                    surface=sentence,
                    source="pinyin.pypinyin",
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
            chunk = syllables[char_cursor : char_cursor + length]
            pinyin_str = " ".join(s for s in chunk if s) or None
            out.append(
                ChineseToken(
                    surface=tok.surface,
                    pos=tok.pos,
                    span=tok.span,
                    pinyin=pinyin_str,
                    confidence=None,
                )
            )
            char_cursor += length
        return out


def _load() -> PypinyinResolver:
    import pypinyin  # noqa: WPS433 — lazy

    def converter(text: str) -> list[str]:
        return pypinyin.lazy_pinyin(text, style=pypinyin.Style.TONE3, neutral_tone_with_five=True)

    return PypinyinResolver(converter=converter)
