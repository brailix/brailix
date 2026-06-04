"""Golden tests: LaTeX → braille end-to-end with real latex2mathml.

Each case feeds a ``$...$`` snippet through the full Pipeline and
locks the resulting Unicode-braille string. These goldens are the
canonical regression net for any future Backend / IR / table change:
when a refactor shifts a marker dot pattern or skips a cell, the
diff lands here before it can sneak into user output.

The expected strings are derived from the current ``cn_current``
profile. Every Chinese-math rule referenced is documented in
``ARCHITECTURE.md``.
"""

from __future__ import annotations

import pytest

# Force a real run with the actual converter — skip cleanly when the
# extra isn't available so contributors can still run the rest of the
# suite without LaTeX support installed.
pytest.importorskip("latex2mathml.converter")

from brailix import Pipeline


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    # MathML/LaTeX path doesn't need a Chinese tokenizer — the
    # default char analyzer is fine because the test inputs are pure
    # math fragments wrapped in $...$.
    return Pipeline(profile="cn_current")


def render(pipe: Pipeline, src: str) -> str:
    """Translate and render to Unicode braille."""
    return pipe.translate_text(src).render()


# cn_current no longer emits math-mode wrappers — Chinese math standard
# has no formula-level start/end markers. Profiles that want them can
# flip ``features.math_mode_markers`` back on.


# ---------------------------------------------------------------------------
# Leaves: numbers, identifiers, operators
# ---------------------------------------------------------------------------


class TestLeafGoldens:
    def test_single_lowercase_latin(self, pipe):
        # x = (56 + x), no formula-level wrappers.
        assert render(pipe, r"$x$") == "⠰⠭"

    def test_single_uppercase_latin(self, pipe):
        # A = (6 + a).
        assert render(pipe, r"$A$") == "⠠⠁"

    def test_single_lowercase_greek(self, pipe):
        # π = (46 + π-cell 1234).
        assert render(pipe, r"$\pi$") == "⠨⠏"

    def test_single_uppercase_greek(self, pipe):
        # Δ = (456 + Δ-cell 145).
        assert render(pipe, r"$\Delta$") == "⠸⠙"

    def test_single_digit_gets_number_sign(self, pipe):
        # 5 = number_sign (3456) + digit 5 (15).
        assert render(pipe, r"$5$") == "⠼⠑"


# ---------------------------------------------------------------------------
# Operators / relations / number-sign re-emission
# ---------------------------------------------------------------------------


class TestArithmetic:
    def test_a_plus_b(self, pipe):
        # a + b = 56 a / 235 / 56 b.
        cells = pipe.translate_text(r"$a + b$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        # math_open(2), latin-prefix+a, +, latin-prefix+b, math_close(2)
        assert "math_op" in roles
        plus_idx = roles.index("math_op")
        assert cells[plus_idx].dots == (2, 3, 5)

    def test_a_equals_b(self, pipe):
        cells = pipe.translate_text(r"$a = b$").braille_ir.blocks[0].cells
        eq = next(c for c in cells if c.role == "math_rel")
        assert eq.dots == (2, 3, 5, 6)

    def test_number_sign_repeats_across_operators(self, pipe):
        # 1 + 2: each number run needs its own number-sign cell.
        cells = pipe.translate_text(r"$1 + 2$").braille_ir.blocks[0].cells
        ns = [c for c in cells if c.role == "number_sign"]
        assert len(ns) == 2

    def test_number_sign_repeats_after_identifier(self, pipe):
        # x + 2: identifier resets need_number_sign, so the digit run
        # also gets a sign.
        cells = pipe.translate_text(r"$x + 2$").braille_ir.blocks[0].cells
        ns = [c for c in cells if c.role == "number_sign"]
        assert len(ns) == 1
        # Order: math_open, x, +, number_sign, 2, math_close
        roles = [c.role for c in cells]
        plus_at = roles.index("math_op")
        ns_at = roles.index("number_sign")
        assert ns_at > plus_at


# ---------------------------------------------------------------------------
# Scripts: superscript / subscript / both
# ---------------------------------------------------------------------------


class TestScripts:
    def test_x_squared_simple(self, pipe):
        # Atomic base + atomic exponent: simplifiable → no script_close.
        cells = pipe.translate_text(r"$x^2$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert "math_superscript" in roles
        assert "math_script_close" not in roles
        sup = next(c for c in cells if c.role == "math_superscript")
        assert sup.dots == (3, 4)

    def test_x_subscript_simple(self, pipe):
        cells = pipe.translate_text(r"$x_1$").braille_ir.blocks[0].cells
        sub = next(c for c in cells if c.role == "math_subscript")
        assert sub.dots == (1, 6)
        assert all(c.role != "math_script_close" for c in cells)

    def test_complex_script_emits_close(self, pipe):
        # Multi-token exponent → not simplifiable, script_close fires.
        cells = pipe.translate_text(r"$x^{a+1}$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert "math_script_close" in roles
        close = next(c for c in cells if c.role == "math_script_close")
        assert close.dots == (1, 5, 6)

    def test_pythagorean_style(self, pipe):
        # x^2 + y^2 = z^2 — three simplifiable scripts, no close markers.
        cells = pipe.translate_text(r"$x^2 + y^2 = z^2$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert roles.count("math_superscript") == 3
        assert "math_script_close" not in roles
        assert roles.count("math_op") == 1
        assert roles.count("math_rel") == 1


# ---------------------------------------------------------------------------
# Fractions
# ---------------------------------------------------------------------------


class TestFractions:
    def test_simple_half_uses_antoine(self, pipe):
        # \frac{1}{2}: atomic-digit / atomic-digit → Antoine encoding.
        # Output is number_sign + upper-1 + lower-2 (⠆), no explicit
        # bar / open / close cells.
        cells = pipe.translate_text(r"$\frac{1}{2}$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert "math_fraction_bar" not in roles
        assert "math_fraction_close" not in roles
        assert "math_fraction_open" not in roles
        assert "math_digit_lower" in roles
        lower = next(c for c in cells if c.role == "math_digit_lower")
        assert lower.dots == (2, 3)   # Antoine lower 2

    def test_typed_slash_with_letter_renders_slash_bar(self, pipe):
        # 1/x — typed slash, recognised by the IR builder and routed
        # through the fraction path. Denominator is a letter so Antoine
        # doesn't apply; the slash mark ⠳ (1256) shows up as the bar.
        cells = pipe.translate_text(r"$1/x$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        bar = next(c for c in cells if c.role == "math_fraction_bar")
        assert bar.dots == (1, 2, 5, 6)
        assert "math_fraction_open" not in roles
        assert "math_fraction_close" not in roles

    def test_rules_txt_half(self, pipe):
        # rules.txt: 1/2 = ⠼⠁⠆
        assert render(pipe, r"$1/2$") == "⠼⠁⠆"

    def test_rules_txt_one_over_x(self, pipe):
        # rules.txt: 1/x = ⠼⠁⠳⠰⠭
        assert render(pipe, r"$1/x$") == "⠼⠁⠳⠰⠭"

    def test_rules_txt_compound_fraction(self, pipe):
        # rules.txt: \frac{1}{x+1} = ⠆⠼⠁ ⠳⠰⠭ ⠖⠼⠁⠰
        # The blanks in the spec are real braille blanks (⠀ U+2800).
        assert render(pipe, r"$\frac{1}{x+1}$") == "⠆⠼⠁⠀⠳⠰⠭⠀⠖⠼⠁⠰"

    def test_complex_numerator_adds_open_and_close(self, pipe):
        # \frac{a+1}{b}: numerator is multi-token → not simplifiable →
        # explicit open + close markers wrap the fraction.
        cells = pipe.translate_text(r"$\frac{a+1}{b}$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        assert "math_fraction_open" in roles
        assert "math_fraction_close" in roles
        close = next(c for c in cells if c.role == "math_fraction_close")
        assert close.dots == (5, 6)
        open_cell = next(c for c in cells if c.role == "math_fraction_open")
        assert open_cell.dots == (2, 3)


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


class TestRoots:
    def test_sqrt_x_full_layout(self, pipe):
        # sqrt(x) = sqrt_open(146) + sqrt_indicator(156) + content + sqrt_close(1456)
        cells = pipe.translate_text(r"$\sqrt{x}$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        # Two-cell math_open and math_close still bracket the formula.
        open_at = roles.index("math_sqrt_open")
        ind_at = roles.index("math_sqrt_indicator")
        close_at = roles.index("math_sqrt_close")
        assert open_at < ind_at < close_at
        assert cells[open_at].dots == (1, 4, 6)
        assert cells[ind_at].dots == (1, 5, 6)
        assert cells[close_at].dots == (1, 4, 5, 6)

    def test_cube_root_inserts_degree_before_indicator(self, pipe):
        # sqrt[3]{x} = sqrt_open + number_sign + digit 3 + sqrt_indicator + x + sqrt_close
        cells = pipe.translate_text(r"$\sqrt[3]{x}$").braille_ir.blocks[0].cells
        roles = [c.role for c in cells]
        open_at = roles.index("math_sqrt_open")
        ind_at = roles.index("math_sqrt_indicator")
        # Degree (number_sign + digit) sits between open and indicator.
        between = roles[open_at + 1 : ind_at]
        assert "number_sign" in between
        assert "math_digit" in between


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    def test_sin_x_uses_function_prefix_and_abbrev(self, pipe):
        # \sin x = function_prefix(1246) + s + (56 + x)
        cells = pipe.translate_text(r"$\sin x$").braille_ir.blocks[0].cells
        prefix = next(c for c in cells if c.role == "math_function_prefix")
        assert prefix.dots == (1, 2, 4, 6)
        name_cells = [c for c in cells if c.role == "math_function_name"]
        # ``sin`` is a single-cell abbreviation in the table.
        assert [c.dots for c in name_cells] == [(2, 3, 4)]

    def test_ln_x_full_spelling(self, pipe):
        cells = pipe.translate_text(r"$\ln x$").braille_ir.blocks[0].cells
        name_cells = [c for c in cells if c.role == "math_function_name"]
        assert [c.dots for c in name_cells] == [(1, 2, 3), (1, 3, 4, 5)]

    def test_arcsin_a_plus_base_letter(self, pipe):
        # rules §53 \arcsin = ⠫⠁⠎ = function_prefix + a + s.
        cells = pipe.translate_text(r"$\arcsin x$").braille_ir.blocks[0].cells
        name_cells = [c for c in cells if c.role == "math_function_name"]
        assert [c.dots for c in name_cells] == [(1,), (2, 3, 4)]


# ---------------------------------------------------------------------------
# Greek letters
# ---------------------------------------------------------------------------


class TestGreek:
    def test_alpha_plus_beta(self, pipe):
        cells = pipe.translate_text(r"$\alpha + \beta$").braille_ir.blocks[0].cells
        idents = [c for c in cells if c.role == "math_identifier"]
        # α = 46 + (1)  ;  β = 46 + (1,2)
        assert [c.dots for c in idents] == [(4, 6), (1,), (4, 6), (1, 2)]

    def test_capital_delta_uses_456_prefix(self, pipe):
        cells = pipe.translate_text(r"$\Delta$").braille_ir.blocks[0].cells
        idents = [c for c in cells if c.role == "math_identifier"]
        assert [c.dots for c in idents] == [(4, 5, 6), (1, 4, 5)]


# ---------------------------------------------------------------------------
# Matrices & determinants — <mtable> linear notation (《盲文常用数学符号》§十七)
#
# Regression lock for the "LaTeX matrix gets squished" worry: latex2mathml
# (pinned to 3.81.0 in uv.lock) converts a ``\\`` row break into a *separate*
# ``<mtr>`` row, so a multi-row LaTeX matrix translates per-row exactly like
# the Word / OMML path — the rows are NOT collapsed onto one line. An earlier
# project note claimed LaTeX matrices merged their rows upstream; that was a
# shell-escaping artifact during manual testing (``\\`` → ``\`` turns a row
# break into a LaTeX control space), not a real converter limitation. These
# goldens make the false belief impossible to reintroduce silently.
# ---------------------------------------------------------------------------


class TestMatrices:
    def test_pmatrix_two_rows_each_parenthesised(self, pipe):
        # Row 1 ⠣a b⠜ + Row 2 ⠣c d⠜: each row fenced with ⠣(126)…⠜(345),
        # elements space-separated. Two ⠣…⠜ pairs ⇒ rows kept distinct.
        out = render(pipe, r"$\begin{pmatrix} a & b \\ c & d \end{pmatrix}$")
        assert out == "⠣⠰⠁⠀⠰⠃⠜⠣⠰⠉⠀⠰⠙⠜"

    def test_plain_matrix_defaults_to_paren(self, pipe):
        # \begin{matrix} carries no fence of its own → parentheses by
        # default, identical to pmatrix.
        out = render(pipe, r"$\begin{matrix} a & b \\ c & d \end{matrix}$")
        assert out == "⠣⠰⠁⠀⠰⠃⠜⠣⠰⠉⠀⠰⠙⠜"

    def test_bmatrix_uses_square_brackets(self, pipe):
        # square brackets ⠷(12356)…⠾(23456); digits keep their number sign.
        out = render(pipe, r"$\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}$")
        assert out == "⠷⠼⠁⠀⠼⠃⠾⠷⠼⠉⠀⠼⠙⠾"

    def test_vmatrix_determinant_uses_vertical_bars(self, pipe):
        # Determinant: each row fenced with the determinant vertical bar
        # ⠸(456). Row 1's closing bar and Row 2's opening bar sit adjacent
        # (⠸⠸).
        out = render(pipe, r"$\begin{vmatrix} a & b \\ c & d \end{vmatrix}$")
        assert out == "⠸⠰⠁⠀⠰⠃⠸⠸⠰⠉⠀⠰⠙⠸"

    def test_three_by_three_keeps_all_three_rows(self, pipe):
        out = render(
            pipe,
            r"$\begin{matrix} a & b & c \\ d & e & f \\ g & h & i \end{matrix}$",
        )
        assert out == (
            "⠣⠰⠁⠀⠰⠃⠀⠰⠉⠜⠣⠰⠙⠀⠰⠑⠀⠰⠋⠜⠣⠰⠛⠀⠰⠓⠀⠰⠊⠜"
        )

    def test_rows_are_not_merged_structurally(self, pipe):
        # The specific anti-"squish" invariant, independent of cell
        # encoding: each row contributes one open + one close fence, so a
        # 3-row matrix yields 6 per-row math_delim cells (a squished single
        # row would yield only 2).
        cells = pipe.translate_text(
            r"$\begin{matrix} a & b \\ c & d \\ e & f \end{matrix}$"
        ).braille_ir.blocks[0].cells
        fences = [c for c in cells if c.role == "math_delim"]
        assert len(fences) == 6


class TestVectors:
    """Vector markers (《盲文常用数学符号》§十五) end-to-end. latex2mathml
    gives \\vec / \\overrightarrow an accent character of → (U+2192); the
    backend remaps that at the accent slot into the arrow marker (≠ the
    relation arrow ⠒⠕) and picks the single- vs double-letter form by
    letter count. \\overline works the same way with the short overline."""

    def test_vec_single_letter(self, pipe):
        # v + over-mark ⠘ + single-letter arrow marker ⠒⠂.
        assert render(pipe, r"$\vec{v}$") == "⠰⠧⠘⠒⠂"

    def test_vec_double_letter(self, pipe):
        # AB + over-mark ⠘ + double-letter arrow marker ⠒⠆.
        assert render(pipe, r"$\vec{AB}$") == "⠠⠁⠠⠃⠘⠒⠆"

    def test_overrightarrow_double_letter(self, pipe):
        # \overrightarrow{AB} has the same form as \vec{AB} (same → over
        # mrow(A,B)).
        assert render(pipe, r"$\overrightarrow{AB}$") == "⠠⠁⠠⠃⠘⠒⠆"

    def test_overline_single_vs_double(self, pipe):
        # short overline single-letter ⠒ vs double-letter ⠒⠒.
        assert render(pipe, r"$\overline{x}$") == "⠰⠭⠘⠒"
        assert render(pipe, r"$\overline{AB}$") == "⠠⠁⠠⠃⠘⠒⠒"

    def test_vector_length_single_bar(self, pipe):
        # vector length |v⃗| = single absolute-value bar ⠸ … ⠸ (docx §十五
        # item 5), reusing verbar.
        assert render(pipe, r"$|\vec{v}|$") == "⠸⠰⠧⠘⠒⠂⠸"

    def test_vector_norm_double_bar(self, pipe):
        # vector magnitude / norm ‖v⃗‖ = double vertical bar ⠻ … ⠻ (docx
        # §十五 item 6); ‖ ≠ the absolute-value |.
        assert render(pipe, r"$\|\vec{v}\|$") == "⠻⠰⠧⠘⠒⠂⠻"
        # A bare norm also works and no longer reports MATH_UNKNOWN_SYMBOL.
        res = pipe.translate_text(r"$\|x\|$")
        assert res.render() == "⠻⠰⠭⠻"
        assert not any(w.code.startswith("MATH_") for w in res.warnings)

    def test_zero_vector_has_no_arrow_dot(self, pipe):
        # The zero vector has no dedicated symbol and no second dot ⠂. The
        # conventional spelling is the short-overline form ⠼⠚⠘⠒ (0 +
        # over-mark + short overline), i.e. \bar{0} / \overline{0}.
        assert render(pipe, r"$\bar{0}$") == "⠼⠚⠘⠒"
        # \vec{0} producing ⠼⠚⠘⠒⠂ is just the mechanical result of \vec
        # applied to 0 (arrow marker ⠘⠒⠂), not the conventional spelling
        # of the zero vector.
        assert render(pipe, r"$\vec{0}$") == "⠼⠚⠘⠒⠂"


class TestGeometry:
    """Geometry symbols: the angle ∠ has a dedicated braille symbol ⠫⠪
    (docx geometry section, angle (1) = 1246+246), distinct from the
    shapes (triangle / square / circle, written in Chinese as 「三角形
    ABC」). A following letter takes no space and still gets a letter sign
    (docx rule 2)."""

    def test_angle_with_letters(self, pipe):
        # ∠ABC = ⠫⠪ + ABC (each with the capital sign ⠠), no space.
        assert render(pipe, r"$\angle ABC$") == "⠫⠪⠠⠁⠠⠃⠠⠉"

    def test_angle_in_equation(self, pipe):
        # ∠ABC = 90: the angle symbol does not affect the following
        # relation / digits.
        assert render(pipe, r"$\angle ABC = 90$") == "⠫⠪⠠⠁⠠⠃⠠⠉⠀⠶⠼⠊⠚"

    def test_triangle_with_letters(self, pipe):
        # triangle △ABC: same as the angle, ⠫⠲ + letters (no space,
        # letter sign added).
        assert render(pipe, r"$\triangle ABC$") == "⠫⠲⠠⠁⠠⠃⠠⠉"

    def test_figure_symbols(self, pipe):
        # Geometric shapes (latex2mathml path): all translated per docx as
        # ⠫(1246) + marker.
        assert render(pipe, r"$\triangle$") == "⠫⠲"      # triangle △
        assert render(pipe, r"$\square$") == "⠫⠶"        # square ◻ U+25FB
        assert render(pipe, r"$\bigcirc$") == "⠫⠂"       # circle ◯ U+25EF
        assert render(pipe, r"$\lozenge$") == "⠫⠙"       # rhombus ◊ U+25CA
        assert render(pipe, r"$\rightangle$") == "⠫⠦"    # right angle ∟ U+221F


class TestGeometryRelations:
    """Geometry relations (docx elementary-geometry section): perpendicular
    ⊥ ⠼⠄, similar ∼/∽ ⠔, parallel ∥ ⠇⠇, congruent ≅ ⠔⠶. latex2mathml
    codepoint traps: \\perp→U+27C2 (≠ the entity perp's U+22A5), \\sim→
    ~U+007E (same codepoint as \\tilde, treated as a tilde ⠢) — so similar
    goes through \\backsim/\\thicksim or directly ∼∽, not \\sim."""

    def test_perpendicular(self, pipe):
        # perpendicular ⊥ = ⠼⠄: both \perp(U+27C2) and \bot(U+22A5)
        # codepoints map.
        assert render(pipe, r"$\perp$") == "⠼⠄"
        assert render(pipe, r"$\bot$") == "⠼⠄"

    def test_similar(self, pipe):
        # similar = ⠔: \backsim(∽ U+223D) / \thicksim(∼ U+223C) hit.
        assert render(pipe, r"$\backsim$") == "⠔"
        assert render(pipe, r"$\thicksim$") == "⠔"

    def test_parallel_and_congruent_regression(self, pipe):
        # Regression: parallel ∥ ⠇⠇ and congruent ≅ ⠔⠶ were already
        # supported and are unaffected.
        assert render(pipe, r"$\parallel$") == "⠇⠇"
        assert render(pipe, r"$\cong$") == "⠔⠶"


class TestPercentArrowsOrder:
    """Percent sign % / per-mille ‰ (docx fraction section), vertical
    arrows ↑↓↕⇑⇓ (arrow section), order relations ≺≻⪯⪰ (set-theory
    section)."""

    def test_percent(self, pipe):
        # percent sign % = ⠼⠚⠴ (previously mistranslated as ⠨ via the
        # punctuation table); 50% = 50 + percent sign.
        assert render(pipe, r"$\%$") == "⠼⠚⠴"
        assert render(pipe, r"$50\%$") == "⠼⠑⠚⠼⠚⠴"

    def test_vertical_arrows(self, pipe):
        # Fill in the vertical arrows to complement the existing →←↔⇒⇔.
        assert render(pipe, r"$\uparrow$") == "⠰⠌"       # ↑
        assert render(pipe, r"$\downarrow$") == "⠘⠡"     # ↓
        assert render(pipe, r"$\updownarrow$") == "⠹⠄"   # ↕
        assert render(pipe, r"$\Uparrow$") == "⠌⠌"       # ⇑
        assert render(pipe, r"$\Downarrow$") == "⠡⠡"     # ⇓

    def test_order_relations(self, pipe):
        # ≺≻ strict order takes a space on both sides (like </>); ⪯⪰
        # (or-equal) takes a space before but not after (like ≤≥).
        assert render(pipe, r"$a \prec b$") == "⠰⠁⠀⠒⠪⠀⠰⠃"
        assert render(pipe, r"$a \succ b$") == "⠰⠁⠀⠕⠒⠀⠰⠃"
        assert render(pipe, r"$a \preceq b$") == "⠰⠁⠀⠒⠪⠶⠰⠃"
        assert render(pipe, r"$a \succeq b$") == "⠰⠁⠀⠕⠒⠶⠰⠃"


class TestFunctionAbbreviations:
    """New function abbreviations (docx complex-number / calculus / matrix
    sections): arg/mod/sgn/Tr/Sp/grad/div/rot, all ⠫ (function prefix) +
    letters. Most arrive via \\operatorname{...}; \\arg is emitted by
    latex2mathml as a literal \\arg, but the backend still hits it after
    lstripping the backslash; mod goes through \\bmod."""

    def test_arg_and_mod(self, pipe):
        assert render(pipe, r"$\arg z$") == "⠫⠁⠰⠵"          # argument arg = ⠫⠁
        assert render(pipe, r"$a \bmod b$") == "⠰⠁⠫⠍⠰⠃"    # modulo mod = ⠫⠍

    def test_sgn(self, pipe):
        assert render(pipe, r"$\operatorname{sgn} x$") == "⠫⠎⠛⠝⠰⠭"

    def test_trace(self, pipe):
        # matrix trace Tr / Sp: capital sign ⠠ + letters.
        assert render(pipe, r"$\operatorname{Tr} A$") == "⠫⠠⠞⠗⠠⠁"
        assert render(pipe, r"$\operatorname{Sp} A$") == "⠫⠠⠎⠏⠠⠁"

    def test_vector_calculus(self, pipe):
        # gradient/divergence/curl, single-letter abbreviations ⠫⠛ / ⠫⠙ /
        # ⠫⠗.
        assert render(pipe, r"$\operatorname{grad} f$") == "⠫⠛⠰⠋"
        assert render(pipe, r"$\operatorname{div} F$") == "⠫⠙⠠⠋"
        assert render(pipe, r"$\operatorname{rot} F$") == "⠫⠗⠠⠋"


# ---------------------------------------------------------------------------
# Warning hygiene: a clean LaTeX formula must not emit MATH_* warnings.
# ---------------------------------------------------------------------------


class TestWarningHygiene:
    @pytest.mark.parametrize(
        "src",
        [
            r"$x$",
            r"$x^2 + y^2 = z^2$",
            r"$\frac{1}{2}$",
            r"$\sqrt{x}$",
            r"$\sqrt[3]{x}$",
            r"$\sin x$",
            r"$\ln x$",
            r"$\arcsin x$",
            r"$\pi$",
            r"$\alpha + \beta$",
            r"$\begin{pmatrix} a & b \\ c & d \end{pmatrix}$",
            r"$\begin{vmatrix} a & b \\ c & d \end{vmatrix}$",
        ],
    )
    def test_clean_inputs_produce_no_math_warnings(self, pipe, src):
        result = pipe.translate_text(src)
        bad = [
            w for w in result.warnings
            if w.code.startswith("MATH_") or w.code in {"UNKNOWN_PUNCT", "MISSING_FINAL"}
        ]
        assert bad == [], f"unexpected warnings for {src}: {[w.code for w in bad]}"


# ---------------------------------------------------------------------------
# Provenance: every rendered char must be a real braille codepoint.
# ---------------------------------------------------------------------------


class TestRenderedCodepoints:
    @pytest.mark.parametrize(
        "src",
        [
            r"$x^2$",
            r"$\frac{a+1}{b}$",
            r"$\sqrt[3]{x+1}$",
            r"$\sin(\alpha + \beta)$",
            r"$\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}$",
        ],
    )
    def test_all_chars_in_braille_block(self, pipe, src):
        rendered = render(pipe, src)
        assert rendered  # non-empty
        for ch in rendered:
            cp = ord(ch)
            assert 0x2800 <= cp <= 0x28FF, f"non-braille char in {src!r}: U+{cp:04X}"
