"""Japanese frontend — segmenter + morphological-analysis prose builder.

The segmenter groups Japanese script (kana **and** kanji) into one
``ja_text`` run so the analyzer can resolve readings and particles across
the kana↔kanji boundary (私は → 私 = ワタシ + は particle → ワ). The actual
text→reading work is the analyzer subsystem (:mod:`.analyzer`): a registry
of adapters (janome / sudachi / fugashi) plus a dependency-free ``kana``
fallback that reads pure kana and leaves kanji unread.

The :class:`~brailix.core.protocols.LanguageFrontend` shell (``_JaFrontend``)
lives in :mod:`brailix.frontend` (alongside the Chinese one) and chains
:func:`~brailix.frontend.ja.analyzer.analyze` →
:func:`~brailix.frontend.ja.analyzer.tokens_to_inline`. Kana
classification used by both the segmenter and the ``kana`` analyzer is in
the leaf module :mod:`brailix.frontend.ja._chars` (avoids an import cycle).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from brailix.core.span import Span
from brailix.frontend.ja._chars import _is_kana, _ja_category
from brailix.frontend.ja.analyzer import analyze, tokens_to_inline
from brailix.frontend.segment import _segment_text, segmenter_registry
from brailix.ir.inline import Connector, Number, Word

if TYPE_CHECKING:
    from brailix.core.config import BrailleProfile
    from brailix.core.context import FrontendContext
    from brailix.ir.document import Block
    from brailix.ir.inline import InlineNode, Segment


@dataclass(slots=True)
class JapaneseSegmenter:
    """Segmenter that groups Japanese script (kana + kanji) into one
    ``ja_text`` run, reusing the built-in chunking for everything else."""

    name: str = "ja"

    def segment(
        self, block: Block, ctx: FrontendContext | None = None
    ) -> list[Segment]:
        text = block.text
        if not text:
            return []
        base = block.span.start if block.span is not None else 0
        return _segment_text(text, base_offset=base, categorize=_ja_category)


segmenter_registry.register("ja", JapaneseSegmenter)


# ア行 / ラ行 kana share cells with the digits (あ=⠁=1, ら=⠑=5, …), so a
# number directly followed by a word starting with one of them would read
# as a digit continuation. A 第1つなぎ符 (③⑥, the profile's connector cell)
# goes between them. Both scripts are listed so the rule fires whether the
# reading is katakana (analyzer) or hiragana (kana fallback).
_TSUNAGI_HEAD: frozenset[str] = frozenset(
    "アイウエオラリルレロあいうえおらりるれろ"
)


def _needs_tsunagi(prev: InlineNode, cur: InlineNode) -> bool:
    if not isinstance(prev, Number) or not isinstance(cur, Word):
        return False
    reading = cur.reading or ""
    return bool(reading) and reading[0] in _TSUNAGI_HEAD


def ja_boundary(
    nodes: list[InlineNode], profile: BrailleProfile
) -> list[InlineNode]:
    """Japanese boundary pass: insert a 第1つなぎ符 (a :class:`Connector`,
    rendered as the profile's connector cell ③⑥) between a number and a
    following word whose reading begins with an ア行 / ラ行 kana — keeping
    the digits distinct from the kana that share their cells. Registered as
    the ``ja`` handler in :data:`brailix.frontend.boundary_registry`.
    """
    if len(nodes) < 2:
        return nodes
    out: list[InlineNode] = [nodes[0]]
    for prev, cur in zip(nodes, nodes[1:], strict=False):
        if _needs_tsunagi(prev, cur):
            boundary = prev.span.end if prev.span else 0
            out.append(Connector(surface="", span=Span(boundary, boundary)))
        out.append(cur)
    return out


__all__ = (
    "JapaneseSegmenter",
    "analyze",
    "tokens_to_inline",
    "ja_boundary",
    "_is_kana",
)
