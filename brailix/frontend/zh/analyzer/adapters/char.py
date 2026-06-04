"""Fallback Chinese analyzer: one token per character.

This adapter has zero third-party dependencies, so the pipeline can
always produce *some* tokenization even when no real tokenizer is
installed. The output is correct (every Chinese character becomes a
single-character token) but coarse — multi-character words are not
recognized, which means downstream pinyin disambiguation for
context-sensitive readings (e.g. 重庆 vs 重新) will be wrong.

Use this only as a fallback for demos and tests; production pipelines
should select ``hanlp`` or another real tokenizer.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class CharChineseAnalyzer:
    name: str = "char"

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        if not text:
            return []
        tokens: list[ChineseToken] = []
        for i, ch in enumerate(text):
            tokens.append(
                ChineseToken(
                    surface=ch,
                    pos=None,
                    span=Span(i, i + 1),
                )
            )
        return tokens


def _load() -> CharChineseAnalyzer:
    return CharChineseAnalyzer()
