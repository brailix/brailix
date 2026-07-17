"""Stateful property test: incremental recompilation ≡ fresh full compile.

:meth:`Pipeline.translate_document`'s incremental contract: the document is
mutated in place — children populated where missing, reused where safe —
guarded two ways (text mismatch drops stale children; a differing pipeline
fingerprint drops children built under another configuration). The promise
behind all of that machinery is single: **reuse is an optimization, never
observable in the output**. Whatever edit sequence a front-end performs,
compiling the in-place document must equal compiling a from-scratch
DocumentIR over the same current texts.

Hypothesis drives randomized edit sequences (edit text, clear to empty —
a past regression, add / remove blocks, recompile without changes, hand
the SAME document to a differently-configured pipeline) against a
reference model that is just the list of block texts. Every compile step
checks output equivalence, diagnostic equivalence and recompile
idempotence. This replaces an unbounded family of "edit A then B then
recompile" example tests.

The block-level primitive (:meth:`Pipeline.translate_block`) gets the same
treatment stateleslly below: block output must match the document path,
and ``source_hash`` must key exactly on (content, configuration).
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    precondition,
    rule,
)

from brailix.ir.document import DocumentIR, Paragraph
from brailix.pipeline import Pipeline

_PROFILES = ("cn_current", "cn_ncb")

_TEXT_ALPHABET = "我在重庆年好中文行长数一二三abcXY012,。!?, .%©"
_texts = st.text(alphabet=st.sampled_from(list(_TEXT_ALPHABET)), max_size=10)

# One pipeline per profile for the whole module: Pipeline holds no
# document cache (documented), so sharing across examples is sound — and
# it keeps adapter model loading a per-process cost.
_PIPES = {name: Pipeline(profile=name) for name in _PROFILES}


def _braille(result) -> list[dict]:
    return [b.to_dict() for b in result.braille_ir.blocks]


def _codes(result) -> list[str]:
    return sorted(w.code for w in result.warnings)


class IncrementalCompileMachine(RuleBasedStateMachine):
    """Random edit sequences over one in-place document vs a text model."""

    def __init__(self) -> None:
        super().__init__()
        self.profile = _PROFILES[0]
        self.texts: list[str] = []
        self.doc = DocumentIR()

    # --- edits ---------------------------------------------------------

    @precondition(lambda self: len(self.texts) < 5)
    @rule(text=_texts)
    def add_block(self, text: str) -> None:
        self.doc.blocks.append(Paragraph(text=text))
        self.texts.append(text)

    @precondition(lambda self: self.texts)
    @rule(index=st.integers(0, 9))
    def remove_block(self, index: int) -> None:
        i = index % len(self.texts)
        del self.doc.blocks[i]
        del self.texts[i]

    @precondition(lambda self: self.texts)
    @rule(index=st.integers(0, 9), text=_texts)
    def edit_block_text(self, index: int, text: str) -> None:
        # Mutating ``text`` on a block whose children were built from the
        # old text — the populate guard must drop the stale children.
        i = index % len(self.texts)
        self.doc.blocks[i].text = text
        self.texts[i] = text

    @precondition(lambda self: self.texts)
    @rule(index=st.integers(0, 9))
    def clear_block_text(self, index: int) -> None:
        # Clearing to the empty string is its own rule: falsy-text staleness
        # was a real regression (empty new text once kept the old children).
        i = index % len(self.texts)
        self.doc.blocks[i].text = ""
        self.texts[i] = ""

    @rule(name=st.sampled_from(_PROFILES))
    def switch_profile(self, name: str) -> None:
        # The SAME in-place document will next compile under this pipeline;
        # children stamped by the other configuration must be rebuilt.
        self.profile = name

    # --- the property ----------------------------------------------------

    @rule()
    def compile_and_check(self) -> None:
        pipe = _PIPES[self.profile]
        incremental = pipe.translate_document(self.doc)
        fresh = pipe.translate_document(
            DocumentIR(blocks=[Paragraph(text=t) for t in self.texts])
        )
        assert _braille(incremental) == _braille(fresh)
        assert _codes(incremental) == _codes(fresh)
        # Compiling again with no edit in between must change nothing.
        again = pipe.translate_document(self.doc)
        assert _braille(again) == _braille(incremental)

    def teardown(self) -> None:
        # Every generated sequence validates at least once, even if the
        # rule mix never drew an explicit compile step.
        self.compile_and_check()


TestIncrementalCompile = IncrementalCompileMachine.TestCase
TestIncrementalCompile.settings = settings(
    max_examples=12, stateful_step_count=20, deadline=None
)


# --- block-level primitive ----------------------------------------------------


class TestTranslateBlockEquivalence:
    @settings(max_examples=25)
    @given(text=_texts, profile=st.sampled_from(_PROFILES))
    def test_block_path_matches_document_path(self, text: str, profile: str) -> None:
        # One paragraph compiled through the incremental block primitive
        # must byte-match the same paragraph compiled as a document — the
        # two entries are the same compiler, not two dialects.
        pipe = _PIPES[profile]
        compiled = pipe.translate_block(Paragraph(text=text))
        doc_result = pipe.translate_document(DocumentIR(blocks=[Paragraph(text=text)]))
        assert [b.to_dict() for b in compiled.braille_blocks] == _braille(doc_result)

    @settings(max_examples=25)
    @given(text=_texts, profile=st.sampled_from(_PROFILES))
    def test_recompile_is_deterministic_with_stable_hash(
        self, text: str, profile: str
    ) -> None:
        pipe = _PIPES[profile]
        first = pipe.translate_block(Paragraph(text=text))
        second = pipe.translate_block(Paragraph(text=text))
        assert first.source_hash == second.source_hash
        assert [b.to_dict() for b in first.braille_blocks] == [
            b.to_dict() for b in second.braille_blocks
        ]

    @settings(max_examples=25)
    @given(text=_texts)
    def test_source_hash_keys_on_configuration(self, text: str) -> None:
        # The same block under differently-configured pipelines must hash
        # apart, or a front-end cache would serve one standard's braille
        # for the other.
        hashes = {
            profile: _PIPES[profile].translate_block(Paragraph(text=text)).source_hash
            for profile in _PROFILES
        }
        assert hashes["cn_current"] != hashes["cn_ncb"]
