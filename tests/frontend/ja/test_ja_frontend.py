"""J1 Japanese frontend: kana segmenter + pure-kana end-to-end.

Covers the ja segmenter (kana_text recognition on top of the built-in
Han-aware categories), ``prose_to_inline`` (kana run -> Word, kanji ->
placeholder), and the full Pipeline on pure-kana input. Wakachigaki and
kanji readings are later phases; here a kana run is one word and kanji
degrades to a MISSING_READING placeholder.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.core.span import Span
from brailix.frontend.ja import JapaneseSegmenter, _is_kana, prose_to_inline
from brailix.ir.document import Paragraph
from brailix.ir.inline import HanziChar, Word


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
    def test_katakana_run_one_segment(self):
        assert _seg_types("コンニチハ") == [("kana_text", "コンニチハ")]

    def test_hiragana_run_one_segment(self):
        assert _seg_types("こんにちは") == [("kana_text", "こんにちは")]

    def test_kanji_is_hanzi_text(self):
        assert _seg_types("私") == [("hanzi_text", "私")]

    def test_mixed_splits_by_script(self):
        assert _seg_types("私はサクラ") == [
            ("hanzi_text", "私"),
            ("kana_text", "はサクラ"),
        ]

    def test_space_and_digit_split(self):
        assert _seg_types("サクラ 5") == [
            ("kana_text", "サクラ"),
            ("space", " "),
            ("digit_run", "5"),
        ]

    def test_long_vowel_stays_in_kana(self):
        assert _seg_types("トーキョー") == [("kana_text", "トーキョー")]

    def test_nakaguro_is_punct_not_kana(self):
        # ・ (U+30FB) is punctuation between two kana mora, not part of a run.
        assert _seg_types("ア・イ") == [
            ("kana_text", "ア"),
            ("punct", "・"),
            ("kana_text", "イ"),
        ]


class TestProseToInline:
    def test_kana_run_becomes_one_word(self):
        nodes = prose_to_inline("コンニチハ", 0)
        assert len(nodes) == 1
        assert isinstance(nodes[0], Word)
        assert nodes[0].surface == "コンニチハ"
        assert nodes[0].reading == "コンニチハ"
        assert nodes[0].span == Span(0, 5)

    def test_kanji_becomes_placeholder(self):
        nodes = prose_to_inline("私", 0)
        assert len(nodes) == 1
        assert isinstance(nodes[0], HanziChar)
        assert nodes[0].reading is None

    def test_base_offset_applied(self):
        nodes = prose_to_inline("ハ", 10)
        assert nodes[0].span == Span(10, 11)


class TestPipelineEndToEnd:
    @pytest.fixture(scope="class")
    def pipe(self):
        return Pipeline(profile="ja_current")

    def _dots(self, result):
        return [
            c.dots
            for blk in result.braille_ir.blocks
            for c in getattr(blk, "cells", [])
        ]

    def test_konnichiha(self, pipe):
        r = pipe.translate_text("コンニチハ")
        assert self._dots(r) == [
            (2, 4, 6),    # コ
            (3, 5, 6),    # ン
            (1, 2, 3),    # ニ
            (1, 2, 3, 5), # チ
            (1, 3, 6),    # ハ
        ]
        assert [w.code for w in r.warnings] == []

    def test_hiragana_same_as_katakana(self, pipe):
        assert self._dots(pipe.translate_text("こんにちは")) == self._dots(
            pipe.translate_text("コンニチハ")
        )

    def test_tokyo_long_vowel_and_youon(self, pipe):
        r = pipe.translate_text("トーキョー")
        assert self._dots(r) == [
            (2, 3, 4, 5),  # ト
            (2, 5),        # ー
            (4,), (2, 4, 6),  # キョ
            (2, 5),        # ー
        ]
        assert [w.code for w in r.warnings] == []

    def test_manual_space_preserved(self, pipe):
        # No automatic wakachigaki yet, but a typed space becomes a blank
        # cell, and dakuon/sokuon still expand.
        r = pipe.translate_text("サクラ ガッコウ")
        assert self._dots(r) == [
            (1, 5, 6),  # サ
            (1, 4, 6),  # ク
            (1, 5),     # ラ
            (),         # space
            (5,), (1, 6),  # ガ
            (2,),       # ッ
            (2, 4, 6),  # コ
            (1, 4),     # ウ
        ]

    def test_kanji_degrades_with_warning(self, pipe):
        r = pipe.translate_text("私はサクラ")
        assert "MISSING_READING" in [w.code for w in r.warnings]
        # The kana tail はサクラ still translates (は -> ハ literally; the
        # particle は -> ワ reading needs POS, which is a later phase).
        assert (1, 3, 6) in self._dots(r)  # は -> ハ
