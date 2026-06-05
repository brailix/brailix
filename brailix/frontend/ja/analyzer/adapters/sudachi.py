"""SudachiPy morphological analyzer.

Reading is ``reading_form()`` — the dictionary 読み (katakana). NOTE this
is the *reading*, not the pronunciation form: long vowels stay as spelled
(東京 → トウキョウ, not トーキョー), so the braille long-vowel mark isn't
applied. For accurate 発音形 long vowels prefer ``janome`` or ``fugashi``;
this adapter is here for environments already using Sudachi. POS rides
``JapaneseToken.pos``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.ja.analyzer import JapaneseToken


@dataclass(slots=True)
class SudachiJapaneseAnalyzer:
    tokenizer: Any
    mode: Any
    name: str = "sudachi"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        out: list[JapaneseToken] = []
        for m in self.tokenizer.tokenize(text, self.mode):
            reading = m.reading_form() or None
            out.append(
                JapaneseToken(
                    surface=m.surface(),
                    reading=reading,
                    pos=",".join(m.part_of_speech()),
                    span=Span(m.begin(), m.end()),
                )
            )
        return out


def _load() -> SudachiJapaneseAnalyzer:
    from sudachipy import dictionary, tokenizer

    return SudachiJapaneseAnalyzer(
        tokenizer=dictionary.Dictionary().create(),
        mode=tokenizer.Tokenizer.SplitMode.C,
    )
