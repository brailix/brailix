"""J4: Japanese punctuation + the number つなぎ符 (第1つなぎ符).

Exercised with analyzer='kana' so they're deterministic without a
morphological analyzer (punctuation and the number-boundary rule are both
analyzer-independent). The number → ア/ラ-row joiner uses katakana inputs
directly so no kanji reading is involved.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.frontend import boundary_registry


def _dots(result):
    return [
        c.dots
        for blk in result.braille_ir.blocks
        for c in getattr(blk, "cells", [])
    ]


@pytest.fixture(scope="module")
def pipe():
    return Pipeline(profile="ja_current", analyzer="kana")


class TestPunctuation:
    def test_period(self, pipe):
        assert (2, 5, 6) in _dots(pipe.translate_text("ア。"))

    def test_comma(self, pipe):
        assert (5, 6) in _dots(pipe.translate_text("ア、"))

    def test_question(self, pipe):
        assert (2, 6) in _dots(pipe.translate_text("ア？"))

    def test_exclamation(self, pipe):
        assert (2, 3, 5) in _dots(pipe.translate_text("ア！"))

    def test_no_unknown_punct_warning(self, pipe):
        r = pipe.translate_text("ア。イ、ウ？エ！")
        assert "UNKNOWN_PUNCT" not in [w.code for w in r.warnings]


class TestTsunagi:
    def test_tsunagi_before_a_row(self, pipe):
        # 5エン -> 数符 · 5 · つなぎ符③⑥ · エ · ン
        assert _dots(pipe.translate_text("5エン")) == [
            (3, 4, 5, 6), (1, 5), (3, 6), (1, 2, 4), (3, 5, 6)
        ]

    def test_tsunagi_before_ra_row(self, pipe):
        assert (3, 6) in _dots(pipe.translate_text("3ラク"))

    def test_no_tsunagi_before_ka_row(self, pipe):
        assert (3, 6) not in _dots(pipe.translate_text("5カイ"))

    def test_no_tsunagi_before_na_row(self, pipe):
        assert (3, 6) not in _dots(pipe.translate_text("5ニン"))


class TestBoundaryRegistry:
    def test_ja_and_zh_registered(self):
        assert "ja" in boundary_registry
        assert "zh" in boundary_registry
