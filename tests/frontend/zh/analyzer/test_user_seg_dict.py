"""Personal segmentation dictionary post-pass in
:func:`brailix.frontend.zh.analyzer.tokenize`.

One mapping serves both fixes: a one-piece value folds tokens into a word,
a multi-piece value cuts a token apart. The pass runs after whichever
analyzer ran, so a pinned division wins over every engine and composes with
all of them.
"""

from __future__ import annotations

import pytest

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.zh.analyzer import tokenize
from brailix.frontend.zh.analyzer._user_dict import (
    apply_user_seg_dict,
    normalize_seg_dict,
)
from brailix.frontend.zh.tokens import ChineseToken


def _tok(surface: str, start: int) -> ChineseToken:
    return ChineseToken(surface=surface, span=Span(start, start + len(surface)))


def _surfaces(tokens: list[ChineseToken]) -> list[str]:
    return [t.surface for t in tokens]


def _spans(tokens: list[ChineseToken]) -> list[tuple[int, int]]:
    return [(t.span.start, t.span.end) for t in tokens if t.span is not None]


class TestFold:
    """One piece = "this is a word": consecutive tokens get joined."""

    def test_joins_two_tokens(self) -> None:
        # The real THULAC failure this was built for: 国家 split apart, which
        # in braille writes the two characters as separate words.
        tokens = [_tok("国", 0), _tok("家", 1), _tok("通用", 2)]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["国家", "通用"]
        assert _spans(out) == [(0, 2), (2, 4)]

    def test_joins_a_run_longer_than_two(self) -> None:
        tokens = [_tok("盲文", 0), _tok("出版", 2), _tok("社", 4)]
        out = apply_user_seg_dict(tokens, {"盲文出版社": ("盲文出版社",)})
        assert _surfaces(out) == ["盲文出版社"]
        assert _spans(out) == [(0, 5)]

    def test_leaves_an_already_correct_token_alone(self) -> None:
        # Entry present, tokenizer already agreed — the rewrite is a no-op in
        # content, and must not disturb the span either.
        tokens = [_tok("国家", 0)]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["国家"]
        assert _spans(out) == [(0, 2)]


class TestCut:
    """Several pieces = "cut it here": one token becomes many."""

    def test_splits_one_token(self) -> None:
        tokens = [_tok("国家通用", 0)]
        out = apply_user_seg_dict(tokens, {"国家通用": ("国家", "通用")})
        assert _surfaces(out) == ["国家", "通用"]
        assert _spans(out) == [(0, 2), (2, 4)]

    def test_splits_across_a_token_run(self) -> None:
        # The key spans two tokens AND cuts them somewhere else entirely —
        # the general case that fold and cut are both instances of.
        tokens = [_tok("自二", 0), _tok("〇二六年", 2)]
        out = apply_user_seg_dict(tokens, {"自二〇二六年": ("自", "二〇二六年")})
        assert _surfaces(out) == ["自", "二〇二六年"]
        assert _spans(out) == [(0, 1), (1, 6)]

    def test_splits_into_single_characters(self) -> None:
        # Pieces may be one character each even though KEYS may not be.
        tokens = [_tok("中国", 0)]
        out = apply_user_seg_dict(tokens, {"中国": ("中", "国")})
        assert _surfaces(out) == ["中", "国"]


class TestMatchingIsBoundaryAnchored:
    """A key matches only from one token boundary to another."""

    def test_does_not_match_across_the_middle_of_words(self) -> None:
        # 中国 / 家庭 concatenates to 中国家庭, which CONTAINS 国家 — but no
        # token starts there. A substring search would emit 中 / 国家 / 庭,
        # inventing a word division the user never asked for in a sentence
        # that has nothing to do with 国家.
        tokens = [_tok("中国", 0), _tok("家庭", 2)]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["中国", "家庭"]

    def test_does_not_match_a_prefix_of_a_token(self) -> None:
        # Starts at a boundary but ends mid-token → no match.
        tokens = [_tok("国家通用", 0)]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["国家通用"]

    def test_prefers_the_longest_match(self) -> None:
        tokens = [_tok("国", 0), _tok("家", 1), _tok("通", 2), _tok("用", 3)]
        seg = {"国家": ("国家",), "国家通用": ("国家通用",)}
        out = apply_user_seg_dict(tokens, seg)
        assert _surfaces(out) == ["国家通用"]


class TestSourceAdjacency:
    """A run broken by dropped source text is not one written unit."""

    def test_refuses_to_fold_across_a_span_gap(self) -> None:
        # THULAC drops the space in "brailix 是", leaving (0,7) then (8,9).
        # Folding across that gap would erase a separator the source had.
        tokens = [_tok("你好", 0), _tok("世界", 3)]  # gap at offset 2
        out = apply_user_seg_dict(tokens, {"你好世界": ("你好世界",)})
        assert _surfaces(out) == ["你好", "世界"]

    def test_folds_when_spans_are_absent(self) -> None:
        # Hand-built tokens with no provenance fall back to list adjacency,
        # matching the convention the boundary predicates use.
        tokens = [ChineseToken(surface="国"), ChineseToken(surface="家")]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["国家"]
        assert out[0].span is None  # no coordinates invented

    def test_a_gap_does_not_block_a_later_match(self) -> None:
        tokens = [_tok("你好", 0), _tok("国", 3), _tok("家", 4)]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert _surfaces(out) == ["你好", "国家"]


class TestInvariants:
    def test_surface_text_is_preserved(self) -> None:
        tokens = [_tok("国", 0), _tok("家", 1), _tok("通用", 2)]
        before = "".join(_surfaces(tokens))
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert "".join(_surfaces(out)) == before

    def test_spans_stay_ordered_and_non_overlapping(self) -> None:
        tokens = [_tok("国家通用", 0), _tok("盲文", 4)]
        out = apply_user_seg_dict(tokens, {"国家通用": ("国家", "通用")})
        spans = _spans(out)
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))

    def test_input_list_is_not_mutated(self) -> None:
        tokens = [_tok("国", 0), _tok("家", 1)]
        original = list(tokens)
        apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert tokens == original

    def test_empty_dictionary_returns_the_same_list(self) -> None:
        tokens = [_tok("国家", 0)]
        assert apply_user_seg_dict(tokens, {}) is tokens

    def test_pos_is_dropped_on_rewritten_tokens(self) -> None:
        # POS described the division the user just overrode.
        tokens = [
            ChineseToken(surface="国", pos="n", span=Span(0, 1)),
            ChineseToken(surface="家", pos="n", span=Span(1, 2)),
        ]
        out = apply_user_seg_dict(tokens, {"国家": ("国家",)})
        assert out[0].pos is None

    def test_untouched_tokens_keep_their_pos(self) -> None:
        tokens = [
            ChineseToken(surface="国家", pos="ns", span=Span(0, 2)),
            ChineseToken(surface="通用", pos="v", span=Span(2, 4)),
        ]
        out = apply_user_seg_dict(tokens, {"盲文": ("盲文",)})
        assert [t.pos for t in out] == ["ns", "v"]


class TestNormalize:
    """Entries that can't describe a division of their key are dropped."""

    def test_keeps_a_valid_entry(self) -> None:
        assert normalize_seg_dict({"国家": ["国家"]}) == {"国家": ("国家",)}

    def test_drops_single_char_surface(self) -> None:
        # Nothing to divide, nothing to join.
        assert normalize_seg_dict({"国": ["国"]}) == {}

    def test_drops_pieces_that_do_not_spell_the_surface(self) -> None:
        # Would inject a character the document never contained.
        assert normalize_seg_dict({"国家": ["国", "家", "们"]}) == {}
        assert normalize_seg_dict({"国家": ["国"]}) == {}

    def test_drops_empty_pieces(self) -> None:
        assert normalize_seg_dict({"国家": ["国家", ""]}) == {}
        assert normalize_seg_dict({"国家": []}) == {}

    def test_survivors_are_unaffected_by_a_bad_neighbour(self) -> None:
        # One junk line in a hand-edited file must not cost the good ones.
        out = normalize_seg_dict({"国家": ["国家"], "通用": ["通", "X"]})
        assert out == {"国家": ("国家",)}

    @pytest.mark.parametrize(
        "value",
        [None, 123, object(), ("国", None), ("国", 7), (["国"], ["家"])],
        ids=repr,
    )
    def test_drops_structurally_impossible_pieces(self, value: object) -> None:
        """The checks above all assume the value can be walked as strings,
        and the walk itself used to be what raised: ``tuple(None)`` threw a
        ``TypeError`` straight out of a function whose whole contract is to
        skip the record and carry on."""
        assert normalize_seg_dict({"国家": value}) == {}  # type: ignore[dict-item]

    def test_drops_a_bare_string_rather_than_iterating_it(self) -> None:
        """``{"国家": "国家"}`` looks like *this is one word* and iterates to
        ``("国", "家")`` — *cut it apart*, the opposite instruction, and it
        even spells the surface, so every later check would wave it through.
        Pieces are written as a sequence; anything else is too ambiguous to
        act on."""
        assert normalize_seg_dict({"国家": "国家"}) == {}

    def test_a_structurally_bad_entry_costs_only_itself(self) -> None:
        out = normalize_seg_dict({"国家": ["国家"], "通用": None})  # type: ignore[dict-item]
        assert out == {"国家": ("国家",)}


class TestThroughTokenize:
    """The wiring: ``tokenize`` reads the option and applies the pass."""

    def test_option_reaches_the_post_pass(self) -> None:
        ctx = FrontendContext(
            profile="cn_current",
            options={"zh_analyzer": "char", "user_seg_dict": {"国家": ("国家",)}},
        )
        # ``char`` emits one token per character, so the fold is unambiguous.
        assert _surfaces(tokenize("国家通用", ctx)) == ["国家", "通", "用"]

    def test_absent_option_leaves_tokenization_alone(self) -> None:
        ctx = FrontendContext(profile="cn_current", options={"zh_analyzer": "char"})
        assert _surfaces(tokenize("国家", ctx)) == ["国", "家"]

    def test_invalid_entries_are_ignored_not_raised(self) -> None:
        # A hand-edited dictionary file must never stop a document compiling.
        ctx = FrontendContext(
            profile="cn_current",
            options={
                "zh_analyzer": "char",
                "user_seg_dict": {"国家": ("国", "家", "们")},
            },
        )
        assert _surfaces(tokenize("国家", ctx)) == ["国", "家"]

    def test_spans_are_document_relative_after_the_pass(self) -> None:
        # tokenize() emits segment-local spans; the fold must leave them
        # consistent so ``shift_token_spans`` can lift them correctly.
        ctx = FrontendContext(
            profile="cn_current",
            options={"zh_analyzer": "char", "user_seg_dict": {"国家": ("国家",)}},
        )
        tokens = tokenize("我在国家", ctx)
        for t in tokens:
            assert t.span is not None
            assert "我在国家"[t.span.start : t.span.end] == t.surface


class TestReadingsResolveOnTheRewrittenWord:
    """The ordering payoff: readings are looked up AFTER re-division."""

    def test_folded_word_gets_a_whole_word_reading(self) -> None:
        pytest.importorskip("pypinyin")
        from brailix.frontend.zh.pinyin import annotate

        ctx = FrontendContext(
            profile="cn_current",
            options={
                "zh_analyzer": "char",
                "pinyin_resolver": "pypinyin",
                "user_seg_dict": {"银行": ("银行",)},
            },
        )
        tokens = annotate(tokenize("银行", ctx), ctx)
        # One token, one reading covering both characters — the resolver got
        # to see a word, which is what disambiguates 行 (hang2 not xing2).
        assert [t.surface for t in tokens] == ["银行"]
        assert tokens[0].pinyin == "yin2 hang2"
