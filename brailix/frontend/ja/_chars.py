"""Japanese character classification — a leaf module shared by the
segmenter and the dependency-free ``kana`` analyzer.

Kept separate from :mod:`brailix.frontend.ja` so the analyzer adapters
can reuse :func:`_is_kana` without an import cycle (``frontend.ja``
re-exports the analyzer, which would otherwise import back into it).
"""

from __future__ import annotations

from brailix.frontend.segment import _category


def _is_kana(ch: str) -> bool:
    """True for a syllabic kana character (hiragana / katakana + ー).

    Excludes the katakana middle dot ・ (U+30FB) and iteration marks —
    those are punctuation, not mora.
    """
    cp = ord(ch)
    return (
        0x3041 <= cp <= 0x3096       # hiragana ぁ-ゖ
        or 0x30A1 <= cp <= 0x30FA    # katakana ァ-ヺ
        or cp == 0x30FC              # ー prolonged sound mark
    )


def _ja_category(ch: str) -> str:
    """Japanese segment category.

    Kana **and** kanji both fold into one ``ja_text`` run: the
    morphological analyzer needs them together to resolve readings and
    particles across the kana↔kanji boundary (私は → 私=ワタシ + は
    particle → ワ). Splitting kana_text / hanzi_text would break that.
    Other characters use the built-in Han-aware classifier (digit /
    latin / greek / punct / space / math_op).
    """
    if _is_kana(ch):
        return "ja_text"
    cat = _category(ch)
    if cat == "hanzi_text":
        return "ja_text"
    return cat
