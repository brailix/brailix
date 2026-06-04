"""Music layout: scheme registry + single_line format (BANA §24.1).

``score`` / ``music_block`` are laid out by the active BANA *format* — a
:class:`~brailix.renderer.music_layout.MusicLayoutScheme` looked up by
``LayoutOptions.music_scheme``, not a hard-coded branch.  The default
``single_line`` (BANA §24.1):

* breaks **only** at measure-separator cells (``music_measure_sep``),
  keeping every measure indivisible (BANA Pars. 11 / 17);
* never emits a continuation hyphen;
* lets a measure too wide for the line run over unbroken;
* uses a hanging indent — first line at the margin, run-over lines at
  the third cell (§24.1.1).
"""

from __future__ import annotations

from brailix.core.span import Span
from brailix.ir.braille import BrailleBlock, BrailleCell, BrailleDocument
from brailix.renderer.layout import LayoutOptions, LayoutRenderer
from brailix.renderer.music_layout import (
    SingleLineScheme,
    get_scheme,
    scheme_names,
    wrap_measures,
)
from brailix.renderer.unicode_braille import dots_to_char

BLANK_CHAR = dots_to_char(())  # U+2800 — what a dots=() cell renders to
NOTE_CHAR = dots_to_char((1,))
HYPHEN_CHAR = dots_to_char((3, 6))  # default text continuation hyphen
SEP = BrailleCell(dots=(), role="music_measure_sep")
PART_SEP = BrailleCell(dots=(), role="music_part_sep")


def _measure(n: int) -> list[BrailleCell]:
    """An ``n``-cell measure — note cells with dot 1 (non-blank)."""
    return [BrailleCell(dots=(1,), role="music_note") for _ in range(n)]


def _score_cells(*sizes: int) -> list[BrailleCell]:
    """Concatenate measures of the given sizes, joined by one
    ``music_measure_sep`` between adjacent measures (mirrors the
    backend's per-part emission)."""
    out: list[BrailleCell] = []
    for i, n in enumerate(sizes):
        if i:
            out.append(SEP)
        out.extend(_measure(n))
    return out


def _score_2part(p1: list[int], p2: list[int]) -> list[BrailleCell]:
    """Two parts (measure-size lists) joined by a ``music_part_sep`` —
    mirrors :func:`_emit_score_partwise`'s between-parts emission."""
    return _score_cells(*p1) + [PART_SEP] + _score_cells(*p2)


def _wrap(cells, line_width, *, first=0, cont=0, **kw):
    """Drive ``wrap_measures`` directly with explicit indents so the
    position-exact assertions test the wrap logic, not the indent
    policy (which the scheme owns)."""
    return wrap_measures(
        cells,
        LayoutOptions(line_width=line_width, **kw),
        first_indent=first,
        cont_indent=cont,
    )


def _no_frame(line_width: int = 40, **kw) -> LayoutOptions:
    """LayoutOptions with the score / music_block framing blanks zeroed
    so render-based tests see just the scheme's lines."""
    base = dict(
        line_width=line_width,
        score_blank_before=0, score_blank_after=0,
        music_block_blank_before=0, music_block_blank_after=0,
    )
    base.update(kw)
    return LayoutOptions(**base)


def _render(cells, *, block_type="score", opts=None) -> str:
    opts = opts if opts is not None else _no_frame()
    doc = BrailleDocument(
        blocks=[BrailleBlock(block_type=block_type, cells=cells)]
    )
    out = LayoutRenderer(options=opts).render(doc)
    assert isinstance(out, str)
    return out


# ---------------------------------------------------------------------------
# Scheme registry — selectable BANA formats, looked up not branched
# ---------------------------------------------------------------------------


class TestSchemeRegistry:
    def test_single_line_registered(self):
        assert isinstance(get_scheme("single_line"), SingleLineScheme)
        assert "single_line" in scheme_names()

    def test_default_scheme_is_single_line(self):
        assert LayoutOptions().music_scheme == "single_line"

    def test_bar_over_bar_registered(self):
        assert get_scheme("bar_over_bar").name == "bar_over_bar"
        assert "bar_over_bar" in scheme_names()

    def test_unknown_scheme_falls_back_to_single_line(self):
        # line_by_line isn't registered yet → fall back; so does an
        # unknown name and None.
        assert get_scheme("line_by_line").name == "single_line"
        assert get_scheme("nonsense").name == "single_line"
        assert get_scheme(None).name == "single_line"

    def test_default_measure_break_role(self):
        assert "music_measure_sep" in LayoutOptions().measure_break_roles

    def test_score_not_verbatim(self):
        opts = LayoutOptions()
        assert "score" not in opts.verbatim_block_types
        assert "music_block" not in opts.verbatim_block_types


# ---------------------------------------------------------------------------
# wrap_measures — measure-boundary-only wrapping (tested at indent 0)
# ---------------------------------------------------------------------------


class TestWrapMeasures:
    def test_breaks_at_separator(self):
        # 3 measures of 10; width 25.
        # line1: m1(10) + sep(1) + m2(10) = 21 <= 25; + sep + 10 = 32 > 25.
        lines = _wrap(_score_cells(10, 10, 10), 25)
        assert [len(ln) for ln in lines] == [21, 10]

    def test_one_measure_per_line_when_pair_overflows(self):
        lines = _wrap(_score_cells(10, 10, 10), 15)
        assert [len(ln) for ln in lines] == [10, 10, 10]

    def test_measure_never_split_midway(self):
        # 7 + 1 + 7 = 15 <= 20; + 1 + 7 = 23 > 20 → [m1 m2][m3 m4].
        lines = _wrap(_score_cells(7, 7, 7, 7), 20)
        assert [len(ln) for ln in lines] == [15, 15]

    def test_oversize_measure_runs_over_unbroken(self):
        lines = _wrap(_score_cells(100), 40)
        assert [len(ln) for ln in lines] == [100]

    def test_oversize_measure_then_small_measure(self):
        lines = _wrap(_score_cells(100, 5), 40)
        assert [len(ln) for ln in lines] == [100, 5]

    def test_separator_preserved_between_measures_on_a_line(self):
        # 3 + sep + 3 on one line; the middle cell is the kept separator.
        lines = _wrap(_score_cells(3, 3), 40)
        assert len(lines) == 1
        assert len(lines[0]) == 7
        assert lines[0][3].is_blank
        assert lines[0][3].role == "music_measure_sep"

    def test_custom_break_role(self):
        cells = [
            BrailleCell(dots=(1,), role="music_note"),
            BrailleCell(dots=(), role="my_sep"),
            BrailleCell(dots=(1,), role="music_note"),
        ]
        lines = _wrap(
            cells, 1, measure_break_roles=frozenset({"my_sep"})
        )
        assert [len(ln) for ln in lines] == [1, 1]

    def test_no_continuation_hyphen(self):
        out = _render(_score_cells(10, 10, 10), opts=_no_frame(15))
        assert HYPHEN_CHAR not in out


# ---------------------------------------------------------------------------
# single_line geometry — hanging indent (BANA §24.1.1: run-over to cell 3)
# ---------------------------------------------------------------------------


class TestSingleLineGeometry:
    def test_first_line_at_margin_runover_indented(self):
        # width 15: m1(10) on line 1 at the margin; m2 won't fit
        # (10+1+10=21) → run-over line indented to the third cell (2 blanks).
        out = _render(_score_cells(10, 10), opts=_no_frame(15))
        lines = out.split("\n")
        assert [len(ln) for ln in lines] == [10, 12]
        assert lines[0][0] == NOTE_CHAR          # first line flush
        assert lines[1][:2] == BLANK_CHAR * 2    # run-over at cell 3
        assert lines[1][2] == NOTE_CHAR

    def test_single_measure_one_line_flush(self):
        out = _render(_score_cells(5), opts=_no_frame(40))
        assert out == NOTE_CHAR * 5


# ---------------------------------------------------------------------------
# bar_over_bar — parts stacked into measure-aligned parallels (BANA §28.1)
# ---------------------------------------------------------------------------


class TestBarOverBar:
    def test_two_parts_aligned_into_one_parallel(self):
        # p1 measures 3,3; p2 measures 5,2. col widths = [5, 3].
        lines = get_scheme("bar_over_bar").lay_out(
            _score_2part([3, 3], [5, 2]), LayoutOptions(line_width=40)
        )
        assert len(lines) == 2  # one parallel, one line per part
        assert [len(ln) for ln in lines] == [9, 8]
        # Measure 1 starts at the same column in both parts (aligned),
        # and the separator column lines up too.
        assert lines[0][5].is_blank and lines[1][5].is_blank
        assert lines[0][6].role == "music_note"
        assert lines[1][6].role == "music_note"

    def test_parallels_split_by_width_with_blank_between(self):
        lines = get_scheme("bar_over_bar").lay_out(
            _score_2part([10, 10, 10], [10, 10, 10]),
            LayoutOptions(line_width=25),
        )
        # m0+m1 fit one parallel (21<=25); m2 in a second. Blank line
        # between the two parallels.
        assert [len(ln) for ln in lines] == [21, 21, 1, 10, 10]
        assert len(lines[2]) == 1 and lines[2][0].is_blank  # blank line

    def test_render_uses_selected_scheme(self):
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="score", cells=_score_2part([3], [3]))
        ])
        opts = LayoutOptions(
            line_width=40, music_scheme="bar_over_bar",
            score_blank_before=0, score_blank_after=0,
        )
        out = LayoutRenderer(options=opts).render(doc)
        # One parallel, two parts → two lines of 3 notes each.
        assert out.split("\n") == [NOTE_CHAR * 3, NOTE_CHAR * 3]

    def test_single_line_breaks_on_part_separator(self):
        # In single_line a part boundary is just a break point: the two
        # one-measure parts run on one line (3 + sep + 3).
        out = _render(_score_2part([3], [3]), opts=_no_frame(40))
        assert out == NOTE_CHAR * 3 + BLANK_CHAR + NOTE_CHAR * 3


# ---------------------------------------------------------------------------
# Block framing — score set off with blank lines, music_block not
# ---------------------------------------------------------------------------


class TestFraming:
    def test_score_blank_before_and_after(self):
        out = _render(_score_cells(5), opts=LayoutOptions(
            line_width=40, score_blank_before=1, score_blank_after=1
        ))
        lines = out.split("\n")
        assert len(lines) == 3
        assert lines[0] == BLANK_CHAR
        assert len(lines[1]) == 5
        assert lines[2] == BLANK_CHAR

    def test_music_block_no_surrounding_blanks(self):
        # music_block defaults to no framing blanks.
        out = _render(
            _score_cells(5), block_type="music_block",
            opts=LayoutOptions(line_width=40),
        )
        assert out == NOTE_CHAR * 5

    def test_empty_score_emits_nothing(self):
        out = _render([], opts=LayoutOptions(line_width=40))
        assert out == ""


# ---------------------------------------------------------------------------
# lay_out_block seam: the public hook a front-end's braille view renders through
# ---------------------------------------------------------------------------


class TestLayOutBlockSeam:
    @staticmethod
    def _encode(lines) -> str:
        from brailix.renderer.unicode_braille import cell_to_char
        return "\n".join("".join(cell_to_char(c) for c in ln) for ln in lines)

    def test_seam_matches_render_for_score(self):
        block = BrailleBlock(block_type="score", cells=_score_cells(10, 10, 10))
        doc = BrailleDocument(blocks=[block])
        r = LayoutRenderer(options=_no_frame(25))
        assert self._encode(r.lay_out_block(block)) == r.render(doc)

    def test_seam_matches_render_for_paragraph(self):
        cells = [
            BrailleCell(dots=(1,), source_span=Span(i, i + 1)) for i in range(50)
        ]
        block = BrailleBlock(block_type="paragraph", cells=cells)
        doc = BrailleDocument(blocks=[block])
        r = LayoutRenderer(options=LayoutOptions(line_width=20))
        assert self._encode(r.lay_out_block(block)) == r.render(doc)

    def test_original_cells_carried_by_reference(self):
        cells = _score_cells(5, 5)
        block = BrailleBlock(block_type="score", cells=cells)
        r = LayoutRenderer(options=_no_frame(40))
        seen = {id(c) for line in r.lay_out_block(block) for c in line}
        notes = [c for c in cells if c.role == "music_note"]
        assert all(id(c) in seen for c in notes)


# ---------------------------------------------------------------------------
# Mixed document: text wraps by word, score wraps by measure
# ---------------------------------------------------------------------------


class TestMixedDocument:
    def test_paragraph_word_wraps_while_score_measure_wraps(self):
        para = [
            BrailleCell(dots=(1,), source_span=Span(i, i + 1))
            for i in range(60)
        ]
        doc = BrailleDocument(blocks=[
            BrailleBlock(block_type="paragraph", cells=para),
            BrailleBlock(block_type="score", cells=_score_cells(10, 10)),
        ])
        opts = LayoutOptions(
            line_width=40, score_blank_before=1, score_blank_after=0
        )
        out = LayoutRenderer(options=opts).render(doc)
        assert isinstance(out, str)
        lines = out.split("\n")
        # Paragraph spills to ≥2 lines; then a blank separator; then the
        # score on a single line (10 + sep + 10 = 21, flush at margin).
        assert len(lines) >= 4
        assert lines[-1] == NOTE_CHAR * 10 + BLANK_CHAR + NOTE_CHAR * 10
        assert lines[-2] == BLANK_CHAR  # score_blank_before
