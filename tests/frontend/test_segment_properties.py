"""Property-based tests for the segmenter's span contract.

The segmenter is the first provenance-bearing stage: everything downstream
(inline nodes, braille cells, proofreading jumps) inherits its spans. Two
invariants must hold for *any* input text, not just curated examples:

* **Tiling** — the segments partition the text exactly: surfaces concatenate
  back to the input, spans are contiguous, ascending and non-empty, and each
  surface equals the span's slice of the text. Losing or duplicating even one
  character here silently corrupts every cell↔source mapping built on top.
* **Coordinates** — segment spans are expressed in the *block's* coordinate
  system: shifted by ``block.span.start`` when the block carries a span, and
  0-based (leaf-local) when it does not (the ``run_frontend`` path always
  passes a bare, span-less paragraph).

Type-classification behaviour (which run is hanzi vs latin vs digit) is
example-tested in ``test_segment.py``; this module only pins the geometry.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.segment import DefaultSegmenter
from brailix.ir.document import Paragraph
from brailix.ir.inline import Segment

# A deliberately mixed alphabet: CJK (including a supplementary-plane hanzi,
# which is one code point but two UTF-16 units — a classic off-by-one source),
# Latin, ASCII + fullwidth digits, Chinese + ASCII punctuation, whitespace
# variants, and a few characters with no braille meaning at all.
_ALPHABET = (
    "我在重庆年月好电脑中文盲字数一二三〇𠀀"
    "abcXYZ"
    "0123456789０１２"
    ",。!?、;:「」,.!?;:()\"'-%"
    " \t\n　"
    "©émoji🎵"
)

_texts = st.text(alphabet=st.sampled_from(list(_ALPHABET)), max_size=60)


def _segment(text: str, span: Span | None) -> list[Segment]:
    block = Paragraph(text=text, span=span)
    return DefaultSegmenter().segment(block, FrontendContext(profile="cn_current"))


def _assert_tiles(segments: list[Segment], text: str, base: int) -> None:
    assert "".join(s.surface for s in segments) == text
    cursor = base
    for seg in segments:
        assert seg.span is not None
        assert seg.span.start == cursor
        assert seg.span.length == len(seg.surface)
        assert not seg.span.is_empty()
        assert text[seg.span.start - base : seg.span.end - base] == seg.surface
        cursor = seg.span.end
    assert cursor == base + len(text)


class TestSegmentSpans:
    @given(text=_texts, base=st.integers(0, 40))
    def test_segments_tile_the_block_span(self, text: str, base: int) -> None:
        segments = _segment(text, Span(base, base + len(text)))
        _assert_tiles(segments, text, base)

    @given(text=_texts)
    def test_spanless_block_segments_are_leaf_local(self, text: str) -> None:
        # ``run_frontend`` hands the segmenter a bare Paragraph with no span;
        # the resulting segment spans must be 0-based offsets into the text —
        # this is what makes inline-node spans leaf-local by construction.
        segments = _segment(text, None)
        _assert_tiles(segments, text, 0)

    @given(text=_texts, base=st.integers(0, 40))
    def test_segmentation_is_deterministic(self, text: str, base: int) -> None:
        span = Span(base, base + len(text))
        first = [(s.type, s.surface, s.span) for s in _segment(text, span)]
        second = [(s.type, s.surface, s.span) for s in _segment(text, span)]
        assert first == second

    @given(text=_texts)
    def test_empty_text_yields_no_segments(self, text: str) -> None:
        if not text:
            assert _segment(text, None) == []

    def test_touching_protected_regions_both_survive(self) -> None:
        # Two inline-math islands sharing a boundary character position:
        # half-open overlap must treat touching as NON-overlapping, or the
        # second island is silently dropped from protection. (Mutation
        # testing: a <= boundary flip in the overlap check survived — the
        # generated alphabet carries no ``$`` so this shape never arose.)
        segments = _segment("$x$$y$", None)
        assert [s.type for s in segments] == ["math_inline", "math_inline"]
        assert [s.surface for s in segments] == ["$x$", "$y$"]
