import pytest

from brailix.backend.block import translate_document
from brailix.backend.dispatch import translate_node
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Heading, Paragraph
from brailix.ir.inline import (
    CodeInline,
    Connector,
    InlineNode,
    LatinWord,
    MathInline,
    Number,
    Punct,
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


class _SpanlessDateMarkerBackend:
    """A LanguageBackend whose ``translate_word`` is impeccable and whose
    ``translate_date_marker`` returns the span-less sentinel.

    The shape the second enforcement point exists for: a plugin can satisfy
    every check the dispatcher makes and still break traceability, because
    ``translate_date`` resolves the very same registry through its own call
    path."""

    def translate_word(self, node, ctx, profile):
        from brailix.ir.braille import BrailleCell

        return [
            BrailleCell(dots=(1,), role="zh_final", source_span=node.span)
        ]

    def translate_date_marker(self, component, ctx, profile):
        from brailix.ir.braille import BLANK_CELL

        return [BLANK_CELL]


class TestTraceabilityContractAtDateMarker:
    """The date path resolves the same open registry, so it is held to the
    same post-condition — the marker translator is the second (and only
    other) boundary a third-party ``LanguageBackend`` is called across."""

    def test_spanless_date_marker_cells_raise(self, ctx, profile):
        from brailix.backend.dispatch import language_backend_registry
        from brailix.backend.number import translate_date
        from brailix.core.errors import BackendContractError
        from brailix.ir.inline import Date, DateComponent

        date = Date(
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
        with language_backend_registry.overriding(
            "zh", _SpanlessDateMarkerBackend
        ):
            with pytest.raises(BackendContractError) as exc_info:
                translate_date(date, ctx, profile)
        message = str(exc_info.value)
        assert "translate_date_marker" in message
        assert "DateComponent" in message

    def test_word_path_alone_would_have_passed(self, ctx, profile):
        # Pins why this needed its own enforcement: the identical plugin
        # sails through the dispatcher's check.
        from brailix.backend.dispatch import language_backend_registry

        with language_backend_registry.overriding(
            "zh", _SpanlessDateMarkerBackend
        ):
            cells = translate_node(
                Word(surface="我", reading="wo3", span=Span(0, 1)),
                ctx,
                profile,
            )
        assert all(c.source_span is not None for c in cells)

    def test_spanless_marker_node_is_exempt(self, ctx, profile):
        # Same exemption as the dispatcher's: a marker carrying no span
        # promises nothing about the cells built from it.
        from brailix.backend.dispatch import language_backend_registry
        from brailix.backend.number import translate_date
        from brailix.ir.inline import Date, DateComponent

        date = Date(
            surface="5月",
            components=[DateComponent(digits="5", marker="月", reading="yue4")],
        )
        with language_backend_registry.overriding(
            "zh", _SpanlessDateMarkerBackend
        ):
            cells = translate_date(date, ctx, profile)
        assert cells


class TestDispatchPerNodeType:
    def test_word(self, ctx, profile):
        cells = translate_node(Word(surface="我", reading="wo3"), ctx, profile)
        assert any(c.role == "zh_final" for c in cells)

    def test_multi_character_word_takes_the_same_route(self, ctx, profile):
        # One node type, one language-backend method, whatever the length —
        # there was a second case here asserting the identical thing on the
        # identical input, left over from when single characters had a node
        # type (and a protocol method) of their own.
        cells = translate_node(
            Word(surface="重庆", reading="chong2 qing4"), ctx, profile
        )
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

    def test_all_caps_word_takes_the_doubled_capital_sign(self, ctx, profile):
        # "CPU" → doubled upper prefix (whole-word capitals, ⠠⠠) +
        # 3 bare letter cells = 5 cells.
        #
        # The rule is driven by the SURFACE, not by a node type: there used
        # to be a ``LatinAcronym`` node that looked like it carried this,
        # while the backend read ``surface.isupper()`` and never the type
        # (with a stricter test than the node's own creation condition, so
        # the two did not even agree). This is what actually decides it.
        assert len(translate_node(LatinWord(surface="CPU"), ctx, profile)) == 5

    def test_a_lowercase_word_of_the_same_length_does_not(self, ctx, profile):
        # Same node type, different surface, different cells — which is the
        # whole reason the type was redundant.
        assert len(translate_node(LatinWord(surface="cpu"), ctx, profile)) == 4

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

    def test_code_inline(self, ctx, profile):
        cells = translate_node(CodeInline(surface="ab"), ctx, profile)
        assert len(cells) == 2

    def test_unhandled_node_emits_warning(self, ctx, profile):
        class _Mystery(InlineNode):
            """An InlineNode subclass the dispatcher has no branch for."""

        cells = translate_node(_Mystery(surface="?"), ctx, profile)
        assert cells == []
        assert any(w.code == "UNHANDLED_NODE_TYPE" for w in ctx.warnings)


class TestTranslateDocument:
    def test_metadata_carries_profile(self, ctx, profile):
        doc = DocumentIR(metadata={"language": "zh-CN"}, blocks=[Paragraph()])
        bd = translate_document(doc, ctx, profile)
        assert bd.metadata["profile"] == "cn_current"
        assert bd.metadata["language"] == "zh-CN"

    def test_multi_block(self, ctx, profile):
        doc = DocumentIR(blocks=[
            Heading(level=1, inlines=[Word(surface="一", reading="yi1")]),
            Paragraph(inlines=[Word(surface="二", reading="er4")]),
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


class TestLatinWordIsNotAWordThatHappensToBeLatin:
    """Why there are two prose-ish node types, measured rather than asserted.

    ``Word`` is language prose: the dispatcher routes it to the profile
    language's registered ``LanguageBackend``, which spells it from its
    *reading*. ``LatinWord`` is a letter run: it goes to the language-neutral
    Latin translator, which spells it from the letters and adds the capital /
    class signs. Feeding one to the other's path is not a near-miss — it is a
    different set of cells, or none.

    This exists because "isn't a Latin word just a Word?" is a reasonable
    question to ask of a taxonomy, and the answer has to be findable without
    re-deriving it. (The same question about ``Quantity`` had the opposite
    answer: that node's output was identical to ``Number`` + ``LatinWord``,
    so it is gone.)
    """

    def test_the_same_surface_compiles_to_different_cells(self, ctx, profile):
        latin = translate_node(LatinWord(surface="CPU", span=Span(0, 3)), ctx, profile)
        word = translate_node(Word(surface="CPU", span=Span(0, 3)), ctx, profile)
        assert [c.dots for c in latin] != [c.dots for c in word]
        # The letter run spells out, with the doubled capital sign in front.
        assert all(c.role == "latin_letter" for c in latin)
        # Routed as prose it has no reading to spell from, so every cell is
        # unknown and the run is reported — not silently rendered.
        assert all(c.role == "unknown" for c in word)
        assert any(w.code == "MISSING_PINYIN" for w in ctx.warnings)

    def test_the_boundary_rule_keys_on_the_type_too(self, profile):
        """``x轴`` is one compound word joined by the connector ⠤; the rule
        that decides it looks for a *letter run* beside the hanzi. A ``Word``
        there is prose meeting prose, and nothing is inserted at all."""
        from brailix.frontend.zh import insert_cross_kind_boundary_spaces

        as_latin = insert_cross_kind_boundary_spaces(
            [LatinWord(surface="x", span=Span(0, 1)),
             Word(surface="轴", span=Span(1, 2))],
            profile.zh_compounds,
        )
        as_word = insert_cross_kind_boundary_spaces(
            [Word(surface="x", span=Span(0, 1)),
             Word(surface="轴", span=Span(1, 2))],
            profile.zh_compounds,
        )
        assert [type(n).__name__ for n in as_latin] == [
            "LatinWord", "Connector", "Word"
        ]
        assert [type(n).__name__ for n in as_word] == ["Word", "Word"]


class TestAdjacentBlanksCollapse:
    """Two rules agreeing on "a blank goes here" must write one blank.

    Spacing is decided independently in several places — the punctuation
    table's ``space_before`` / ``space_after``, the boundary pass at a
    hanzi↔letter seam, the source's own typed space — and none of them can see
    the others. Collapsing the overlap is what lets each be stated without
    first proving the others are silent.

    What must NOT collapse is the interesting half, and it is why this is
    provenance-aware rather than a plain de-duplication.
    """

    def test_a_typed_run_of_spaces_is_content(self, pipe=None):
        """``选项是(   )`` — the fill-in blank of a multiple-choice item is
        three cells wide because the writer made it three wide."""
        from brailix import Pipeline

        out = Pipeline(profile="cn_current").translate_text("选项是(   )").render()
        assert "⠣⠀⠀⠀⠜" in out

    def test_two_synthesised_separators_become_one(self, ctx, profile):
        from brailix.backend.block import _collapse_adjacent_blanks
        from brailix.ir.braille import BrailleCell

        sep = BrailleCell(dots=(), role="space", source_span=Span(3, 3), source_text="")
        kept = _collapse_adjacent_blanks([sep, sep])
        assert len(kept) == 1
        assert kept[0].source_span == Span(3, 3)  # the first one's coordinate

    def test_a_synthesised_separator_beside_a_typed_space_gives_way(
        self, ctx, profile
    ):
        from brailix.backend.block import _collapse_adjacent_blanks
        from brailix.ir.braille import BrailleCell

        sep = BrailleCell(dots=(), role="space", source_span=Span(3, 3), source_text="")
        typed = BrailleCell(
            dots=(), role="space", source_span=Span(3, 4), source_text=" "
        )
        for run in ([sep, typed], [typed, sep]):
            kept = _collapse_adjacent_blanks(list(run))
            assert [c.source_text for c in kept] == [" "], run

    def test_layout_sentinels_are_not_blanks(self, ctx, profile):
        """``line_break`` / ``hang_open`` / ``hang_close`` carry empty dots too
        and are backend→renderer wire protocol, not spacing — merging them
        would drop a matrix row break. Judged by role, never by ``dots``."""
        from brailix.backend.block import _collapse_adjacent_blanks
        from brailix.ir.braille import BrailleCell

        run = [
            BrailleCell(dots=(), role="line_break"),
            BrailleCell(dots=(), role="line_break"),
            BrailleCell(dots=(), role="hang_open"),
        ]
        assert _collapse_adjacent_blanks(list(run)) == run


class TestSeparatorBeforeAttachedPunct:
    """A blank separates two words. A mark that closes the word just written
    is not the next word, so no blank goes in front of it.

    The other half of what lets ``space_after`` be stated without checking who
    comes next: :class:`TestAdjacentBlanksCollapse` settles two rules that
    agree, this settles one rule against the next mark's own table entry.
    """

    def test_space_after_does_not_survive_the_next_mark(self):
        """``（注）。`` — ）asks for a blank after it, but 。is written
        against what it follows, so the two marks meet."""
        from brailix import Pipeline

        out = Pipeline(profile="cn_current").translate_text("见（注）。").render()
        assert "⠠⠆⠐⠆" in out

    def test_an_opening_mark_keeps_its_blank(self):
        """``：“`` — ：wants one after, “ wants one before. They collapse to
        one blank; the veto is about what the next mark declares, not about
        two marks being adjacent."""
        from brailix import Pipeline

        out = Pipeline(profile="cn_current").translate_text("他说：“好”").render()
        assert "⠒⠀⠘⠘" in out

    def test_a_typed_space_before_a_mark_is_content(self, profile):
        """The author's own space is not a rule's opinion and is not vetoed —
        the same line :func:`_collapse_adjacent_blanks` draws."""
        from brailix.backend.block import _drop_separator_before_attached_punct
        from brailix.ir.braille import BrailleCell

        typed = BrailleCell(
            dots=(), role="space", source_span=Span(3, 4), source_text=" "
        )
        comma = BrailleCell(
            dots=(5,), role="punct", source_span=Span(4, 5), source_text="，"
        )
        assert _drop_separator_before_attached_punct([typed, comma], profile) == [
            typed,
            comma,
        ]

    def test_only_a_punct_cell_vetoes(self, profile):
        """A dots-empty layout sentinel is not punctuation, and neither is the
        unknown placeholder — a separator in front of either stays."""
        from brailix.backend.block import _drop_separator_before_attached_punct
        from brailix.ir.braille import BrailleCell

        sep = BrailleCell(dots=(), role="space", source_span=Span(3, 3), source_text="")
        for nxt in (
            BrailleCell(dots=(), role="line_break"),
            BrailleCell(dots=(), role="unknown", source_text="。"),
            BrailleCell(dots=(5,), role="list_marker", source_text="。"),
        ):
            run = [sep, nxt]
            assert _drop_separator_before_attached_punct(list(run), profile) == run, nxt

    def test_a_trailing_separator_has_no_mark_to_answer_to(self, profile):
        """Nothing follows, so nothing vetoes. Trimming a block-edge blank is
        a separate orthographic question (``你，`` ends in one on purpose)."""
        from brailix.backend.block import _drop_separator_before_attached_punct
        from brailix.ir.braille import BrailleCell

        comma = BrailleCell(
            dots=(5,), role="punct", source_span=Span(0, 1), source_text="，"
        )
        sep = BrailleCell(dots=(), role="space", source_span=Span(1, 1), source_text="")
        assert _drop_separator_before_attached_punct([comma, sep], profile) == [
            comma,
            sep,
        ]
