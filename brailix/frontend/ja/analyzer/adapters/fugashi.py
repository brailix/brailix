"""fugashi (MeCab) morphological analyzer.

With UniDic (``unidic-lite`` / ``unidic``) the reading is the ``pron``
feature — the pronunciation form (発音形) — so long vowels come out as ー
and particles read correctly, the same quality as janome's phonetic.
Falls back to the ``kana`` / ``reading`` feature when ``pron`` is absent
(e.g. an IPADIC-style dictionary). ``pos1`` rides ``JapaneseToken.pos``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.ja.analyzer import JapaneseToken


def _feature(word: Any, *names: str) -> str | None:
    feat = word.feature
    for name in names:
        val = getattr(feat, name, None)
        if val and val != "*":
            return val
    return None


@dataclass(slots=True)
class FugashiJapaneseAnalyzer:
    tagger: Any
    name: str = "fugashi"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        out: list[JapaneseToken] = []
        cursor = 0
        for word in self.tagger(text):
            surface = word.surface
            # MeCab drops inter-token whitespace, so a running length sum
            # drifts from the real source offsets. Re-locate each surface
            # from the cursor (find skips the dropped gap).
            start = text.find(surface, cursor)
            if start < 0:
                start = cursor
            cursor = start + len(surface)
            out.append(
                JapaneseToken(
                    surface=surface,
                    reading=_feature(word, "pron", "kana", "reading"),
                    pos=_feature(word, "pos1"),
                    span=Span(start, cursor),
                )
            )
        return out


def _load() -> FugashiJapaneseAnalyzer:
    import fugashi

    return FugashiJapaneseAnalyzer(tagger=fugashi.Tagger())
