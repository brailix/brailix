

from brailix.core.context import FrontendContext
from brailix.core.segment import Segment
from brailix.core.span import Span
from brailix.frontend.normalization import (
    DefaultNormalizer,
    _peel_marker_if_starts_with,
    normalizer_registry,
)
from brailix.frontend.segmentation import DefaultSegmenter
from brailix.ir.document import Paragraph
from brailix.ir.inline import (
    Date,
    LatinWord,
    MathInline,
    Number,
    Punct,
    Space,
    Unknown,
)


def _normalize_text(text: str):
    block = Paragraph(text=text)
    segs = DefaultSegmenter().segment(block, FrontendContext(profile="cn_current"))
    return DefaultNormalizer().normalize(segs, FrontendContext(profile="cn_current"))


# ---------------------------------------------------------------------------
# Atomic conversions
# ---------------------------------------------------------------------------


class TestAtomicConversions:
    def test_bare_number(self):
        out = _normalize_text("2026")
        assert len(out) == 1
        assert isinstance(out[0], Number)
        assert out[0].surface == "2026"

    def test_punct(self):
        out = _normalize_text("。")
        assert isinstance(out[0], Punct)

    def test_space(self):
        out = _normalize_text(" ")
        assert isinstance(out[0], Space)

    def test_inline_math(self):
        out = _normalize_text("$x^2$")
        assert isinstance(out[0], MathInline)
        assert out[0].surface == "$x^2$"
        assert out[0].source == "latex"
        assert out[0].math is None  # filled by the MathParser

    def test_math_op_becomes_prefilled_mathinline(self):
        # Half-width ( inside Chinese prose → single-character MathInline, with
        # the math field pre-filled as the single-element <math><mo>(</mo></math>.
        # When FrontendDriver.attach_math sees math already filled, it skips the math
        # frontend and does not call latex2mathml.
        out = _normalize_text("(")
        assert len(out) == 1
        assert isinstance(out[0], MathInline)
        assert out[0].surface == "("
        assert out[0].source == "mathml"
        assert out[0].math is not None
        assert out[0].math.tag == "math"
        kids = list(out[0].math)
        assert len(kids) == 1
        assert kids[0].tag == "mo"
        assert kids[0].text == "("

    def test_math_op_each_char_separately(self):
        # `()` — two characters, each its own MathInline.
        out = _normalize_text("()")
        assert len(out) == 2
        assert all(isinstance(n, MathInline) for n in out)
        assert [n.surface for n in out] == ["(", ")"]
        # Each one's <mo> text matches.
        assert [list(n.math)[0].text for n in out] == ["(", ")"]

    def test_math_op_hyphen_aliases_to_minus_in_mo(self):
        # ASCII `-` (U+002D) has no HTML5 entity and is not found in the symbols
        # table; when building MathML, the <mo> text is rewritten to `−` (U+2212)
        # so the backend hits `minus;`. MathInline.surface stays the original
        # `-`, preserving the source text highlighted during proofreading.
        out = _normalize_text("-")
        assert len(out) == 1
        assert isinstance(out[0], MathInline)
        assert out[0].surface == "-"
        assert out[0].math is not None
        kids = list(out[0].math)
        assert len(kids) == 1
        assert kids[0].tag == "mo"
        assert kids[0].text == "−"

    def test_latin_lowercase_word(self):
        out = _normalize_text("hello")
        assert isinstance(out[0], LatinWord)

    def test_all_caps_run_is_a_plain_word_node(self):
        # There is no separate acronym node: capitalisation is in the
        # surface, and the backend is what reads it (see
        # tests/backend/test_latin.py). The normalizer's job here is only
        # to say "this is a Latin run".
        out = _normalize_text("CPU")
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "CPU"

    def test_single_uppercase_letter_is_the_same_node(self):
        out = _normalize_text("A")
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "A"

    def test_greek_lowercase_letter_becomes_latin_word(self):
        # τ takes the same IR path as Latin letters: the backend's translate_latin
        # automatically adds the Greek lowercase sign ⠨ via profile.letter().
        out = _normalize_text("τ")
        assert len(out) == 1
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "τ"

    def test_greek_uppercase_run_becomes_acronym(self):
        out = _normalize_text("ΑΒΓ")
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "ΑΒΓ"

    def test_greek_mixed_case_word(self):
        out = _normalize_text("ταυ")
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "ταυ"

    def test_hanzi_text_passes_through_as_segment(self):
        out = _normalize_text("我在")
        assert len(out) == 1
        assert isinstance(out[0], Segment)
        assert out[0].type == "hanzi_text"
        assert out[0].surface == "我在"


# ---------------------------------------------------------------------------
# Date pattern
# ---------------------------------------------------------------------------


class TestDate:
    def test_full_date(self):
        out = _normalize_text("2026年5月17日")
        assert len(out) == 1
        d = out[0]
        assert isinstance(d, Date)
        assert d.surface == "2026年5月17日"
        assert d.span == Span(0, 10)  # 2,0,2,6,年,5,月,1,7,日 = 10 chars
        # components: 2026年 / 5月 / 17日
        assert [(c.digits, c.marker) for c in d.components] == [
            ("2026", "年"),
            ("5", "月"),
            ("17", "日"),
        ]
        # ARCHITECTURE#arch-boundaries: structural-marker readings are filled by the
        # normalizer (fixed 年→nián etc.), NOT the PinyinResolver — guard
        # that observable result so a deleted/renamed _MARKER_PINYIN can't
        # pass green while the braille silently changes.
        assert [c.reading for c in d.components] == ["nian2", "yue4", "ri4"]

    def test_year_only(self):
        out = _normalize_text("2026年")
        d = out[0]
        assert isinstance(d, Date)
        assert [(c.digits, c.marker) for c in d.components] == [("2026", "年")]

    def test_year_and_month(self):
        out = _normalize_text("2026年5月")
        d = out[0]
        assert isinstance(d, Date)
        assert [(c.digits, c.marker) for c in d.components] == [
            ("2026", "年"),
            ("5", "月"),
        ]

    def test_a_missing_month_does_not_swallow_the_day(self):
        """``2026年17日`` keeps its day: each optional marker is probed on
        its own, so the month failing to match must not end the scan."""
        d = _normalize_text("2026年17日")[0]
        assert isinstance(d, Date)
        assert [(c.digits, c.marker) for c in d.components] == [
            ("2026", "年"),
            ("17", "日"),
        ]

    def test_date_followed_by_hanzi_splits_correctly(self):
        # 日 is peeled off the trailing hanzi_text "日去了重庆"
        out = _normalize_text("2026年5月17日去了重庆")
        assert isinstance(out[0], Date)
        assert out[0].surface == "2026年5月17日"
        # Trailing hanzi remains as a Segment for ChineseAnalyzer.
        assert isinstance(out[1], Segment)
        assert out[1].type == "hanzi_text"
        assert out[1].surface == "去了重庆"

    def test_date_with_leading_hanzi(self):
        out = _normalize_text("今天是2026年5月17日。")
        # [Segment hanzi "今天是"], [Date "2026年5月17日"], [Punct "。"]
        assert isinstance(out[0], Segment) and out[0].surface == "今天是"
        assert isinstance(out[1], Date)
        assert isinstance(out[2], Punct)

    def test_year_marker_alone_is_not_date(self):
        # "年" without leading digits stays as hanzi_text.
        out = _normalize_text("年终")
        assert isinstance(out[0], Segment)
        assert out[0].surface == "年终"


# ---------------------------------------------------------------------------
# Percentages — not a composite
# ---------------------------------------------------------------------------


class TestPercentageIsNotAComposite:
    """``12%`` is a :class:`Number` beside a :class:`Punct`.

    It had a node type of its own, and the one thing that node decided was
    the blank between a percentage and the word after it. That blank now
    comes from ``%``'s own ``space_after`` in the punctuation table — where
    the sign's cells already lived — so there is nothing left for a composite
    to hold. Same shape as ``3.5kg`` above it.
    """

    def test_a_percentage_is_a_number_and_a_punct(self):
        out = _normalize_text("12%")
        assert [type(n).__name__ for n in out] == ["Number", "Punct"]
        assert out[0].surface == "12"
        assert out[1].surface == "%"

    def test_the_fullwidth_sign_reads_the_same_way(self):
        out = _normalize_text("12％")
        assert [type(n).__name__ for n in out] == ["Number", "Punct"]
        assert out[1].surface == "％"

    def test_a_decimal_percentage_keeps_its_number_whole(self):
        out = _normalize_text("3.5%")
        assert isinstance(out[0], Number)
        assert out[0].surface == "3.5"


class TestEmDash:
    """The Chinese em-dash 「——」(two consecutive em-dashes) merges into one
    Punct(surface="——"); a single 「—」(English em-dash) does not merge. The
    backend looks up the surface in the punctuation table to get ⠠⠤ / ⠤."""

    def test_two_em_dashes_merge_into_one_punct(self):
        out = _normalize_text("——")
        assert len(out) == 1
        assert isinstance(out[0], Punct)
        assert out[0].surface == "——"
        assert out[0].span == Span(0, 2)

    def test_single_em_dash_stays_single_punct(self):
        out = _normalize_text("—")
        assert len(out) == 1
        assert isinstance(out[0], Punct)
        assert out[0].surface == "—"

    def test_em_dash_pair_in_context(self):
        # 他——你: the em-dash pair merges, and the hanzi on each side form their
        # own segments.
        out = _normalize_text("他——你")
        puncts = [n for n in out if isinstance(n, Punct)]
        assert len(puncts) == 1
        assert puncts[0].surface == "——"

    def test_three_em_dashes_pair_then_single(self):
        # 「———」→ one 「——」(em-dash) + one 「—」(English em-dash).
        out = _normalize_text("———")
        puncts = [n for n in out if isinstance(n, Punct)]
        assert [p.surface for p in puncts] == ["——", "—"]


class TestUnknownSegment:
    def test_non_printable_char_becomes_unknown_node(self):
        # NULL byte is not printable → Segmenter labels it "unknown" →
        # Normalizer converts to Unknown inline node.
        out = _normalize_text("\x00")
        assert len(out) == 1
        assert isinstance(out[0], Unknown)


class TestPeelMarkerHelper:
    def test_returns_false_when_index_out_of_range(self):
        segs: list[Segment] = []
        assert _peel_marker_if_starts_with(segs, 0, "年") is False

    def test_returns_false_for_non_hanzi_segment(self):
        segs = [Segment(type="punct", surface="，", span=Span(0, 1))]
        assert _peel_marker_if_starts_with(segs, 0, "年") is False

    def test_returns_false_when_span_is_none(self):
        # Defensive branch — segments produced by DefaultSegmenter
        # always carry spans, but the helper must not crash if a
        # hand-built segment has span=None.
        segs = [Segment(type="hanzi_text", surface="年终", span=None)]
        assert _peel_marker_if_starts_with(segs, 0, "年") is False


# ---------------------------------------------------------------------------
# End-to-end paragraph
# ---------------------------------------------------------------------------


class TestParagraph:
    def test_complete_sentence(self):
        out = _normalize_text("我在2026年5月17日去了重庆银行。")
        # Expected: [Seg "我在"], [Date], [Seg "去了重庆银行"], [Punct "。"]
        types = [type(x).__name__ for x in out]
        assert types == ["Segment", "Date", "Segment", "Punct"]
        assert out[0].surface == "我在"
        assert out[1].surface == "2026年5月17日"
        assert out[2].surface == "去了重庆银行"
        assert out[3].surface == "。"

    def test_round_trip_surface(self):
        text = "我在2026年5月17日去了重庆银行。3.5kg大米和12%糖。"
        out = _normalize_text(text)
        rebuilt = "".join(item.surface for item in out)
        assert rebuilt == text

    def test_mixed_with_math(self):
        text = "见 计算 $a+b$ 后"
        out = _normalize_text(text)
        types = [type(x).__name__ for x in out]
        # Segmenter yields: [hanzi "见"][space " "][hanzi "计算"][space " "][math]...
        # Normalizer: hanzi → Segment, space → Space, math → MathInline
        assert "MathInline" in types
        assert "Space" in types
        assert "".join(item.surface for item in out) == text


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_default_registered(self):
        assert normalizer_registry.has("default")
        inst = normalizer_registry.get("default")
        assert inst.name == "default"

    def test_registry_lookup_produces_working_normalizer(self):
        norm = normalizer_registry.get("default")
        block = Paragraph(text="2026年")
        segs = DefaultSegmenter().segment(block, FrontendContext(profile="cn_current"))
        out = norm.normalize(segs, FrontendContext(profile="cn_current"))
        assert isinstance(out[0], Date)
