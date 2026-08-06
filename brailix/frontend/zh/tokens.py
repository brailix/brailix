"""The Chinese frontend's intermediate token type.

:class:`ChineseToken` is what the analyzer emits and the pinyin resolver
annotates — the normalized format the two Chinese subsystems agree on. It is
**not** an IR node: it never reaches a :class:`~brailix.ir.document.DocumentIR`
and carries no entry in the inline-node registry. ``tokens_to_inline`` is what
converts a finished token stream into real IR (:class:`~brailix.ir.inline.Word`
nodes).

It lives in its own module, rather than beside either subsystem, because the
two must stay swap-independent (see :mod:`brailix.frontend.zh`): if the token
were defined in the analyzer, replacing the analyzer would drag the resolver
with it, and the pair would no longer be independently replaceable. A shared
mediator format belongs to neither end — the same reasoning that keeps the
library's other normalized formats out of their adapters.

Japanese keeps its equivalent (``JapaneseToken``) inside its analyzer package
because it has only one subsystem, so there is no second consumer to stay
independent of. What both languages share is the rule that a language's own
types live under that language, not on a shared layer.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import Any as _Any

from brailix.core.span import Span


@_dataclass(slots=True)
class ChineseToken:
    """A single token emitted by a
    :class:`~brailix.frontend.zh.analyzer.ChineseAnalyzer`.

    The ``pinyin`` field is initially ``None`` and filled in by a
    :class:`~brailix.frontend.zh.pinyin.PinyinResolver`. The resolver
    must not change the token's surface or span — checked at
    :func:`brailix.frontend.zh.pinyin.annotate`, which compares the tokens
    coming back against the ones it handed over.
    """

    surface: str
    pos: str | None = None
    span: Span | None = None
    pinyin: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, _Any]:
        d: dict[str, _Any] = {"surface": self.surface}
        if self.pos is not None:
            d["pos"] = self.pos
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        if self.pinyin is not None:
            d["pinyin"] = self.pinyin
        if self.confidence is not None:
            d["confidence"] = self.confidence
        return d


__all__ = ("ChineseToken",)
