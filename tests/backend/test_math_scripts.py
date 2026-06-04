"""Math backend tests for scripts and under/over constructs: <msub>,
<msup>, <msubsup>, <munder>, <mover>, <munderover>, big-operator limits,
and vector / over-bar accents.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

from tests.backend._math_common import emit, mml, roles

# ---------------------------------------------------------------------------
# 18-23: scripts (msub / msup / msubsup) with various base shapes
# ---------------------------------------------------------------------------


class TestScripts:
    def test_msup_with_big_op_sum_uses_script_prefix(self, profile):
        # ∑^n — ∑ now takes the 46-prefix too (《盲文常用数学符号》: all
        # big-op limits go directly above / directly below ⠨⠡/⠨⠌).
        cells, _ = emit(
            mml("<math><msup><mo>∑</mo><mi>n</mi></msup></math>"), profile
        )
        r = roles(cells)
        # We should see the big_op cells, the 46-prefix, sup indicator, then n.
        assert "math_big_op" in r
        assert "math_superscript" in r
        assert "math_big_op_script_prefix" in r

    def test_msub_with_integral_uses_script_prefix(self, profile):
        # ∫_0 — int has script_prefix=true; with atomic_script_lower_digit,
        # 0 becomes Antoine lower-form, no close.
        cells, _ = emit(
            mml("<math><msub><mo>∫</mo><mn>0</mn></msub></math>"), profile
        )
        seq = [(c.role, c.dots) for c in cells]
        # Expected: big_op (1 cell), big_op_prefix, subscript, lower-0.
        assert seq == [
            ("math_big_op", (2, 3, 4, 6)),
            ("math_big_op_script_prefix", (4, 6)),
            ("math_subscript", (1, 6)),
            ("math_digit_lower", (3, 5, 6)),
        ]

    def test_msubsup_with_integral(self, profile):
        # ∫_0^1 — both sides emit with the 46-prefix.
        cells, _ = emit(
            mml("<math><msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup></math>"),
            profile,
        )
        seq = [(c.role, c.dots) for c in cells]
        assert seq == [
            ("math_big_op", (2, 3, 4, 6)),
            ("math_big_op_script_prefix", (4, 6)),
            ("math_subscript", (1, 6)),
            ("math_digit_lower", (3, 5, 6)),
            ("math_big_op_script_prefix", (4, 6)),
            ("math_superscript", (3, 4)),
            ("math_digit_lower", (2,)),
        ]

    def test_msub_with_lim_function_uses_script_prefix(self, profile):
        # lim_{x → 0} — lim is a big_op function with script_prefix.
        cells, _ = emit(
            mml(
                "<math><msub><mi>lim</mi>"
                "<mrow><mi>x</mi><mo>→</mo><mn>0</mn></mrow>"
                "</msub></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_function_name" in r
        assert "math_big_op_script_prefix" in r
        assert "math_subscript" in r
        # The complex content closes with script_close.
        assert "math_script_close" in r
        # x present
        assert any(c.source_text == "x" for c in cells if c.role == "math_identifier")
        # → present
        assert any(c.role == "math_rel" for c in cells)
        # number_sign for the 0
        assert "number_sign" in r

    def test_msub_regular_atomic_no_close(self, profile):
        # a_n — atomic subscript with simplify_script on → no close.
        cells, _ = emit(
            mml("<math><msub><mi>a</mi><mi>n</mi></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_subscript" in r
        assert "math_script_close" not in r

    def test_msup_regular_complex_emits_close(self, profile):
        # x^{n+1} — non-atomic sup → script_close fires.
        cells, _ = emit(
            mml(
                "<math><msup><mi>x</mi>"
                "<mrow><mi>n</mi><mo>+</mo><mn>1</mn></mrow>"
                "</msup></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_superscript" in r
        assert "math_script_close" in r


# ---------------------------------------------------------------------------
# 30-33: under / over / underover
# ---------------------------------------------------------------------------


class TestUnderOver:
    def test_mover_accent_unknown_char_warns(self, profile):
        # `^` (U+005E ASCII caret) isn't in cn_current's symbols / letters /
        # punctuation, so the accent handler falls back to unknown + the
        # standard MATH_UNKNOWN_SYMBOL warning.
        cells, wc = emit(
            mml('<math><mover accent="true"><mi>x</mi><mo>^</mo></mover></math>'),
            profile,
        )
        codes = [w.code for w in wc]
        assert "MATH_UNKNOWN_SYMBOL" in codes
        # The base x is still emitted before the warning fires.
        assert any(c.source_text == "x" for c in cells)

    def test_mover_known_accent_emits_cells(self, profile):
        # Use a known accent character (prime ′ U+2032 entity 'prime')
        # so the lookup chain succeeds and we see math_accent cells.
        cells, wc = emit(
            mml('<math><mover accent="true"><mi>f</mi><mo>′</mo></mover></math>'),
            profile,
        )
        codes = [w.code for w in wc]
        assert "MATH_UNKNOWN_SYMBOL" not in codes
        assert "MATH_UNSUPPORTED_ACCENT" not in codes
        # base f + accent cells from symbols.prime.
        roles_seq = [c.role for c in cells]
        assert "math_accent" in roles_seq
        # source_text on the accent cells is the prime char.
        accent_cells = [c for c in cells if c.role == "math_accent"]
        assert all(c.source_text == "′" for c in accent_cells)

    def test_mover_routes_by_accent_role_without_attr(self, profile):
        # latex2mathml emits \bar / \dot WITHOUT accent="true"; the backend
        # must still route to the accent path by recognising the role=accent
        # char — base + accent.over marker (⠘) + accent symbol (horizontal
        # bar ⠒).
        cells, wc = emit(
            mml("<math><mover><mi>x</mi><mo>¯</mo></mover></math>"), profile
        )
        seq = [(c.role, c.dots) for c in cells]
        assert ("math_accent_prefix", (4, 5)) in seq  # accent.over ⠘
        assert ("math_accent", (2, 5)) in seq         # horizontal bar ⠒
        assert "MATH_UNKNOWN_SYMBOL" not in [w.code for w in wc]

    def test_munder_routes_by_accent_role_uses_under_marker(self, profile):
        # \underline → munder with ― under-script: accent.under marker (⠰).
        cells, _ = emit(
            mml("<math><munder><mi>x</mi><mo>―</mo></munder></math>"), profile
        )
        seq = [(c.role, c.dots) for c in cells]
        assert ("math_accent_prefix", (5, 6)) in seq  # accent.under ⠰
        assert ("math_accent", (2, 5)) in seq         # horizontal bar ⠒

    def test_prime_superscript_skips_indicator(self, profile):
        # x' = msup(x, ′); prime is role=accent → emit ⠨⠔ directly, no
        # script.sup indicator ⠌ and no close (it's an upper-right mark, not
        # an exponent).
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mo>′</mo></msup></math>"), profile
        )
        seq = [(c.role, c.dots) for c in cells]
        assert ("math_superscript", (3, 4)) not in seq  # no ⠌ indicator
        assert ("math_accent", (4, 6)) in seq
        assert ("math_accent", (3, 5)) in seq

    def test_munder_no_accent_routes_like_msub(self, profile):
        # ∑ as munder base — same behaviour as msub with big_op base.
        cells, _ = emit(
            mml(
                "<math><munder><mo>∑</mo>"
                "<mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
                "</munder></math>"
            ),
            profile,
        )
        r = roles(cells)
        # Sum now emits the 46-dot prefix on its limit, plus the
        # subscript marker.
        assert "math_big_op" in r
        assert "math_subscript" in r
        assert "math_big_op_script_prefix" in r

    def test_mover_no_accent_routes_like_msup(self, profile):
        cells, _ = emit(
            mml("<math><mover><mo>∑</mo><mi>n</mi></mover></math>"), profile
        )
        r = roles(cells)
        assert "math_big_op" in r
        assert "math_superscript" in r

    def test_munderover_no_accent(self, profile):
        cells, _ = emit(
            mml(
                "<math><munderover><mo>∑</mo>"
                "<mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
                "<mi>n</mi></munderover></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_big_op" in r
        assert "math_subscript" in r
        assert "math_superscript" in r


# ---------------------------------------------------------------------------
# Additional script coverage
# ---------------------------------------------------------------------------


class TestScriptsExtras:
    def test_msub_simple_digit(self, profile):
        # x_1 — atomic digit subscript, simplifiable.
        cells, _ = emit(
            mml("<math><msub><mi>x</mi><mn>1</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_subscript" in r
        assert "math_script_close" not in r
        sub = next(c for c in cells if c.role == "math_subscript")
        assert sub.dots == (1, 6)

    def test_msup_simple_digit(self, profile):
        # x^2 — atomic digit superscript, simplifiable.
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mn>2</mn></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_superscript" in r
        assert "math_script_close" not in r
        sup = next(c for c in cells if c.role == "math_superscript")
        assert sup.dots == (3, 4)

    def test_msubsup_both_simple(self, profile):
        cells, _ = emit(
            mml("<math><msubsup><mi>x</mi><mn>1</mn><mn>2</mn></msubsup></math>"),
            profile,
        )
        r = roles(cells)
        # Order in our handler: base, sup, sub.
        sup_at = r.index("math_superscript")
        sub_at = r.index("math_subscript")
        assert sup_at < sub_at
        # Both atomic so no close.
        assert "math_script_close" not in r

    def test_msup_simplify_off_emits_close(self, profile, monkeypatch):
        # Use a non-digit (identifier) script content so the Antoine
        # lower-digit path doesn't preempt the simplify_script test.
        # x^n with simplify_script=off should emit close.
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "simplify_script", False
        )
        cells, _ = emit(
            mml("<math><msup><mi>x</mi><mi>n</mi></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_script_close" in r

    def test_msup_with_big_op_sum_complex_sup(self, profile):
        # ∑^{n+1} — non-atomic content closes with script_close.
        cells, _ = emit(
            mml(
                "<math><msup><mo>∑</mo>"
                "<mrow><mi>n</mi><mo>+</mo><mn>1</mn></mrow>"
                "</msup></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_big_op" in r
        assert "math_superscript" in r
        assert "math_script_close" in r

    def test_msub_with_integral_atomic_lower_digit_disabled(
        self, profile, monkeypatch
    ):
        # When atomic_script_lower_digit is off, atomic 0 should go
        # through the regular path (number_sign + digit + close).
        monkeypatch.setitem(
            profile.features.setdefault("math", {}),
            "atomic_script_lower_digit",
            False,
        )
        cells, _ = emit(
            mml("<math><msub><mo>∫</mo><mn>0</mn></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_digit_lower" not in r
        assert "number_sign" in r
        assert "math_script_close" in r


# ---------------------------------------------------------------------------
# More under/over coverage
# ---------------------------------------------------------------------------


class TestUnderOverExtras:
    def test_munder_with_int_uses_prefix(self, profile):
        # <munder><mo>∫</mo>...</munder> — int has script_prefix, so
        # the 46-dot prefix appears.
        cells, _ = emit(
            mml(
                "<math><munder><mo>∫</mo>"
                "<mrow><mi>a</mi><mo>=</mo><mn>0</mn></mrow>"
                "</munder></math>"
            ),
            profile,
        )
        r = roles(cells)
        assert "math_big_op_script_prefix" in r
        assert "math_subscript" in r

    def test_munder_accent_unknown_warns_as_symbol(self, profile):
        # `§` isn't in any table → standard unknown-symbol warning. (`~`
        # used to fill this role but became a real role=accent entry:
        # latex2mathml's \tilde output U+007E now maps to the tilde.)
        cells, wc = emit(
            mml(
                '<math><munder accent="true"><mi>x</mi><mo>§</mo></munder></math>'
            ),
            profile,
        )
        codes = [w.code for w in wc]
        assert "MATH_UNKNOWN_SYMBOL" in codes
        # base x still emitted before accent fallback.
        assert any(c.source_text == "x" for c in cells)

    def test_munderover_accent_unknown_warns_as_symbol(self, profile):
        # ``§`` and ``^`` are absent from every table — picked precisely
        # because they have no symbol/letter/punctuation mapping, so each
        # falls through to the unknown-cell + warning path. (``_`` used
        # to fill this role but became a real punctuation entry.)
        cells, wc = emit(
            mml(
                '<math><munderover accent="true"><mi>x</mi>'
                "<mo>§</mo><mo>^</mo></munderover></math>"
            ),
            profile,
        )
        codes = [w.code for w in wc]
        # Two unknown accent chars → two MATH_UNKNOWN_SYMBOL warnings.
        assert codes.count("MATH_UNKNOWN_SYMBOL") == 2

    def test_munder_accent_known_char_emits_cells(self, profile):
        # `˙` dot accent (U+02D9, entity 'dot') is in symbols.json.
        cells, wc = emit(
            mml('<math><munder accent="true"><mi>x</mi><mo>˙</mo></munder></math>'),
            profile,
        )
        codes = [w.code for w in wc]
        assert "MATH_UNKNOWN_SYMBOL" not in codes
        roles_seq = [c.role for c in cells]
        assert "math_accent" in roles_seq


# ---------------------------------------------------------------------------
# Big-op extras
# ---------------------------------------------------------------------------


class TestBigOpExtras:
    def test_sum_subscript_only(self, profile):
        cells, _ = emit(
            mml(
                "<math><msub><mo>∑</mo>"
                "<mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow>"
                "</msub></math>"
            ),
            profile,
        )
        r = roles(cells)
        # Sum now carries the 46-dot script prefix on its limit.
        assert "math_big_op_script_prefix" in r
        assert "math_subscript" in r

    def test_prod_subscript_only(self, profile):
        cells, _ = emit(
            mml("<math><msub><mo>∏</mo><mi>i</mi></msub></math>"), profile
        )
        big = [c for c in cells if c.role == "math_big_op"]
        # ∏ = 456 + 1234.
        assert [c.dots for c in big] == [(4, 5, 6), (1, 2, 3, 4)]

    def test_int_sup_only(self, profile):
        cells, _ = emit(
            mml("<math><msup><mo>∫</mo><mi>n</mi></msup></math>"), profile
        )
        r = roles(cells)
        assert "math_big_op" in r
        assert "math_superscript" in r
        # int has script_prefix.
        assert "math_big_op_script_prefix" in r

    def test_max_function_big_op_uses_prefix(self, profile):
        # max now has big_op=true AND script_prefix=true.
        cells, _ = emit(
            mml("<math><msub><mi>max</mi><mi>i</mi></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_subscript" in r
        # 46-dot prefix now present (limits go directly below).
        assert "math_big_op_script_prefix" in r

    def test_min_function_big_op_uses_prefix(self, profile):
        cells, _ = emit(
            mml("<math><msub><mi>min</mi><mi>j</mi></msub></math>"), profile
        )
        r = roles(cells)
        assert "math_function_prefix" in r
        assert "math_subscript" in r
        assert "math_big_op_script_prefix" in r


class TestVectorAccent:
    """Vector sign (《盲文常用数学符号》 §15): \\vec / \\overrightarrow render
    → / ← as the arrow sign (distinct from the relation arrow ⠒⠕), while
    \\overline uses the short bar; each picks the single- or two-letter form
    by the number of base letters. Outside the accent position the arrow-sign
    character is still a role=rel relation arrow."""

    @staticmethod
    def _accent_dots(cells):
        return [
            c.dots
            for c in cells
            if c.role in ("math_accent_prefix", "math_accent")
        ]

    def test_vec_single_letter_is_arrow_mark(self, profile):
        # mover(v, →) → ⠘ (directly above) + arrow sign single-letter ⠒⠂.
        cells, wc = emit(
            mml("<math><mover><mi>v</mi><mo>→</mo></mover></math>"), profile
        )
        assert self._accent_dots(cells) == [(4, 5), (2, 5), (2,)]
        assert "MATH_UNKNOWN_SYMBOL" not in [w.code for w in wc]

    def test_vec_double_letter_is_arrow_double(self, profile):
        # mover(AB, →) → ⠘ + arrow sign two-letter ⠒⠆ = (2,5)(2,3).
        cells, _ = emit(
            mml(
                "<math><mover><mrow><mi>A</mi><mi>B</mi></mrow>"
                "<mo>→</mo></mover></math>"
            ),
            profile,
        )
        assert self._accent_dots(cells) == [(4, 5), (2, 5), (2, 3)]

    def test_left_arrow_reuses_arrow_mark(self, profile):
        # ← (\overleftarrow) — docx §15 has no leftward variant, so it
        # reuses the same arrow sign.
        cells, _ = emit(
            mml("<math><mover><mi>v</mi><mo>←</mo></mover></math>"), profile
        )
        assert self._accent_dots(cells) == [(4, 5), (2, 5), (2,)]

    def test_overline_double_letter_is_bar_double(self, profile):
        # ― over AB → short bar two-letter ⠒⠒ (vs single-letter ⠒).
        cells, _ = emit(
            mml(
                "<math><mover><mrow><mi>A</mi><mi>B</mi></mrow>"
                "<mo>―</mo></mover></math>"
            ),
            profile,
        )
        assert self._accent_dots(cells) == [(4, 5), (2, 5), (2, 5)]

    def test_bar_single_letter_unchanged(self, profile):
        # Regression: \bar{x} is still ⠘ + short bar single-letter ⠒,
        # unaffected by the single/two-letter mechanism.
        cells, _ = emit(
            mml("<math><mover><mi>x</mi><mo>¯</mo></mover></math>"), profile
        )
        assert self._accent_dots(cells) == [(4, 5), (2, 5)]

    def test_arrow_outside_accent_stays_relation(self, profile):
        # → as an ordinary binary relation is unaffected: a → b is still the
        # relation arrow ⠒⠕ (including ⠕ = (1,3,5)), not the arrow sign ⠒⠂.
        cells, _ = emit(
            mml("<math><mi>a</mi><mo>→</mo><mi>b</mi></math>"), profile
        )
        seq = [c.dots for c in cells]
        assert (1, 3, 5) in seq  # trailing ⠕ of the relation arrow; the arrow sign lacks it
