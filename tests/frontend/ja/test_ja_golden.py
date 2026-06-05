"""End-to-end golden examples for the Japanese chain (J0–J4).

Each case locks a representative sentence to its exact braille, so the
whole pipeline — segment → analyze → readings → wakachigaki → cells →
punctuation → つなぎ符 — is regression-guarded as one unit. Pure-kana
cases pin ``analyzer="kana"`` (deterministic, no dependency); kanji cases
use janome (importorskip) for readings + word-spacing.

Re-verify these against 日本点字表記法 2018 if a rule changes; a diff here
is a human-readable record of exactly what moved.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline

# (source, expected Unicode braille). Pure kana — no analyzer needed.
_KANA_GOLDEN = [
    ("コンニチハ", "⠪⠴⠇⠗⠥"),                 # seion run
    ("トーキョー", "⠞⠒⠈⠪⠒"),                 # 長音 + youon
    ("ガッコウ", "⠐⠡⠂⠪⠉"),                   # dakuon + 促音
    ("サクラ ガッコウ", "⠱⠩⠑⠀⠐⠡⠂⠪⠉"),       # a manual space is preserved
    ("ア。", "⠁⠲⠀"),                          # 句点 + trailing space
    ("5エン", "⠼⠑⠤⠋⠴"),                      # number + つなぎ符 + ア-row word
]

# (source, expected). Kanji readings + wakachigaki — needs a real analyzer.
_JANOME_GOLDEN = [
    ("東京", "⠞⠒⠈⠪⠒"),                       # 発音形 長音 (トーキョー)
    ("私は本を読む", "⠄⠕⠳⠄⠀⠮⠴⠔⠀⠜⠽"),       # は→ワ, を→ヲ, bunsetsu spaces
    ("学校へ行く", "⠐⠡⠂⠪⠒⠋⠀⠃⠩"),           # へ→エ, 長音, one space
    ("これは？", "⠪⠛⠄⠢⠀"),                   # は→ワ + 疑問符
    ("5円", "⠼⠑⠤⠋⠴"),                        # 円→エン, つなぎ符
]


@pytest.fixture(scope="module")
def kana_pipe():
    return Pipeline(profile="ja_current", analyzer="kana")


@pytest.fixture(scope="module")
def janome_pipe():
    pytest.importorskip("janome")
    return Pipeline(profile="ja_current", analyzer="janome")


@pytest.mark.parametrize(
    "src,expected", _KANA_GOLDEN, ids=[s for s, _ in _KANA_GOLDEN]
)
def test_kana_golden(kana_pipe, src, expected):
    assert kana_pipe.translate_text(src).render() == expected


@pytest.mark.parametrize(
    "src,expected", _JANOME_GOLDEN, ids=[s for s, _ in _JANOME_GOLDEN]
)
def test_janome_golden(janome_pipe, src, expected):
    assert janome_pipe.translate_text(src).render() == expected
