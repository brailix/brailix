"""Math backend tests for radicals: <msqrt> and <mroot> (degree, layout).

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

from tests.backend._math_common import emit, mml, roles

# ---------------------------------------------------------------------------
# 28-29: sqrt + nth root
# ---------------------------------------------------------------------------


class TestSqrtMroot:
    def test_msqrt_layout(self, profile):
        cells, _ = emit(mml("<math><msqrt><mi>x</mi></msqrt></math>"), profile)
        r = roles(cells)
        assert r[0] == "math_sqrt_open"
        assert "math_sqrt_indicator" in r
        assert r[-1] == "math_sqrt_close"
        assert cells[0].dots == (1, 4, 6)
        assert cells[-1].dots == (1, 4, 5, 6)

    def test_mroot_with_degree(self, profile):
        # mroot has order (base, degree); output should be open + degree +
        # indicator + base + close.
        cells, _ = emit(
            mml("<math><mroot><mi>x</mi><mn>3</mn></mroot></math>"), profile
        )
        r = roles(cells)
        open_at = r.index("math_sqrt_open")
        ind_at = r.index("math_sqrt_indicator")
        close_at = r.index("math_sqrt_close")
        assert open_at < ind_at < close_at
        # Between open and indicator we should see the degree (number_sign + digit).
        between = r[open_at + 1 : ind_at]
        assert "number_sign" in between
        assert "math_digit" in between


# ---------------------------------------------------------------------------
# Sqrt / mroot extras
# ---------------------------------------------------------------------------


class TestSqrtExtras:
    def test_msqrt_with_multiple_children(self, profile):
        # <msqrt> with multiple children is allowed — they're emitted
        # in sequence inside the indicator.
        cells, _ = emit(
            mml("<math><msqrt><mi>x</mi><mo>+</mo><mi>y</mi></msqrt></math>"),
            profile,
        )
        r = roles(cells)
        assert r[0] == "math_sqrt_open"
        assert "math_sqrt_indicator" in r
        assert "math_op" in r
        assert r[-1] == "math_sqrt_close"

    def test_msqrt_empty(self, profile):
        cells, _ = emit(mml("<math><msqrt></msqrt></math>"), profile)
        r = roles(cells)
        assert r[0] == "math_sqrt_open"
        assert "math_sqrt_indicator" in r
        assert r[-1] == "math_sqrt_close"

    def test_mroot_dots(self, profile):
        cells, _ = emit(
            mml("<math><mroot><mi>x</mi><mn>3</mn></mroot></math>"), profile
        )
        # Just check structural integrity: open + ... + close.
        r = roles(cells)
        assert r[0] == "math_sqrt_open"
        assert r[-1] == "math_sqrt_close"
        # Number_sign and digit somewhere between open and indicator.
        ind_at = r.index("math_sqrt_indicator")
        between = r[:ind_at]
        assert "number_sign" in between
