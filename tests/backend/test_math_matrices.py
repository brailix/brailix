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

    def test_paren_around_non_matrix_not_a_matrix(self, profile):
        # (x) stays a single paren-delimited group, NOT a per-row matrix.
        cells, _ = emit(
            mml("<math><mo>(</mo><mi>x</mi><mo>)</mo></math>"), profile
        )
        opens = [c for c in cells if c.role == "math_delim" and c.dots == (1, 2, 6)]
        assert len(opens) == 1


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
