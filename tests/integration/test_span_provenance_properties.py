"""Property-based tests for cell-level provenance through a real compile.

ARCHITECTURE §3's core promise — *every braille cell maps to a source span* —
is what the whole proofreading system (click a cell, land on the character)
stands on. The backend upholds it structurally (the dispatch boundary raises
on a span-less cell), and :meth:`BrailleDocument.validate_traceability` turns
it into a first-class check. Here we drive whole compiles over generated
text — prose, digits, punctuation, characters with no braille mapping at all
— and assert the promise holds unconditionally, plus that every span stays
inside its owning leaf's coordinate space (spans are leaf-local, 0-based per
block).

Curated end-to-end examples live in ``test_source_span_contract.py``; this
module exists to catch the shapes nobody thought to enumerate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.pipeline import Pipeline

# Mixed prose alphabet: hanzi (BMP + supplementary plane), Latin, ASCII and
# fullwidth digits, Chinese + ASCII punctuation, whitespace, plus characters
# guaranteed to have no braille rule (dingbats) so the unknown-fallback path
# is exercised too — its placeholder cells must carry spans like any other.
_ALPHABET = (
    "我在重庆年月日好电脑中文盲字数学一二三百〇𠀀"
    "abcdXYZ"
    "0123456789０１２"
    ",。!?、;:,.!?;:()%-"
    " \t　"
    "©☂🎵"
)

_texts = st.text(alphabet=st.sampled_from(list(_ALPHABET)), max_size=40)

# One pipeline per module: profile loading and lazy adapter model loading
# (tokenizer, pinyin) are per-process costs, not per-example ones.
_PIPE = Pipeline(profile="cn_current")


class TestCellProvenance:
    @settings(max_examples=40)
    @given(text=_texts)
    def test_every_cell_carries_a_span_inside_the_leaf(self, text: str) -> None:
        result = _PIPE.translate_text(text)
        assert result.braille_ir.validate_traceability() == []
        for cell in result.braille_ir.all_cells():
            assert cell.source_span is not None
            # Leaf-local: offsets into this one-paragraph document's text.
            assert 0 <= cell.source_span.start <= cell.source_span.end <= len(text)

    @settings(max_examples=25)
    @given(
        lines=st.lists(
            st.text(alphabet=st.sampled_from(list(_ALPHABET.replace("\t", ""))), min_size=1, max_size=15).map(
                lambda s: s.strip() or "字"
            ),
            min_size=1,
            max_size=4,
        )
    )
    def test_multi_block_spans_stay_leaf_local(self, lines: list[str]) -> None:
        # In a multi-block document every block restarts leaf-local
        # coordinates at 0. A cell span reaching past its own block's text
        # length would mean provenance leaked across block boundaries — the
        # bug class the two-tier coordinate contract exists to prevent.
        source = "\n".join(lines)
        doc = _PIPE.parse_text(source, format="plain")
        result = _PIPE.translate_document(doc)
        assert result.braille_ir.validate_traceability() == []
        assert len(result.braille_ir.blocks) == len(doc.blocks)
        for src_block, braille_block in zip(doc.blocks, result.braille_ir.blocks, strict=True):
            assert src_block.text is not None
            for cell in braille_block.cells:
                assert cell.source_span is not None
                assert 0 <= cell.source_span.start
                assert cell.source_span.end <= len(src_block.text)
