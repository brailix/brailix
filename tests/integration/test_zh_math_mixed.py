"""End-to-end tests for prose that mixes Chinese text with inline
math fragments.

These are the realistic textbook / exam scenarios brailix ships
for: a Chinese sentence with one or more ``$...$`` formulas. The
goal here is to confirm that:

* segmenter peels the math out without swallowing surrounding hanzi;
* the math IR is filled (no ``MATH_*`` warnings);
* the rendered output contains both real hanzi syllables and the
  expected math markers (math_open/close, fraction_bar, etc);
* there are no unknown / blank-only cells leaking through.

We use jieba + pypinyin so multi-character words like ``面积`` /
``公式`` become real :class:`Word` nodes with pinyin, exercising the
"connect within words, inter-word space" rule alongside the math
sub-pipeline.
"""

from __future__ import annotations

import pytest

pytest.importorskip("jieba")
pytest.importorskip("pypinyin")
pytest.importorskip("latex2mathml.converter")

from brailix import Pipeline
from brailix.ir.inline import MathInline, Space


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    # jieba + pypinyin are pinned explicitly so this test exercises
    # the real multi-character word path. ``auto`` would also pick
    # them up if they're the only installed extras, but pinning is
    # explicit + reproducible.
    return Pipeline(
        profile="cn_current",
        analyzer="jieba",
        resolver="pypinyin",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _children(result):
    return result.ir.blocks[0].children


def _roles(result):
    return [c.role for block in result.braille_ir.blocks for c in block.cells]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestCircleArea:
    """圆面积 $S = \\pi r^2$ — Latin uppercase, Greek lowercase, script."""

    SRC = r"圆面积 $S = \pi r^2$"

    def test_ir_has_zh_words_and_math(self, pipe):
        result = pipe.translate_text(self.SRC)
        children = _children(result)
        kinds = [type(c).__name__ for c in children]
        assert "Word" in kinds      # 面积 should at least tokenize as a Word
        assert "MathInline" in kinds
        math = next(c for c in children if isinstance(c, MathInline))
        assert math.math is not None
        assert math.source == "latex"

    def test_render_contains_math_wrappers_and_pi(self, pipe):
        result = pipe.translate_text(self.SRC)
        roles = _roles(result)
        # cn_current has no formula-level wrappers; the inner cells are
        # still present.
        assert "math_rel" in roles  # =
        assert "math_superscript" in roles
        # Both Latin (S, r) and Greek (π) identifiers must appear.
        ident_dots = [
            c.dots for c in _all_cells(result) if c.role == "math_identifier"
        ]
        assert (6,) in ident_dots                # uppercase Latin prefix
        assert (4, 6) in ident_dots              # lowercase Greek prefix

    def test_no_math_warnings(self, pipe):
        result = pipe.translate_text(self.SRC)
        bad = [w for w in result.warnings if w.code.startswith("MATH_")]
        assert bad == []


class TestPythagorean:
    """勾股定理 $a^2 + b^2 = c^2$ — three simple scripts side by side."""

    SRC = r"勾股定理 $a^2 + b^2 = c^2$"

    def test_three_supers_no_close(self, pipe):
        result = pipe.translate_text(self.SRC)
        roles = _roles(result)
        assert roles.count("math_superscript") == 3
        assert "math_script_close" not in roles  # all atomic, simplifiable
        # One '+' and one '=' in the math part.
        assert roles.count("math_op") == 1
        assert roles.count("math_rel") == 1


class TestFractionInChinese:
    """求 $\\frac{1}{x+1}$ 的值 — simplifiable=False because denominator is a group."""

    SRC = r"求 $\frac{1}{x+1}$ 的值"

    def test_fraction_close_emitted(self, pipe):
        result = pipe.translate_text(self.SRC)
        roles = _roles(result)
        assert "math_fraction_bar" in roles
        assert "math_fraction_close" in roles

    def test_chinese_text_renders_too(self, pipe):
        result = pipe.translate_text(self.SRC)
        # The hanzi 求 / 的 / 值 should produce zh syllable cells.
        roles = _roles(result)
        assert any(r.startswith("zh_") for r in roles)


class TestMixedSpacing:
    """学习 $\\sin x$ 很有用 — confirms Space tokens survive on both
    sides of the math fragment and word-boundary spaces still fire."""

    SRC = r"学习 $\sin x$ 很有用"

    def test_math_surrounded_by_spaces(self, pipe):
        result = pipe.translate_text(self.SRC)
        children = _children(result)
        kinds = [type(c).__name__ for c in children]
        math_idx = kinds.index("MathInline")
        # User-typed spaces flanking the formula are preserved.
        assert kinds[math_idx - 1] == "Space"
        assert kinds[math_idx + 1] == "Space"

    def test_word_boundary_spaces_for_word_runs(self, pipe):
        result = pipe.translate_text(self.SRC)
        children = _children(result)
        # 学习 / 很 / 有用 are separate Chinese tokens; between adjacent
        # Word/HanziChar nodes a synthetic empty-surface Space sits.
        synthetic_spaces = [
            c for c in children if isinstance(c, Space) and c.surface == ""
        ]
        assert synthetic_spaces, "expected at least one synthetic word-boundary Space"

    def test_function_prefix_in_render(self, pipe):
        result = pipe.translate_text(self.SRC)
        roles = _roles(result)
        assert "math_function_prefix" in roles
        assert "math_function_name" in roles


class TestUnspacedChineseMathBoundary:
    """已知α (user typed no space) — the pipeline should synthesize a
    zero-width Space between Chinese and Greek/Latin/Math, satisfying the
    cross-script boundary requirement of segmentation-based connected
    writing.
    """

    def test_chinese_then_greek_gets_synthetic_space(self, pipe):
        result = pipe.translate_text("已知α")
        kinds = [type(c).__name__ for c in _children(result)]
        # There must be a Space separating Word(已知) and LatinWord(α).
        assert "Space" in kinds
        word_idx = kinds.index("Word")
        latin_idx = kinds.index("LatinWord")
        assert latin_idx == word_idx + 2
        assert kinds[word_idx + 1] == "Space"

    def test_chinese_then_math_gets_synthetic_space(self, pipe):
        result = pipe.translate_text(r"学习$\sin x$")
        kinds = [type(c).__name__ for c in _children(result)]
        math_idx = kinds.index("MathInline")
        assert math_idx >= 2
        assert kinds[math_idx - 1] == "Space"

    def test_user_typed_space_not_doubled(self, pipe):
        """When the user already typed a space, "已知 α" should not get an
        extra blank cell."""
        result = pipe.translate_text("已知 α")
        # In the rendered result, Word and LatinWord should be separated by
        # exactly one Space node (the user's space with surface=" ", not a
        # synthesized one).
        children = _children(result)
        word_idx = next(
            i for i, c in enumerate(children) if type(c).__name__ == "Word"
        )
        latin_idx = next(
            i for i, c in enumerate(children) if type(c).__name__ == "LatinWord"
        )
        between = children[word_idx + 1 : latin_idx]
        assert len(between) == 1
        assert type(between[0]).__name__ == "Space"

    def test_chinese_alpha_chinese_both_sides_get_space(self, pipe):
        """已知α的值 — α should get an automatic blank cell on both sides."""
        result = pipe.translate_text("已知α的值")
        kinds = [type(c).__name__ for c in _children(result)]
        latin_idx = kinds.index("LatinWord")
        # The immediate neighbours on both sides should be Space.
        assert kinds[latin_idx - 1] == "Space"
        assert kinds[latin_idx + 1] == "Space"


class TestExamSentence:
    """计算 $\\sqrt{x^2 + 1}$ 的导数 — combines sqrt + nested script."""

    SRC = r"计算 $\sqrt{x^2 + 1}$ 的导数"

    def test_sqrt_pipeline(self, pipe):
        result = pipe.translate_text(self.SRC)
        roles = _roles(result)
        assert "math_sqrt_open" in roles
        assert "math_sqrt_indicator" in roles
        assert "math_sqrt_close" in roles
        # The script inside the radicand contributes a superscript marker.
        assert "math_superscript" in roles

    def test_no_unknown_or_blank_warnings(self, pipe):
        result = pipe.translate_text(self.SRC)
        bad = [
            w for w in result.warnings
            if w.code in {"MATH_UNKNOWN_IDENTIFIER", "MATH_UNKNOWN_SYMBOL",
                          "MATH_NO_IR", "UNKNOWN_DIGIT"}
        ]
        assert bad == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_cells(result):
    return [c for block in result.braille_ir.blocks for c in block.cells]
