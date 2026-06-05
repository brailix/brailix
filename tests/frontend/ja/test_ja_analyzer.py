"""J2 Japanese morphological-analysis subsystem.

* ``tokens_to_inline`` — pure logic, tested with canned tokens (no
  analyzer dependency).
* the dependency-free ``kana`` analyzer.
* ``janome`` — the minimal real engine (in the dev group, so this runs in
  CI): kanji readings, particle は→ワ / を→ヲ via the pronunciation form,
  and 長音 (東京→トーキョー).
* ``sudachi`` / ``fugashi`` — guarded by importorskip; they run only where
  the (heavier) engine + dictionary is installed.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.core.span import Span
from brailix.frontend.ja.analyzer import JapaneseToken, tokens_to_inline
from brailix.frontend.ja.analyzer.registry import analyzer_registry
from brailix.ir.inline import HanziChar, Word


class TestTokensToInline:
    def test_reading_token_becomes_word(self):
        nodes = tokens_to_inline(
            [JapaneseToken("東京", "トーキョー", "名詞", Span(0, 2))]
        )
        assert len(nodes) == 1
        assert isinstance(nodes[0], Word)
        assert nodes[0].surface == "東京"
        assert nodes[0].reading == "トーキョー"
        assert nodes[0].pos == "名詞"
        assert nodes[0].span == Span(0, 2)

    def test_kanji_without_reading_becomes_placeholder(self):
        nodes = tokens_to_inline([JapaneseToken("私", None, None, Span(0, 1))])
        assert len(nodes) == 1
        assert isinstance(nodes[0], HanziChar)
        assert nodes[0].reading is None

    def test_multi_kanji_placeholder_per_char(self):
        nodes = tokens_to_inline([JapaneseToken("漢字", None, None, Span(0, 2))])
        assert [type(n) for n in nodes] == [HanziChar, HanziChar]
        assert nodes[0].span == Span(0, 1)
        assert nodes[1].span == Span(1, 2)

    def test_all_kana_without_reading_falls_back_to_kana(self):
        # An unknown katakana word (janome returns phonetic "*") is its own
        # pronunciation — it must become a Word, not a placeholder.
        nodes = tokens_to_inline(
            [JapaneseToken("コンニチハ", None, None, Span(0, 5))]
        )
        assert len(nodes) == 1
        assert isinstance(nodes[0], Word)
        assert nodes[0].reading == "コンニチハ"

    def test_base_offset_shifts_spans(self):
        nodes = tokens_to_inline(
            [JapaneseToken("ア", "ア", None, Span(0, 1))], base=10
        )
        assert nodes[0].span == Span(10, 11)


class TestKanaAnalyzer:
    def test_splits_kanji_and_kana_run(self):
        ana = analyzer_registry.get("kana")
        toks = ana.analyze("私はサクラ")
        assert [(t.surface, t.reading) for t in toks] == [
            ("私", None),               # kanji — unread
            ("はサクラ", "はサクラ"),    # kana run — reads as itself
        ]

    def test_pure_kana_is_one_token(self):
        ana = analyzer_registry.get("kana")
        toks = ana.analyze("コンニチハ")
        assert [(t.surface, t.reading) for t in toks] == [
            ("コンニチハ", "コンニチハ")
        ]


class TestJanome:
    """janome is in the dev group, so these run in CI."""

    def test_kanji_reading_and_particles(self):
        pytest.importorskip("janome")
        ana = analyzer_registry.get("janome")
        toks = ana.analyze("私は本を読む")
        readings = {t.surface: t.reading for t in toks}
        assert readings["私"] == "ワタシ"
        assert readings["は"] == "ワ"   # topic particle は -> ワ (発音形)
        assert readings["を"] == "ヲ"   # object particle を -> ヲ
        pos = {t.surface: t.pos for t in toks}
        assert pos["は"].startswith("助詞")  # POS available for J3

    def test_long_vowel_pronunciation_form(self):
        pytest.importorskip("janome")
        ana = analyzer_registry.get("janome")
        toks = ana.analyze("東京")
        assert "".join(t.reading or "" for t in toks) == "トーキョー"

    def test_pipeline_end_to_end(self):
        pytest.importorskip("janome")
        pipe = Pipeline(profile="ja_current", analyzer="janome")
        r = pipe.translate_text("東京")
        dots = [
            c.dots
            for blk in r.braille_ir.blocks
            for c in getattr(blk, "cells", [])
        ]
        assert dots == [(2, 3, 4, 5), (2, 5), (4,), (2, 4, 6), (2, 5)]  # トーキョー
        assert [w.code for w in r.warnings] == []


class TestSudachi:
    def test_reads_kanji(self):
        pytest.importorskip("sudachipy")
        pytest.importorskip("sudachidict_core")
        ana = analyzer_registry.get("sudachi")
        toks = ana.analyze("私")
        assert any(t.reading == "ワタシ" for t in toks)


class TestFugashi:
    def test_reads_kanji(self):
        pytest.importorskip("fugashi")
        pytest.importorskip("unidic_lite")
        ana = analyzer_registry.get("fugashi")
        toks = ana.analyze("東京")
        # UniDic pron gives the 発音形 (長音); assert it at least read it.
        assert any(t.reading and "キョ" in t.reading for t in toks)
