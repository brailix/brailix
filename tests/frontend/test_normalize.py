
from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.normalize import (
    DefaultNormalizer,
    _peel_marker_if_starts_with,
    normalizer_registry,
)
from brailix.frontend.segment import DefaultSegmenter
from brailix.ir.document import Paragraph
from brailix.ir.inline import (
    Date,
    HanziMarker,
    LatinAcronym,
    LatinWord,
    MathInline,
    Number,
    Percent,
    Punct,
    Quantity,
    Segment,
    Space,
    Unknown,
)


def _normalize_text(text: str):
    block = Paragraph(text=text)
    segs = DefaultSegmenter().segment(block, FrontendContext())
    return DefaultNormalizer().normalize(segs, FrontendContext())


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
        # When Pipeline._attach_math sees math already filled, it skips the math
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

    def test_latin_acronym(self):
        out = _normalize_text("CPU")
        assert isinstance(out[0], LatinAcronym)

    def test_latin_single_uppercase_is_word_not_acronym(self):
        out = _normalize_text("A")
        # Single uppercase letter is a word, not an acronym.
        assert isinstance(out[0], LatinWord)

    def test_greek_lowercase_letter_becomes_latin_word(self):
        # τ takes the same IR path as Latin letters: the backend's translate_latin
        # automatically adds the Greek lowercase sign ⠨ via profile.letter().
        out = _normalize_text("τ")
        assert len(out) == 1
        assert isinstance(out[0], LatinWord)
        assert out[0].surface == "τ"

    def test_greek_uppercase_run_becomes_acronym(self):
        out = _normalize_text("ΑΒΓ")
        assert isinstance(out[0], LatinAcronym)
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
        # parts: year, 年, month, 月, day, 日
        assert len(d.parts) == 6
        assert isinstance(d.parts[0], Number) and d.parts[0].role == "year"
        assert isinstance(d.parts[1], HanziMarker) and d.parts[1].surface == "年"
        assert d.parts[2].role == "month"
        assert d.parts[4].role == "day"
        # ARCHITECTURE §12: structural-marker readings are filled by the
        # normalizer (fixed 年→nián etc.), NOT the PinyinResolver — guard
        # that observable result so a deleted/renamed _MARKER_PINYIN can't
        # pass green while the braille silently changes.
        assert d.parts[1].reading == "nian2"
        assert d.parts[3].reading == "yue4"
        assert d.parts[5].reading == "ri4"

    def test_year_only(self):
        out = _normalize_text("2026年")
        d = out[0]
        assert isinstance(d, Date)
        assert len(d.parts) == 2
        assert d.parts[0].role == "year"
        assert d.parts[1].surface == "年"

    def test_year_and_month(self):
        out = _normalize_text("2026年5月")
        d = out[0]
        assert isinstance(d, Date)
        assert len(d.parts) == 4
        assert d.parts[2].role == "month"

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
# Percent
# ---------------------------------------------------------------------------


class TestPercent:
    def test_basic(self):
        out = _normalize_text("12%")
        p = out[0]
        assert isinstance(p, Percent)
        assert p.surface == "12%"
        assert p.number.surface == "12"

    def test_fullwidth_percent(self):
        out = _normalize_text("12％")
        assert isinstance(out[0], Percent)

    def test_decimal_percent(self):
        out = _normalize_text("3.5%")
        assert isinstance(out[0], Percent)
        assert out[0].number.surface == "3.5"


# ---------------------------------------------------------------------------
# Quantity
# ---------------------------------------------------------------------------


class TestQuantity:
    def test_basic(self):
        out = _normalize_text("3.5kg")
        q = out[0]
        assert isinstance(q, Quantity)
        assert q.surface == "3.5kg"
        assert q.number.surface == "3.5"
        assert q.unit == "kg"
        assert q.unit_canonical == "kilogram"

    def test_case_insensitive_unit(self):
        out = _normalize_text("3GHz")
        q = out[0]
        assert isinstance(q, Quantity)
        assert q.unit_canonical == "gigahertz"

    def test_unknown_unit_falls_back(self):
        # "100foo" — foo isn't a unit, should split as Number + LatinWord
        out = _normalize_text("100foo")
        assert isinstance(out[0], Number)
        assert isinstance(out[1], LatinWord)

    def test_digits_followed_by_non_latin_is_not_quantity(self):
        # Hits _try_quantity's early-out: digit_run + non-latin next.
        # "12 foo" — digit + space + latin → digit not absorbed.
        out = _normalize_text("12 foo")
        assert isinstance(out[0], Number)
        assert isinstance(out[1], Space)
        assert isinstance(out[2], LatinWord)


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
        segs = DefaultSegmenter().segment(block, FrontendContext())
        out = norm.normalize(segs, FrontendContext())
        assert isinstance(out[0], Date)
