"""J3 分かち書き (wakachigaki word-spacing) + the per-language boundary seam.

``tokens_to_inline`` inserts a blank cell before each 自立語 (bunsetsu
head) using the analyzer's POS — 付属語 (助詞/助動詞) and 接尾 suffixes
attach backward, a word after a 接頭詞 attaches forward, and the first
token never takes a leading space. With no POS (the ``kana`` fallback) no
spaces are inserted. The boundary pass is now selected by language, so
generalizing it must not change Chinese behaviour.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.core.config import load_profile
from brailix.core.span import Span
from brailix.frontend import apply_boundary, boundary_registry
from brailix.frontend.ja.analyzer import JapaneseToken, tokens_to_inline
from brailix.ir.inline import Space, Word


def _kinds(nodes):
    return [type(n).__name__ for n in nodes]


class TestWakachigakiRule:
    """Pure logic via canned POS-tagged tokens (no analyzer dependency)."""

    def test_space_before_jiritsugo(self):
        # 私(名詞) は(助詞) 本(名詞) を(助詞) — a space precedes 本 only.
        toks = [
            JapaneseToken("私", "ワタシ", "名詞,代名詞,一般,*", Span(0, 1)),
            JapaneseToken("は", "ワ", "助詞,係助詞,*,*", Span(1, 2)),
            JapaneseToken("本", "ホン", "名詞,一般,*,*", Span(2, 3)),
            JapaneseToken("を", "ヲ", "助詞,格助詞,一般,*", Span(3, 4)),
        ]
        assert _kinds(tokens_to_inline(toks)) == [
            "Word", "Word", "Space", "Word", "Word"
        ]

    def test_first_token_has_no_leading_space(self):
        toks = [JapaneseToken("本", "ホン", "名詞,一般,*,*", Span(0, 1))]
        assert _kinds(tokens_to_inline(toks)) == ["Word"]

    def test_no_pos_means_no_spacing(self):
        # kana-fallback tokens carry no POS -> source spaces only.
        toks = [
            JapaneseToken("サクラ", "サクラ", None, Span(0, 3)),
            JapaneseToken("ガッコウ", "ガッコウ", None, Span(3, 7)),
        ]
        assert _kinds(tokens_to_inline(toks)) == ["Word", "Word"]

    def test_prefix_attaches_forward(self):
        # お(接頭詞) 名前(名詞) -> no space between them.
        toks = [
            JapaneseToken("お", "オ", "接頭詞,名詞接続,*,*", Span(0, 1)),
            JapaneseToken("名前", "ナマエ", "名詞,一般,*,*", Span(1, 3)),
        ]
        assert _kinds(tokens_to_inline(toks)) == ["Word", "Word"]

    def test_suffix_attaches_backward(self):
        # 田中(名詞) さん(名詞,接尾) -> no space before さん.
        toks = [
            JapaneseToken("田中", "タナカ", "名詞,固有名詞,人名,姓", Span(0, 2)),
            JapaneseToken("さん", "サン", "名詞,接尾,人名,*", Span(2, 4)),
        ]
        assert _kinds(tokens_to_inline(toks)) == ["Word", "Word"]

    def test_space_span_is_zero_width_at_boundary(self):
        toks = [
            JapaneseToken("本", "ホン", "名詞,一般,*,*", Span(0, 1)),
            JapaneseToken("です", "デス", "助動詞,*,*,*", Span(1, 3)),
            JapaneseToken("猫", "ネコ", "名詞,一般,*,*", Span(3, 4)),
        ]
        nodes = tokens_to_inline(toks)
        # 本 です | 猫 — space before 猫 at offset 3.
        space = next(n for n in nodes if isinstance(n, Space))
        assert space.span == Span(3, 3)


class TestWakachigakiJanome:
    def test_bunsetsu_spaces(self):
        pytest.importorskip("janome")
        r = Pipeline(profile="ja_current", analyzer="janome").translate_text(
            "私は本を読む"
        )
        # 私は | 本を | 読む -> 2 blank cells between 3 bunsetsu.
        assert r.render().count("⠀") == 2
        assert [w.code for w in r.warnings] == []

    def test_single_bunsetsu_has_no_space(self):
        pytest.importorskip("janome")
        r = Pipeline(profile="ja_current", analyzer="janome").translate_text(
            "東京"
        )
        assert "⠀" not in r.render()


class TestBoundarySeam:
    def test_zh_registered(self):
        # J4 also registers a ja handler (the number つなぎ符); J3 only had zh.
        assert "zh" in boundary_registry
        assert "ja" in boundary_registry

    def test_unregistered_lang_is_identity(self):
        nodes = [Word(surface="ア", reading="ア")]
        prof = load_profile("ja_current")
        assert apply_boundary(nodes, "ja", prof) is nodes

    def test_zh_boundary_unchanged(self):
        # Generalizing the seam must not change Chinese: 我用CPU keeps a
        # blank cell at the hanzi<->latin boundary.
        r = Pipeline(profile="cn_current").translate_text("我用CPU")
        assert "⠀" in r.render()
