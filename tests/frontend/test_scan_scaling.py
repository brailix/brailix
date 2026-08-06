"""The text scanners stay linear in the text they walk.

Three of them were not, and all three ran on the ordinary path — every prose
block of every document, under a whole-text ceiling of tens of millions of
characters. None of them had a shape a unit test would notice: they produced
the right answer, and only the *cost* of producing it grew with the square (or
the cube) of the input.

These are fuses, not benchmarks. Each input is sized so that the old behaviour
could not finish inside the budget on any machine — minutes to hours, not
"a bit slower" — while the linear one finishes in milliseconds, so there is no
timing margin to tune and nothing to go flaky. What is asserted is the shape of
the algorithm; that is why they are not marked ``perf`` and do run by default.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math.utils import _merge_function_name_runs
from brailix.core.config import load_profile
from brailix.core.span import Span
from brailix.frontend.segmentation import (
    _find_protected_regions,
    _iter_phonetic_spans,
)
from brailix.frontend.zh.analyzer._user_dict import apply_user_seg_dict
from brailix.frontend.zh.tokens import ChineseToken

_BUDGET = 5.0  # seconds; the quadratic versions needed orders of magnitude more


class TestProtectedRegions:
    def test_a_run_of_unmatched_openers_does_not_rescan_the_tail(self) -> None:
        # Every ``[`` searched the rest of the text for its ``]`` from scratch.
        # 200k openers is 2e10 character comparisons that way.
        text = "[" * 200_000
        started = time.perf_counter()
        assert list(_iter_phonetic_spans(text)) == []
        assert time.perf_counter() - started < _BUDGET

    def test_a_closer_far_away_is_not_re_examined_per_opener(self) -> None:
        # A closer exists, so the search succeeds every time and the content
        # between was sliced out to be tested — copying the whole region once
        # per opener.
        text = "[" * 100_000 + "aɪ]"
        started = time.perf_counter()
        found = list(_iter_phonetic_spans(text))
        assert time.perf_counter() - started < _BUDGET
        # Only the last opener has content that qualifies; the 99_999 before it
        # each saw a region starting with ``[`` and rejected it.
        assert found == [(99_999, 100_003, "phonetic_inline")]

    def test_phonetic_candidates_do_not_rescan_every_math_span(self) -> None:
        # Overlap was decided by walking all math spans per phonetic span.
        text = "$a$/aɪ/" * 40_000
        started = time.perf_counter()
        spans = _find_protected_regions(text)
        assert time.perf_counter() - started < _BUDGET
        assert len(spans) == 80_000
        assert [s[2] for s in spans[:4]] == [
            "math_inline",
            "phonetic_inline",
            "math_inline",
            "phonetic_inline",
        ]

    def test_the_overlap_rule_itself_is_unchanged(self) -> None:
        # Math wins: the ``/æ/`` inside the island is not a second region.
        spans = _find_protected_regions("x $a/æ/b$ y /æ/ z")
        assert [(s[2], s[0]) for s in spans] == [
            ("math_inline", 2),
            ("phonetic_inline", 12),
        ]


class TestUserSegDict:
    def test_a_long_entry_does_not_widen_every_other_position(self) -> None:
        # The run was extended until the accumulated surface passed the longest
        # key *in the dictionary*, so one legitimate long entry made every
        # token position accumulate 200 characters, re-copying each step.
        long_key = "国" * 200
        seg_dict = {long_key: (long_key,), "国家": ("国家",)}
        tokens = [
            ChineseToken(surface="文", span=Span(i, i + 1)) for i in range(20_000)
        ]
        started = time.perf_counter()
        out = apply_user_seg_dict(tokens, seg_dict)
        assert time.perf_counter() - started < _BUDGET
        assert len(out) == len(tokens)

    def test_a_run_stops_at_the_first_character_no_entry_begins_with(
        self,
    ) -> None:
        """The bound is the text, not the dictionary — asserted by counting.

        A wall clock says "fast enough"; this says *why*. No entry starts with
        文, so no run at any of these 500 positions can reach a key, and the
        dictionary is never consulted at all. Any number here that grows with
        the length of the longest entry is the old behaviour returning.
        """

        class Counting(dict):
            lookups = 0

            def get(self, key, default=None):  # type: ignore[override]
                type(self).lookups += 1
                return super().get(key, default)

        seg_dict = Counting({"国" * 50: ("国" * 50,)})
        tokens = [
            ChineseToken(surface="文", span=Span(i, i + 1)) for i in range(500)
        ]
        apply_user_seg_dict(tokens, seg_dict)
        assert Counting.lookups == 0

        # And a run that *does* follow a real entry is looked up once per
        # token it grows by — the walk stops where the entry does.
        Counting.lookups = 0
        seg_dict = Counting({"国家": ("国家",)})
        apply_user_seg_dict(
            [
                ChineseToken(surface=c, span=Span(i, i + 1))
                for i, c in enumerate("国家人")
            ],
            seg_dict,
        )
        assert Counting.lookups == 2

    def test_folding_and_cutting_still_do_what_they_did(self) -> None:
        def tokens(text: str) -> list[ChineseToken]:
            return [
                ChineseToken(surface=c, span=Span(i, i + 1))
                for i, c in enumerate(text)
            ]

        # Fold: 国 / 家 become one word.
        folded = apply_user_seg_dict(tokens("中国家庭"), {"国家": ("国家",)})
        assert [t.surface for t in folded] == ["中", "国家", "庭"]
        # Longest match still wins over the shorter entry it starts with.
        both = {"国家": ("国家",), "国家通用": ("国家", "通用")}
        longest = apply_user_seg_dict(tokens("国家通用"), both)
        assert [t.surface for t in longest] == ["国家", "通用"]


class TestMathmlNormalizerWidth:
    def test_a_wide_row_is_not_rebuilt_per_removed_child(self) -> None:
        """``Element.remove`` shifts the list; three passes used it per child.

        Depth is capped at 150; width never was, and a formula from a
        converter is exactly where a very wide ``<mrow>`` comes from. Each
        collapsed wrapper and each dropped ``<mspace>`` cost a shift of every
        sibling after it.
        """
        from brailix.frontend.math.normalizer import normalize

        width = 30_000
        parts = []
        for _ in range(width):
            parts.append("<mrow><mi>x</mi></mrow>")  # collapses to its child
            parts.append("<mspace width='1em'/>")  # dropped outright
        started = time.perf_counter()
        out = normalize("<math>" + "".join(parts) + "</math>")
        assert time.perf_counter() - started < _BUDGET
        assert out is not None
        # The rewrite itself is unchanged: wrappers gone, spacers gone.
        assert len(out) == width
        assert {child.tag for child in out} == {"mi"}


class TestFunctionNameRuns:
    def _run(self, text: str) -> list[ET.Element]:
        kids = []
        for ch in text:
            e = ET.Element("mi")
            e.text = ch
            kids.append(e)
        return _merge_function_name_runs(kids, load_profile("cn_ncb"))

    def test_a_wide_run_of_bare_letters_is_not_cubic(self) -> None:
        # Every start tried every remaining length, re-joining the characters
        # it spanned. Depth is capped at 150 elsewhere; width never was.
        started = time.perf_counter()
        out = self._run("xyzw" * 5_000)
        assert time.perf_counter() - started < _BUDGET
        assert len(out) == 20_000

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("sin", ["sin"]),
            ("arcsin", ["arcsin"]),  # longest match, not arc + sin
            ("xsinx", ["x", "sin", "x"]),
            ("lim", ["lim"]),
        ],
    )
    def test_longest_match_semantics_are_unchanged(
        self, text: str, expected: list[str]
    ) -> None:
        assert [e.text for e in self._run(text)] == expected
