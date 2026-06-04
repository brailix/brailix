"""The letter+hanzi compound lexicon, loaded as a profile table.

The lexicon is the signal :func:`insert_cross_kind_boundary_spaces` uses
to pick the connector (compound word: x轴 / T恤) over a blank cell (two
words: 已知 α). It is scheme-neutral Chinese language data, so both cn
profiles load it via ``tables.zh.compounds`` into ``profile.zh_compounds``.
A miss falls back to a blank cell, so the lexicon may be incomplete —
these tests pin the seed terms and the load contract.
"""

from __future__ import annotations

from brailix.core.config import load_profile


class TestCompoundLexicon:
    def test_seed_compounds_present(self) -> None:
        compounds = load_profile("cn_current").zh_compounds
        for term in ("x轴", "y轴", "T恤", "X光", "B超", "维生素C", "卡拉OK", "pH值"):
            assert term in compounds, term

    def test_is_frozenset(self) -> None:
        assert isinstance(load_profile("cn_current").zh_compounds, frozenset)

    def test_both_profiles_share_the_lexicon(self) -> None:
        # Scheme-neutral language data — current and ncb load the same file.
        cur = load_profile("cn_current").zh_compounds
        ncb = load_profile("cn_ncb").zh_compounds
        assert cur == ncb
        assert "x轴" in ncb

    def test_two_word_cases_absent(self) -> None:
        # The segmentation "blank cell" cases must NOT be in the lexicon, else
        # 已知 α would wrongly fuse with a connector.
        compounds = load_profile("cn_current").zh_compounds
        assert "已知α" not in compounds
        assert "使用CPU" not in compounds
        assert "" not in compounds
