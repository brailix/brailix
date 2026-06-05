"""Japanese kana-braille backend.

Three things are covered:

* the kana table itself, re-derived here from the gojuon construction
  rules *independently* of the resource generator, so a typo in either
  ``resources/ja/current/kana.json`` or the loader fails loudly;
* ``backend.ja`` mora translation (seion / dakuon / handakuon / youon /
  long-vowel / sokuon / hatsuon, hiragana tolerance, soft-failure);
* the worked examples from the primer (``A42025.pdf``).

Readings are katakana pronunciation forms — the ja frontend will fill
them in a later phase; here we feed them directly, the way the
``backend.zh`` tests feed pinyin.
"""

from __future__ import annotations

import unicodedata

import pytest

from brailix.backend.dispatch import translate_node
from brailix.backend.ja import translate_hanzi_char, translate_word
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import HanziChar, Word


@pytest.fixture(scope="module")
def profile():
    return load_profile("ja_current")


@pytest.fixture
def ctx():
    return BackendContext()


def _dots(cells):
    return [c.dots for c in cells]


def _roles(cells):
    return [c.role for c in cells]


def _seq(ctx, profile, reading):
    cells = translate_word(
        Word(surface=reading, reading=reading, span=Span(0, len(reading))),
        ctx, profile,
    )
    return _dots(cells)


# ---------------------------------------------------------------------------
# Independent re-derivation of the kana table (guards the resource + loader)
# ---------------------------------------------------------------------------

_A, _I, _U, _E, _O = (1,), (1, 2), (1, 4), (1, 2, 4), (2, 4)
_VOWELS = (_A, _I, _U, _E, _O)
_ROWS = (
    (("ア", "イ", "ウ", "エ", "オ"), ()),
    (("カ", "キ", "ク", "ケ", "コ"), (6,)),
    (("サ", "シ", "ス", "セ", "ソ"), (5, 6)),
    (("タ", "チ", "ツ", "テ", "ト"), (3, 5)),
    (("ナ", "ニ", "ヌ", "ネ", "ノ"), (3,)),
    (("ハ", "ヒ", "フ", "ヘ", "ホ"), (3, 6)),
    (("マ", "ミ", "ム", "メ", "モ"), (3, 5, 6)),
    (("ラ", "リ", "ル", "レ", "ロ"), (5,)),
)
_SMALLS = ("ャ", "ュ", "ョ")


def _seion():
    s = {}
    for row, add in _ROWS:
        for kana, v in zip(row, _VOWELS, strict=True):
            s[kana] = tuple(sorted(set(v) | set(add)))
    s["ヤ"], s["ユ"], s["ヨ"] = (3, 4), (3, 4, 6), (3, 4, 5)
    s["ワ"], s["ヲ"], s["ン"] = (3,), (3, 5), (3, 5, 6)
    s["ー"], s["ッ"] = (2, 5), (2,)
    return s


def _expected_kana():
    s = _seion()
    table = {k: (v,) for k, v in s.items()}
    for base in "カキクケコサシスセソタチツテトハヒフヘホウ":
        table[unicodedata.normalize("NFC", base + "゙")] = ((5,), s[base])
    for base in "ハヒフヘホ":
        table[unicodedata.normalize("NFC", base + "゚")] = ((6,), s[base])
    youon = {
        "キ": ("カ", "ク", "コ"), "シ": ("サ", "ス", "ソ"),
        "チ": ("タ", "ツ", "ト"), "ニ": ("ナ", "ヌ", "ノ"),
        "ヒ": ("ハ", "フ", "ホ"), "ミ": ("マ", "ム", "モ"),
        "リ": ("ラ", "ル", "ロ"),
    }
    for ik, bases in youon.items():
        for sm, bk in zip(_SMALLS, bases, strict=True):
            table[ik + sm] = ((4,), s[bk])
    youon_daku = {
        "ギ": ("カ", "ク", "コ"), "ジ": ("サ", "ス", "ソ"),
        "ヂ": ("タ", "ツ", "ト"), "ビ": ("ハ", "フ", "ホ"),
    }
    for ik, bases in youon_daku.items():
        for sm, bk in zip(_SMALLS, bases, strict=True):
            table[ik + sm] = ((4, 5), s[bk])
    for sm, bk in zip(_SMALLS, ("ハ", "フ", "ホ"), strict=True):
        table["ピ" + sm] = ((4, 6), s[bk])
    return table


class TestKanaTable:
    def test_matches_independent_derivation(self, profile):
        assert profile.lang_table("kana") == _expected_kana()

    def test_mora_count(self, profile):
        assert len(profile.lang_table("kana")) == 110


# ---------------------------------------------------------------------------
# Backend mora translation
# ---------------------------------------------------------------------------

class TestSeion:
    def test_vowel_a(self, ctx, profile):
        cells = translate_word(
            Word(surface="ア", reading="ア", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(1,)]
        assert _roles(cells) == ["ja_kana"]
        assert cells[0].source_text == "ア"

    def test_ka(self, ctx, profile):
        assert _seq(ctx, profile, "カ") == [(1, 6)]

    def test_ra(self, ctx, profile):
        assert _seq(ctx, profile, "ラ") == [(1, 5)]


class TestTwoCellMora:
    def test_dakuon_ga(self, ctx, profile):
        cells = translate_word(
            Word(surface="ガ", reading="ガ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(5,), (1, 6)]
        assert _roles(cells) == ["ja_kana", "ja_kana"]

    def test_handakuon_pa(self, ctx, profile):
        assert _seq(ctx, profile, "パ") == [(6,), (1, 3, 6)]

    def test_youon_kya(self, ctx, profile):
        assert _seq(ctx, profile, "キャ") == [(4,), (1, 6)]

    def test_youon_dakuon_gya(self, ctx, profile):
        assert _seq(ctx, profile, "ギャ") == [(4, 5), (1, 6)]

    def test_youon_handakuon_pya(self, ctx, profile):
        assert _seq(ctx, profile, "ピャ") == [(4, 6), (1, 3, 6)]


class TestSpecials:
    def test_long_vowel(self, ctx, profile):
        assert _seq(ctx, profile, "ー") == [(2, 5)]

    def test_sokuon(self, ctx, profile):
        assert _seq(ctx, profile, "ッ") == [(2,)]

    def test_hatsuon(self, ctx, profile):
        assert _seq(ctx, profile, "ン") == [(3, 5, 6)]


class TestPrimerExamples:
    """Worked examples from A42025.pdf (readings = pronunciation forms)."""

    def test_tokyo(self, ctx, profile):
        # 東京 -> トーキョー : ト ー キョ ー
        assert _seq(ctx, profile, "トーキョー") == [
            (2, 3, 4, 5),       # ト
            (2, 5),             # ー
            (4,), (2, 4, 6),    # キョ (youon + コ)
            (2, 5),             # ー
        ]

    def test_sansuu(self, ctx, profile):
        # 算数 -> サンスー
        assert _seq(ctx, profile, "サンスー") == [
            (1, 5, 6),          # サ
            (3, 5, 6),          # ン
            (1, 4, 5, 6),       # ス
            (2, 5),             # ー
        ]

    def test_bokuwa(self, ctx, profile):
        # ぼくは(助詞) -> ボクワ : ボ ク ワ
        assert _seq(ctx, profile, "ボクワ") == [
            (5,), (2, 3, 4, 6),  # ボ (dakuon + ホ)
            (1, 4, 6),           # ク
            (3,),                # ワ
        ]

    def test_ishikawa(self, ctx, profile):
        # 石川 -> イシカワ
        assert _seq(ctx, profile, "イシカワ") == [
            (1, 2),              # イ
            (1, 2, 5, 6),        # シ
            (1, 6),              # カ
            (3,),                # ワ
        ]


class TestHiraganaTolerance:
    def test_hiragana_resolves_like_katakana(self, ctx, profile):
        kata = _seq(ctx, profile, "ガ")
        hira = _seq(ctx, profile, "が")
        assert hira == kata == [(5,), (1, 6)]

    def test_hiragana_youon(self, ctx, profile):
        assert _seq(ctx, profile, "きょ") == _seq(ctx, profile, "キョ")


class TestSoftFailure:
    def test_missing_reading(self, ctx, profile):
        cells = translate_word(
            Word(surface="東京", reading=None, span=Span(0, 2)), ctx, profile
        )
        assert _roles(cells) == ["unknown", "unknown"]
        assert any(w.code == "MISSING_READING" for w in ctx.warnings)

    def test_unknown_mora(self, ctx, profile):
        cells = translate_word(
            Word(surface="x", reading="A", span=Span(0, 1)), ctx, profile
        )
        assert _roles(cells) == ["unknown"]
        assert any(w.code == "UNKNOWN_KANA" for w in ctx.warnings)


class TestDispatchRouting:
    """A ja profile routes prose nodes to backend.ja via the registry."""

    def test_word_routes_to_ja(self, ctx, profile):
        cells = translate_node(
            Word(surface="ガ", reading="ガ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(5,), (1, 6)]
        assert _roles(cells) == ["ja_kana", "ja_kana"]

    def test_hanzi_char_routes_to_ja(self, ctx, profile):
        # 水 -> ミズ : ミ, ズ(dakuon + ス) — the kanji->reading case.
        cells = translate_node(
            HanziChar(surface="水", reading="ミズ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(1, 2, 3, 5, 6), (5,), (1, 4, 5, 6)]

    def test_translate_hanzi_char_entry(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="木", reading="キ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(1, 2, 6)]
