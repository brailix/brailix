"""Japanese segmenter + the dependency-free (kana) end-to-end path.

The segmenter groups Japanese script (kana + kanji) into one ``ja_text``
run. The end-to-end tests here pin ``analyzer="kana"`` so they exercise
the deterministic, dependency-free fallback regardless of whether a real
morphological analyzer is installed; the analyzer path (kanji readings,
は→ワ) is covered in ``test_ja_analyzer.py``.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.frontend.ja import JapaneseSegmenter, _is_kana
from brailix.ir.document import Paragraph


def _seg_types(text: str) -> list[tuple[str, str]]:
    segs = JapaneseSegmenter().segment(Paragraph(text=text))
    return [(s.type, s.surface) for s in segs]


class TestIsKana:
    def test_kana(self):
        assert _is_kana("あ") and _is_kana("ア") and _is_kana("ー") and _is_kana("ッ")

    def test_not_kana(self):
        for ch in ("私", "・", "A", "5", "、", " "):
            assert not _is_kana(ch)


class TestSegmenter:
    def test_kana_run_is_ja_text(self):
        assert _seg_types("コンニチハ") == [("ja_text", "コンニチハ")]

    def test_kanji_is_ja_text(self):
        assert _seg_types("私") == [("ja_text", "私")]

    def test_kana_and_kanji_merge_into_one_run(self):
        # The analyzer needs 私 + は together (私=ワタシ, は particle -> ワ).
        assert _seg_types("私はサクラ") == [("ja_text", "私はサクラ")]

    def test_space_and_digit_split_the_run(self):
        assert _seg_types("サクラ 5") == [
            ("ja_text", "サクラ"),
            ("space", " "),
            ("digit_run", "5"),
        ]

    def test_long_vowel_stays_in_run(self):
        assert _seg_types("トーキョー") == [("ja_text", "トーキョー")]

    def test_nakaguro_is_punct(self):
        # ・ (U+30FB) is punctuation, so it breaks the run.
        assert _seg_types("ア・イ") == [
            ("ja_text", "ア"),
            ("punct", "・"),
            ("ja_text", "イ"),
        ]


class TestKanaFallbackEndToEnd:
    """analyzer='kana' — the deterministic dependency-free path."""

    @pytest.fixture(scope="class")
    def pipe(self):
        return Pipeline(profile="ja_current", analyzer="kana")

    def _dots(self, result):
        return [
            c.dots
            for blk in result.braille_ir.blocks
            for c in getattr(blk, "cells", [])
        ]

    def test_konnichiha(self, pipe):
        r = pipe.translate_text("コンニチハ")
        assert self._dots(r) == [
            (2, 4, 6), (3, 5, 6), (1, 2, 3), (1, 2, 3, 5), (1, 3, 6)
        ]
        assert [w.code for w in r.warnings] == []

    def test_hiragana_same_as_katakana(self, pipe):
        # The kana fallback reads kana literally, so hiragana and katakana
        # of the same syllables produce identical cells.
        assert self._dots(pipe.translate_text("こんにちは")) == self._dots(
            pipe.translate_text("コンニチハ")
        )

    def test_tokyo_long_vowel_and_youon(self, pipe):
        r = pipe.translate_text("トーキョー")
        assert self._dots(r) == [(2, 3, 4, 5), (2, 5), (4,), (2, 4, 6), (2, 5)]

    def test_manual_space_preserved(self, pipe):
        r = pipe.translate_text("サクラ ガッコウ")
        assert self._dots(r) == [
            (1, 5, 6), (1, 4, 6), (1, 5),       # サクラ
            (),                                 # space
            (5,), (1, 6), (2,), (2, 4, 6), (1, 4),  # ガッコウ
        ]

    def test_kanji_degrades_with_warning(self, pipe):
        # No analyzer -> kanji can't be read; the kana tail still translates
        # (は stays ハ — particle conversion needs the analyzer).
        r = pipe.translate_text("私はサクラ")
        assert "MISSING_READING" in [w.code for w in r.warnings]
        assert (1, 3, 6) in self._dots(r)  # は -> ハ
