"""The segmentation dictionary end to end: option → tokens → braille cells.

The unit tests in ``tests/frontend/zh/analyzer/test_user_seg_dict.py`` pin the
rewrite itself. What matters here is that a dictionary entry reaches the
*cells* — Chinese braille writes a word's characters together and separates
words with a blank cell, so a re-division is a visible braille change, and a
pass that ran but didn't reach the renderer would look identical in a token
assertion.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from brailix.pipeline import Pipeline, block_hash

# A real reading engine, not ``null``: with no readings every hanzi renders as
# a blank cell, which is also what a word boundary renders as — so the very
# thing under test would be indistinguishable from the text around it.
pytest.importorskip("pypinyin")

# ``char`` makes the division the only variable: one token per character going
# in, so any word longer than one character is the dictionary's doing.
_BASE = {"profile": "cn_current", "analyzer": "char", "resolver": "pypinyin"}

_BLANK = "⠀"  # unicode braille blank — what a word boundary renders as


def _cells(pipe: Pipeline, text: str) -> str:
    return pipe.translate_text(text).render("unicode")


class TestFoldReachesTheCells:
    def test_a_pinned_word_loses_its_internal_blank_cell(self) -> None:
        plain = Pipeline(**_BASE)
        pinned = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})

        before = _cells(plain, "国家")
        after = _cells(pinned, "国家")
        # Two words become one: the boundary blank between them goes away.
        assert _BLANK in before
        assert _BLANK not in after
        assert len(after) == len(before) - 1

    def test_an_unrelated_word_is_untouched(self) -> None:
        plain = Pipeline(**_BASE)
        pinned = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})
        assert _cells(pinned, "盲文") == _cells(plain, "盲文")

    def test_only_the_boundary_changes_not_the_syllables(self) -> None:
        # Folding is a division change, not a reading change: the syllable
        # cells either side of the removed boundary must be identical, or the
        # pass is doing something to the text beyond joining it.
        plain = Pipeline(**_BASE)
        pinned = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})
        assert _cells(pinned, "国家") == _cells(plain, "国家").replace(_BLANK, "")


class TestCutReachesTheCells:
    def test_a_pinned_cut_adds_a_blank_cell(self) -> None:
        # Start from a pipeline that folds 国家通用 into one word, then cut it
        # — so the comparison isolates the cut rather than the analyzer.
        folded = Pipeline(**_BASE, user_seg_dict={"国家通用": ("国家通用",)})
        cut = Pipeline(**_BASE, user_seg_dict={"国家通用": ("国家", "通用")})

        assert _BLANK not in _cells(folded, "国家通用")
        assert _BLANK in _cells(cut, "国家通用")


class TestConfigurationIdentity:
    def test_two_divisions_of_one_surface_compile_differently(self) -> None:
        a = Pipeline(**_BASE, user_seg_dict={"甲乙丙": ("甲", "乙丙")})
        b = Pipeline(**_BASE, user_seg_dict={"甲乙丙": ("甲乙", "丙")})
        assert _cells(a, "甲乙丙") != _cells(b, "甲乙丙")

    def test_block_hash_moves_with_the_dictionary(self) -> None:
        # The cache-poisoning guard: same text, same profile, different
        # division must not share a block cache key.
        plain = Pipeline(**_BASE)
        pinned = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})
        block = plain.parse_text("国家").blocks[0]
        h0 = block_hash(block, "cn_current", fingerprint=plain.fingerprint)
        h1 = block_hash(block, "cn_current", fingerprint=pinned.fingerprint)
        assert h0 != h1


class TestConstructionContract:
    def test_field_is_read_only_after_construction(self) -> None:
        pipe = Pipeline(**_BASE)
        with pytest.raises(AttributeError, match="read-only"):
            pipe.user_seg_dict = {"国家": ("国家",)}

    def test_caller_cannot_mutate_the_dict_it_passed(self) -> None:
        caller = {"国家": ("国家",)}
        pipe = Pipeline(**_BASE, user_seg_dict=caller)
        caller["盲文"] = ("盲文",)
        assert "盲文" not in pipe.user_seg_dict

    def test_caller_cannot_mutate_a_piece_list_it_passed(self) -> None:
        # The subtler half: a MappingProxyType over a copied dict protects the
        # mapping, not the lists inside it. Appending here would change what
        # the pipeline segments, past a fingerprint computed from the old
        # contents.
        pieces = ["国家", "通用"]
        pipe = Pipeline(**_BASE, user_seg_dict={"国家通用": pieces})
        pieces.append("盲文")
        assert pipe.user_seg_dict["国家通用"] == ("国家", "通用")

    def test_replace_carries_the_dictionary(self) -> None:
        pipe = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})
        derived = replace(pipe, mode="strict")
        assert derived.user_seg_dict["国家"] == ("国家",)

    def test_empty_dictionary_is_a_no_op(self) -> None:
        assert Pipeline(**_BASE).fingerprint == Pipeline(
            **_BASE, user_seg_dict={}
        ).fingerprint


class TestAMalformedEntryDoesNotStopConstruction:
    """A personal dictionary is hand-editable, so an entry can be any shape.

    The consuming side has always said an unusable record is skipped rather
    than raised on — "one bad record cannot stop a document compiling" — but
    the Pipeline froze the mapping with ``tuple(v)`` before anything looked at
    it, so ``{"国家": None}`` raised ``TypeError`` out of ``Pipeline(...)``
    itself. An application whose stored dictionary had one bad line could not
    build a pipeline at all: not a compile that degrades, a front-end that
    won't start.
    """

    @pytest.mark.parametrize(
        "entry",
        [
            {"国家": None},
            {"国家": 123},
            {"国家": ("国", None)},
            {"国家": ("国", 7)},
            {"国家": object()},
            {"国家": "国家"},  # a bare string is ambiguous, see below
            {123: ("国", "家")},
            {"国家": [["国"], ["家"]]},
        ],
        ids=repr,
    )
    def test_the_pipeline_still_builds(self, entry: dict) -> None:
        pipe = Pipeline(**_BASE, user_seg_dict=entry)
        assert "国家" not in pipe.user_seg_dict
        # And it still compiles — the fingerprint is computed from what the
        # pipeline actually holds, so a dropped entry cannot poison it either.
        assert pipe.fingerprint == Pipeline(**_BASE).fingerprint

    def test_a_bare_string_value_is_refused_not_iterated(self) -> None:
        """``{"国家": "国家"}`` reads as *this is one word* and would iterate
        to ``("国", "家")`` — the opposite instruction. Too ambiguous to
        guess at, so it is dropped; pieces are written as a sequence."""
        pipe = Pipeline(**_BASE, user_seg_dict={"国家": "国家"})
        assert "国家" not in pipe.user_seg_dict
        assert _BLANK in _cells(pipe, "国家")  # unchanged: still two words

    def test_the_good_entries_beside_a_bad_one_still_apply(self) -> None:
        pipe = Pipeline(
            **_BASE,
            user_seg_dict={"国家": ("国家",), "银行": None},  # type: ignore[dict-item]
        )
        assert pipe.user_seg_dict["国家"] == ("国家",)
        assert "银行" not in pipe.user_seg_dict
        assert _BLANK not in _cells(pipe, "国家")

    def test_a_semantically_invalid_entry_is_still_dropped_downstream(self) -> None:
        """Structure is the Pipeline's business; whether the pieces spell
        their own key is the language's, and stays with the tokenizer
        post-pass. The entry survives the freeze and changes nothing."""
        pipe = Pipeline(**_BASE, user_seg_dict={"国家": ("国", "家", "们")})
        assert pipe.user_seg_dict["国家"] == ("国", "家", "们")
        assert _cells(pipe, "国家") == _cells(Pipeline(**_BASE), "国家")


class TestPopulatedIrIsRebuiltForANewDictionary:
    def test_reused_ir_does_not_keep_the_other_divisions(self) -> None:
        # A DocumentIR populated by one pipeline, translated by another: the
        # frontend stamp must invalidate the children, or the second compile
        # silently emits the first configuration's word divisions.
        plain = Pipeline(**_BASE)
        pinned = Pipeline(**_BASE, user_seg_dict={"国家": ("国家",)})

        # parse_text populates children under ``plain``'s configuration.
        doc = plain.parse_text("国家")
        plain.translate_document(doc)
        rebuilt = pinned.translate_document(doc).render("unicode")

        assert rebuilt == _cells(pinned, "国家")
        assert _BLANK not in rebuilt
