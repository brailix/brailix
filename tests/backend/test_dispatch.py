import pytest

from brailix.backend.block import translate_block, translate_document
from brailix.backend.dispatch import translate_node
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Heading, Paragraph
from brailix.ir.inline import (
    CodeInline,
    Connector,
    HanziChar,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    Number,
    Percent,
    Punct,
    Quantity,
    Space,
    Unknown,
    Word,
)


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


class _SpanlessBackend:
    """A LanguageBackend that violates traceability: it returns the
    span-less :data:`BLANK_CELL` sentinel instead of span-carrying cells."""

    def translate_word(self, node, ctx, profile):
        from brailix.ir.braille import BLANK_CELL

        return [BLANK_CELL]

    def translate_hanzi_char(self, node, ctx, profile):
        from brailix.ir.braille import BLANK_CELL

        return [BLANK_CELL]

    def translate_date_marker(self, marker, follows_number, ctx, profile):
        return []


class TestTraceabilityContractAtDispatch:
    """Backend → dispatcher post-condition: a span-carrying node must come
    back as cells that all carry a ``source_span``. The
    ``language_backend_registry`` is the open plugin seam, so a
    non-conforming third-party backend is caught HERE — named in a
    :class:`BackendContractError` — instead of silently producing a
    proofreading JSON whose cells can't jump back to source."""

    def test_plugin_returning_blank_cell_raises(self, ctx, profile):
        from brailix.backend.dispatch import language_backend_registry
        from brailix.core.errors import BackendContractError

        with language_backend_registry.overriding("zh", _SpanlessBackend):
            with pytest.raises(BackendContractError) as exc_info:
                translate_node(
                    Word(surface="我", reading="wo3", span=Span(0, 1)),
                    ctx,
                    profile,
                )
        assert "language backend 'zh'" in str(exc_info.value)

    def test_pipeline_compile_fails_immediately(self):
        # End-to-end shape: a whole-document compile through Pipeline
        # identifies the plugin violation at once.
        from brailix import Pipeline
        from brailix.backend.dispatch import language_backend_registry
        from brailix.core.errors import BackendContractError

        pipe = Pipeline(profile="cn_current")
        with language_backend_registry.overriding("zh", _SpanlessBackend):
            with pytest.raises(BackendContractError):
                pipe.translate_text("你好")

    def test_lenient_mode_does_not_swallow_the_violation(self):
        # A contract error is a code defect, not bad input — no run mode
        # may downgrade it.
        from brailix import Pipeline
        from brailix.backend.dispatch import language_backend_registry
        from brailix.core.errors import BackendContractError

        pipe = Pipeline(profile="cn_current", mode="lenient")
        with language_backend_registry.overriding("zh", _SpanlessBackend):
            with pytest.raises(BackendContractError):
                pipe.translate_text("你好")

    def test_spanless_node_is_exempt(self, ctx, profile):
        # Hand-built IR without spans promises nothing — the same plugin
        # output is accepted for a node that carries no span.
        from brailix.backend.dispatch import language_backend_registry

        with language_backend_registry.overriding("zh", _SpanlessBackend):
            cells = translate_node(
                Word(surface="我", reading="wo3"), ctx, profile
            )
        assert len(cells) == 1 and cells[0].source_span is None


class TestDispatchPerNodeType:
    def test_word(self, ctx, profile):
        cells = translate_node(Word(surface="我", reading="wo3"), ctx, profile)
        assert any(c.role == "zh_final" for c in cells)

    def test_hanzi_char(self, ctx, profile):
        cells = translate_node(HanziChar(surface="我", reading="wo3"), ctx, profile)
        assert any(c.role == "zh_final" for c in cells)

    def test_number(self, ctx, profile):
        cells = translate_node(Number(surface="42"), ctx, profile)
        assert cells[0].role == "number_sign"

    def test_punct(self, ctx, profile):
        cells = translate_node(Punct(surface="，"), ctx, profile)
        assert cells[0].role == "punct"

    def test_space(self, ctx, profile):
        cells = translate_node(Space(surface=" "), ctx, profile)
        assert cells[0].is_blank

    def test_connector(self, ctx, profile):
        cells = translate_node(Connector(surface="", span=Span(1, 1)), ctx, profile)
        assert len(cells) == 1
        assert cells[0].role == "connector"
        assert cells[0].dots == profile.connector

    def test_latin_word(self, ctx, profile):
        # V3 Latin: 1 prefix on first letter + bare cells for the rest.
        # "hi" → 1 + 2 = 3 cells.
        assert len(translate_node(LatinWord(surface="hi"), ctx, profile)) == 3

    def test_latin_acronym(self, ctx, profile):
        # "CPU" → doubled upper prefix (whole-word capitals, ⠠⠠) +
        # 3 bare letter cells = 5 cells.
        assert len(translate_node(LatinAcronym(surface="CPU"), ctx, profile)) == 5

    def test_unknown(self, ctx, profile):
        cells = translate_node(Unknown(surface="?"), ctx, profile)
        assert cells[0].role == "unknown"
        assert any(w.code == "UNKNOWN_NODE" for w in ctx.warnings)

    def test_math_inline_without_ir_warns_and_falls_back(self, ctx, profile):
        # MathInline whose `math` field was never populated (e.g. no
        # adapter ran) should warn and emit unknown-surface cells.
        cells = translate_node(MathInline(surface="x^2"), ctx, profile)
        assert any(w.code == "MATH_NO_IR" for w in ctx.warnings)
        assert all(c.role == "unknown" for c in cells)
        assert len(cells) == len("x^2")

    def test_math_inline_with_ir_translates(self, ctx, profile):
        # MathInline with a parsed MathML tree goes through the math
        # backend and lands in the cell stream as real math cells,
        # not unknown.
        import xml.etree.ElementTree as ET

        tree = ET.fromstring("<math><mi>x</mi></math>")
        node = MathInline(surface="x", math=tree)
        cells = translate_node(node, ctx, profile)
        assert any(c.role == "math_identifier" for c in cells)

    def test_percent(self, ctx, profile):
        node = Percent(surface="12%", number=Number(surface="12"))
        cells = translate_node(node, ctx, profile)
        # number_sign + digits + percent
        assert cells[0].role == "number_sign"

    def test_quantity(self, ctx, profile):
        node = Quantity(
            surface="3kg",
            number=Number(surface="3", span=Span(0, 1)),
            unit="kg",
            unit_canonical="kilogram",
            span=Span(0, 3),
        )
        cells = translate_node(node, ctx, profile)
        # number_sign + 1 digit + (56 + k + g) = 5 cells — one letter
        # sign covers the same-class run "kg".
        assert cells[0].role == "number_sign"
        assert len(cells) == 5
        assert any(c.role == "quantity_unit" for c in cells)

    def test_code_inline(self, ctx, profile):
        cells = translate_node(CodeInline(surface="ab"), ctx, profile)
        assert len(cells) == 2

    def test_unhandled_node_emits_warning(self, ctx, profile):
        class _Mystery(InlineNode):
            """An InlineNode subclass the dispatcher has no branch for."""

        cells = translate_node(_Mystery(surface="?"), ctx, profile)
        assert cells == []
        assert any(w.code == "UNHANDLED_NODE_TYPE" for w in ctx.warnings)


class TestTranslateBlock:
    def test_paragraph_with_mixed_children(self, ctx, profile):
        para = Paragraph(children=[
            HanziChar(surface="我", reading="wo3", span=Span(0, 1)),
            HanziChar(surface="在", reading="zai4", span=Span(1, 2)),
            Punct(surface="。", span=Span(2, 3)),
        ])
        block = translate_block(para, ctx, profile)
        assert block.block_type == "paragraph"
        # 我 (final+tone) = 2, 在 (init+final+tone) = 3, 。 (⠐⠆) = 2
        # cells with no trailing blank.
        assert len(block.cells) == 2 + 3 + 2

    def test_heading_preserves_level(self, ctx, profile):
        h = Heading(level=2, children=[HanziChar(surface="标", reading="biao1")])
        block = translate_block(h, ctx, profile)
        assert block.block_type == "heading"
        assert block.heading_level == 2


class TestTranslateDocument:
    def test_metadata_carries_profile(self, ctx, profile):
        doc = DocumentIR(metadata={"language": "zh-CN"}, blocks=[Paragraph()])
        bd = translate_document(doc, ctx, profile)
        assert bd.metadata["profile"] == "cn_current"
        assert bd.metadata["language"] == "zh-CN"

    def test_multi_block(self, ctx, profile):
        doc = DocumentIR(blocks=[
            Heading(level=1, children=[HanziChar(surface="一", reading="yi1")]),
            Paragraph(children=[HanziChar(surface="二", reading="er4")]),
        ])
        bd = translate_document(doc, ctx, profile)
        assert len(bd.blocks) == 2
        assert bd.blocks[0].block_type == "heading"
        assert bd.blocks[1].block_type == "paragraph"


class TestPipeline:
    def test_end_to_end(self):
        from brailix import Pipeline

        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("我在重庆。")
        rendered = result.render()
        assert isinstance(rendered, str)
        # Output is non-empty, contains Unicode braille chars.
        assert len(rendered) > 0
        for ch in rendered:
            cp = ord(ch)
            assert 0x2800 <= cp <= 0x28FF or ch == "\n"

    def test_empty_text(self):
        from brailix import Pipeline

        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("")
        assert result.render() == ""
        assert len(result.warnings) == 0

    def test_warnings_accessible(self):
        from brailix import Pipeline

        # ``null`` resolver leaves pinyin empty \u2014 the backend then
        # warns with MISSING_PINYIN for every char.
        pipe = Pipeline(profile="cn_current", resolver="null")
        result = pipe.translate_text("\u6211")
        assert any(w.code == "MISSING_PINYIN" for w in result.warnings)

    def test_proofread_json_shape(self):
        from brailix import Pipeline

        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text("。")
        payload = result.proofread_json()
        assert set(payload) == {"text", "ir", "braille_ir", "warnings"}
        assert payload["text"] == "。"
        assert payload["ir"]["type"] == "document"
        assert payload["braille_ir"]["type"] == "braille_document"
