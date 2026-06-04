import pytest

from brailix.backend.number import (
    translate_date,
    translate_number,
    translate_percent,
    translate_quantity,
)
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import Date, HanziMarker, Number, Percent, Quantity


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext()


class TestTranslateNumber:
    def test_simple(self, ctx, profile):
        cells = translate_number(Number(surface="123", span=Span(0, 3)), ctx, profile)
        # number_sign + 3 digits
        assert len(cells) == 4
        assert cells[0].role == "number_sign"
        assert cells[0].dots == profile.number_sign
        assert cells[1].role == "digit"
        assert cells[1].dots == profile.digits["1"]
        assert cells[2].dots == profile.digits["2"]
        assert cells[3].dots == profile.digits["3"]

    def test_zero(self, ctx, profile):
        cells = translate_number(Number(surface="0"), ctx, profile)
        assert cells[1].dots == profile.digits["0"]

    def test_empty_number_emits_nothing(self, ctx, profile):
        cells = translate_number(Number(surface=""), ctx, profile)
        assert cells == []

    def test_decimal(self, ctx, profile):
        cells = translate_number(Number(surface="3.5"), ctx, profile)
        # number_sign + 3 + decimal_point + 5
        assert len(cells) == 4
        assert cells[2].role == "decimal_point"

    def test_thousands(self, ctx, profile):
        cells = translate_number(Number(surface="1,234"), ctx, profile)
        # number_sign + 1 + comma + 2 + 3 + 4
        assert len(cells) == 6
        assert cells[2].role == "thousands_sep"

    def test_source_text_preserved(self, ctx, profile):
        cells = translate_number(Number(surface="42", span=Span(5, 7)), ctx, profile)
        digit_cells = [c for c in cells if c.role == "digit"]
        assert [c.source_text for c in digit_cells] == ["4", "2"]
        assert [c.source_span for c in digit_cells] == [Span(5, 6), Span(6, 7)]

    def test_number_sign_disabled_via_profile(self, ctx, profile):
        # Mutate a copy of the profile by directly changing its features.
        profile.features["number_sign"] = False
        try:
            cells = translate_number(Number(surface="9"), ctx, profile)
            assert len(cells) == 1
            assert cells[0].role == "digit"
        finally:
            profile.features["number_sign"] = True

    def test_unknown_digit_emits_warning(self, ctx, profile):
        # Superscript 2 is a digit char, but not a decimal digit.
        cells = translate_number(Number(surface="²"), ctx, profile)
        warnings = list(ctx.warnings)
        assert any(w.code == "UNKNOWN_DIGIT" for w in warnings)
        # Still produced an unknown cell
        unknowns = [c for c in cells if c.role == "unknown"]
        assert len(unknowns) == 1

    def test_fullwidth_digits_use_ascii_digit_table(self, ctx, profile):
        cells = translate_number(
            Number(surface="１２３", span=Span(10, 13)), ctx, profile
        )
        digit_cells = [c for c in cells if c.role == "digit"]
        assert [c.dots for c in digit_cells] == [
            profile.digits["1"],
            profile.digits["2"],
            profile.digits["3"],
        ]
        assert [c.source_text for c in digit_cells] == ["１", "２", "３"]
        assert not any(w.code == "UNKNOWN_DIGIT" for w in ctx.warnings)


class TestTranslatePercent:
    def test_basic(self, ctx, profile):
        node = Percent(
            surface="12%",
            span=Span(0, 3),
            number=Number(surface="12", span=Span(0, 2)),
        )
        cells = translate_percent(node, ctx, profile)
        # number_sign + 1 + 2 + percent punct  → 4 cells
        assert len(cells) == 4
        assert cells[-1].role == "punct"
        assert cells[-1].source_text == "%"

    def test_fullwidth_digits_in_percent(self, ctx, profile):
        node = Percent(
            surface="１２％",
            span=Span(0, 3),
            number=Number(surface="１２", span=Span(0, 2)),
        )
        cells = translate_percent(node, ctx, profile)
        digit_cells = [c for c in cells if c.role == "digit"]
        assert [c.dots for c in digit_cells] == [
            profile.digits["1"],
            profile.digits["2"],
        ]
        assert cells[-1].role == "punct"
        assert cells[-1].source_text == "％"
        assert not any(w.code == "UNKNOWN_DIGIT" for w in ctx.warnings)


class TestTranslateQuantity:
    def test_kg(self, ctx, profile):
        node = Quantity(
            surface="3kg",
            span=Span(0, 3),
            number=Number(surface="3", span=Span(0, 1)),
            unit="kg",
            unit_canonical="kilogram",
        )
        cells = translate_quantity(node, ctx, profile)
        # number_sign + digit 3 + (56 + k) + (56 + g) = 6 cells.
        assert cells[0].role == "number_sign"
        assert cells[1].role == "digit"
        unit_cells = [c for c in cells if c.role == "quantity_unit"]
        assert len(unit_cells) == 4  # latin_lower_prefix + letter, twice
        assert [c.source_text for c in unit_cells] == ["k", "k", "g", "g"]
        # Unit lookup hits math_identifiers → no UNKNOWN_NUMBER_PART warnings.
        assert not any(w.code == "UNKNOWN_NUMBER_PART" for w in ctx.warnings)

    def test_unit_falls_back_to_unknown_when_table_misses(self, ctx, profile):
        # An exotic unit char absent from both math_identifiers and
        # punctuation should warn and emit one unknown cell.
        node = Quantity(
            surface="3·",
            span=Span(0, 2),
            number=Number(surface="3", span=Span(0, 1)),
            unit="☃",  # snowman — not in any table
            unit_canonical=None,
        )
        cells = translate_quantity(node, ctx, profile)
        assert cells[-1].role == "unknown"
        assert any(w.code == "UNKNOWN_NUMBER_PART" for w in ctx.warnings)

    def test_unit_falls_back_to_punctuation_table(self, ctx, profile):
        # A unit char that's NOT in the math identifier/letter table
        # but IS in the punctuation table should be emitted as a
        # "punct"-role cell (no warning). This exercises the
        # punctuation-fallback arm of ``_unit_char_cells``.
        # ":" is in cn_current's punctuation table but not in any
        # letter / math-identifier table.
        node = Quantity(
            surface="3:",
            span=Span(0, 2),
            number=Number(surface="3", span=Span(0, 1)),
            unit=":",  # punctuation char (cn_current ⠒)
            unit_canonical=None,
        )
        cells = translate_quantity(node, ctx, profile)
        punct_cells = [c for c in cells if c.role == "punct"]
        assert punct_cells, "expected at least one punct-role cell"
        # The cell carries the source char as its source_text.
        assert punct_cells[0].source_text == ":"
        # No warning — the punctuation table hit cleanly.
        assert not any(w.code == "UNKNOWN_NUMBER_PART" for w in ctx.warnings)


class TestTranslatePercentPunctMiss:
    """``_punct_cells`` returns ``[]`` when the trailing char is absent
    from the punctuation table. Hit that path via a deliberately broken
    Percent surface."""

    def test_percent_with_unmapped_trailing_char_emits_no_punct_cell(
        self, ctx, profile
    ):
        # Surface ends in a char (`§`) that has no punctuation mapping
        # in cn_current. The percent helper still emits the digit
        # cells but ``_punct_cells`` returns an empty list — no punct
        # cell is appended.
        node = Percent(
            surface="5§",
            span=Span(0, 2),
            number=Number(surface="5", span=Span(0, 1)),
        )
        cells = translate_percent(node, ctx, profile)
        # Digits are still rendered (number_sign + "5").
        assert any(c.role == "number_sign" for c in cells)
        assert any(c.role == "digit" for c in cells)
        # But no punct cell because § isn't mapped.
        assert all(c.role != "punct" for c in cells)


class TestMissingNumberPart:
    """``_digit_run_cells`` falls back to an ``unknown`` cell + warning
    when the profile has no mapping for ``decimal_point`` or
    ``thousands_sep``. The shipped ``cn_current`` profile always maps
    them, so we strip the mapping at runtime to hit the path."""

    def test_missing_decimal_point_warns_and_emits_unknown(self, ctx, profile):
        original = profile.decimal_point
        profile.decimal_point = ()
        try:
            cells = translate_number(Number(surface="3.5", span=Span(0, 3)), ctx, profile)
        finally:
            profile.decimal_point = original

        # number_sign + digit "3" + unknown for "." + digit "5"
        unknowns = [c for c in cells if c.role == "unknown"]
        assert len(unknowns) == 1
        assert unknowns[0].source_text == "."
        codes = [w.code for w in ctx.warnings]
        assert "MISSING_NUMBER_PART" in codes

    def test_missing_thousands_sep_warns_and_emits_unknown(self, ctx, profile):
        original = profile.thousands_sep
        profile.thousands_sep = ()
        try:
            cells = translate_number(Number(surface="1,000", span=Span(0, 5)), ctx, profile)
        finally:
            profile.thousands_sep = original

        unknowns = [c for c in cells if c.role == "unknown"]
        assert len(unknowns) == 1
        assert unknowns[0].source_text == ","


class TestTranslateDate:
    def test_full_date(self, ctx, profile):
        node = Date(
            surface="2026年5月17日",
            span=Span(0, 10),
            parts=[
                Number(surface="2026", role="year", span=Span(0, 4)),
                HanziMarker(surface="年", span=Span(4, 5)),
                Number(surface="5", role="month", span=Span(5, 6)),
                HanziMarker(surface="月", span=Span(6, 7)),
                Number(surface="17", role="day", span=Span(7, 9)),
                HanziMarker(surface="日", span=Span(9, 10)),
            ],
        )
        cells = translate_date(node, ctx, profile)
        # 3 number_sign + (4+1+2) digits + 3 marker syllables. 月/日 each
        # take a connector (digit-to-hanzi joiner); 年 is the lone exception
        # → 2 connector cells, before 月 and 日 only.
        num_signs = [c for c in cells if c.role == "number_sign"]
        digits = [c for c in cells if c.role == "digit"]
        connectors = [c for c in cells if c.role == "connector"]
        assert len(num_signs) == 3
        assert len(digits) == 7  # 4 + 1 + 2
        assert len(connectors) == 2  # before 月 and 日, not 年
        assert all(c.dots == profile.connector for c in connectors)

    def test_year_only(self, ctx, profile):
        # Frontend Normalizer is responsible for filling in pinyin on
        # Date markers (see frontend/normalize._marker). The Backend
        # itself is language-agnostic and only translates what the IR
        # already carries, so this test mirrors what the Normalizer
        # would produce.
        node = Date(
            surface="2026年",
            span=Span(0, 5),
            parts=[
                Number(surface="2026", role="year", span=Span(0, 4)),
                HanziMarker(surface="年", span=Span(4, 5), reading="nian2"),
            ],
        )
        cells = translate_date(node, ctx, profile)
        # number_sign + 4 digits + 年 (zh syllable: initial + final + tone)
        assert cells[0].role == "number_sign"
        digits = [c for c in cells if c.role == "digit"]
        assert len(digits) == 4
        assert any(c.role == "zh_initial" for c in cells)
        assert any(c.role == "zh_final" for c in cells)
        assert not any(c.role == "unknown" for c in cells)
        # 年 is the exception — no connector between the year digits and 年.
        assert not any(c.role == "connector" for c in cells)

    def test_month_marker_gets_connector(self, ctx, profile):
        # 月 (unlike 年) takes the digit-to-hanzi connector, even though
        # 月's first cell ⠾ doesn't itself collide with a digit — 年 is the
        # only exception.
        node = Date(
            surface="5月",
            span=Span(0, 2),
            parts=[
                Number(surface="5", role="month", span=Span(0, 1)),
                HanziMarker(surface="月", span=Span(1, 2), reading="yue4"),
            ],
        )
        cells = translate_date(node, ctx, profile)
        connectors = [c for c in cells if c.role == "connector"]
        assert len(connectors) == 1
        assert connectors[0].dots == profile.connector

    def test_marker_without_pinyin_falls_back(self, ctx, profile):
        # If the frontend left a marker without pinyin (e.g. an
        # exotic char the Normalizer doesn't recognise as a date
        # part), backend/zh emits MISSING_PINYIN and an unknown cell.
        # The Backend never guesses readings — that's the frontend's
        # job (see ARCHITECTURE §12).
        node = Date(
            surface="3旬",
            span=Span(0, 2),
            parts=[
                Number(surface="3", role="year", span=Span(0, 1)),
                HanziMarker(surface="旬", span=Span(1, 2)),  # no pinyin
            ],
        )
        cells = translate_date(node, ctx, profile)
        assert cells[-1].role == "unknown"
        assert any(w.code == "MISSING_PINYIN" for w in ctx.warnings)

    def test_marker_uses_explicit_pinyin_when_provided(self, ctx, profile):
        # Backend is intentionally dumb about language knowledge —
        # whatever pinyin the frontend attached to the marker is what
        # gets translated. If the frontend left pinyin empty, the
        # backend emits the zh layer's MISSING_PINYIN warning and an
        # unknown cell rather than guessing.
        node = Date(
            surface="3旬",
            span=Span(0, 2),
            parts=[
                Number(surface="3", role="year", span=Span(0, 1)),
                HanziMarker(surface="旬", span=Span(1, 2), reading="xun2"),
            ],
        )
        cells = translate_date(node, ctx, profile)
        assert not any(c.role == "unknown" for c in cells)
        assert any(c.role == "zh_initial" for c in cells)
