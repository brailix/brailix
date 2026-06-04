"""Math backend tests for fractions: <mfrac>, Antoine digit form,
slash-bar simplification, and open/close bracketing.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

from tests.backend._math_common import emit, mml, roles

# ---------------------------------------------------------------------------
# 24-27: fractions
# ---------------------------------------------------------------------------


class TestFraction:
    def test_atomic_digit_fraction_uses_antoine(self, profile):
        # 1/2 → number_sign + upper 1 + lower 2 (Antoine).
        cells, _ = emit(
            mml("<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        assert "math_fraction_bar" not in r
        assert "math_digit_lower" in r
        lower = next(c for c in cells if c.role == "math_digit_lower")
        assert lower.dots == (2, 3)

    def test_atomic_letter_fraction_uses_slash_bar(self, profile):
        # x/y — both leaves but identifiers (not digits) → slash bar,
        # no open/close.
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mi>y</mi></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        bar = next(c for c in cells if c.role == "math_fraction_bar")
        assert bar.dots == (1, 2, 5, 6)

    def test_complex_fraction_has_open_close_with_blank(self, profile):
        # \frac{a+b}{c} — numerator is multi-token → open + content +
        # blank + bar + denom + close.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
                "<mi>c</mi></mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_bar" in r
        assert "math_fraction_close" in r
        # There must be at least one blank between numerator and bar.
        bar_idx = r.index("math_fraction_bar")
        assert cells[bar_idx - 1].is_blank

    def test_fraction_simplify_off_forces_open_close(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "simplify_fraction", False
        )
        # Even an atomic letter / letter pair gets open/close now.
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mi>y</mi></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
        # Restore for other tests in this module (monkeypatch undoes it).

    def test_sqrt_over_digit_drops_open_close(self, profile):
        # √3 / 2 — numerator is a single msqrt (self-fenced by sqrt.close),
        # denominator is a single mn. Both single structures → simplified
        # form, no ⠆…⠰ brackets even though the numerator is a structure.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<msqrt><mn>3</mn></msqrt>"
                "<mn>2</mn>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        assert "math_fraction_bar" in r
        assert "math_sqrt_close" in r

    def test_nested_fraction_over_digit_drops_open_close(self, profile):
        # (1/2) / 3 — numerator is a single mfrac, denominator a single
        # mn. Both single structures → simplified form. The Antoine
        # lower-form digit closes the inner fraction.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mfrac><mn>1</mn><mn>2</mn></mfrac>"
                "<mn>3</mn>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        # Outer bar present, inner bar suppressed by Antoine.
        assert r.count("math_fraction_bar") == 1
        assert "math_digit_lower" in r

    def test_msup_over_digit_drops_open_close(self, profile):
        # x² / 2 — numerator is a single msup, denominator a single mn.
        # With math.atomic_script_lower_digit on, the script content
        # closes naturally with the lower-form digit.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<msup><mi>x</mi><mn>2</mn></msup>"
                "<mn>2</mn>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        assert "math_fraction_bar" in r

    def test_mrow_wrapped_single_structure_still_simplifies(self, profile):
        # latex2mathml wraps numerator / denominator in <mrow> even when
        # they hold one element. Single-child <mrow> must be transparent
        # to the simplifiability check.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><msqrt><mrow><mn>3</mn></mrow></msqrt></mrow>"
                "<mrow><mn>2</mn></mrow>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        assert "math_fraction_bar" in r

    def test_multi_token_mrow_numerator_keeps_open_close(self, profile):
        # √a + b in numerator — single-element mrow wraps a multi-token
        # expression, so it stays compound. Mirrors quadratic-formula
        # numerators (-b ± √...). Ensures the unwrap doesn't go too far.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><msqrt><mn>3</mn></msqrt><mo>+</mo><mn>1</mn></mrow>"
                "<mn>2</mn>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r


# ---------------------------------------------------------------------------
# Additional fraction coverage
# ---------------------------------------------------------------------------


class TestFractionExtras:
    def test_fraction_with_no_children_does_not_crash(self, profile):
        cells, wc = emit(mml("<math><mfrac></mfrac></math>"), profile)
        # We expect open + bar + close, no content.
        # bar at minimum.
        assert any(c.role == "math_fraction_bar" for c in cells)

    def test_fraction_with_only_numerator(self, profile):
        cells, _ = emit(
            mml("<math><mfrac><mn>1</mn></mfrac></math>"), profile
        )
        # Should still emit bar (denominator empty).
        assert any(c.role == "math_fraction_bar" for c in cells)

    def test_multi_digit_atomic_falls_through_to_slash(self, profile):
        # 12/34 — both single-token <mn> (multi-digit) but Antoine only
        # fires for single-digit operands; falls through to the
        # simplified slash form.
        cells, _ = emit(
            mml("<math><mfrac><mn>12</mn><mn>34</mn></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r
        # Multi-digit mn isn't leaf-like for the simplify check —
        # actually it is leaf-like (single mn with no children). So
        # simplified form: number_sign + 12 + bar + number_sign + 34
        # (because the bar resets the number sign).
        assert "math_fraction_bar" in r
        # No open/close because mn is leaf-like.
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_fraction_with_letter_over_digit_simplifies(self, profile):
        # x/3 — both leaf-like. Antoine doesn't apply (numerator not
        # digit), but simplify still fires.
        cells, _ = emit(
            mml("<math><mfrac><mi>x</mi><mn>3</mn></mfrac></math>"), profile
        )
        r = roles(cells)
        assert "math_fraction_bar" in r
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
