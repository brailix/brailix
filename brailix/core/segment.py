"""The segmenter's output format — the frontend's coarse-region mediator.

:class:`Segment` is what a :class:`~brailix.core.protocols.Segmenter` emits and
a :class:`~brailix.core.protocols.Normalizer` consumes: the normalized format
the two agree on. It is **not** an IR node. It never reaches a
:class:`~brailix.ir.document.DocumentIR`, carries no entry in the inline-node
registry, and nothing downstream of the frontend ever sees one — the normalizer
promotes what it recognises into real inline IR, and the language frontend turns
the rest (``hanzi_text`` and friends) into :class:`~brailix.ir.inline.Word`
nodes. Living in :mod:`brailix.ir` made it read as a *stage* of the IR — as if
the pipeline went raw → Segment IR → inline IR → document IR — which is not the
shape of anything.

Its sibling is :class:`~brailix.frontend.zh.tokens.ChineseToken`, the mediator
between the Chinese analyzer and the pinyin resolver, and it lives apart from
both ends for the same reason: if the type were defined in the segmenter, then
replacing the segmenter would drag the normalizer with it, and the pair would
stop being independently replaceable. Where the two differ is how far out
"apart from both ends" reaches. ``ChineseToken`` gets to sit under its own
language because both its ends do; this one is named by a **core** protocol
signature (:class:`~brailix.core.protocols.Segmenter` returns a list of them),
and core may not import the frontend, so the neutral module both ends can reach
is this one.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import Any as _Any

from brailix.core.span import Span


@_dataclass(slots=True)
class Segment:
    """A coarse region produced by a :class:`~brailix.core.protocols.Segmenter`.

    The Segmenter only classifies regions by type (hanzi_text, date,
    number, math_inline, latin_text, punct, ...). Deeper analysis
    (tokenization, pinyin, math parsing) happens later in the pipeline.
    """

    type: str
    surface: str
    span: Span | None = None

    def to_dict(self) -> dict[str, _Any]:
        d: dict[str, _Any] = {"type": self.type, "surface": self.surface}
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        return d


__all__ = ("Segment",)
