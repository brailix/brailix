"""janome morphological analyzer (pure-Python, bundled IPADIC).

The light, offline default. Reading is IPADIC's ``phonetic`` field (発音)
— the *pronunciation* form — so long vowels come out as ー (東京 →
トーキョー) and the topic / object particles read correctly straight from
the dictionary (は → ワ, へ → エ, を → ヲ), no special-casing needed.
``part_of_speech`` rides ``JapaneseToken.pos`` for J3 wakachigaki.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.ja.analyzer import JapaneseToken


@dataclass(slots=True)
class JanomeJapaneseAnalyzer:
    tokenizer: Any
    name: str = "janome"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        out: list[JapaneseToken] = []
        offset = 0
        for tok in self.tokenizer.tokenize(text):
            surface = tok.surface
            phonetic = tok.phonetic
            reading = phonetic if phonetic and phonetic != "*" else None
            out.append(
                JapaneseToken(
                    surface=surface,
                    reading=reading,
                    pos=tok.part_of_speech,
                    span=Span(offset, offset + len(surface)),
                )
            )
            offset += len(surface)
        return out


def _load() -> JanomeJapaneseAnalyzer:
    from janome.tokenizer import Tokenizer

    return JanomeJapaneseAnalyzer(tokenizer=Tokenizer())
