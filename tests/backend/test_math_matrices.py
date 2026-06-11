"""Math backend tests for matrices (<mtable> linear notation) and
elementary geometry shapes.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

import pytest

from tests.backend._math_common import emit, mml


class TestMatrix:
    """<mtable> linear notation (《盲文常用数学符号》 §17). These build the
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

    def test_table_is_bracketed_in_hang_region(self, profile):
        # The whole table sits inside hang_open … hang_close so the
        # layout hangs width-overflow continuations by two cells
        # (§17 规则1: 某一行写不完，下一行空两方继续).
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


class TestEquationSystem:
    """``{``-fenced <mtable> with no closing fence — \\begin{cases} /
    \\left\\{…\\right. equation systems. The print brace spans every row;
    braille prefixes each row with the matching brace segment: ⠎(234)
    first row, ⠇(123) middle rows, ⠣(126) last row. Each row is one
    braille line (LINE_BREAK_CELL between rows); no row-end marker."""

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
        # plain left brace ⠪(246), not a brace segment; no hang region
        # (nothing spans multiple lines).
        cells, _ = emit(mml(self._cases([["<mi>x</mi>"]])), profile)
        delims = [c.dots for c in cells if c.role == "math_delim"]
        assert delims == [(2, 4, 6)]
        assert not any(c.role == "hang_open" for c in cells)

    def test_system_is_bracketed_in_hang_region(self, profile):
        cells, _ = emit(
            mml(self._cases([["<mi>x</mi>"], ["<mi>y</mi>"]])), profile
        )
        roles = [c.role for c in cells]
        assert roles[0] == "hang_open"
        assert roles[-1] == "hang_close"

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
    """Elementary geometry symbols (《盲文常用数学符号》, geometry-symbols
    section): ∠△□○◇▭∟ etc. are role=shape, led by ⠫(1246). latex2mathml and
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
