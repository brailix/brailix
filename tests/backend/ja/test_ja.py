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
from brailix.backend.ja import translate_word
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import Word


@pytest.fixture(scope="module")
def profile():
    return load_profile("ja_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


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


def test_nfd_decomposed_dakuten_folds_to_one_cell(ctx, profile):
    # An NFD-encoded source (カ U+30AB + ◌゙ U+3099) must translate identically
    # to its NFC form (ガ U+30AC): the backend normalises to NFC before the
    # mora split, so dakuten doesn't fall through as UNKNOWN_KANA.
    nfc = "ガ"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc and len(nfd) == 2  # sanity: actually decomposed
    assert _seq(ctx, profile, nfd) == _seq(ctx, profile, nfc)


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
    # ---- 外来音 (foreign sounds), 日本点字表記法 2018 外来語の表記 ----
    # Same prefix+base mechanism as the youon above, extended per the
    # 2018 rules (cross-checked: ja.wikipedia 点字「外来語の表記」 and
    # 点字支援サイト かわら版38; the prefix cells 4 / 45 / 5 already in
    # this derivation corroborate the mechanism).
    _GOU_YOUON = (2, 6)          # 合拗音 prefix (the 疑問符 cell)
    _GOU_YOUON_DAKU = (2, 5, 6)  # voiced 合拗音 prefix (the 句点 cell)
    # Closed youon (合拗音): prefix + the row's kana in the small
    # vowel's column — ウ+ァ..ォ → ア行, ク/グ → カ行, ツ → タ行,
    # フ/ヴ → ハ行, and トゥ/ドゥ → ツ by consonant replacement.
    _CLOSED = (
        ("ウァ", False, "ア"), ("ウィ", False, "イ"),
        ("ウェ", False, "エ"), ("ウォ", False, "オ"),
        ("クァ", False, "カ"), ("クィ", False, "キ"),
        ("クェ", False, "ケ"), ("クォ", False, "コ"),
        ("ツァ", False, "タ"), ("ツィ", False, "チ"),
        ("ツェ", False, "テ"), ("ツォ", False, "ト"),
        ("ファ", False, "ハ"), ("フィ", False, "ヒ"),
        ("フェ", False, "ヘ"), ("フォ", False, "ホ"),
        ("グァ", True, "カ"), ("グィ", True, "キ"),
        ("グェ", True, "ケ"), ("グォ", True, "コ"),
        ("ヴァ", True, "ハ"), ("ヴィ", True, "ヒ"),
        ("ヴェ", True, "ヘ"), ("ヴォ", True, "ホ"),
        ("トゥ", False, "ツ"), ("ドゥ", True, "ツ"),
    )
    for mora, voiced, base in _CLOSED:
        table[mora] = (_GOU_YOUON_DAKU if voiced else _GOU_YOUON, s[base])
    # E-row open youon (開拗音): the youon prefix 4 (voiced 45) + the
    # row's e-column kana — the a/u/o-column rule above generalised.
    for base, ek in (
        ("イ", "エ"), ("キ", "ケ"), ("シ", "セ"), ("チ", "テ"),
        ("ニ", "ネ"), ("ヒ", "ヘ"),
    ):
        table[base + "ェ"] = ((4,), s[ek])
    table["ジ" + "ェ"] = ((4, 5), s["セ"])
    # Consonant-replaced i-sounds: ティ/ディ → prefix + チ, スィ/ズィ → + シ.
    table["テ" + "ィ"] = ((4,), s["チ"])
    table["デ" + "ィ"] = ((4, 5), s["チ"])
    table["ス" + "ィ"] = ((4,), s["シ"])
    table["ズ" + "ィ"] = ((4, 5), s["シ"])
    return table


class TestKanaTable:
    def test_matches_independent_derivation(self, profile):
        assert profile.lang_table("kana") == _expected_kana()

    def test_mora_count(self, profile):
        # 110 base entries (seion / dakuon / handakuon / youon / specials)
        # + 37 外来音 entries (26 closed youon incl. トゥ/ドゥ, 7 e-row
        # open youon, 4 consonant-replaced i-sounds).
        assert len(profile.lang_table("kana")) == 147


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


class TestForeignSounds:
    """外来音 (loanword sounds) — each entry's derivation basis is
    stated inline. The mechanism (prefix cell + base kana cell) is the
    one the 110 base entries already use; the per-family rules come
    from 日本点字表記法 2018 外来語の表記 (cross-checked against
    ja.wikipedia 点字「外来語の表記」 and 点字支援サイト かわら版38)."""

    def test_file(self, ctx, profile):
        # ファイル = ファ + イ + ル.
        # ファ = 合拗音 prefix ⠢ (2,6) + ハ (1,3,6) — the ha-row kana in
        # the small-ァ column (rule: は行 + 小ア → prefix+ハ行).
        assert _seq(ctx, profile, "ファイル") == [
            (2, 6), (1, 3, 6),   # ファ
            (1, 2),              # イ
            (1, 4, 5),           # ル
        ]

    def test_check(self, ctx, profile):
        # チェック = チェ + ッ + ク.
        # チェ = youon prefix ⠘ (4) + テ (1,2,3,4,5) — the ta-row e-column
        # kana (rule: 開拗音 uses the same 4-prefix as キャ=4+カ).
        assert _seq(ctx, profile, "チェック") == [
            (4,), (1, 2, 3, 4, 5),  # チェ
            (2,),                   # ッ
            (1, 4, 6),              # ク
        ]

    def test_party(self, ctx, profile):
        # パーティー = パ + ー + ティ + ー.
        # ティ = youon prefix ⠘ + チ — consonant replacement
        # (rule: ティ = 拗音符+チ, like スィ = 拗音符+シ).
        assert _seq(ctx, profile, "パーティー") == [
            (6,), (1, 3, 6),     # パ (handakuon prefix + ハ)
            (2, 5),              # ー
            (4,), (1, 2, 3, 5),  # ティ
            (2, 5),              # ー
        ]

    def test_violin(self, ctx, profile):
        # ヴァイオリン: ヴァ = voiced 合拗音 prefix ⠲ (2,5,6) + ハ
        # (rule: ヴァ行 = 句点符 + ハ行); ヴ itself = 5+ウ was already
        # in the base table — the same voiced-prefix mechanism.
        assert _seq(ctx, profile, "ヴァイオリン") == [
            (2, 5, 6), (1, 3, 6),  # ヴァ
            (1, 2),                # イ
            (2, 4),                # オ
            (1, 2, 5),             # リ
            (3, 5, 6),             # ン
        ]

    def test_je(self, ctx, profile):
        # ジェ = 拗濁音符 (4,5) + セ — the voiced counterpart of シェ
        # (rule: ジェ = 拗濁音符+セ, same 45-prefix as ギャ=45+カ).
        assert _seq(ctx, profile, "ジェ") == [(4, 5), (1, 2, 4, 5, 6)]

    def test_wi_we_wo(self, ctx, profile):
        # ウィ/ウェ/ウォ = ⠢ + イ/エ/オ (a-row closed youon).
        assert _seq(ctx, profile, "ウィ") == [(2, 6), (1, 2)]
        assert _seq(ctx, profile, "ウェ") == [(2, 6), (1, 2, 4)]
        assert _seq(ctx, profile, "ウォ") == [(2, 6), (2, 4)]

    def test_tu_du(self, ctx, profile):
        # トゥ/ドゥ = ⠢/⠲ + ツ (consonant replacement → ツ).
        assert _seq(ctx, profile, "トゥ") == [(2, 6), (1, 3, 4, 5)]
        assert _seq(ctx, profile, "ドゥ") == [(2, 5, 6), (1, 3, 4, 5)]

    def test_small_kana_binding(self, ctx, profile):
        # _split_mora binds ァィゥェォ onto the preceding kana (the
        # ァ of ファ never stands alone as an unknown mora).
        from brailix.backend.ja import _split_mora

        assert _split_mora("ファイル") == ["ファ", "イ", "ル"]
        assert _split_mora("チェック") == ["チェ", "ッ", "ク"]
        assert _split_mora("きゃ") == ["キャ"]  # hiragana folds first

    def test_youon_base_entries_unchanged(self, ctx, profile):
        # The pre-existing youon readings still split and resolve the
        # same way with the widened binding set.
        assert _seq(ctx, profile, "キャ") == [(4,), (1, 6)]
        assert _seq(ctx, profile, "シュ") == [(4,), (1, 4, 5, 6)]


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

    def test_single_character_word_routes_to_ja(self, ctx, profile):
        # 水 -> ミズ : ミ, ズ(dakuon + ス) — the kanji->reading case.
        cells = translate_node(
            Word(surface="水", reading="ミズ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(1, 2, 3, 5, 6), (5,), (1, 4, 5, 6)]

    def test_translate_word_entry_takes_a_single_character(self, ctx, profile):
        cells = translate_word(
            Word(surface="木", reading="キ", span=Span(0, 1)), ctx, profile
        )
        assert _dots(cells) == [(1, 2, 6)]
