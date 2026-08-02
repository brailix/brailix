"""Element-subscript notation: a₁₁ versus a_{n,1}.

The standard's rule for an indexed element has two halves, and which one
applies turns on whether the subscript carries a **comma**:

* **A plain non-negative number, no comma** — drop the number sign and
  write the digits lowered. No terminator either: lowered digits cannot
  run into what follows, so nothing has to close them.
  ``a₁₁`` → ``⠰⠁⠡⠂⠂``.
* **A comma between the two subscripts** (either of which may be a digit,
  a letter, or an expression containing letters) — the digits stay in
  their upper form, the number sign stays, and the subscript closes with
  ⠱. ``a_{n,1}`` → ``⠰⠁⠡⠝⠐ ⠼⠁⠱``.

The rule is stated for matrix elements, because that is where indexed
symbols mostly live, but it is the notation for an indexed symbol
anywhere — a sequence term is written the same way, which is why these
tests exercise both a bare formula and a matrix element.

Also pinned here: the subscript's letter takes **no letter sign of its
own** (``⠡⠝``, not ``⠡⠰⠝``). Consecutive letters of the same class take
one sign, on the first, and a base and its script letter are written
consecutively.
"""

from __future__ import annotations

import pytest

from tests.backend._math_common import emit, mml


def _uni(cells) -> str:
    from brailix.renderer.unicode_braille import cell_to_char

    return "".join(cell_to_char(c) for c in cells)


def _sub(base: str, script: str) -> str:
    return f"<math><msub>{base}{script}</msub></math>"


_A = "<mi>a</mi>"


class TestNumericSubscriptIsLowered:
    """No comma, a plain number → lowered digits, no 数号, no 结束符."""

    @pytest.mark.parametrize(
        ("digits", "expected"),
        [
            ("1", "⠰⠁⠡⠂"),
            ("11", "⠰⠁⠡⠂⠂"),
            ("12", "⠰⠁⠡⠂⠆"),
            ("22", "⠰⠁⠡⠆⠆"),
            ("123", "⠰⠁⠡⠂⠆⠒"),
        ],
    )
    def test_every_digit_is_lowered(self, profile, digits, expected):
        cells, _ = emit(mml(_sub(_A, f"<mn>{digits}</mn>")), profile)
        assert _uni(cells) == expected

    def test_a_sequence_term_is_written_the_same_way(self, profile):
        """The rule is stated for a matrix element, but an indexed symbol
        is an indexed symbol: xₙ in a sequence takes the same form."""
        cells, _ = emit(mml(_sub("<mi>x</mi>", "<mn>10</mn>")), profile)
        assert _uni(cells) == "⠰⠭⠡⠂⠴"


class TestSubscriptWithACommaKeepsTheNumberSign:
    def test_the_reference_form(self, profile):
        """``a_{n,1}`` verbatim: letter, comma, upper digit with its 数号,
        and the ⠱ terminator the comma form requires."""
        cells, _ = emit(
            mml(_sub(_A, "<mrow><mi>n</mi><mo>,</mo><mn>1</mn></mrow>")),
            profile,
        )
        assert _uni(cells) == "⠰⠁⠡⠝⠐⠀⠼⠁⠱"

    def test_the_digits_are_not_lowered(self, profile):
        cells, _ = emit(
            mml(_sub(_A, "<mrow><mn>1</mn><mo>,</mo><mn>2</mn></mrow>")),
            profile,
        )
        assert "math_digit_lower" not in [c.role for c in cells]
        assert "number_sign" in [c.role for c in cells]
        assert "math_script_close" in [c.role for c in cells]


class TestTheSubscriptLetterSharesTheBasesRun:
    """Consecutive letters of the same class take one letter sign, on the
    first, and the subscript indicator does not interrupt that."""

    def test_a_lone_letter_subscript_takes_no_sign_of_its_own(self, profile):
        cells, _ = emit(mml(_sub(_A, "<mi>n</mi>")), profile)
        assert _uni(cells) == "⠰⠁⠡⠝⠱"

    def test_a_different_letter_class_still_announces_itself(self, profile):
        """The run is shared per *type*: a Greek subscript under a Latin
        base is a different class, so it takes its own sign."""
        cells, _ = emit(mml(_sub(_A, "<mi>ρ</mi>")), profile)
        got = _uni(cells)
        assert got.startswith("⠰⠁⠡")
        assert "⠨" in got, got  # lowercase Greek sign

    def test_a_following_baseline_letter_still_shares(self, profile):
        """The base's run survives the script body, unchanged behaviour:
        ``a²b`` is one lowercase run, matching ``ab²``."""
        cells, _ = emit(
            mml(
                "<math><msup><mi>a</mi><mn>2</mn></msup><mi>b</mi></math>"
            ),
            profile,
        )
        assert _uni(cells) == "⠰⠁⠌⠆⠃"


class TestInsideAMatrix:
    """「这个不管是在矩阵还是数列都是这么写的」— the same element notation,
    reached through the matrix path."""

    def test_matrix_elements_use_the_lowered_form(self, profile):
        from brailix.ir.braille import BrailleBlock, BrailleDocument
        from brailix.renderer.layout import LayoutOptions, LayoutRenderer

        def el(sub: str) -> str:
            return f"<mtd><msub>{_A}<mn>{sub}</mn></msub></mtd>"

        cells, _ = emit(mml(
            "<math><mo>|</mo><mtable>"
            f"<mtr>{el('11')}{el('12')}</mtr>"
            f"<mtr>{el('21')}{el('22')}</mtr>"
            "</mtable><mo>|</mo></math>"
        ), profile)
        doc = BrailleDocument(blocks=[BrailleBlock(cells=cells)])
        lines = LayoutRenderer(
            options=LayoutOptions(line_width=40, paragraph_indent=0)
        ).render(doc).split("\n")
        # ⠸ a₁₁ ⠀ a₁₂ ⠸ / ⠸ a₂₁ ⠀ a₂₂ ⠸ — every index a pair of lowered
        # digits, no number signs anywhere.
        assert lines == [
            "⠸⠰⠁⠡⠂⠂⠀⠰⠁⠡⠂⠆⠸",
            "⠸⠰⠁⠡⠆⠂⠀⠰⠁⠡⠆⠆⠸",
        ]
