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

from brailix.frontend.ja._chars import _is_kana, _ja_category
from brailix.frontend.ja.analyzer import analyze, tokens_to_inline
from brailix.frontend.segment import _segment_text, segmenter_registry

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext
    from brailix.ir.document import Block
    from brailix.ir.inline import Segment


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

__all__ = ("JapaneseSegmenter", "analyze", "tokens_to_inline", "_is_kana")
