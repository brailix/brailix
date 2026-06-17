"""End-to-end test: text → Unicode braille string.

Exercises the full stack through the default Pipeline. Chinese pinyin
is now filled by the ``auto`` resolver when ``pypinyin`` is installed;
the hand-annotated cases below still demonstrate the backend without
depending on any frontend resolver.
"""

from __future__ import annotations

from brailix import Pipeline
from brailix.backend.block import translate_document
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Heading, Paragraph
from brailix.ir.inline import (
    Date,
    HanziChar,
    HanziMarker,
    Number,
    Punct,
    Word,
)
from brailix.renderer.unicode_braille import UnicodeBrailleRenderer

# ---------------------------------------------------------------------------
# Plain-pipeline smoke
# ---------------------------------------------------------------------------


class TestPipelineSmoke:
    def test_pure_punct_through_pipeline(self):
        """Punct survives the full pipeline as Unicode braille."""
        pipe = Pipeline()
        result = pipe.translate_text("，。！？")
        # ， → 1 cell + space (2);
        # 。 → 2 cells, no space (2);
        # ！ → 2 cells, no space (2);
        # ？ → 2 cells, no space (2).  Total: 8.
        assert len(result.render()) == 8
        # No warnings — every char is mapped.
        codes = {w.code for w in result.warnings}
        assert "UNKNOWN_PUNCT" not in codes

    def test_number_through_pipeline(self):
        pipe = Pipeline()
        result = pipe.translate_text("2026")
        # number_sign + 4 digits
        assert len(result.render()) == 5

    def test_greek_letter_does_not_emit_unknown_punct(self):
        # Regression: the Greek letter τ didn't go through the
        # letter table and ended up in the punct fallback, triggering
        # UNKNOWN_PUNCT. After the fix it should go through the Latin/Greek
        # letter path, emitting the Greek lowercase sign ⠨ + the τ cell ⠞,
        # and produce no UNKNOWN_PUNCT warning.
        pipe = Pipeline()
        result = pipe.translate_text("τ")
        codes = {w.code for w in result.warnings}
        assert "UNKNOWN_PUNCT" not in codes
        rendered = result.render()
        # ⠨ = U+2828 (dots 4,6); ⠞ = U+281E (dots 2,3,4,5).
        assert rendered == "⠨⠞"

    def test_round_trip_to_proofread_json(self):
        pipe = Pipeline()
        result = pipe.translate_text("。")
        payload = result.proofread_json()
        assert payload["text"] == "。"
        # 。 = ⠐⠆ — two cells.
        cells = payload["braille_ir"]["blocks"][0]["cells"]
        assert [c["dots"] for c in cells] == [[5], [2, 3]]


class TestLetterHanziConnector:
    """For letter+hanzi compound words run through the full pipeline, the
    letter↔hanzi seam emits a connector ⠤ rather than a blank cell;
    non-compounds still get a blank cell. The assertions go through the
    cell role/dots of proofread_json and do not depend on whether a pinyin
    resolver is installed (the connector is independent of pinyin)."""

    def _cells(self, text: str):
        result = Pipeline().translate_text(text)
        return result.proofread_json()["braille_ir"]["blocks"][0]["cells"]

    def test_x_axis_emits_connector_cell(self):
        cells = self._cells("x轴")
        connectors = [c for c in cells if c.get("role") == "connector"]
        assert len(connectors) == 1
        # connector ⠤ = dots 3-6.
        assert connectors[0]["dots"] == [3, 6]

    def test_t_shirt_emits_connector_cell(self):
        cells = self._cells("T恤")
        assert any(c.get("role") == "connector" for c in cells)

    def test_non_compound_has_no_connector(self):
        # 已知α is two words → blank cell, no connector cell should appear.
        cells = self._cells("已知α")
        assert not any(c.get("role") == "connector" for c in cells)

    def test_date_year_has_no_connector(self):
        # Date 2026年: the year is the sole exception to the number→hanzi
        # connector — the year marker is written connected directly, with
        # neither a connector nor a blank cell (month/day do take it, see
        # test_number.TestTranslateDate).
        cells = self._cells("2026年")
        assert not any(c.get("role") == "connector" for c in cells)

    def test_number_then_hanzi_emits_connector(self):
        # 10页: a number directly followed by a hanzi → connector ⠤ (the
        # leading cell of 页 is ⠑=5; without a separator it would read as
        # 105).
        cells = self._cells("10页")
        connectors = [c for c in cells if c.get("role") == "connector"]
        assert len(connectors) == 1
        assert connectors[0]["dots"] == [3, 6]

    def test_hanzi_then_number_has_no_connector(self):
        # The reverse, 第3: the number sign provides its own separation, so
        # no connector is emitted.
        cells = self._cells("第3")
        assert not any(c.get("role") == "connector" for c in cells)


class TestEmDashPipeline:
    """Full pipeline: the Chinese em dash 「——」 produces two cells ⠠⠤; a
    single 「—」 produces one cell ⠤."""

    def _punct_cells(self, text: str):
        cells = Pipeline().translate_text(text).proofread_json()["braille_ir"]["blocks"][0]["cells"]
        return [c for c in cells if c.get("role") == "punct"]

    def test_chinese_dash_pair_is_two_cells(self):
        # 他说——你好: 「——」 → ⠠⠤ (6 + 36), two cells, not four.
        puncts = self._punct_cells("他说——你好")
        assert [p["dots"] for p in puncts] == [[6], [3, 6]]

    def test_single_em_dash_is_one_cell(self):
        # A single 「—」 → the one-cell English dash ⠤ (36).
        puncts = self._punct_cells("甲—乙")
        assert [p["dots"] for p in puncts] == [[3, 6]]


# ---------------------------------------------------------------------------
# Hand-annotated end-to-end through the dispatcher (skip frontend pinyin)
# ---------------------------------------------------------------------------


class TestHandAnnotatedDocument:
    """When pinyin is supplied directly, the backend can produce a
    complete braille output without relying on a heavyweight
    PinyinResolver. This is what the architecture promises: Backend
    decisions are independent of which Frontend adapter ran."""

    def test_canonical_sentence(self):
        profile = load_profile("cn_current")
        ctx = BackendContext()

        # 我 在 2026 年 5 月 17 日 去 了 重庆 银行 。
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="我", reading="wo3", span=Span(0, 1)),
            HanziChar(surface="在", reading="zai4", span=Span(1, 2)),
            Date(
                surface="2026年5月17日",
                span=Span(2, 12),
                parts=[
                    Number(surface="2026", role="year", span=Span(2, 6)),
                    HanziMarker(surface="年", span=Span(6, 7), reading="nian2"),
                    Number(surface="5", role="month", span=Span(7, 8)),
                    HanziMarker(surface="月", span=Span(8, 9), reading="yue4"),
                    Number(surface="17", role="day", span=Span(9, 11)),
                    HanziMarker(surface="日", span=Span(11, 12), reading="ri4"),
                ],
            ),
            HanziChar(surface="去", reading="qu4", span=Span(12, 13)),
            HanziChar(surface="了", reading="le5", span=Span(13, 14)),
            Word(surface="重庆", reading="chong2 qing4", span=Span(14, 16)),
            Word(surface="银行", reading="yin2 hang2", span=Span(16, 18)),
            Punct(surface="。", span=Span(18, 19)),
        ])])

        braille_doc = translate_document(doc, ctx, profile)
        rendered = UnicodeBrailleRenderer().render(braille_doc)

        # Every emitted char must be in the braille block.
        for ch in rendered:
            cp = ord(ch)
            assert 0x2800 <= cp <= 0x28FF or ch == "\n"

        # No warnings — every syllable/digit/marker has a mapping.
        unmapped = [
            w for w in ctx.warnings
            if w.code in {"MISSING_PINYIN", "BAD_PINYIN", "MISSING_INITIAL",
                          "MISSING_FINAL", "UNKNOWN_PUNCT", "UNKNOWN_DIGIT",
                          "PINYIN_LENGTH_MISMATCH"}
        ]
        assert unmapped == []

        # Expected cell count:
        # 我 (wo3):     final + tone                 = 2
        # 在 (zai4):    init + final + tone          = 3
        # Date:         3 number_signs + 7 digits + 年 (3) + space (1) +
        #               月 (2) + connector (1) + space (1) + 日 (2) +
        #               connector (1) = 21
        #               (the year/month/day components are space-separated:
        #               2026年 5月 17日. Within a component the number
        #               attaches to its marker — 年 directly, 月/日 via the
        #               number→hanzi connector ⠤.)
        # 去 (qu4):     init + final + tone          = 3
        # 了 (le5):     init + final                 = 2 (neutral tone suppressed)
        # 重庆 (chong2 qing4):                       = 6
        # 银 (yin2):    final + tone                 = 2
        # 行 (hang2):   init + final + tone          = 3
        # 。 (⠐⠆):                                    = 2 cells, no blank
        # Total: 2 + 3 + 21 + 3 + 2 + 6 + 2 + 3 + 2 = 44
        assert len(rendered) == 44


# ---------------------------------------------------------------------------
# Heading + multiple blocks
# ---------------------------------------------------------------------------


class TestMultipleBlocks:
    def test_heading_and_paragraph_joined_by_newline(self):
        profile = load_profile("cn_current")
        ctx = BackendContext()
        doc = DocumentIR(blocks=[
            Heading(level=1, children=[HanziChar(surface="一", reading="yi1")]),
            Paragraph(children=[HanziChar(surface="文", reading="wen2")]),
        ])
        rendered = UnicodeBrailleRenderer().render(
            translate_document(doc, ctx, profile)
        )
        assert "\n" in rendered


# ---------------------------------------------------------------------------
# Provenance round-trip
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_every_braille_cell_traces_back_to_source(self):
        profile = load_profile("cn_current")
        ctx = BackendContext()
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="我", reading="wo3", span=Span(0, 1)),
            HanziChar(surface="在", reading="zai4", span=Span(1, 2)),
            Punct(surface="。", span=Span(2, 3)),
        ])])
        bd = translate_document(doc, ctx, profile)
        cells = bd.all_cells()
        for c in cells:
            # Every cell either has a source span pointing into the
            # original text, OR is a backend-inserted helper (number
            # sign, capital indicator, trailing blank after punctuation).
            if c.role not in ("number_sign", "capital_sign", "math_start", "space"):
                assert c.source_span is not None
                assert c.source_span.start < c.source_span.end

    def test_date_connector_carries_zero_length_boundary_span(self):
        # A Date emits a ``connector`` cell with an intentional zero-length
        # boundary span (anti-collision between the year digits and 日).
        # The strict ``start < end`` invariant above doesn't cover it
        # because the other provenance test has no Date input.
        from brailix import Pipeline

        result = Pipeline(profile="cn_current").translate_text("2026年5月17日")
        cells = result.braille_ir.all_cells()
        connectors = [c for c in cells if c.role == "connector"]
        assert connectors, "expected a connector cell between date parts"
        for c in connectors:
            assert c.source_span is not None
            assert c.source_span.start == c.source_span.end  # zero-length
        # Every other content cell still carries a real (non-empty) span.
        for c in cells:
            if c.role in (
                "number_sign", "capital_sign", "math_start", "space", "connector",
            ):
                continue
            assert c.source_span is not None
            assert c.source_span.start < c.source_span.end


# ---------------------------------------------------------------------------
# Profile swap doesn't crash
# ---------------------------------------------------------------------------


class TestProfileFeature:
    def test_disabling_tone_shortens_output(self):
        """When tone is suppressed, the same content yields fewer cells."""
        profile = load_profile("cn_current")
        ctx = BackendContext()
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="在", reading="zai4", span=Span(0, 1)),
        ])])

        rendered_with_tone = UnicodeBrailleRenderer().render(
            translate_document(doc, ctx, profile)
        )

        profile.features["tone"] = False
        try:
            ctx2 = BackendContext()
            rendered_no_tone = UnicodeBrailleRenderer().render(
                translate_document(doc, ctx2, profile)
            )
        finally:
            profile.features["tone"] = True

        assert len(rendered_no_tone) < len(rendered_with_tone)
