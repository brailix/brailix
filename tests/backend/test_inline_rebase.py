"""Unit tests for :mod:`brailix.backend._inline` — the
InlineTextTranslator seam's coordinate contract.

The injected translator runs a private frontend over a throwaway
one-paragraph document, so its cells carry 0-based spans of that
document.  All four call sites (math ``<mtext>``, chem conditions,
music ``<words>``, inline lyrics) re-anchor through this helper —
regression: the raw spans made proofread double-clicks jump to the
start of the file.
"""

from __future__ import annotations

from brailix.backend._inline import rebase_translated_cells
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell


def _cell(span: Span | None, role: str = "zh_syllable") -> BrailleCell:
    return BrailleCell(
        dots=(1,), role=role, source_span=span, source_text="中"
    )


class TestRebaseTranslatedCells:
    def test_spans_move_to_host_span(self):
        host = Span(100, 120)
        cells = [_cell(Span(0, 1)), _cell(Span(1, 2))]
        out = rebase_translated_cells(cells, host)
        assert [c.source_span for c in out] == [host, host]
        # source_text and role survive untouched.
        assert all(c.source_text == "中" for c in out)
        assert all(c.role == "zh_syllable" for c in out)

    def test_role_retag(self):
        out = rebase_translated_cells(
            [_cell(Span(0, 1))], Span(5, 9), role="music_lyric"
        )
        assert out[0].role == "music_lyric"
        assert out[0].source_span == Span(5, 9)

    def test_none_host_span_allowed(self):
        out = rebase_translated_cells([_cell(Span(0, 1))], None)
        assert out[0].source_span is None

    def test_empty_input(self):
        assert rebase_translated_cells([], Span(0, 1)) == []
