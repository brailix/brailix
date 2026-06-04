"""Math backend tests for structural grouping: <mrow> edge cases and the
typed-slash fraction form.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

from tests.backend._math_common import emit, mml, roles

# ---------------------------------------------------------------------------
# Math root + mrow extras
# ---------------------------------------------------------------------------


class TestMrowExtras:
    def test_empty_mrow(self, profile):
        cells, _ = emit(mml("<math><mrow></mrow></math>"), profile)
        assert cells == []

    def test_typed_slash_x_over_y(self, profile):
        # x/y with typed-slash → simplified slash form, no open/close.
        cells, _ = emit(
            mml("<math><mrow><mi>x</mi><mo>/</mo><mi>y</mi></mrow></math>"),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_bar" in r
        bar = next(c for c in cells if c.role == "math_fraction_bar")
        assert bar.dots == (1, 2, 5, 6)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_typed_slash_with_complex_numerator(self, profile):
        # (a+b)/c — typed slash but numerator is multi-token, so falls
        # back to compound form? Actually the typed-slash detection
        # requires exactly 3 children at top level — here we have
        # <mrow><mrow>(a+b)</mrow><mo>/</mo><mi>c</mi></mrow>. Let's
        # check what happens.
        cells, _ = emit(
            mml(
                "<math><mrow>"
                "<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
                "<mo>/</mo><mi>c</mi></mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        # The mrow has 3 kids: <mrow>, <mo>/</mo>, <mi>c</mi>. typed-slash
        # detection triggers but numerator isn't leaf-like → open/close
        # fires.
        assert "math_fraction_bar" in r
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
