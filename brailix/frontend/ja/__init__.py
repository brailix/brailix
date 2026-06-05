"""Japanese frontend — kana segmenter + pure-kana prose builder (J1).

J1 scope: recognise kana (hiragana / katakana) as ``kana_text`` runs and
turn them into prose IR whose ``reading`` is the kana itself, so
:mod:`brailix.backend.ja` translates them to cells. Kanji reaches the
frontend as ``hanzi_text``; with no reading source yet it degrades to
``reading=None`` placeholders (the backend warns ``MISSING_READING``).
Morphological analysis for kanji readings (J2) and wakachigaki
word-spacing (J3) come later; spaces in the source are preserved as-is.

The segmenter reuses :mod:`brailix.frontend.segment`'s chunking with a
Japanese category function (kana -> ``kana_text``; everything else via
the built-in Han-aware classifier, so kanji -> ``hanzi_text``). The
matching :class:`~brailix.core.protocols.LanguageFrontend` (``_JaFrontend``)
is defined and registered in :mod:`brailix.frontend`, alongside the
Chinese one; it delegates the per-segment work to :func:`prose_to_inline`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from brailix.core.span import Span
from brailix.frontend.segment import _category, _segment_text, segmenter_registry
from brailix.ir.inline import HanziChar, InlineNode, Word

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext
    from brailix.ir.document import Block
    from brailix.ir.inline import Segment


def _is_kana(ch: str) -> bool:
    """True for a syllabic kana character (hiragana / katakana + ー).

    Deliberately excludes the katakana middle dot ・ (U+30FB) and the
    iteration marks — those are punctuation, not mora.
    """
    cp = ord(ch)
    return (
        0x3041 <= cp <= 0x3096       # hiragana ぁ-ゖ
        or 0x30A1 <= cp <= 0x30FA    # katakana ァ-ヺ
        or cp == 0x30FC              # ー prolonged sound mark
    )


def _ja_category(ch: str) -> str:
    """Japanese character category: kana -> ``kana_text``; everything else
    via the built-in classifier (kanji -> ``hanzi_text``, plus digit /
    latin / greek / punct / space / math_op)."""
    if _is_kana(ch):
        return "kana_text"
    return _category(ch)


@dataclass(slots=True)
class JapaneseSegmenter:
    """Segmenter that recognises kana runs (``kana_text``) on top of the
    built-in Han-aware categories."""

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


def prose_to_inline(surface: str, base: int) -> list[InlineNode]:
    """Build inline IR for one Japanese prose run (J1, pure kana).

    A maximal run of kana becomes one :class:`Word` whose ``reading`` is
    the kana itself (the backend reads it as the pronunciation form, and
    tolerates hiragana). A non-kana character — kanji, reaching here via a
    ``hanzi_text`` segment — becomes a :class:`HanziChar` with
    ``reading=None``; the backend emits a ``MISSING_READING`` placeholder
    until J2 supplies kanji readings. No wakachigaki: a kana run is one
    word regardless of its internal structure (automatic spacing is J3).
    """
    out: list[InlineNode] = []
    i, n = 0, len(surface)
    while i < n:
        if _is_kana(surface[i]):
            j = i
            while j < n and _is_kana(surface[j]):
                j += 1
            run = surface[i:j]
            out.append(Word(surface=run, reading=run, span=Span(base + i, base + j)))
            i = j
        else:
            out.append(
                HanziChar(
                    surface=surface[i],
                    reading=None,
                    span=Span(base + i, base + i + 1),
                )
            )
            i += 1
    return out
