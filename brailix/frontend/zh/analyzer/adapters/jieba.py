"""jieba-backed Chinese analyzer adapter.

jieba is small (pure-python with C accel), fast to import, and has
no model downloads — making it the natural "first real tokenizer"
for users who don't want to pull in HanLP. POS tags aren't emitted
here: jieba's POS module (``jieba.posseg``) is a separate path and
not all installs have it. Resolvers downstream don't need POS for
the pypinyin path; if you want POS, switch to the HanLP adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class JiebaChineseAnalyzer:
    """Wraps :func:`jieba.tokenize`.

    ``tokenize_fn`` is injectable for tests so we don't have to install
    jieba just to verify the conversion logic. The real one is plugged
    in by :func:`_load`.
    """

    name: str = "jieba"
    tokenize_fn: Callable[[str], Any] = field(default=None)  # type: ignore[assignment]

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        if not text:
            return []
        out: list[ChineseToken] = []
        for word, start, end in self.tokenize_fn(text):
            out.append(
                ChineseToken(
                    surface=word,
                    pos=None,
                    span=Span(start, end),
                )
            )
        return out


def _load() -> JiebaChineseAnalyzer:
    """Lazy-import jieba and build a configured analyzer."""
    import jieba  # noqa: WPS433 — lazy by design

    # ``jieba.tokenize`` yields (word, start, end) triples with HMM
    # enabled by default; that's the right precision/recall balance
    # for prose. Users who want strict dictionary-only tokenization
    # can register their own adapter.
    def tokenize_fn(text: str) -> Any:
        return jieba.tokenize(text)

    return JiebaChineseAnalyzer(tokenize_fn=tokenize_fn)
