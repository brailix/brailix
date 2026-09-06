"""janome morphological analyzer (pure-Python, bundled IPADIC).

The light, offline default. Reading is IPADIC's ``phonetic`` field (発音)
— the *pronunciation* form — so long vowels come out as ー (東京 →
トーキョー) and the topic / object particles read correctly straight from
the dictionary (は → ワ, へ → エ, を → ヲ), no special-casing needed.
``part_of_speech`` rides ``JapaneseToken.pos`` for word-spacing.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import FrontendContext
from brailix.frontend.ja.analyzer import JapaneseToken
from brailix.frontend.ja.analyzer.adapters._spans import recover_span

if _TYPE_CHECKING:
    from typing import Any


@_dataclass(slots=True)
class JanomeJapaneseAnalyzer:
    tokenizer: Any
    name: str = "janome"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        out: list[JapaneseToken] = []
        cursor = 0
        for tok in self.tokenizer.tokenize(text):
            surface = tok.surface
            phonetic = tok.phonetic
            reading = phonetic if phonetic and phonetic != "*" else None
            # janome drops whitespace between tokens, so spans come from
            # the shared cursor recovery (see adapters._spans).
            span, cursor = recover_span(text, surface, cursor)
            out.append(
                JapaneseToken(
                    surface=surface,
                    reading=reading,
                    pos=tok.part_of_speech,
                    span=span,
                )
            )
        return out


def _load() -> JanomeJapaneseAnalyzer:
    from janome.tokenizer import Tokenizer

    return JanomeJapaneseAnalyzer(tokenizer=Tokenizer())
