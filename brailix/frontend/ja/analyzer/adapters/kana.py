"""Dependency-free Japanese analyzer: kana read as-is, kanji left unread.

Zero third-party dependencies, so the ja pipeline always produces *some*
output even with no morphological analyzer installed. A maximal kana run
becomes one token whose reading is the kana itself (the backend tolerates
hiragana); a kanji becomes a reading-less token (a ``MISSING_READING``
placeholder downstream). It can't read kanji and does no particle
conversion (は stays ハ) — install janome / fugashi / sudachi for that.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.ja._chars import _is_kana
from brailix.frontend.ja.analyzer import JapaneseToken


@dataclass(slots=True)
class KanaJapaneseAnalyzer:
    name: str = "kana"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]:
        out: list[JapaneseToken] = []
        i, n = 0, len(text)
        while i < n:
            if _is_kana(text[i]):
                j = i
                while j < n and _is_kana(text[j]):
                    j += 1
                out.append(
                    JapaneseToken(
                        surface=text[i:j], reading=text[i:j], span=Span(i, j)
                    )
                )
                i = j
            else:
                out.append(
                    JapaneseToken(
                        surface=text[i], reading=None, span=Span(i, i + 1)
                    )
                )
                i += 1
        return out


def _load() -> KanaJapaneseAnalyzer:
    return KanaJapaneseAnalyzer()
