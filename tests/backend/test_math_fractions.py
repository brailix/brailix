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


# ---------------------------------------------------------------------------
# Function ↔ fraction interactions
# ---------------------------------------------------------------------------


class TestFunctionFractionMarkers:
    """《盲文常用数学符号》 function/fraction scope rules.

    A function application (``cos α``) is one term, so it keeps the
    simple bar form when it sits in a numerator / denominator. A
    fraction that is itself a function's ARGUMENT must keep the compound
    ⠆…⠰ brackets — without them, cos(α/a) would emit the same cells as
    the bracket-free simple form of (cos α)/a.
    """

    def test_function_application_numerator_drops_open_close(self, profile):
        # \frac{\cos α}{a} — numerator "cos α" is a single term.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>cos</mi><mi>α</mi></mrow>"
                "<mi>a</mi></mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_fraction_bar" in r
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_function_application_denominator_drops_open_close(self, profile):
        # \frac{a}{\cos α} — same rule on the denominator side.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mi>a</mi>"
                "<mrow><mi>cos</mi><mi>α</mi></mrow>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_function_application_both_operands(self, profile):
        # \frac{\sin α}{\cos α} — tangent written as a quotient.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>sin</mi><mi>α</mi></mrow>"
                "<mrow><mi>cos</mi><mi>α</mi></mrow>"
                "</mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert r.count("math_function_prefix") == 2
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_multi_token_function_argument_keeps_open_close(self, profile):
        # \frac{\cos 2α}{a} — "cos 2α" is a three-sibling run, not a
        # single term; the compound form stays.
        cells, _ = emit(
            mml(
                "<math><mfrac>"
                "<mrow><mi>cos</mi><mn>2</mn><mi>α</mi></mrow>"
                "<mi>a</mi></mfrac></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r

    def test_fraction_after_function_forces_open_close(self, profile):
        # \cos\frac{α}{a} — the fraction is cos's argument: the simple
        # α/a must take the compound form, with the brackets emitted
        # after the function name.
        cells, _ = emit(
            mml(
                "<math><mrow><mi>cos</mi>"
                "<mfrac><mi>α</mi><mi>a</mi></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
        assert r.index("math_function_name") < r.index("math_fraction_open")

    def test_fraction_after_omml_function_shape_forces_open_close(self, profile):
        # The OMML m:func shape carries an apply-function <mo>&#x2061;</mo>
        # (U+2061) between name and argument; the normalizer drops it,
        # so the same forcing applies to docx-sourced formulas.
        cells, wc = emit(
            mml(
                "<math><mrow><mi>cos</mi><mo>&#x2061;</mo>"
                "<mfrac><mi>α</mi><mi>a</mi></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r
        assert "unknown" not in r
        assert not [w for w in wc.warnings if w.code == "MATH_UNKNOWN_SYMBOL"]

    def test_fraction_after_scripted_function_forces_open_close(self, profile):
        # \log_2\frac{x}{y} — a script-wrapped head (msub base) counts.
        cells, _ = emit(
            mml(
                "<math><mrow>"
                "<msub><mi>log</mi><mn>2</mn></msub>"
                "<mfrac><mi>x</mi><mi>y</mi></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r

    def test_fraction_after_mo_function_forces_open_close(self, profile):
        # latex2mathml emits \lim as <mo>lim</mo>; a registered function
        # name arriving as <mo> is a head too.
        cells, _ = emit(
            mml(
                "<math><mrow><mo>lim</mo>"
                "<mfrac><mi>x</mi><mn>2</mn></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r

    def test_antoine_after_function_stays_compact(self, profile):
        # \cos\frac{1}{2} — the Antoine lower-form digit makes ⠼⠁⠆ one
        # self-delimiting token; no brackets forced.
        cells, _ = emit(
            mml(
                "<math><mrow><mi>cos</mi>"
                "<mfrac><mn>1</mn><mn>2</mn></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_digit_lower" in r
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r
        assert "math_fraction_bar" not in r

    def test_parenthesised_fraction_after_function_not_forced(self, profile):
        # \cos(\frac{α}{a}) — the <mo>(</mo> sits between the function
        # name and the fraction, and already delimits the argument.
        cells, _ = emit(
            mml(
                "<math><mrow><mi>cos</mi><mo>(</mo>"
                "<mfrac><mi>α</mi><mi>a</mi></mfrac>"
                "<mo>)</mo></mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_bar" in r
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_variable_before_fraction_not_forced(self, profile):
        # a·(x/y) written as juxtaposition — a single-char <mi> is a
        # variable, not a function head; the simple form stays.
        cells, _ = emit(
            mml(
                "<math><mrow><mi>a</mi>"
                "<mfrac><mi>x</mi><mi>y</mi></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" not in r
        assert "math_fraction_close" not in r

    def test_typed_slash_after_function_forces_open_close(self, profile):
        # Function head directly before a typed ``x / 2`` mrow (the OMML
        # m:func argument shape) — same forcing through the slash path.
        cells, _ = emit(
            mml(
                "<math><mrow><mi>cos</mi>"
                "<mrow><mi>x</mi><mo>/</mo><mn>2</mn></mrow>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_fraction_open" in r
        assert "math_fraction_close" in r

    def test_flag_does_not_leak_into_nested_operands(self, profile):
        # \cos\frac{\frac{α}{β}}{γ} — the forced compound applies to the
        # OUTER fraction only; the nested simple α/β inside the numerator
        # must not inherit the flag (exactly one open/close pair).
        cells, _ = emit(
            mml(
                "<math><mrow><mi>cos</mi>"
                "<mfrac>"
                "<mfrac><mi>α</mi><mi>β</mi></mfrac>"
                "<mi>γ</mi></mfrac>"
                "</mrow></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert r.count("math_fraction_open") == 1
        assert r.count("math_fraction_close") == 1
        assert r.count("math_fraction_bar") == 2
