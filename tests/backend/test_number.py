import pytest

from brailix.backend.number import translate_date, translate_number
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import Date, DateComponent, Number


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


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

    def test_number_sign_disabled_via_profile(self, ctx, profile, monkeypatch):
        # The flag by the name the profile writes it under — ``zh.number_sign``,
        # the Chinese prose one, not ``math.number_sign``.
        monkeypatch.setitem(profile.features["zh"], "number_sign", False)
        cells = translate_number(Number(surface="9"), ctx, profile)
        assert len(cells) == 1
        assert cells[0].role == "digit"

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
            components=[
                DateComponent(
                    digits="2026",
                    digits_span=Span(0, 4),
                    marker="年",
                    marker_span=Span(4, 5),
                ),
                DateComponent(
                    digits="5",
                    digits_span=Span(5, 6),
                    marker="月",
                    marker_span=Span(6, 7),
                ),
                DateComponent(
                    digits="17",
                    digits_span=Span(7, 9),
                    marker="日",
                    marker_span=Span(9, 10),
                ),
            ],
        )
        cells = translate_date(node, ctx, profile)
        # 3 number_sign + (4+1+2) digits + 3 marker syllables. 月/日 each
        # take a connector (digit-to-hanzi joiner) within their component;
        # 年 is the lone exception → 2 connector cells, before 月 and 日 only.
        num_signs = [c for c in cells if c.role == "number_sign"]
        digits = [c for c in cells if c.role == "digit"]
        connectors = [c for c in cells if c.role == "connector"]
        assert len(num_signs) == 3
        assert len(digits) == 7  # 4 + 1 + 2
        assert len(connectors) == 2  # before 月 and 日, not 年
        assert all(c.dots == profile.connector for c in connectors)
        # The three components (2026年 / 5月 / 17日) are space-separated: a
        # word-boundary blank precedes the 2nd and 3rd components' numbers.
        spaces = [c for c in cells if c.role == "space"]
        assert len(spaces) == 2
        sign_idx = [i for i, c in enumerate(cells) if c.role == "number_sign"]
        for i in sign_idx[1:]:  # 5 and 17 each follow a component space
            assert cells[i - 1].role == "space"

    def test_year_only(self, ctx, profile):
        # Frontend Normalizer is responsible for filling in pinyin on
        # Date markers (see frontend/normalization._marker). The Backend
        # itself is language-agnostic and only translates what the IR
        # already carries, so this test mirrors what the Normalizer
        # would produce.
        node = Date(
            surface="2026年",
            span=Span(0, 5),
            components=[
                DateComponent(
                    digits="2026",
                    digits_span=Span(0, 4),
                    marker="年",
                    marker_span=Span(4, 5),
                    reading="nian2",
                ),
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
            components=[
                DateComponent(
                    digits="5",
                    digits_span=Span(0, 1),
                    marker="月",
                    marker_span=Span(1, 2),
                    reading="yue4",
                ),
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
        # job (see ARCHITECTURE#arch-boundaries).
        node = Date(
            surface="3旬",
            span=Span(0, 2),
            components=[
                DateComponent(
                    digits="3",
                    digits_span=Span(0, 1),
                    marker="旬",
                    marker_span=Span(1, 2),
                ),  # no pinyin
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
            components=[
                DateComponent(
                    digits="3",
                    digits_span=Span(0, 1),
                    marker="旬",
                    marker_span=Span(1, 2),
                    reading="xun2",
                ),
            ],
        )
        cells = translate_date(node, ctx, profile)
        assert not any(c.role == "unknown" for c in cells)
        assert any(c.role == "zh_initial" for c in cells)


class TestDateMarkerDecoupling:
    """Guard: the date-marker rule (年 connector exemption + marker reading)
    lives in the per-language ``LanguageBackend``, not in the
    language-neutral number backend (ARCHITECTURE#arch-language-slots / #arch-boundaries). Locks the
    decoupling so the Chinese rule can't drift back into number.py."""

    def test_number_module_has_no_chinese_date_rule(self):
        import inspect

        import brailix.backend.number as number_mod

        src = inspect.getsource(number_mod)
        assert "_DATE_CONNECTOR_EXEMPT" not in src
        assert "backend.zh" not in src
        assert "import zh" not in src

    def test_zh_backend_owns_connector_rule(self, ctx, profile):
        from brailix.backend.zh import translate_date_marker

        def component(marker: str, reading: str, digits: str) -> DateComponent:
            return DateComponent(
                digits=digits,
                digits_span=Span(0, len(digits)) if digits else None,
                marker=marker,
                marker_span=Span(len(digits), len(digits) + 1),
                reading=reading,
            )

        # 年 written against digits takes NO connector (NCB exemption)...
        assert not any(
            c.role == "connector"
            for c in translate_date_marker(
                component("年", "nian2", "2026"), ctx, profile
            )
        )
        # ...but 月 does.
        assert any(
            c.role == "connector"
            for c in translate_date_marker(
                component("月", "yue4", "5"), ctx, profile
            )
        )
        # A marker with no digits in front of it takes no connector either.
        assert not any(
            c.role == "connector"
            for c in translate_date_marker(
                component("月", "yue4", ""), ctx, profile
            )
        )

    def test_language_backend_protocol_declares_marker_method(self):
        from brailix.core.protocols import LanguageBackend

        assert hasattr(LanguageBackend, "translate_date_marker")
