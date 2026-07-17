"""Property-based tests for the input adapters' block-span contract.

``Block.span`` documents a two-tier coordinate contract (see
:class:`brailix.ir.document.Block`): the plain-text adapter upholds the
*exact-slice* guarantee ``source[span] == block.text`` for every block, the
Markdown adapter for headings, list items and single-line paragraphs (with
the ``#`` / ``-`` / ``1.`` markers *outside* the span). Derived-text blocks
(multi-line paragraphs, quotes, fences, table cells) only promise a
line-range location.

Everything that rebases leaf-local cell spans into the source document —
editor highlighting, proofreading jumps — leans on this contract, so it is
pinned here over generated documents rather than a handful of samples.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st

from brailix.input import parse_markdown, parse_plain
from brailix.ir.document import Block

# Content characters for a single source line: no newlines (line structure is
# generated explicitly), no Markdown structure characters at arbitrary
# positions that could turn a paragraph into a different block kind
# (# - * > | ` $ digits-with-dot are exercised via the explicit line shapes
# below instead).
_CONTENT_ALPHABET = "我在重庆年好中文盲字abcXYZ089,。!?;:%é"

_content = st.text(alphabet=st.sampled_from(list(_CONTENT_ALPHABET)), min_size=1, max_size=20).map(
    # Leading / trailing spaces are structure in Markdown (4-space indent is
    # a code block; trailing doubles are a hard break) — keep content snug.
    lambda s: s.strip()
).filter(lambda s: s)


def _walk(blocks: list[Block]):
    for block in blocks:
        yield block
        for attr in ("items", "rows", "cells"):
            kids = getattr(block, attr, None)
            if kids:
                yield from _walk(kids)


# --- plain text --------------------------------------------------------------

# A plain document is just lines and blank separators. Unlike Markdown,
# leading / trailing whitespace carries no structure in plain text — the
# adapter trims it and re-anchors the span at the first non-blank character,
# so padded lines are generated on purpose to exercise that adjustment.
_padding = st.sampled_from(["", " ", "  ", "\t"])
_padded_line = st.builds(lambda lead, body, tail: lead + body + tail, _padding, _content, _padding)
_plain_lines = st.lists(
    st.one_of(_padded_line, _padding),
    min_size=0,
    max_size=10,
)


class TestPlainExactSlice:
    @given(lines=_plain_lines)
    def test_every_block_is_an_exact_slice(self, lines: list[str]) -> None:
        source = "\n".join(lines)
        doc = parse_plain(source, language="zh-CN", profile="cn_current")
        for block in _walk(doc.blocks):
            if block.span is None:
                # Documented fallback: genuinely empty input keeps a single
                # anchor block with nothing to point at. Any OTHER span-less
                # block would break the exact-slice contract.
                assert source == ""
                continue
            assert source[block.span.start : block.span.end] == block.text

    @given(lines=_plain_lines)
    def test_blocks_are_ordered_and_disjoint(self, lines: list[str]) -> None:
        source = "\n".join(lines)
        doc = parse_plain(source, language="zh-CN", profile="cn_current")
        spans = [b.span for b in _walk(doc.blocks) if b.span is not None]
        for prev, cur in zip(spans, spans[1:], strict=False):
            assert prev.end <= cur.start
        for span in spans:
            assert 0 <= span.start <= span.end <= len(source)


# --- markdown ----------------------------------------------------------------

# One generated Markdown "unit" per draw: (kind, rendered source lines).
_heading = st.tuples(st.integers(1, 6), _content).map(
    lambda t: ("heading", ["#" * t[0] + " " + t[1]])
)
_paragraph_single = _content.map(lambda c: ("paragraph", [c]))
_paragraph_multi = st.lists(_content, min_size=2, max_size=3).map(
    lambda cs: ("paragraph", list(cs))
)
_unordered_list = st.lists(_content, min_size=1, max_size=3).map(
    lambda cs: ("list", ["- " + c for c in cs])
)
_ordered_list = st.lists(_content, min_size=1, max_size=3).map(
    lambda cs: ("list", [f"{i + 1}. {c}" for i, c in enumerate(cs)])
)
_units = st.lists(
    st.one_of(_heading, _paragraph_single, _paragraph_multi, _unordered_list, _ordered_list),
    min_size=1,
    max_size=6,
)


def _render(units: list[tuple[str, list[str]]]) -> str:
    return "\n\n".join("\n".join(lines) for _, lines in units)


class TestMarkdownExactSlice:
    @given(units=_units)
    def test_marker_bearing_blocks_slice_exactly(self, units: list[tuple[str, list[str]]]) -> None:
        # Headings and list items promise the exact-slice contract with the
        # marker prefix OUTSIDE the span: the span must point at the content
        # a proofreading jump should land on, never at ``# `` / ``- ``.
        source = _render(units)
        doc = parse_markdown(source, language="zh-CN", profile="cn_current")
        for block in _walk(doc.blocks):
            if block.type in ("heading", "list_item"):
                assert block.span is not None
                assert source[block.span.start : block.span.end] == block.text

    @given(units=_units)
    def test_single_line_paragraphs_slice_exactly(self, units: list[tuple[str, list[str]]]) -> None:
        # A paragraph whose span covers no newline was a single source line —
        # exactly the case the contract promises to keep content-exact. (A
        # multi-line paragraph's text is space-joined, so it only carries a
        # line-range span and is deliberately not asserted here.)
        source = _render(units)
        doc = parse_markdown(source, language="zh-CN", profile="cn_current")
        for block in _walk(doc.blocks):
            if block.type != "paragraph" or block.span is None:
                continue
            slice_ = source[block.span.start : block.span.end]
            if "\n" not in slice_:
                assert slice_ == block.text

    @given(units=_units)
    def test_every_span_lies_inside_the_source(self, units: list[tuple[str, list[str]]]) -> None:
        source = _render(units)
        doc = parse_markdown(source, language="zh-CN", profile="cn_current")
        for block in _walk(doc.blocks):
            if block.span is not None:
                assert 0 <= block.span.start <= block.span.end <= len(source)

    @given(units=_units)
    def test_list_items_lie_inside_their_list(self, units: list[tuple[str, list[str]]]) -> None:
        source = _render(units)
        doc = parse_markdown(source, language="zh-CN", profile="cn_current")
        for block in doc.blocks:
            items = getattr(block, "items", None)
            if not items or block.span is None:
                continue
            for item in items:
                if item.span is not None:
                    assert block.span.contains(item.span)
