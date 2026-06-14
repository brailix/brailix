import pytest

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.segment import (
    DefaultSegmenter,
    _segment_text,
    segmenter_registry,
)
from brailix.ir.document import Paragraph


def _segs(text: str, *, base: int = 0):
    block = Paragraph(text=text, span=Span(base, base + len(text)) if text else None)
    seg = DefaultSegmenter()
    return seg.segment(block, FrontendContext())


def _types(segments) -> list[str]:
    return [s.type for s in segments]


def _surfaces(segments) -> list[str]:
    return [s.surface for s in segments]


class TestEmpty:
    def test_empty_text(self):
        assert _segs("") == []

    def test_none_text(self):
        block = Paragraph(text=None)
        assert DefaultSegmenter().segment(block, FrontendContext()) == []


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

    def test_decimal_then_unit(self):
        s = _segs("3.5kg")
        assert _types(s) == ["digit_run", "latin_text"]
        assert s[0].surface == "3.5"


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


class TestSpace:
    def test_space_run(self):
        s = _segs("a b")
        assert _types(s) == ["latin_text", "space", "latin_text"]

    def test_multiple_spaces_collapsed_into_one_segment(self):
        s = _segs("a   b")
        assert _types(s) == ["latin_text", "space", "latin_text"]
        assert s[1].surface == "   "


class TestSpans:
    def test_span_offsets_match_text(self):
        text = "我在2026年"
        s = _segs(text)
        for seg in s:
            assert seg.surface == text[seg.span.start : seg.span.end]

    def test_base_offset_applied(self):
        block = Paragraph(text="abc", span=Span(100, 103))
        s = DefaultSegmenter().segment(block, FrontendContext())
        assert s[0].span == Span(100, 103)

    def test_zero_base_when_no_block_span(self):
        block = Paragraph(text="abc")
        s = DefaultSegmenter().segment(block, FrontendContext())
        assert s[0].span == Span(0, 3)


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


class TestRegistry:
    def test_default_is_registered(self):
        assert segmenter_registry.has("default")
        inst = segmenter_registry.get("default")
        assert inst.name == "default"

    def test_registry_lookup_returns_working_segmenter(self):
        seg = segmenter_registry.get("default")
        block = Paragraph(text="我在2026")
        out = seg.segment(block, FrontendContext())
        assert _types(out) == ["hanzi_text", "digit_run"]


class TestUnknownCategory:
    def test_non_printable_yields_unknown_segment(self):
        # NULL byte and other non-printables fall through to "unknown".
        s = _segs("\x00")
        assert _types(s) == ["unknown"]
        assert s[0].surface == "\x00"


class TestSegmentTextHelper:
    def test_empty_string_returns_empty_list(self):
        # Direct call path used by callers that have already stripped text.
        assert _segment_text("") == []


