import pytest

from brailix.core.context import FrontendContext
from brailix.core.inline_math import wrap
from brailix.core.span import Span
from brailix.frontend.segmentation import (
    char_category,
    segment,
    segment_text,
)
from brailix.ir.document import Paragraph


def _segs(text: str, *, base: int = 0):
    block = Paragraph(text=text, span=Span(base, base + len(text)) if text else None)
    return segment(block, FrontendContext(profile="cn_current"))


def _types(segments) -> list[str]:
    return [s.type for s in segments]


def _surfaces(segments) -> list[str]:
    return [s.surface for s in segments]


class TestEmpty:
    def test_empty_text(self):
        assert _segs("") == []

    def test_none_text(self):
        block = Paragraph(text=None)
        assert segment(block, FrontendContext(profile="cn_current")) == []


class TestCharacterClasses:
    def test_pure_hanzi(self):
        s = _segs("我在重庆")
        assert _types(s) == ["hanzi_text"]
        assert _surfaces(s) == ["我在重庆"]

    def test_pure_digits(self):
        s = _segs("2026")
        assert _types(s) == ["digit_run"]
        assert s[0].surface == "2026"

    def test_pure_latin(self):
        s = _segs("hello")
        assert _types(s) == ["latin_text"]

    def test_mixed_hanzi_and_digits(self):
        s = _segs("我在2026年")
        assert _types(s) == ["hanzi_text", "digit_run", "hanzi_text"]
        assert _surfaces(s) == ["我在", "2026", "年"]

    def test_mixed_with_latin(self):
        s = _segs("电脑CPU性能")
        assert _types(s) == ["hanzi_text", "latin_text", "hanzi_text"]
        assert _surfaces(s) == ["电脑", "CPU", "性能"]

    def test_supplementary_plane_hanzi(self):
        # Rare given-name / dictionary characters live in CJK Ext B+
        # (supplementary planes). They must categorize as hanzi, not fall
        # through to ``unknown`` and a blank cell. 𠀀 = U+20000 (Ext B);
        # 𰀀 = U+30000 (Ext G).
        assert _types(_segs("𠀀")) == ["hanzi_text"]
        assert _types(_segs("𰀀")) == ["hanzi_text"]

    def test_supplementary_plane_hanzi_joins_bmp_run(self):
        # A supplementary-plane hanzi between BMP hanzi stays in one run.
        s = _segs("张𠀀华")
        assert _types(s) == ["hanzi_text"]
        assert _surfaces(s) == ["张𠀀华"]

    def test_ideographic_number_zero_is_hanzi(self):
        # 〇 (U+3007) reads líng and must route through the Chinese
        # frontend, not fall to punct → UNKNOWN_PUNCT + blank cell.
        s = _segs("〇")
        assert _types(s) == ["hanzi_text"]

    def test_ideographic_zero_keeps_year_run_intact(self):
        # 二〇二六年 must stay a single hanzi run; a misclassified 〇 would
        # split it into 二 / punct / 二六年 and drop the líng syllable.
        s = _segs("二〇二六年")
        assert _types(s) == ["hanzi_text"]
        assert _surfaces(s) == ["二〇二六年"]

    def test_iteration_marks_stay_punct(self):
        # 々 (U+3005) / 〆 (U+3006) are deliberately NOT hanzi: they carry
        # no standalone reading. Pinning the current behavior so a future
        # change to _is_hanzi is a conscious one.
        assert _types(_segs("々")) == ["punct"]
        assert _types(_segs("〆")) == ["punct"]

    def test_single_greek_letter(self):
        s = _segs("测量τ值")
        assert _types(s) == ["hanzi_text", "greek_text", "hanzi_text"]
        assert _surfaces(s) == ["测量", "τ", "值"]

    def test_greek_run_groups(self):
        s = _segs("ταυ")
        assert _types(s) == ["greek_text"]
        assert s[0].surface == "ταυ"

    def test_uppercase_greek(self):
        s = _segs("ΑΒΓ")
        assert _types(s) == ["greek_text"]
        assert s[0].surface == "ΑΒΓ"

    def test_latin_greek_split_into_separate_runs(self):
        # Latin/Greek script boundaries must be split so downstream can carry
        # their own prefixes.
        s = _segs("xτy")
        assert _types(s) == ["latin_text", "greek_text", "latin_text"]
        assert _surfaces(s) == ["x", "τ", "y"]

    def test_greek_variant_phi(self):
        # latex2mathml maps \phi to ϕ (U+03D5); it must take the greek_text path.
        s = _segs("ϕ")
        assert _types(s) == ["greek_text"]


class TestDigitClassification:
    """``_is_digit`` accepts ONLY ASCII and fullwidth digits, not every
    ``str.isdigit()`` codepoint. Superscripts, circled digits and other
    scripts' decimals must not fold into a number run."""

    def test_superscript_not_folded_into_number(self):
        # 5² must split into digit_run "5" + punct "²". A misclassified ²
        # gets swallowed into Number("5²"), which the backend cannot render.
        s = _segs("5²")
        assert _types(s) == ["digit_run", "punct"]
        assert _surfaces(s) == ["5", "²"]

    def test_circled_digit_is_not_a_digit_run(self):
        # ① (U+2460) satisfies str.isdigit() but is not a numeral.
        assert _types(_segs("①")) == ["punct"]

    def test_other_script_decimal_is_not_a_digit_run(self):
        # Arabic-Indic ٢ (U+0662) satisfies str.isdigit()/isdecimal() but is
        # neither ASCII nor fullwidth, so it must not take the number path.
        assert _types(_segs("٢")) == ["punct"]

    def test_fullwidth_digits_still_classify(self):
        # Regression guard: fullwidth ０-９ remain a digit_run.
        s = _segs("２０２６")
        assert _types(s) == ["digit_run"]
        assert s[0].surface == "２０２６"


class TestDecimalNumbers:
    def test_simple_decimal(self):
        s = _segs("3.5")
        assert _types(s) == ["digit_run"]
        assert s[0].surface == "3.5"

    def test_thousands_comma(self):
        s = _segs("1,234")
        assert _types(s) == ["digit_run"]
        assert s[0].surface == "1,234"

    def test_trailing_dot_not_absorbed(self):
        # "12." → digit_run "12" + punct "."
        s = _segs("12.")
        assert _types(s) == ["digit_run", "punct"]
        assert _surfaces(s) == ["12", "."]

    def test_decimal_then_unit(self):
        s = _segs("3.5kg")
        assert _types(s) == ["digit_run", "latin_text"]
        assert s[0].surface == "3.5"


class TestFullwidthDecimalNumbers:
    """Full-width ．/， between digits fold into the digit_run as their
    ASCII separators, so 「３．５」 and 「３，５００」 segment (and translate)
    exactly like their half-width spellings. Full-width *digits* keep
    riding the run verbatim — the prose backend folds those at render
    time (backend/_digits.py fold_nonascii) — so only the separators are
    folded, here, into the surface."""

    def test_fullwidth_decimal_point_folds(self):
        s = _segs("３．５米")
        assert _types(s) == ["digit_run", "hanzi_text"]
        assert _surfaces(s) == ["３.５", "米"]

    def test_fullwidth_comma_folds(self):
        s = _segs("３，５００米")
        assert _types(s) == ["digit_run", "hanzi_text"]
        assert _surfaces(s) == ["３,５００", "米"]

    def test_mixed_multi_separator_run(self):
        s = _segs("１，２３４．５６")
        assert _types(s) == ["digit_run"]
        assert s[0].surface == "１,２３４.５６"

    def test_folded_run_keeps_one_char_per_span_position(self):
        # Each folded separator is one char for one char, so span lengths
        # still tile the source even where the surface differs from it.
        s = _segs("３．５")
        assert s[0].span == Span(0, 3)
        assert s[0].span.length == len(s[0].surface)

    def test_fullwidth_separator_needs_digits_on_both_sides(self):
        # Flanked by non-digits it stays prose punctuation, like its
        # ASCII twin ("12." / "1,," shapes).
        assert _types(_segs("３．")) == ["digit_run", "punct"]
        assert _surfaces(_segs("３．")) == ["３", "．"]
        assert _types(_segs("．５")) == ["punct", "digit_run"]

    def test_ideographic_full_stop_is_not_a_decimal(self):
        # 。(U+3002) is the Chinese sentence end, never a decimal point —
        # unlike ． (U+FF0E), its fold_fullwidth target is not ".".
        s = _segs("３。５")
        assert _types(s) == ["digit_run", "punct", "digit_run"]
        assert _surfaces(s) == ["３", "。", "５"]


class TestPunctuation:
    def test_chinese_punct_one_per_segment(self):
        s = _segs("你好，世界。")
        assert _types(s) == ["hanzi_text", "punct", "hanzi_text", "punct"]
        assert _surfaces(s) == ["你好", "，", "世界", "。"]

    def test_consecutive_puncts_split(self):
        s = _segs("！？")
        assert _types(s) == ["punct", "punct"]
        assert _surfaces(s) == ["！", "？"]


class TestMathOp:
    """Half-width math operators form their own segments (math_op) inside Chinese
    prose, not mixed into punct.

    Design premise: half-width = math semantics, full-width = Chinese punctuation
    semantics (see the ``_BARE_MATH_OPERATORS`` comment in segment.py). The
    downstream Normalizer turns each math_op segment into a single-character
    MathInline, rendered through the math backend.
    """

    @pytest.mark.parametrize("ch", list("()[]{}+-*/=<>|"))
    def test_each_half_width_math_op_isolated(self, ch):
        s = _segs(ch)
        assert _types(s) == ["math_op"]
        assert _surfaces(s) == [ch]

    def test_one_char_per_segment_never_merged(self):
        # "()" and "( )" — both the adjacent and spaced forms should be one
        # character per segment.
        s = _segs("()")
        assert _types(s) == ["math_op", "math_op"]
        assert _surfaces(s) == ["(", ")"]

    def test_fillin_blank_with_spaces(self):
        # The `(   )` blank commonly found at the end of multiple-choice questions.
        s = _segs("选项是(   )")
        assert _types(s) == ["hanzi_text", "math_op", "space", "math_op"]
        assert _surfaces(s) == ["选项是", "(", "   ", ")"]

    def test_subquestion_number(self):
        # `(1)求` — sub-question number: math_op + digit_run + math_op + hanzi
        s = _segs("(1)求")
        assert _types(s) == ["math_op", "digit_run", "math_op", "hanzi_text"]
        assert _surfaces(s) == ["(", "1", ")", "求"]

    def test_bare_math_op_between_letters(self):
        # a+b: the math_op is not absorbed by the adjacent latin.
        s = _segs("a+b")
        assert _types(s) == ["latin_text", "math_op", "latin_text"]
        assert _surfaces(s) == ["a", "+", "b"]

    def test_hyphen_minus_after_equals(self):
        # x=-5: the two math_ops "=-" are adjacent; "-" must not fall back to
        # punct (otherwise it is not found in the Chinese punctuation table,
        # which only has the — em-dash, and UNKNOWN_PUNCT would insert a blank
        # cell, making it look like an extra space after "=").
        s = _segs("x=-5")
        assert _types(s) == ["latin_text", "math_op", "math_op", "digit_run"]
        assert _surfaces(s) == ["x", "=", "-", "5"]

    def test_full_width_parens_still_punct(self):
        # Full-width （） go through the Chinese punctuation table, untouched.
        s = _segs("（提示）")
        assert _types(s) == ["punct", "hanzi_text", "punct"]
        assert _surfaces(s) == ["（", "提示", "）"]

    def test_english_punct_still_punct(self):
        # Half-width , . ! ? : ; are not in _BARE_MATH_OPERATORS, so they still
        # go through punct.
        s = _segs("a, b.")
        assert _types(s) == ["latin_text", "punct", "space", "latin_text", "punct"]
        assert _surfaces(s) == ["a", ",", " ", "b", "."]

    def test_math_op_inside_protected_region_unchanged(self):
        # $...$ is protected; the ( ) inside it stays part of the whole
        # math_inline segment.
        s = _segs("$f(x)$")
        assert _types(s) == ["math_inline"]
        assert _surfaces(s) == ["$f(x)$"]


class TestNonAsciiMathSymbol:
    """Non-ASCII Unicode math symbols (category Sm: ∈ ≤ ∀ ∑ → …) are bare
    math operators too — the same "half-width = math" routing as the ASCII
    ops, so ``x∈A`` reaches the math backend instead of dying as an unknown
    cell.

    The category-Sm boundary is doing real work: it is exactly what keeps the
    Chinese name separator · (category Po) and the degree sign ° (So) out of
    the math path, and what (with fold_fullwidth) excludes full-width ＝ so it
    keeps its "use the half-width form" diagnostic.
    """

    @pytest.mark.parametrize("ch", list("∈∉≤≥≠∀∃∑∏∫√∞∂∇⊂⊃∪∩→←↔±×÷"))
    def test_non_ascii_math_symbol_is_math_op(self, ch):
        s = _segs(ch)
        assert _types(s) == ["math_op"]
        assert _surfaces(s) == [ch]

    def test_element_of_between_operands(self):
        s = _segs("x∈A")
        assert _types(s) == ["latin_text", "math_op", "latin_text"]
        assert _surfaces(s) == ["x", "∈", "A"]

    def test_middle_dot_stays_punct(self):
        # · (U+00B7, category Po) is the Chinese name separator 间隔号, not a
        # multiplication dot — it must stay prose punctuation.
        s = _segs("卡尔·马克思")
        assert _types(s) == ["hanzi_text", "punct", "hanzi_text"]
        assert _surfaces(s) == ["卡尔", "·", "马克思"]

    def test_full_width_equals_stays_punct(self):
        # ＝ (U+FF1D) is category Sm but a full-width form; fold_fullwidth
        # catches it, so it is NOT routed to math and keeps its
        # "use the half-width form" warning downstream.
        s = _segs("＝")
        assert _types(s) == ["punct"]
        assert _surfaces(s) == ["＝"]

    def test_ascii_tilde_stays_punct(self):
        # ~ (U+007E) is category Sm but ASCII; the explicit _BARE_MATH_OPERATORS
        # set governs ASCII and omits ~, so ~ stays prose punct.
        s = _segs("1~10")
        assert _types(s) == ["digit_run", "punct", "digit_run"]
        assert _surfaces(s) == ["1", "~", "10"]

    def test_degree_sign_stays_punct(self):
        # ° (U+00B0, category So) is not a math symbol (Sm), so it is not
        # routed to math.
        s = _segs("30°")
        assert _types(s) == ["digit_run", "punct"]
        assert _surfaces(s) == ["30", "°"]


class TestSpace:
    def test_space_run(self):
        s = _segs("a b")
        assert _types(s) == ["latin_text", "space", "latin_text"]

    def test_multiple_spaces_collapsed_into_one_segment(self):
        s = _segs("a   b")
        assert _types(s) == ["latin_text", "space", "latin_text"]
        assert s[1].surface == "   "


class TestProtectedRegions:
    def test_inline_math_dollar(self):
        s = _segs("计算 $x^2+y^2$ 即可")
        m = next(seg for seg in s if seg.type == "math_inline")
        # Surface includes the surrounding $ markers; downstream
        # math adapter strips them.
        assert m.surface == "$x^2+y^2$"

    def test_display_math_dollars_are_not_inline_math(self):
        s = _segs("before $$x$$ after")
        assert all(seg.type != "math_inline" for seg in s)
        assert _surfaces(s) == [
            "before",
            " ",
            "$",
            "$",
            "x",
            "$",
            "$",
            " ",
            "after",
        ]

    def test_adjacent_inline_math_islands(self):
        # Two inline-math islands with no space between them ($a$$b$) must
        # parse as two protected regions. The old lookaround regex rejected
        # the whole run because the $$ junction tripped its guards, silently
        # turning both formulas into literal latin letters.
        s = _segs("$a$$b$")
        assert _types(s) == ["math_inline", "math_inline"]
        assert _surfaces(s) == ["$a$", "$b$"]

    def test_adjacent_inline_math_islands_in_prose(self):
        s = _segs("看 $x^2$$y^2$ 这里")
        math = [seg.surface for seg in s if seg.type == "math_inline"]
        assert math == ["$x^2$", "$y^2$"]


class TestTaggedMathIslands:
    """The docx-import shape: prose that mixes literal ``$`` characters
    with machine-written tagged islands (:func:`brailix.core.inline_math.wrap`).
    The island is one atom; a prose ``$`` never pairs with it, across it
    or into it — the greedy pair used to shred the island and leak its
    ``\\x1d`` markers into the braille as "space" cells."""

    def test_tagged_island_is_one_atomic_math_inline(self):
        island = wrap("omml", "<m:oMath>x+1</m:oMath>")
        s = _segs("公式" + island + "结束")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "公式"),
            ("math_inline", island),
            ("hanzi_text", "结束"),
        ]

    def test_island_payload_with_literal_dollar_survives_whole(self):
        # wrap escapes an inner ``$`` as RS (\\x1e), so the island's own
        # close is the only ``$`` the scan can find — the island cannot be
        # split by its payload.
        island = wrap("omml", "a<$>b")
        s = _segs("前" + island + "后")
        assert [seg.surface for seg in s if seg.type == "math_inline"] == [island]

    def test_bare_dollar_does_not_pair_with_island_open(self):
        # 费用$100，公式<ISLAND> — the prose ``$`` must not take the
        # island's opening ``$`` as its close.
        island = wrap("omml", "x+1")
        s = _segs("费用$100，公式" + island + "结束")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "费用"),
            ("punct", "$"),
            ("digit_run", "100"),
            ("punct", "，"),
            ("hanzi_text", "公式"),
            ("math_inline", island),
            ("hanzi_text", "结束"),
        ]

    def test_bare_dollar_does_not_pair_across_an_island(self):
        # A ``$`` before the island and one after it: the span between
        # them would swallow the machine island whole.
        island = wrap("omml", "x+1")
        s = _segs("总价$" + island + "中的$5元")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "总价"),
            ("punct", "$"),
            ("math_inline", island),
            ("hanzi_text", "中的"),
            ("punct", "$"),
            ("digit_run", "5"),
            ("hanzi_text", "元"),
        ]

    def test_island_after_bare_dollar_still_protects_itself(self):
        island = wrap("omml", "x+1")
        s = _segs(island + "总价$5元")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("math_inline", island),
            ("hanzi_text", "总价"),
            ("punct", "$"),
            ("digit_run", "5"),
            ("hanzi_text", "元"),
        ]

    def test_doubled_dollars_glued_to_an_island_prefer_the_island(self):
        # The pair's second ``$`` opens the island; skipping the ``$$``
        # would shred it, so the island reading wins and the stray ``$``
        # stays punct.
        island = wrap("omml", "x+1")
        s = _segs("a$" + island + "b")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("latin_text", "a"),
            ("punct", "$"),
            ("math_inline", island),
            ("latin_text", "b"),
        ]

    def test_no_control_marker_leaks_outside_the_island(self):
        island = wrap("omml", "x+1")
        for seg in _segs("费用$100，公式" + island + "结束"):
            if seg.type != "math_inline":
                assert "\x1d" not in seg.surface
                assert "\x1e" not in seg.surface


class TestUntaggedMathIslands:
    """The Word-importer export shape: ``$<math ...>...</math>$`` islands
    with NO wire tag. Same atom rule as tagged islands — a prose ``$``
    never pairs with, across or into one. The payload is raw MathML whose
    ``$`` is entity-escaped by the exporter, and the close is located
    past the ``</math>`` terminator so even an un-escaped payload ``$``
    can't split the island."""

    ISLAND = "$<math><msup><mi>x</mi><mn>2</mn></msup></math>$"

    def test_untagged_island_is_one_atomic_math_inline(self):
        s = _segs("公式" + self.ISLAND + "结束")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "公式"),
            ("math_inline", self.ISLAND),
            ("hanzi_text", "结束"),
        ]

    def test_prose_dollar_before_untagged_island_does_not_pair(self):
        # 这本书$5，求 <ISLAND> 的值 — the price ``$`` must not take the
        # island's opening ``$`` as its close (this exact shape used to
        # swallow 「5，求 」 as a bogus formula and shred the MathML).
        s = _segs("这本书$5，求 " + self.ISLAND + " 的值")
        types = [(seg.type, seg.surface) for seg in s]
        assert ("math_inline", self.ISLAND) in types
        # Exactly one math inline, and the prose between the amounts
        # survives as prose (not inside any math span).
        assert [t for t, _ in types].count("math_inline") == 1
        assert ("punct", "$") in types
        assert ("hanzi_text", "求") in types or any(
            t == "hanzi_text" and "求" in surf for t, surf in types
        )

    def test_payload_dollar_does_not_close_the_island_early(self):
        # Raw MathML carrying an un-escaped ``$`` in an <mi>: the close is
        # the ``$`` after ``</math>``, so the island stays whole.
        island = "$<math><mi>$</mi></math>$"
        s = _segs("a" + island + "b")
        assert [seg.surface for seg in s if seg.type == "math_inline"] == [island]

    def test_bare_dollar_does_not_pair_across_an_untagged_island(self):
        s = _segs("总价$" + self.ISLAND + "中的$5元")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "总价"),
            ("punct", "$"),
            ("math_inline", self.ISLAND),
            ("hanzi_text", "中的"),
            ("punct", "$"),
            ("digit_run", "5"),
            ("hanzi_text", "元"),
        ]

    def test_unterminated_math_prefix_is_prose(self):
        # ``$<math`` with no ``</math>`` and no closing ``$`` — not an
        # island the scanner can close, falls through to pairing rules.
        s = _segs("a$<math b")
        assert [seg.surface for seg in s if seg.type == "math_inline"] == []


class TestCurrencyDollar:
    """A ``$`` glued to a digit is a currency amount, not half of a
    formula: the greedy pair used to swallow the prose between two
    amounts (「苹果$5,梨$6。」 turned 梨 into blank math cells). The
    disambiguation looks only at the character *after* the candidate
    close — never at the island's content, which is what keeps golden
    hanzi-bearing formulas like ``$\\text{速度}$`` legal."""

    def test_two_amounts_and_prose_between_survive(self):
        s = _segs("苹果$5,梨$6。")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "苹果"),
            ("punct", "$"),
            ("digit_run", "5"),
            ("punct", ","),
            ("hanzi_text", "梨"),
            ("punct", "$"),
            ("digit_run", "6"),
            ("punct", "。"),
        ]

    def test_spaced_amounts_stay_prose(self):
        s = _segs("价格 $5 和 $6 元")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "价格"),
            ("space", " "),
            ("punct", "$"),
            ("digit_run", "5"),
            ("space", " "),
            ("hanzi_text", "和"),
            ("space", " "),
            ("punct", "$"),
            ("digit_run", "6"),
            ("space", " "),
            ("hanzi_text", "元"),
        ]

    def test_three_amounts_all_stay_prose(self):
        # The chain must not re-pair the 2nd with the 3rd ``$`` once the
        # 1st is refused: every amount stands alone.
        s = _segs("苹果$5,梨$6,桃$7。")
        assert [seg.surface for seg in s if seg.type == "punct" and seg.surface == "$"] == 3 * ["$"]
        assert [seg.surface for seg in s if seg.type == "digit_run"] == ["5", "6", "7"]
        assert [seg.surface for seg in s if seg.type == "hanzi_text"] == ["苹果", "梨", "桃"]

    def test_digit_led_formula_after_an_amount_survives(self):
        # 价格 $5 和 $1+1=2$ — the candidate close is followed by a digit
        # but its own next ``$`` is a clean close, so it was a formula
        # opener, not a price tag: the amount stays prose AND the formula
        # still pairs.
        s = _segs("价格 $5 和 $1+1=2$ 知道")
        assert [(seg.type, seg.surface) for seg in s] == [
            ("hanzi_text", "价格"),
            ("space", " "),
            ("punct", "$"),
            ("digit_run", "5"),
            ("space", " "),
            ("hanzi_text", "和"),
            ("space", " "),
            ("math_inline", "$1+1=2$"),
            ("space", " "),
            ("hanzi_text", "知道"),
        ]

    def test_digit_starting_math_still_pairs_when_not_after_a_currency(self):
        # The golden shape 我 $1+1=2$ 知道: its closer is followed by a
        # space, so the currency lookahead never fires.
        s = _segs("我 $1+1=2$ 知道")
        assert [seg.surface for seg in s if seg.type == "math_inline"] == ["$1+1=2$"]

    def test_lone_number_island_still_pairs(self):
        # "$5$" — the closer is followed by end-of-text, not a digit.
        assert _surfaces(_segs("$5$")) == ["$5$"]

    def test_hanzi_bearing_formula_still_pairs(self):
        # Pins that the disambiguation is positional, not content-based:
        # 汉字 inside $...$ stays legal math (golden $\text{速度}$ family).
        assert _surfaces(_segs("$\\text{速度}$")) == ["$\\text{速度}$"]
        assert _surfaces(_segs("$v_{\\text{初速度}}$")) == ["$v_{\\text{初速度}}$"]

    def test_close_glued_to_fullwidth_digit_is_currency_too(self):
        s = _segs("苹果$5，梨$６。")
        assert [seg.surface for seg in s if seg.type == "math_inline"] == []
        assert [seg.surface for seg in s if seg.type == "punct"] == ["$", "，", "$", "。"]

class TestRoundTripText:
    @pytest.mark.parametrize(
        "text",
        [
            "我在2026年5月17日去了重庆银行",
            "电脑CPU性能，速度3.5GHz。",
            "计算 $x^2+y^2$ 然后写出来",
            "  leading and  trailing  ",
        ],
    )
    def test_segments_concatenate_to_original(self, text):
        s = _segs(text)
        assert "".join(_surfaces(s)) == text


class TestNoSegmenterFamily:
    """The pass is a function, not an adapter picked out of a registry.

    Pinned because the shape it replaced looked harmless: a ``Segmenter``
    protocol, a ``segmenter_registry``, an ``auto`` adapter that read the
    language off ``ctx.options`` and a ``ctx.options["segmenter"]`` override —
    a whole plugin family keyed by the language subtag, beside the
    ``language_frontend_registry`` keyed by the same subtag. What varies by
    language now lives on that language's frontend
    (:meth:`LanguageFrontend.segment`), and this module keeps only the
    built-in chunking every language builds on.
    """

    def test_the_module_publishes_functions_only(self):
        import brailix.frontend.segmentation as mod

        assert not hasattr(mod, "segmenter_registry")
        assert not hasattr(mod, "DefaultSegmenter")
        assert set(mod.__all__) == {"segment", "segment_text", "char_category"}

    def test_an_option_named_segmenter_is_not_consulted(self):
        # There is no name to resolve, so a stray key cannot redirect the
        # pass — and cannot silently do nothing either, which is what a
        # leftover ``ctx.options["segmenter"]`` in a caller would do if the
        # lookup were still there but the registry empty.
        ctx = FrontendContext(
            profile="cn_current", options={"segmenter": "nonexistent"}
        )
        out = segment(Paragraph(text="我在2026"), ctx)
        assert _types(out) == ["hanzi_text", "digit_run"]

    def test_context_is_optional(self):
        # The pass reads the block and nothing else, so a caller without a
        # built context (a unit test, a language delegating its own) can drive
        # it bare.
        assert _types(segment(Paragraph(text="我在2026"))) == [
            "hanzi_text",
            "digit_run",
        ]


class TestUnknownCategory:
    def test_non_printable_yields_unknown_segment(self):
        # NULL byte and other non-printables fall through to "unknown".
        s = _segs("\x00")
        assert _types(s) == ["unknown"]
        assert s[0].surface == "\x00"


class TestSegmentTextHelper:
    """The published reuse seam: a language passes its own classifier and
    inherits every language-neutral rule."""

    def test_empty_string_returns_empty_list(self):
        # Direct call path used by callers that have already stripped text.
        assert segment_text("") == []

    def test_a_custom_classifier_renames_only_what_it_claims(self):
        # What a new language does: claim your script, defer the rest. The
        # protected-region scan, the digit run and the one-Segment-per-punct
        # rule all still apply — that is the point of reusing this rather
        # than writing a chunker.
        def categorize(ch: str) -> str:
            cat = char_category(ch)
            return "xx_text" if cat == "hanzi_text" else cat

        out = segment_text("我在2026年，$x^2$", categorize=categorize)
        assert [(s.type, s.surface) for s in out] == [
            ("xx_text", "我在"),
            ("digit_run", "2026"),
            ("xx_text", "年"),
            ("punct", "，"),
            ("math_inline", "$x^2$"),
        ]

    def test_base_offset_shifts_every_span(self):
        out = segment_text("我2", base_offset=10)
        assert [(s.span.start, s.span.end) for s in out] == [(10, 11), (11, 12)]


class TestCharCategory:
    """The classifier a language's own composes with."""

    def test_the_categories_a_language_defers_to(self):
        assert char_category("我") == "hanzi_text"
        assert char_category("7") == "digit_run"
        assert char_category("a") == "latin_text"
        assert char_category("α") == "greek_text"
        assert char_category(" ") == "space"
        assert char_category("，") == "punct"
        assert char_category("+") == "math_op"
        assert char_category("\x00") == "unknown"


