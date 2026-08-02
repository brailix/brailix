"""Math backend tests for matrices (<mtable> linear notation) and
elementary geometry shapes.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

import pytest

from brailix.ir.braille import BrailleBlock, BrailleDocument
from brailix.renderer.layout import LayoutOptions, LayoutRenderer
from tests.backend._math_common import emit, mml


def _layout_lines(cells, width=40):
    """Lay a backend cell stream out to display lines (Unicode braille,
    blanks shown as the U+2800 blank pattern)."""
    doc = BrailleDocument(blocks=[BrailleBlock(cells=cells)])
    out = LayoutRenderer(
        options=LayoutOptions(line_width=width, paragraph_indent=0)
    ).render(doc)
    return out.split("\n")


class TestMatrix:
    """<mtable> linear notation. These build the
    multi-<mtr> mtable directly to unit-test the backend in isolation. The
    same shape arrives from OMML / Word import and from latex2mathml's
    \\begin{matrix} alike — its ``\\\\`` row breaks convert to separate
    <mtr> rows, so LaTeX matrices are NOT squished onto one line. The
    end-to-end LaTeX matrix goldens live in
    tests/integration/test_latex_braille_golden.py::TestMatrices."""

    @staticmethod
    def _mtable(rows, o, c):
        body = "".join(
            "<mtr>" + "".join(f"<mtd>{e}</mtd>" for e in r) + "</mtr>"
            for r in rows
        )
        return f"<math><mo>{o}</mo><mtable>{body}</mtable><mo>{c}</mo></math>"

    def test_pmatrix_per_row_paren(self, profile):
        cells, wc = emit(
            mml(self._mtable(
                [["<mi>a</mi>", "<mi>b</mi>"], ["<mi>c</mi>", "<mi>d</mi>"]],
                "(", ")")),
            profile,
        )
        assert "MATH_UNSUPPORTED_ELEMENT" not in [w.code for w in wc]
        # Each of the 2 rows carries its own ⠣ … ⠜ (lpar 126 / rpar 345).
        opens = [c for c in cells if c.role == "math_delim" and c.dots == (1, 2, 6)]
        closes = [c for c in cells if c.role == "math_delim" and c.dots == (3, 4, 5)]
        assert len(opens) == 2 and len(closes) == 2
        # Elements within a row are space-separated.
        assert any(c.is_blank for c in cells)

    def test_vmatrix_per_row_vertical_bar(self, profile):
        # Determinant: each row fenced with ⠸ (verbar 456).
        cells, _ = emit(
            mml(self._mtable([["<mi>a</mi>", "<mi>b</mi>"]], "|", "|")), profile
        )
        bars = [c for c in cells if c.role == "math_delim" and c.dots == (4, 5, 6)]
        assert len(bars) == 2  # one row → open + close bar

    def test_bmatrix_per_row_bracket(self, profile):
        cells, _ = emit(
            mml(self._mtable([["<mn>1</mn>", "<mn>2</mn>"]], "[", "]")), profile
        )
        assert any(c.role == "math_delim" and c.dots == (1, 2, 3, 5, 6) for c in cells)
        assert any(c.role == "math_delim" and c.dots == (2, 3, 4, 5, 6) for c in cells)
        # Digits inside still get a number sign.
        assert any(c.role == "number_sign" for c in cells)

    def test_bare_mtable_defaults_to_paren(self, profile):
        cells, wc = emit(
            mml("<math><mtable><mtr><mtd><mi>a</mi></mtd></mtr></mtable></math>"),
            profile,
        )
        assert "MATH_UNSUPPORTED_ELEMENT" not in [w.code for w in wc]
        assert any(c.role == "math_delim" and c.dots == (1, 2, 6) for c in cells)

    def test_empty_mtable_warns_and_emits_no_delimiters(self, profile):
        # An <mtable> with no <mtr> rows is malformed; the linear path must warn
        # and emit nothing, not a pair of empty hanging delimiters with no
        # signal. (latex2mathml / MTEF never produce this; a corrupt / hand-
        # built tree can.)
        cells, wc = emit(mml("<math><mtable></mtable></math>"), profile)
        assert "MATH_UNSUPPORTED_ELEMENT" in [w.code for w in wc]
        assert not any(
            c.role in ("math_delim", "hang_open", "hang_close") for c in cells
        )

    def test_empty_cases_mtable_warns_and_emits_nothing(self, profile):
        # Same guard on the {-fenced cases path.
        cells, wc = emit(mml("<math><mo>{</mo><mtable></mtable></math>"), profile)
        assert "MATH_UNSUPPORTED_ELEMENT" in [w.code for w in wc]
        assert not any(
            c.role in ("math_delim", "hang_open", "hang_close") for c in cells
        )

    def test_table_is_bracketed_in_hang_region(self, profile):
        # The whole table sits inside hang_open … hang_close so the
        # layout hangs width-overflow continuations by two cells
        # (a row too wide to fit continues two cells in on the next line).
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>a</mi>"], ["<mi>b</mi>"]], "(", ")")),
            profile,
        )
        roles = [c.role for c in cells]
        assert roles[0] == "hang_open"
        assert roles[-1] == "hang_close"
        assert roles.count("hang_open") == roles.count("hang_close") == 1

    def test_paren_around_non_matrix_not_a_matrix(self, profile):
        # (x) stays a single paren-delimited group, NOT a per-row matrix.
        cells, _ = emit(
            mml("<math><mo>(</mo><mi>x</mi><mo>)</mo></math>"), profile
        )
        opens = [c for c in cells if c.role == "math_delim" and c.dots == (1, 2, 6)]
        assert len(opens) == 1

    def test_binary_op_after_determinant_keeps_blank(self, profile):
        # Regression: a matrix / determinant ends in HANG_CLOSE_CELL,
        # which has empty dots. The required space before a following
        # binary operator must survive — |a b| = 5 keeps the blank before
        # ⠶. It was swallowed while _last_is_blank tested ``dots == ()``
        # and so counted the hang_close sentinel as an existing blank.
        cells, _ = emit(
            mml(
                "<math><mo>|</mo><mtable><mtr>"
                "<mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd>"
                "</mtr></mtable><mo>|</mo>"
                "<mo>=</mo><mn>5</mn></math>"
            ),
            profile,
        )
        # The determinant really went through the hang region…
        assert any(c.role == "hang_close" for c in cells)
        # …and the '=' that follows it is immediately preceded by a real
        # space cell, exactly like the parenthesised-operand control.
        eq_idx = next(
            i for i, c in enumerate(cells) if c.source_text == "="
        )
        assert cells[eq_idx - 1].role == "space"

    def test_op_after_paren_operand_keeps_blank_control(self, profile):
        # Control for the regression above: (a) = 5 always kept its blank
        # (a closing paren is not an empty-dots sentinel). Pinned so the
        # two paths stay in agreement.
        cells, _ = emit(
            mml(
                "<math><mo>(</mo><mi>a</mi><mo>)</mo>"
                "<mo>=</mo><mn>5</mn></math>"
            ),
            profile,
        )
        eq_idx = next(
            i for i, c in enumerate(cells) if c.source_text == "="
        )
        assert cells[eq_idx - 1].role == "space"

    def test_function_fraction_inside_cell_forces_compound(self, profile):
        # Regression: a function applied to a fraction inside a matrix cell
        # (\cos\frac{α}{a}) must keep the compound ⠆…⠰ form, exactly like at
        # top level (see test_math_fractions::test_fraction_after_function_
        # forces_open_close). The cell walker previously emitted children
        # straight through _emit_element, bypassing the function-head
        # detection and collapsing it into the ambiguous simple-bar form —
        # the same cells as (cos α)/a.
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>cos</mi><mfrac><mi>α</mi><mi>a</mi></mfrac>"]],
                "(", ")")),
            profile,
        )
        r = [c.role for c in cells]
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
        assert r.index("math_function_name") < r.index("math_fraction_open")

    def test_plain_fraction_inside_cell_stays_simple(self, profile):
        # Control for the regression above: a bare fraction in a cell with
        # no preceding function head keeps the simple bar form. The cell
        # walker must force compound only on function-argument fractions,
        # not on every fraction.
        cells, _ = emit(
            mml(self._mtable(
                [["<mfrac><mi>a</mi><mi>b</mi></mfrac>"]],
                "(", ")")),
            profile,
        )
        r = [c.role for c in cells]
        assert "math_fraction_bar" in r
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r


class TestMatrixOperatorMark:
    """Row elements are blank-separated, so an operator's ordinary
    space_before inside a matrix / determinant CELL would be taken for the
    element separator — a polynomial cell ``a+b`` would read as ``a`` and
    ``+b``. Inside a cell the operator's leading blank becomes the matrix
    operator mark ⠐ (5, structures.matrix.op_prefix) instead, binding the
    term; the real column separator stays a blank. Equation systems keep
    ordinary spacing (each equation owns its braille line)."""

    @staticmethod
    def _mtable(rows, o, c):
        body = "".join(
            "<mtr>" + "".join(f"<mtd>{e}</mtd>" for e in r) + "</mtr>"
            for r in rows
        )
        return f"<math><mo>{o}</mo><mtable>{body}</mtable><mo>{c}</mo></math>"

    _PLUS = (2, 3, 5)
    _MARK = (5,)

    def test_operator_in_cell_gets_op_mark_not_blank(self, profile):
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>a</mi><mo>+</mo><mi>b</mi>", "<mi>c</mi>"]], "(", ")")),
            profile,
        )
        dots = [tuple(c.dots) for c in cells]
        plus_idx = dots.index(self._PLUS)
        # The cell immediately before the + is the ⠐ mark, not a blank.
        assert cells[plus_idx - 1].dots == self._MARK
        assert not cells[plus_idx - 1].is_blank

    def test_only_the_column_separator_stays_a_blank(self, profile):
        # [[a+b, c]] — the + no longer contributes a blank, so the one
        # remaining space cell is the column separator between the cells.
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>a</mi><mo>+</mo><mi>b</mi>", "<mi>c</mi>"]], "(", ")")),
            profile,
        )
        assert sum(1 for c in cells if c.role == "space") == 1

    def test_determinant_and_times_also_marked(self, profile):
        # Determinant cell with × — × has space_before too, so it binds
        # with the ⠐ mark like +.
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>a</mi><mo>×</mo><mi>b</mi>"]], "|", "|")),
            profile,
        )
        times = (2, 3, 6)
        dots = [tuple(c.dots) for c in cells]
        assert cells[dots.index(times) - 1].dots == self._MARK

    def test_nested_matrix_restores_outer_mark(self, profile):
        # An inner matrix cell must not leave the flag off for the rest of
        # the outer row: both the outer + and the inner + get the mark.
        inner = "<mo>(</mo><mtable><mtr><mtd><mi>x</mi><mo>+</mo><mi>y</mi>" \
            "</mtd></mtr></mtable><mo>)</mo>"
        cells, _ = emit(
            mml(self._mtable(
                [["<mi>a</mi><mo>+</mo><mi>b</mi>", inner]], "(", ")")),
            profile,
        )
        marks = [
            i for i, c in enumerate(cells)
            if c.dots == self._MARK and c.role == "math_op"
        ]
        assert len(marks) == 2  # outer a+b and inner x+y

    def test_cases_keeps_ordinary_spacing(self, profile):
        # Equation system: the operator keeps its space_before blank — the
        # ⠐ mark is a matrix-cell thing only, never emitted here.
        body = (
            "<math><mo>{</mo><mtable>"
            "<mtr><mtd><mi>x</mi><mo>+</mo><mi>y</mi></mtd></mtr>"
            "<mtr><mtd><mi>z</mi></mtd></mtr>"
            "</mtable></math>"
        )
        cells, _ = emit(mml(body), profile)
        dots = [tuple(c.dots) for c in cells]
        plus_idx = dots.index(self._PLUS)
        assert cells[plus_idx - 1].is_blank
        assert self._MARK not in dots

    def test_plain_operator_outside_matrix_unaffected(self, profile):
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>+</mo><mi>b</mi></math>"), profile
        )
        dots = [tuple(c.dots) for c in cells]
        assert cells[dots.index(self._PLUS) - 1].is_blank
        assert self._MARK not in dots


class TestEquationSystem:
    """``{``-fenced <mtable> with no closing fence — \\begin{cases} /
    \\left\\{…\\right. equation systems. The print brace spans the whole
    system; the backend brackets it in CASES_OPEN/CLOSE, publishes the
    three brace segments as a ``cases_palette`` (⠎ 234 first / ⠇ 123
    middle / ⠣ 126 last), and writes each equation on its own line
    (LINE_BREAK_CELL between rows) with the per-equation segment as a
    ``math_delim`` PLACEHOLDER. The layout re-stamps the segments onto the
    physical braille lines (so ⠣/126 lands on the last braille line even
    when a row wraps) — see tests/renderer/test_layout.py::TestCasesBrace.
    These backend tests pin the placeholder stream the layout consumes."""

    @staticmethod
    def _cases(rows, close: str | None = None):
        body = "".join(
            "<mtr>" + "".join(f"<mtd>{e}</mtd>" for e in r) + "</mtr>"
            for r in rows
        )
        tail = f"<mo>{close}</mo>" if close is not None else ""
        return f"<math><mo>{{</mo><mtable>{body}</mtable>{tail}</math>"

    def test_three_rows_first_middle_last_segments(self, profile):
        cells, wc = emit(
            mml(self._cases(
                [["<mi>x</mi>"], ["<mi>y</mi>"], ["<mi>z</mi>"]])),
            profile,
        )
        assert not [w for w in wc if w.code.startswith("MATH_")]
        delims = [c.dots for c in cells if c.role == "math_delim"]
        assert delims == [(2, 3, 4), (1, 2, 3), (1, 2, 6)]
        # One line per row: a line-break sentinel between the 3 rows.
        assert sum(1 for c in cells if c.role == "line_break") == 2
        # The segments are marks, not brackets — one blank cell sits
        # between each segment and its row content.
        for i, c in enumerate(cells):
            if c.role == "math_delim":
                assert cells[i + 1].role == "space"

    def test_two_rows_no_middle_segment(self, profile):
        # \right. arrives as an empty postfix <mo> — consumed, no warning.
        cells, wc = emit(
            mml(self._cases([["<mi>x</mi>"], ["<mi>y</mi>"]], close="")),
            profile,
        )
        assert not [w for w in wc if w.code.startswith("MATH_")]
        delims = [c.dots for c in cells if c.role == "math_delim"]
        assert delims == [(2, 3, 4), (1, 2, 6)]

    def test_single_row_degrades_to_plain_left_brace(self, profile):
        # A one-row "system" prints as an ordinary one-line { — emit the
        # plain left brace ⠪(246), not a brace segment; no region at all
        # (nothing spans multiple lines).
        cells, _ = emit(mml(self._cases([["<mi>x</mi>"]])), profile)
        delims = [c.dots for c in cells if c.role == "math_delim"]
        assert delims == [(2, 4, 6)]
        assert not any(c.role in ("hang_open", "cases_open") for c in cells)

    def test_system_is_bracketed_in_cases_region(self, profile):
        cells, _ = emit(
            mml(self._cases([["<mi>x</mi>"], ["<mi>y</mi>"]])), profile
        )
        roles = [c.role for c in cells]
        assert roles[0] == "cases_open"
        assert roles[-1] == "cases_close"

    def test_cases_open_is_followed_by_the_three_segment_palette(self, profile):
        # Right after CASES_OPEN the backend publishes the brace segments
        # (first / middle / last) as a cases_palette so the layout can
        # stamp them per physical line regardless of equation count.
        cells, _ = emit(
            mml(self._cases([["<mi>x</mi>"], ["<mi>y</mi>"]])), profile
        )
        palette = [c.dots for c in cells if c.role == "cases_palette"]
        assert palette == [(2, 3, 4), (1, 2, 3), (1, 2, 6)]
        # The palette sits immediately after CASES_OPEN, before any content.
        open_idx = next(i for i, c in enumerate(cells) if c.role == "cases_open")
        assert [c.role for c in cells[open_idx + 1 : open_idx + 4]] == [
            "cases_palette"
        ] * 3

    def test_paired_braces_are_not_an_equation_system(self, profile):
        # {…} with a REAL closing brace is not a cases form — the brace
        # pair emits as ordinary delimiters around the default
        # parenthesised linear rows (current behaviour, locked).
        cells, _ = emit(
            mml(self._cases([["<mi>x</mi>"], ["<mi>y</mi>"]], close="}")),
            profile,
        )
        delims = [c.dots for c in cells if c.role == "math_delim"]
        assert delims == [
            (2, 4, 6),                  # {
            (1, 2, 6), (3, 4, 5),       # ⠣ x ⠜
            (1, 2, 6), (3, 4, 5),       # ⠣ y ⠜
            (1, 3, 5),                  # }
        ]

    def test_rows_restart_number_sign(self, profile):
        # A digit at a row head must carry its own number sign.
        cells, _ = emit(
            mml(self._cases([["<mn>1</mn>"], ["<mn>2</mn>"]])), profile
        )
        assert sum(1 for c in cells if c.role == "number_sign") == 2


class TestForcedLineBreak:
    """<mspace linebreak="newline"> — a bare ``\\\\`` outside any table
    environment becomes a LINE_BREAK_CELL sentinel (same as matrix /
    equation-system row boundaries); renderers turn it into a real
    line break."""

    def test_newline_mspace_emits_break_sentinel(self, profile):
        cells, wc = emit(
            mml(
                "<math><mi>a</mi>"
                '<mspace linebreak="newline" /><mi>b</mi></math>'
            ),
            profile,
        )
        assert "MATH_UNSUPPORTED_ELEMENT" not in [w.code for w in wc]
        assert sum(1 for c in cells if c.role == "line_break") == 1

    def test_consecutive_breaks_collapse(self, profile):
        cells, _ = emit(
            mml(
                "<math><mi>a</mi>"
                '<mspace linebreak="newline" />'
                '<mspace linebreak="newline" /><mi>b</mi></math>'
            ),
            profile,
        )
        assert sum(1 for c in cells if c.role == "line_break") == 1

    def test_width_only_mspace_ignored_on_direct_feed(self, profile):
        # The normalizer drops width-only <mspace> before dispatch; a
        # direct backend feed must ignore it rather than warn or emit.
        import xml.etree.ElementTree as ET

        from tests.backend._math_common import emit_via_tree

        tree = ET.fromstring(
            '<math><mi>a</mi><mspace width="1em" /><mi>b</mi></math>'
        )
        cells, wc = emit_via_tree(tree, profile)
        assert "MATH_UNSUPPORTED_ELEMENT" not in [w.code for w in wc]
        assert not any(c.is_blank for c in cells)


class TestGeometryShapes:
    """Elementary geometry symbols: ∠△□○◇▭∟ etc. are role=shape, led by
    ⠫(1246). latex2mathml and
    Word / direct MathML often use different code points (\\square→◻U+25FB vs
    Word □U+25A1); both code points map — here we feed the canonical code
    points to confirm the Word/MathML path."""

    @pytest.mark.parametrize(
        "ch, expected_dots",
        [
            ("∠", [(1, 2, 4, 6), (2, 4, 6)]),                  # angle ⠫⠪ U+2220
            ("△", [(1, 2, 4, 6), (2, 5, 6)]),                  # triangle ⠫⠲ U+25B3
            ("□", [(1, 2, 4, 6), (2, 3, 5, 6)]),               # square ⠫⠶ U+25A1
            ("○", [(1, 2, 4, 6), (2,)]),                       # circle ⠫⠂ U+25CB
            ("◇", [(1, 2, 4, 6), (1, 4, 5)]),                  # rhombus ⠫⠙ U+25C7
            ("▭", [(1, 2, 4, 6), (1, 2, 3, 4, 5, 6)]),         # rectangle ⠫⠿ U+25AD
            ("∟", [(1, 2, 4, 6), (2, 3, 6)]),                  # right angle ⠫⠦ U+221F
        ],
    )
    def test_canonical_shape_char_maps(self, profile, ch, expected_dots):
        cells, wc = emit(mml(f"<math><mo>{ch}</mo></math>"), profile)
        assert [c.dots for c in cells] == expected_dots
        assert not any(w.code.startswith("MATH_") for w in wc)
        assert all(c.role == "math_shape" for c in cells)


class TestEllipsisSymbols:
    """Ellipses used in omitted-element matrices / lists: horizontal ⋯
    (dots 5-5-5), vertical ⋮ (46), diagonal ⋱/⋰ (15-3). The HALF-WIDTH
    math ellipsis ⋯ (U+22EF) is the valid horizontal form; the full-width
    text ellipsis … (U+2026, the 省略号 character) is a writing error in a
    formula, so it warns rather than being borrowed as an ellipsis."""

    @pytest.mark.parametrize(
        "ch, expected",
        [
            ("⋯", [(5,), (5,), (5,)]),   # ⋯ half-width horizontal (cdots)
            ("⋮", [(4, 6)]),             # ⋮ vertical (vdots)
            ("⋱", [(1, 5), (3,)]),       # ⋱ diagonal down (ddots)
            ("⋰", [(1, 5), (3,)]),       # ⋰ diagonal up (iddots)
        ],
    )
    def test_ellipsis_cells(self, profile, ch, expected):
        cells, wc = emit(mml(f"<math><mo>{ch}</mo></math>"), profile)
        assert not [w for w in wc if w.code.startswith("MATH_")]
        assert [tuple(c.dots) for c in cells if c.dots] == expected

    def test_fullwidth_ellipsis_warns_in_math(self, profile):
        # … (U+2026) is the full-width text 省略号; in a formula it is a
        # writing error — warn, don't silently borrow it as the math ⋯.
        cells, wc = emit(mml("<math><mo>…</mo></math>"), profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_UNKNOWN_SYMBOL" for w in wc)


class TestMatrixColumnAlignment:
    """A matrix / determinant is a grid, and its braille reads as one:
    every element stands under the element above it, an omitted (empty
    ``<mtd/>``) cell is written as blanks the width of its column, and
    every row runs the full table width so both 界符 stand in a column of
    their own.

    Verified through the layout, so the column positions are visible —
    which matters more than usual here, because with single-cell elements
    (which is what most fixtures use) alignment is invisible: the tests
    that show it are the ones with omitted cells or elements of unequal
    width."""

    @staticmethod
    def _m(grid, o="(", c=")"):
        body = ""
        for r in grid:
            body += "<mtr>" + "".join(
                f"<mtd>{e}</mtd>" if e else "<mtd/>" for e in r
            ) + "</mtr>"
        return f"<math><mo>{o}</mo><mtable>{body}</mtable><mo>{c}</mo></math>"

    @staticmethod
    def _mi(*names):
        return ["" if n == "" else f"<mi>{n}</mi>" for n in names]

    @staticmethod
    def _mn(*vals):
        return ["" if v == "" else f"<mn>{v}</mn>" for v in vals]

    def test_diagonal_layout(self, profile):
        cells, _ = emit(mml(self._m([
            self._mi("a", "", ""),
            self._mi("", "b", ""),
            self._mi("", "", "c"),
        ])), profile)
        # Each element under its own column, and every row padded out to
        # the full width so all three ⠜ stand in the same column.
        assert _layout_lines(cells) == [
            "⠣⠰⠁⠀⠀⠀⠀⠀⠀⠜",
            "⠣⠀⠀⠀⠰⠃⠀⠀⠀⠜",
            "⠣⠀⠀⠀⠀⠀⠀⠰⠉⠜",
        ]

    def test_upper_triangular_aligns_first_nonzero_under_column(self, profile):
        cells, _ = emit(mml(self._m([
            self._mi("a", "b", "c"),
            self._mi("", "d", "e"),
            self._mi("", "", "f"),
        ])), profile)
        lines = _layout_lines(cells)
        # d (row 2) starts at the same offset b (row 1) does; e under c.
        assert lines == [
            "⠣⠰⠁⠀⠰⠃⠀⠰⠉⠜",
            "⠣⠀⠀⠀⠰⠙⠀⠰⠑⠜",
            "⠣⠀⠀⠀⠀⠀⠀⠰⠋⠜",
        ]

    def test_lower_triangular_needs_no_leading_pad(self, profile):
        """Nothing is omitted before a lower-triangular row's elements, so
        they sit where the row below has them with no leading blanks — but
        the trailing omissions are still written out, or the closing 界符
        would climb left one column per row."""
        cells, _ = emit(mml(self._m([
            self._mi("a", "", ""),
            self._mi("b", "c", ""),
            self._mi("d", "e", "f"),
        ])), profile)
        assert _layout_lines(cells) == [
            "⠣⠰⠁⠀⠀⠀⠀⠀⠀⠜",
            "⠣⠰⠃⠀⠰⠉⠀⠀⠀⠜",
            "⠣⠰⠙⠀⠰⠑⠀⠰⠋⠜",
        ]

    @pytest.mark.parametrize(
        "grid",
        [
            # diagonal — trailing omissions on every row but the last
            [("a", "", ""), ("", "b", ""), ("", "", "c")],
            # lower triangular
            [("a", "", ""), ("b", "c", ""), ("d", "e", "f")],
            # upper triangular — leading omissions, already full width
            [("a", "b", "c"), ("", "d", "e"), ("", "", "f")],
            # tridiagonal
            [("a", "b", ""), ("c", "d", "e"), ("", "f", "g")],
            # arrow — here the LAST row is the shortest, so "line up with
            # the last row" can only mean "line up at the full width"
            [("a", "b", "c"), ("d", "", ""), ("e", "", "")],
            # nothing omitted at all: the frame still has to be a frame
            [("a", "b", "c"), ("d", "e", "f"), ("g", "h", "i")],
        ],
        ids=["diagonal", "lower", "upper", "tridiagonal", "arrow", "full"],
    )
    def test_the_closing_delimiter_stands_in_one_column(self, profile, grid):
        """The invariant, stated once over every shape.

        A reader tracks the frame's right edge exactly as they track the
        columns inside it, so a 界符 that climbs left one column per row
        reads as a matrix changing width. Trailing omitted cells used to be
        dropped, which is precisely what moved it: a 3×3 diagonal matrix
        closed at columns 4, 7 and 10 on its three rows.
        """
        cells, _ = emit(mml(self._m([self._mi(*row) for row in grid])), profile)
        lines = _layout_lines(cells)
        assert len(lines) == len(grid)
        assert len({len(line) for line in lines}) == 1, lines
        assert all(line.endswith("⠜") for line in lines)

    def test_a_full_matrix_of_unequal_elements_is_aligned_too(self, profile):
        """The case only a full matrix can show: nothing is omitted, but
        ``100`` is a cell wider than ``1``, so without column widths the
        second element of each row — and the closing 界符 — landed
        somewhere different on every row."""
        cells, _ = emit(mml(self._m([
            self._mn("1", "2"),
            self._mn("100", "3"),
        ])), profile)
        assert _layout_lines(cells) == [
            "⠣⠼⠁⠀⠀⠀⠼⠃⠜",
            "⠣⠼⠁⠚⠚⠀⠼⠉⠜",
        ]

    def test_a_cell_that_breaks_a_line_keeps_the_unmeasured_path(
        self, profile
    ):
        """A column has a width only while each cell is one line long. A
        nested table inside a cell writes lines of its own, so measuring
        that cell would count them all as one width and pad the column to
        it — the outer table stays element-by-element instead."""
        inner = (
            "<mo>(</mo><mtable>"
            "<mtr><mtd><mi>a</mi></mtd></mtr>"
            "<mtr><mtd><mi>b</mi></mtd></mtr>"
            "</mtable><mo>)</mo>"
        )
        cells, _ = emit(mml(
            f"<math><mo>(</mo><mtable><mtr><mtd>{inner}</mtd>"
            "<mtd><mi>c</mi></mtd></mtr></mtable><mo>)</mo></math>"
        ), profile)
        # It renders (the nested rows land on their own lines) rather than
        # padding a column to the combined width of both of them.
        lines = _layout_lines(cells)
        assert len(lines) > 1
        assert all(line for line in lines)

    def test_a_determinant_closes_in_one_column_too(self, profile):
        """Same rule, other 界符: the vertical bars of a determinant."""
        cells, _ = emit(
            mml(self._m(
                [self._mi("a", ""), self._mi("", "b")], o="|", c="|"
            )),
            profile,
        )
        # 2 columns of width 2 plus one separator = 5 cells between the
        # bars on both rows.
        assert _layout_lines(cells) == ["⠸⠰⠁⠀⠀⠀⠸", "⠸⠀⠀⠀⠰⠃⠸"]

    def test_equal_width_elements_need_no_padding(self, profile):
        """Alignment is not visible when every element is one cell wide —
        which is why the whole suite's single-letter fixtures stayed byte
        for byte identical when a full matrix started being aligned too."""
        cells, _ = emit(mml(self._m([
            self._mi("a", "b"),
            self._mi("c", "d"),
        ])), profile)
        assert _layout_lines(cells) == [
            "⠣⠰⠁⠀⠰⠃⠜",
            "⠣⠰⠉⠀⠰⠙⠜",
        ]

    def test_staircase_equation_system_aligns_columns(self, profile):
        # 阶梯型方程组: a {-fenced aligned array with omitted leading terms.
        # The brace segments ⠎ / ⠇ / ⠣ still lead each row, and the columns
        # line up under them (b under b, c under c).
        cells, _ = emit(mml(
            "<math><mo>{</mo><mtable>"
            "<mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd>"
            "<mtd><mi>c</mi></mtd></mtr>"
            "<mtr><mtd/><mtd><mi>b</mi></mtd><mtd><mi>c</mi></mtd></mtr>"
            "<mtr><mtd/><mtd/><mtd><mi>c</mi></mtd></mtr>"
            "</mtable></math>"
        ), profile)
        assert _layout_lines(cells) == [
            "⠎⠀⠰⠁⠀⠰⠃⠀⠰⠉",
            "⠇⠀⠀⠀⠀⠰⠃⠀⠰⠉",
            "⠣⠀⠀⠀⠀⠀⠀⠀⠰⠉",
        ]

    def test_a_staircase_row_is_not_padded_out_at_the_end(self, profile):
        """The matrix rule stops at the fence. An equation system closes
        with nothing, so padding a row whose last terms are omitted would
        run blanks to the line break with no 界符 to line up — noise on a
        line the reader is about to leave anyway."""
        cells, _ = emit(mml(
            "<math><mo>{</mo><mtable>"
            "<mtr><mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr>"
            "<mtr><mtd><mi>c</mi></mtd><mtd/></mtr>"
            "</mtable></math>"
        ), profile)
        assert _layout_lines(cells) == [
            "⠎⠀⠰⠁⠀⠰⠃",
            "⠣⠀⠰⠉",
        ]
