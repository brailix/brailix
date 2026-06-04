"""Unit tests for the zh-frontend IRBuilder slice.

Covers :func:`brailix.frontend.zh.shift_token_spans` and
:func:`brailix.frontend.zh.tokens_to_inline` — the helpers that used
to live inside :mod:`brailix.pipeline` and got moved to the Chinese
frontend subsystem so the orchestrator stops carrying Chinese-braille
typesetting knowledge (ARCHITECTURE §7.1, §12).

These tests are pure-Python — no QApplication, no Pipeline — so the
contract holds regardless of which higher-level integration evolves
around them.
"""

from __future__ import annotations

from brailix.core.config import load_profile
from brailix.core.span import Span
from brailix.frontend.zh import (
    insert_cross_kind_boundary_spaces,
    shift_token_spans,
    tokens_to_inline,
)
from brailix.ir.inline import (
    ChineseToken,
    Connector,
    HanziChar,
    HanziMarker,
    LatinAcronym,
    LatinWord,
    MathInline,
    Number,
    Punct,
    Space,
    Word,
)

# The full shipped lexicon — the boundary helper used to auto-load it;
# now the caller passes it (Pipeline reads ``profile.zh_compounds``).
_COMPOUNDS = load_profile("cn_current").zh_compounds

# ---------------------------------------------------------------------------
# shift_token_spans
# ---------------------------------------------------------------------------


class TestShiftTokenSpans:
    def test_base_zero_returns_input_unchanged(self) -> None:
        """``base == 0`` is the fast path — must return the same list
        object (no allocation) so callers don't pay for a no-op."""
        tokens = [
            ChineseToken(surface="好", span=Span(0, 1)),
            ChineseToken(surface="的", span=Span(1, 2)),
        ]
        out = shift_token_spans(tokens, 0)
        assert out is tokens

    def test_positive_base_shifts_every_span(self) -> None:
        tokens = [
            ChineseToken(surface="好", span=Span(0, 1)),
            ChineseToken(surface="的", span=Span(1, 2)),
        ]
        out = shift_token_spans(tokens, 100)
        assert [t.span for t in out] == [Span(100, 101), Span(101, 102)]

    def test_shift_preserves_other_fields(self) -> None:
        """Pinyin / POS / confidence carry through unchanged — only the
        span moves."""
        tokens = [
            ChineseToken(
                surface="重庆",
                pos="ns",
                span=Span(0, 2),
                pinyin="chong2 qing4",
                confidence=0.99,
            )
        ]
        out = shift_token_spans(tokens, 10)
        assert out[0].surface == "重庆"
        assert out[0].pos == "ns"
        assert out[0].pinyin == "chong2 qing4"
        assert out[0].confidence == 0.99
        assert out[0].span == Span(10, 12)

    def test_returns_fresh_instances_not_aliases(self) -> None:
        """Inputs must not be mutated; callers that keep the originals
        should see them untouched."""
        original_span = Span(0, 1)
        tokens = [ChineseToken(surface="好", span=original_span)]
        out = shift_token_spans(tokens, 5)
        assert out[0] is not tokens[0]
        assert tokens[0].span is original_span
        assert tokens[0].span == Span(0, 1)

    def test_token_without_span_gets_one_from_surface_length(self) -> None:
        """Defensive: shipped adapters all set spans, but the helper
        must not crash if a future adapter omits them."""
        tokens = [ChineseToken(surface="重庆", span=None)]
        out = shift_token_spans(tokens, 5)
        # Synthesized as Span(0, len(surface)) then shifted.
        assert out[0].span == Span(5, 7)


# ---------------------------------------------------------------------------
# tokens_to_inline
# ---------------------------------------------------------------------------


class TestTokensToInline:
    def test_empty_input_returns_empty(self) -> None:
        assert tokens_to_inline([]) == []

    def test_single_char_token_becomes_hanzi_char(self) -> None:
        """Single-char tokens are the unknown / single-syllable case;
        :class:`HanziChar` is the correct IR shape (no ``pos`` /
        ``confidence`` fields — those only make sense for
        multi-character words)."""
        tokens = [
            ChineseToken(surface="好", span=Span(0, 1), pinyin="hao3")
        ]
        out = tokens_to_inline(tokens)
        assert len(out) == 1
        assert isinstance(out[0], HanziChar)
        assert out[0].surface == "好"
        assert out[0].reading == "hao3"
        assert out[0].span == Span(0, 1)

    def test_multi_char_token_becomes_word(self) -> None:
        tokens = [
            ChineseToken(
                surface="重庆",
                pos="ns",
                span=Span(0, 2),
                pinyin="chong2 qing4",
                confidence=0.99,
            )
        ]
        out = tokens_to_inline(tokens)
        assert len(out) == 1
        assert isinstance(out[0], Word)
        assert out[0].surface == "重庆"
        assert out[0].reading == "chong2 qing4"
        assert out[0].pos == "ns"
        assert out[0].confidence == 0.99

    def test_single_token_input_has_no_space_marker(self) -> None:
        """One token = no word boundary, so no Space.  The verification
        guards against an off-by-one in the boundary loop that would
        otherwise emit a leading Space for single-token segments."""
        tokens = [ChineseToken(surface="好", span=Span(0, 1))]
        out = tokens_to_inline(tokens)
        assert len(out) == 1
        assert not any(isinstance(n, Space) for n in out)

    def test_word_boundary_inserts_space_marker(self) -> None:
        """Two adjacent tokens → Word, Space, Word.  This materializes the
        Chinese braille rule of "write each word as a run, with a space
        between words"."""
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
            ChineseToken(surface="好", span=Span(2, 3)),
        ]
        out = tokens_to_inline(tokens)
        assert len(out) == 3
        assert isinstance(out[0], Word)
        assert isinstance(out[1], Space)
        assert isinstance(out[2], HanziChar)

    def test_space_marker_span_is_zero_width_at_boundary(self) -> None:
        """The inserted Space carries a zero-width span at the boundary
        between the two surrounding tokens.  Zero-width keeps it from
        overlapping real source / braille highlights downstream."""
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
            ChineseToken(surface="好", span=Span(2, 3)),
        ]
        out = tokens_to_inline(tokens)
        space = out[1]
        assert isinstance(space, Space)
        assert space.surface == ""
        assert space.span == Span(2, 2)

    def test_three_tokens_get_two_spaces(self) -> None:
        """N tokens → 2N-1 nodes (N tokens + N-1 separators)."""
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
            ChineseToken(surface="好", span=Span(2, 3)),
            ChineseToken(surface="啊", span=Span(3, 4)),
        ]
        out = tokens_to_inline(tokens)
        assert len(out) == 5
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "HanziChar", "Space", "HanziChar"]

    def test_pinyinless_tokens_become_pinyinless_nodes(self) -> None:
        """A pre-pinyin call (or a deliberately empty pinyin) must not
        break tokens_to_inline.  Word.reading / HanziChar.reading stay
        ``None``; backend warning machinery handles it from there."""
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
            ChineseToken(surface="好", span=Span(2, 3)),
        ]
        out = tokens_to_inline(tokens)
        assert out[0].reading is None  # Word
        assert out[2].reading is None  # HanziChar

    def test_tokens_without_spans_still_produce_inline_nodes(self) -> None:
        """Defensive: synthesize spans from surface length when the
        token doesn't carry one.  Boundary Space falls at start==end
        based on the synthesized end position."""
        tokens = [
            ChineseToken(surface="重庆", span=None),
            ChineseToken(surface="好", span=None),
        ]
        out = tokens_to_inline(tokens)
        # First Word span synthesised as Span(0, 2); boundary at 2.
        assert out[0].span == Span(0, 2)
        assert isinstance(out[1], Space)
        assert out[1].span == Span(2, 2)


# ---------------------------------------------------------------------------
# insert_cross_kind_boundary_spaces
# ---------------------------------------------------------------------------


class TestInsertCrossKindBoundarySpaces:
    """Cross-IR-kind segmentation spacing — Chinese (Word / HanziChar /
    HanziMarker) adjacent to Latin / Greek / Math (LatinWord /
    LatinAcronym / MathInline) gets one synthetic zero-width Space
    in between. A Number directly followed by Chinese (10页 / 3个) gets a
    Connector instead; the reverse and Number↔Latin are left
    alone. Punct boundaries are left alone by design.
    """

    def test_empty_input_returns_empty(self) -> None:
        assert insert_cross_kind_boundary_spaces([]) == []

    def test_single_node_unchanged(self) -> None:
        nodes = [Word(surface="已知", span=Span(0, 2))]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert out == nodes

    def test_chinese_then_latin_inserts_space(self) -> None:
        nodes = [
            Word(surface="已知", span=Span(0, 2)),
            LatinWord(surface="α", span=Span(2, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "LatinWord"]
        assert out[1].surface == ""
        assert out[1].span == Span(2, 2)

    def test_latin_then_chinese_inserts_space(self) -> None:
        nodes = [
            LatinWord(surface="α", span=Span(0, 1)),
            Word(surface="已知", span=Span(1, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["LatinWord", "Space", "Word"]

    def test_chinese_then_math_inserts_space(self) -> None:
        nodes = [
            Word(surface="学习", span=Span(0, 2)),
            MathInline(surface="x^2", span=Span(2, 5), source="latex"),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "MathInline"]

    def test_math_then_chinese_inserts_space(self) -> None:
        nodes = [
            MathInline(surface="x^2", span=Span(0, 3), source="latex"),
            Word(surface="学习", span=Span(3, 5)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["MathInline", "Space", "Word"]

    def test_hanzi_char_treated_as_chinese(self) -> None:
        nodes = [
            HanziChar(surface="的", span=Span(0, 1)),
            LatinWord(surface="α", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["HanziChar", "Space", "LatinWord"]

    def test_hanzi_marker_treated_as_chinese(self) -> None:
        nodes = [
            HanziMarker(surface="年", span=Span(0, 1)),
            LatinWord(surface="x", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["HanziMarker", "Space", "LatinWord"]

    def test_latin_acronym_treated_as_foreign(self) -> None:
        nodes = [
            Word(surface="使用", span=Span(0, 2)),
            LatinAcronym(surface="CPU", span=Span(2, 5)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "LatinAcronym"]

    def test_existing_space_not_doubled(self) -> None:
        """Idempotent: if a Space already sits between the two nodes
        (user-typed or previously synthesized), don't add a second."""
        nodes = [
            Word(surface="已知", span=Span(0, 2)),
            Space(surface=" ", span=Span(2, 3)),
            LatinWord(surface="α", span=Span(3, 4)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "LatinWord"]

    def test_punct_between_skips_insertion(self) -> None:
        """Punct neither is Chinese nor foreign — boundary check fails
        at both Word↔Punct and Punct↔LatinWord pairs, so no synthetic
        Space lands. The punct table's own trailing-space rule governs
        the rendered output in that case."""
        nodes = [
            Word(surface="已知", span=Span(0, 2)),
            Punct(surface=",", span=Span(2, 3)),
            LatinWord(surface="α", span=Span(3, 4)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Punct", "LatinWord"]

    def test_number_then_hanzi_inserts_connector(self) -> None:
        """A digit immediately followed by hanzi (3个 / 10页) → Connector
        (the connector ⠤), not Space: the digit cell collides with the
        hanzi's first cell (个 / 的 ⠛=7), and without a separator it would
        be read as a continuation of the number."""
        nodes = [
            Number(surface="3", span=Span(0, 1)),
            HanziChar(surface="个", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Number", "Connector", "HanziChar"]
        assert out[1].surface == ""
        assert out[1].span == Span(1, 1)

    def test_number_then_word_inserts_connector(self) -> None:
        # A multi-character word (10页码) takes the connector the same way.
        nodes = [
            Number(surface="10", span=Span(0, 2)),
            Word(surface="页码", span=Span(2, 4)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == ["Number", "Connector", "Word"]

    def test_hanzi_then_number_unchanged(self) -> None:
        """The reverse (第3) adds no connector: the number sign ⠼ already
        marks where the number starts, so there is no ambiguity."""
        nodes = [
            HanziChar(surface="第", span=Span(0, 1)),
            Number(surface="3", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert out == nodes

    def test_number_hanzi_non_adjacent_not_joined(self) -> None:
        # The source has a gap (10 页, with a space node position in between)
        # → not a single writing unit; when the spans aren't adjacent, no
        # connector is inserted.
        nodes = [
            Number(surface="10", span=Span(0, 2)),
            HanziChar(surface="页", span=Span(5, 6)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert out == nodes

    def test_number_latin_boundary_unchanged(self) -> None:
        """Number ↔ Latin/Greek out of scope — the connector only applies to
        digit→hanzi."""
        nodes = [
            Number(surface="2", span=Span(0, 1)),
            LatinWord(surface="α", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert out == nodes

    def test_three_way_chinese_math_chinese(self) -> None:
        """学习 $...$ 很有用 (study, then a formula, then "is useful") — both
        flanks get a Space inserted."""
        nodes = [
            Word(surface="学习", span=Span(0, 2)),
            MathInline(surface="x", span=Span(2, 3), source="latex"),
            Word(surface="有用", span=Span(3, 5)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["Word", "Space", "MathInline", "Space", "Word"]


class TestLetterHanziCompoundConnector:
    """When a letter + hanzi form a single word (x轴 / T恤 / 维生素C), the
    letter↔hanzi seam takes a :class:`Connector` (the connector ⠤) rather
    than a :class:`Space` (blank cell). Non-compounds (已知 α, 使用 CPU) still
    take a Space. The decision comes from the compound-word lexicon
    (``profile.zh_compounds``, passed in by the caller)."""

    def test_latin_before_hanzi_compound_inserts_connector(self) -> None:
        # x轴: x and 轴 are one word → Connector, not Space.
        nodes = [
            LatinWord(surface="x", span=Span(0, 1)),
            HanziChar(surface="轴", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["LatinWord", "Connector", "HanziChar"]
        assert out[1].surface == ""
        assert out[1].span == Span(1, 1)

    def test_single_letter_before_hanzi_compound(self) -> None:
        # T恤: a single uppercase letter is a LatinWord (len<2, not an acronym).
        nodes = [
            LatinWord(surface="T", span=Span(0, 1)),
            HanziChar(surface="恤", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == [
            "LatinWord", "Connector", "HanziChar",
        ]

    def test_hanzi_before_latin_compound(self) -> None:
        # 维生素C: Word first, LatinWord second; joining 维生素+C = 维生素C.
        nodes = [
            Word(surface="维生素", span=Span(0, 3)),
            LatinWord(surface="C", span=Span(3, 4)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == [
            "Word", "Connector", "LatinWord",
        ]

    def test_acronym_hanzi_compound(self) -> None:
        # AA制: AA is a LatinAcronym (≥2 uppercase); joining AA+制 = AA制.
        nodes = [
            LatinAcronym(surface="AA", span=Span(0, 2)),
            HanziChar(surface="制", span=Span(2, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == [
            "LatinAcronym", "Connector", "HanziChar",
        ]

    def test_non_compound_letter_hanzi_stays_space(self) -> None:
        # 已知α is not a compound → Space (consistent with the existing
        # segmentation-spacing behavior; regression guard).
        nodes = [
            Word(surface="已知", span=Span(0, 2)),
            LatinWord(surface="α", span=Span(2, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == ["Word", "Space", "LatinWord"]

    def test_compound_word_keeps_outer_word_space(self) -> None:
        # 穿T恤: 穿 | T恤 are two words (Space between 穿 and T), while T恤 is a
        # compound (Connector between T and 恤).
        nodes = [
            HanziChar(surface="穿", span=Span(0, 1)),
            LatinWord(surface="T", span=Span(1, 2)),
            HanziChar(surface="恤", span=Span(2, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == [
            "HanziChar", "Space", "LatinWord", "Connector", "HanziChar",
        ]

    def test_explicit_source_space_not_joined(self) -> None:
        # The user typed a space ("x 轴"): a Space node sits in between, so the
        # boundary check never sees x↔轴 as adjacent and inserts no Connector —
        # the user's segmentation intent wins.
        nodes = [
            LatinWord(surface="x", span=Span(0, 1)),
            Space(surface=" ", span=Span(1, 2)),
            HanziChar(surface="轴", span=Span(2, 3)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        kinds = [type(n).__name__ for n in out]
        assert kinds == ["LatinWord", "Space", "HanziChar"]
        assert not any(isinstance(n, Connector) for n in out)

    def test_non_adjacent_spans_not_joined(self) -> None:
        # The lexicon matches, but the spans show a gap in the source → not a
        # single writing unit, so keep the Space.
        nodes = [
            LatinWord(surface="x", span=Span(0, 1)),
            HanziChar(surface="轴", span=Span(5, 6)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == ["LatinWord", "Space", "HanziChar"]

    def test_math_inline_hanzi_never_connector(self) -> None:
        # A MathInline next to hanzi is never a compound (a formula isn't a
        # word) → Space, even when the joined surface "x轴" happens to be in
        # the lexicon.
        nodes = [
            MathInline(surface="x", span=Span(0, 1), source="latex"),
            HanziChar(surface="轴", span=Span(1, 2)),
        ]
        out = insert_cross_kind_boundary_spaces(nodes, _COMPOUNDS)
        assert [type(n).__name__ for n in out] == [
            "MathInline", "Space", "HanziChar",
        ]


# ---------------------------------------------------------------------------
# Subsystem-independence pin
# ---------------------------------------------------------------------------


class TestSubsystemIndependence:
    """Per ARCHITECTURE §7.1, the analyzer subsystem must not import the
    pinyin subsystem.  Coupling them would mean a different pinyin
    adapter would force changes inside ``frontend/zh/analyzer/`` —
    breaking the "swap an adapter, downstream untouched" guarantee."""

    def test_zh_init_does_not_import_pinyin(self) -> None:
        """Static check: parse the module's AST and assert no
        ``brailix.frontend.zh.pinyin`` import lives in it.  This is the
        teeth behind the architectural rule — if someone ever inlines
        ``annotate`` into ``tokens_to_inline``, this test fails before
        the violation lands on main.

        AST-based rather than substring-based so the docstring's own
        mention of the rule doesn't false-positive.
        """
        import ast
        import inspect

        import brailix.frontend.zh.analyzer as zh_mod

        tree = ast.parse(inspect.getsource(zh_mod))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("brailix.frontend.zh.pinyin"):
                        offenders.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("brailix.frontend.zh.pinyin"):
                    offenders.append(module)
        assert not offenders, (
            "frontend/zh/analyzer must not import frontend/zh/pinyin (ARCHITECTURE "
            f"§7.1); found imports: {offenders}.  The orchestrator "
            "chains zh + pinyin separately so either subsystem's "
            "adapter can be swapped independently."
        )
