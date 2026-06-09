"""Tests for :mod:`brailix.backend.block` — block-level expansion.

The block module is what makes List / Table / MathBlock work
end-to-end. These tests cover:

* Lists expand into one ``list_item`` :class:`BrailleBlock` per
  source item, with the right marker prepended.
* Tables expand into one ``table_row`` block per source row, with
  columns separated by blanks.
* Simple blocks (Paragraph, Heading, Quote, CodeBlock, Footnote,
  ImageAlt) round-trip through one block and preserve metadata.
* MathBlock parses via the math frontend; parse failures soft-fail
  with a warning + unknown cells (never crashes the pipeline).
"""

import pytest

from brailix.backend.block import expand_block
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.core.span import Span
from brailix.ir.document import (
    CodeBlock,
    Footnote,
    Heading,
    ImageAlt,
    List,
    ListItem,
    MathBlock,
    Paragraph,
    Quote,
    Table,
    TableCell,
    TableRow,
)
from brailix.ir.inline import HanziChar, Word


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(mode=RunMode.NORMAL, warnings=WarningCollector())


@pytest.fixture(scope="module")
def pipe():
    """Minimal-deps Pipeline for tests that exercise the full
    frontend → backend path on MathBlock / CodeBlock (after the G0
    refactor, raw-text math/code parsing lives in Pipeline, not in
    backend.block)."""
    from brailix import Pipeline

    return Pipeline(profile="cn_current", analyzer="char", resolver="null")


# ---------------------------------------------------------------------------
# Simple blocks
# ---------------------------------------------------------------------------


class TestParagraph:
    def test_single_block_returned(self, ctx, profile):
        para = Paragraph(children=[HanziChar(surface="我", reading="wo3")])
        out = expand_block(para, ctx, profile)
        assert len(out) == 1
        assert out[0].block_type == "paragraph"
        assert out[0].cells, "paragraph should produce some cells"

    def test_empty_paragraph_returns_block_with_no_cells(self, ctx, profile):
        out = expand_block(Paragraph(children=[]), ctx, profile)
        assert len(out) == 1
        assert out[0].cells == []

    def test_align_flows_to_braille_block(self, ctx, profile):
        para = Paragraph(
            align="center", children=[HanziChar(surface="我", reading="wo3")]
        )
        out = expand_block(para, ctx, profile)
        assert out[0].align == "center"

    def test_no_align_defaults_to_none(self, ctx, profile):
        para = Paragraph(children=[HanziChar(surface="我", reading="wo3")])
        out = expand_block(para, ctx, profile)
        assert out[0].align is None


class TestHeading:
    def test_heading_level_preserved(self, ctx, profile):
        h = Heading(level=2, children=[HanziChar(surface="题", reading="ti2")])
        out = expand_block(h, ctx, profile)
        assert out[0].block_type == "heading"
        assert out[0].heading_level == 2


class TestQuote:
    def test_quote_passes_inline_content(self, ctx, profile):
        q = Quote(children=[Word(surface="他说", reading="ta1 shuo1")])
        out = expand_block(q, ctx, profile)
        assert out[0].block_type == "quote"
        assert out[0].cells


class TestCodeBlock:
    def test_code_block_returns_one_block(self, ctx, profile):
        # CodeBlock with no children but raw text — backend just emits
        # whatever children are present. Frontend would populate them.
        cb = CodeBlock(language="python", children=[])
        out = expand_block(cb, ctx, profile)
        assert len(out) == 1
        assert out[0].block_type == "code_block"

    def test_code_block_with_text_emits_cells_verbatim(self, pipe):
        # Pipeline wraps raw CodeBlock.text as a single CodeInline
        # child (avoids running the Chinese tokenizer over source
        # code); the backend then emits one cell per character via
        # the punct path.
        cb = CodeBlock(language="python", text="x=1")
        out = pipe.translate_block(cb)
        assert len(out.braille_blocks) == 1
        assert out.braille_blocks[0].block_type == "code_block"
        # Three source chars → three cells (any mapping; we just care
        # we got per-character emission).
        assert len(out.braille_blocks[0].cells) == 3

    def test_code_block_prefers_children_when_present(self, ctx, profile):
        # If a caller pre-populated children (e.g. a custom frontend),
        # those win — we don't double-translate the same source.
        from brailix.ir.inline import CodeInline

        cb = CodeBlock(
            language="python",
            text="ignored",
            children=[CodeInline(surface="a")],
        )
        out = expand_block(cb, ctx, profile)
        # One child → one cell from the child path, not from block.text.
        assert len(out[0].cells) == 1


class TestImageAlt:
    def test_image_alt_block_type(self, ctx, profile):
        ia = ImageAlt(children=[Word(surface="图片", reading="tu2 pian4")])
        out = expand_block(ia, ctx, profile)
        assert out[0].block_type == "image_alt"


# ---------------------------------------------------------------------------
# Footnote: ref marker prepended
# ---------------------------------------------------------------------------


class TestFootnote:
    def test_no_ref_yields_plain_block(self, ctx, profile):
        f = Footnote(children=[Word(surface="附注", reading="fu4 zhu4")])
        out = expand_block(f, ctx, profile)
        assert out[0].block_type == "footnote"

    def test_numeric_ref_prepends_marker(self, ctx, profile):
        f = Footnote(ref="1", children=[Word(surface="注", reading="zhu4")])
        out = expand_block(f, ctx, profile)
        roles = [c.role for c in out[0].cells]
        # The marker run should contain a number_sign + footnote_ref cells
        # before the body content.
        assert "footnote_ref" in roles

    def test_letter_ref_prepends_marker(self, ctx, profile):
        f = Footnote(ref="a", children=[Word(surface="注", reading="zhu4")])
        out = expand_block(f, ctx, profile)
        roles = [c.role for c in out[0].cells]
        assert "footnote_ref" in roles


# ---------------------------------------------------------------------------
# List: one block per item, marker prepended
# ---------------------------------------------------------------------------


class TestList:
    def test_unordered_list_one_block_per_item(self, ctx, profile):
        lst = List(
            ordered=False,
            items=[
                ListItem(children=[Word(surface="一项", reading="yi1 xiang4")]),
                ListItem(children=[Word(surface="二项", reading="er4 xiang4")]),
            ],
        )
        out = expand_block(lst, ctx, profile)
        assert len(out) == 2
        assert all(b.block_type == "list_item" for b in out)

    def test_unordered_list_marker_uses_middle_dot(self, ctx, profile):
        lst = List(
            ordered=False,
            items=[ListItem(children=[HanziChar(surface="项", reading="xiang4")])],
        )
        out = expand_block(lst, ctx, profile)
        roles = [c.role for c in out[0].cells]
        assert "list_marker" in roles

    def test_ordered_list_marker_uses_number(self, ctx, profile):
        lst = List(
            ordered=True,
            items=[
                ListItem(children=[HanziChar(surface="一", reading="yi1")]),
                ListItem(children=[HanziChar(surface="二", reading="er4")]),
            ],
        )
        out = expand_block(lst, ctx, profile)
        # Each item starts with a number_sign + digit + period.
        roles_per_item = [[c.role for c in b.cells] for b in out]
        for roles in roles_per_item:
            assert "number_sign" in roles
            assert "digit" in roles
            # Period cell comes through as role="list_marker".
            assert "list_marker" in roles


# ---------------------------------------------------------------------------
# Table: one block per row, cells separated by two blanks
# ---------------------------------------------------------------------------


class TestTable:
    def test_table_one_block_per_row(self, ctx, profile):
        t = Table(rows=[
            TableRow(cells=[
                TableCell(children=[HanziChar(surface="甲", reading="jia3")]),
                TableCell(children=[HanziChar(surface="乙", reading="yi3")]),
            ]),
            TableRow(cells=[
                TableCell(children=[HanziChar(surface="丙", reading="bing3")]),
                TableCell(children=[HanziChar(surface="丁", reading="ding1")]),
            ]),
        ])
        out = expand_block(t, ctx, profile)
        assert len(out) == 2
        assert all(b.block_type == "table_row" for b in out)

    def test_table_columns_separated_by_blanks(self, ctx, profile):
        t = Table(rows=[
            TableRow(cells=[
                TableCell(children=[HanziChar(surface="甲", reading="jia3")]),
                TableCell(children=[HanziChar(surface="乙", reading="yi3")]),
            ]),
        ])
        out = expand_block(t, ctx, profile)
        # Find two consecutive blank cells somewhere in the row.
        cells = out[0].cells
        blank_runs = sum(
            1 for i in range(len(cells) - 1)
            if cells[i].is_blank and cells[i + 1].is_blank
        )
        assert blank_runs >= 1


# ---------------------------------------------------------------------------
# MathBlock
# ---------------------------------------------------------------------------


class TestMathBlock:
    def test_empty_math_block_returns_empty_cells(self, ctx, profile):
        mb = MathBlock(source="latex", text="")
        out = expand_block(mb, ctx, profile)
        assert len(out) == 1
        assert out[0].cells == []

    def test_parse_failure_falls_back_to_unknown_cells(self, pipe):
        # ``\foo``-style garbage: the math frontend either returns
        # <merror> or raises. Pipeline catches either path; we just
        # require cells emitted and no crash.
        mb = MathBlock(source="latex", text="\\foobar nonsense {{{")
        out = pipe.translate_block(mb)
        assert len(out.braille_blocks) == 1
        assert out.braille_blocks[0].block_type == "math_block"
        assert out.braille_blocks[0].cells != []

    def test_valid_math_produces_cells(self, pipe):
        mb = MathBlock(source="latex", text="x + y")
        out = pipe.translate_block(mb)
        assert out.braille_blocks[0].cells

    def test_parse_exception_emits_warning_and_unknown_cells(
        self, pipe, monkeypatch
    ):
        # When the math frontend raises (rather than returning <merror>),
        # Pipeline._populate_math_block catches it, records a
        # ``MATH_BLOCK_PARSE_FAILED`` warning, and populates per-char
        # Unknown nodes so the backend emits one cell per source char
        # (layout stays stable). Pipeline uses a lazy import of
        # ``parse_math_tree`` so this monkeypatch is observed at the
        # call site.
        import brailix.frontend as frontend_mod

        def _boom(*_a, **_kw):
            raise RuntimeError("synthetic adapter crash")

        monkeypatch.setattr(frontend_mod, "parse_math_tree", _boom)

        mb = MathBlock(source="latex", text="abc", span=Span(0, 3))
        out = pipe.translate_block(mb)
        assert len(out.braille_blocks) == 1
        cells = out.braille_blocks[0].cells
        # Three source chars → three unknown cells from the fallback path.
        assert len(cells) == 3
        assert all(c.role == "unknown" for c in cells)
        assert [c.source_text for c in cells] == ["a", "b", "c"]
        assert [c.source_span for c in cells] == [
            Span(0, 1), Span(1, 2), Span(2, 3),
        ]
        codes = [w.code for w in out.warnings]
        assert "MATH_BLOCK_PARSE_FAILED" in codes

    def test_parse_exception_without_span_skips_span_attribution(
        self, pipe, monkeypatch
    ):
        # When the source block has no span, fallback Unknown nodes
        # also have no span — preserved through to per-cell source_span.
        import brailix.frontend as frontend_mod

        def _boom(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(frontend_mod, "parse_math_tree", _boom)

        mb = MathBlock(source="latex", text="xy", span=None)
        out = pipe.translate_block(mb)
        assert [c.source_span for c in out.braille_blocks[0].cells] == [None, None]


# ---------------------------------------------------------------------------
# Footnote ref edge cases — exercise the punct / digit-without-number-sign /
# unmapped-char arms of ``_footnote_ref_cells``.
# ---------------------------------------------------------------------------


class TestFootnoteRefEdges:
    def test_empty_ref_emits_no_marker_cells(self, profile):
        # Call the helper directly: the public expander short-circuits
        # before reaching it for empty refs, but the helper is defensive
        # so we lock that behaviour too.
        from brailix.backend.block import _footnote_ref_cells

        assert _footnote_ref_cells("", profile) == []

    def test_digit_after_letter_re_emits_number_sign(self, profile):
        # A ref like ``1a2`` must re-emit the number sign before the
        # trailing ``2`` so it isn't read as a letter; deduping on "any
        # number_sign already present" dropped it.
        from brailix.backend.block import _footnote_ref_cells

        cells = _footnote_ref_cells("1a2", profile)
        roles = [c.role for c in cells]
        assert roles.count("number_sign") == 2  # two separate digit runs
        twos = [i for i, c in enumerate(cells) if c.source_text == "2"]
        assert twos, "no cell carrying digit '2'"
        assert cells[twos[0] - 1].role == "number_sign"

    def test_punctuation_ref_uses_punct_table(self, ctx, profile):
        # A period has no letter / digit mapping but the punctuation
        # table can spell it. The ref cells come out with role
        # ``footnote_ref`` regardless of which table answered.
        f = Footnote(ref=".", children=[Word(surface="注", reading="zhu4")])
        out = expand_block(f, ctx, profile)
        roles = [c.role for c in out[0].cells]
        assert "footnote_ref" in roles

    def test_unknown_char_in_ref_emits_unknown_cell(self, ctx, profile):
        # Snowman has no letter / punct / digit mapping in cn_current.
        # The ref helper still produces one cell so the marker position
        # is preserved — but with role ``unknown``.
        f = Footnote(ref="☃", children=[Word(surface="注", reading="zhu4")])
        out = expand_block(f, ctx, profile)
        roles = [c.role for c in out[0].cells]
        assert "unknown" in roles
