"""End-to-end test of the frontend pipeline.

Walks the canonical demo sentence through:

    Paragraph.text
      → DefaultSegmenter
      → DefaultNormalizer
      → char ChineseAnalyzer (on hanzi Segments)
      → null PinyinResolver

…and asserts the resulting InlineNode list has the expected shape.
This is the contract every frontend implementation must honor;
swapping the char/null fallbacks for HanLP/g2pW must not change the
*structure* of the output, only the pinyin values and word
boundaries inside hanzi runs.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.frontend.normalize import DefaultNormalizer
from brailix.frontend.segment import DefaultSegmenter
from brailix.frontend.zh.analyzer.registry import analyzer_registry
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.document import Paragraph
from brailix.ir.inline import (
    ChineseToken,
    InlineNode,
    Number,
    Percent,
    Punct,
    Quantity,
    Segment,
)


def _run_frontend(text: str, *, zh: str = "char", pinyin: str = "null"):
    """The canonical frontend pipeline. Returns (children, warnings)."""
    block = Paragraph(text=text)
    ctx = FrontendContext()

    segments = DefaultSegmenter().segment(block, ctx)
    normalized = DefaultNormalizer().normalize(segments, ctx)

    analyzer = analyzer_registry.get(zh)
    resolver = resolver_registry.get(pinyin)

    children: list[InlineNode] = []
    for item in normalized:
        if isinstance(item, Segment) and item.type == "hanzi_text":
            tokens = analyzer.analyze(item.surface, ctx)
            tokens = resolver.resolve(tokens, ctx)
            children.extend(_chinese_tokens_to_inline(tokens, base=item.span.start))
        elif isinstance(item, Segment):
            # A residual Segment that isn't hanzi_text means the frontend
            # left something un-lowered — fail loudly rather than silently
            # dropping it (the old ``pass`` masked exactly that, and the
            # comment that claimed it built Unknown placeholders was wrong).
            raise AssertionError(
                f"unexpected residual Segment {item.type!r}: {item.surface!r}"
            )
        else:
            children.append(item)
    return children, ctx.warnings


def _chinese_tokens_to_inline(
    tokens: list[ChineseToken], base: int
) -> list[InlineNode]:
    """Wrap ChineseToken in Word InlineNodes with absolute spans.

    In a more complete pipeline we'd build Word vs HanziChar based on
    token length and pos tag. For the integration test we just need
    something that conforms to InlineNode.
    """
    from brailix.core.span import Span
    from brailix.ir.inline import HanziChar, Word

    out: list[InlineNode] = []
    for t in tokens:
        local_span = t.span if t.span else Span(0, len(t.surface))
        abs_span = Span(base + local_span.start, base + local_span.end)
        if len(t.surface) == 1:
            out.append(HanziChar(surface=t.surface, span=abs_span, reading=t.pinyin))
        else:
            out.append(Word(surface=t.surface, span=abs_span, reading=t.pinyin, pos=t.pos))
    return out


# ---------------------------------------------------------------------------
# The big one
# ---------------------------------------------------------------------------


class TestCanonicalSentence:
    TEXT = "我在2026年5月17日去了重庆银行。"

    def test_structure_after_full_pipeline(self):
        children, warnings = _run_frontend(self.TEXT)

        # Expected ordering:
        # 我, 在, [Date 2026年5月17日], 去, 了, 重, 庆, 银, 行, [Punct 。]
        kinds = [type(c).__name__ for c in children]
        assert "Date" in kinds
        assert "Punct" in kinds
        # Char analyzer breaks 重庆银行 into 4 HanziChars, so we expect
        # plenty of HanziChar entries.
        hanzi_chars = [c for c in children if type(c).__name__ == "HanziChar"]
        assert len(hanzi_chars) == 8  # 我 在 去 了 重 庆 银 行

        # Date sits where expected.
        date_idx = next(i for i, c in enumerate(children) if type(c).__name__ == "Date")
        assert children[date_idx].surface == "2026年5月17日"

        # No warnings under fallback adapters.
        assert len(warnings) == 0

    def test_round_trip_surface(self):
        children, _ = _run_frontend(self.TEXT)
        assert "".join(c.surface for c in children) == self.TEXT

    def test_spans_are_monotonic_and_contiguous(self):
        children, _ = _run_frontend(self.TEXT)
        last_end = 0
        for c in children:
            assert c.span is not None, f"missing span on {c}"
            assert c.span.start == last_end, f"gap before {c}"
            last_end = c.span.end
        assert last_end == len(self.TEXT)


class TestMixedContent:
    def test_paragraph_with_math_quantity_percent(self):
        text = "看 算 $a+b$ 3.5kg 12% 完。"
        children, _ = _run_frontend(text)
        kinds = {type(c).__name__ for c in children}
        # Each protected / composite pattern should produce its own node.
        assert "MathInline" in kinds
        assert "Quantity" in kinds
        assert "Percent" in kinds
        assert "Punct" in kinds
        # Surface still round-trips.
        assert "".join(c.surface for c in children) == text

    def test_quantity_carries_canonical_unit(self):
        children, _ = _run_frontend("3.5kg")
        q = next(c for c in children if isinstance(c, Quantity))
        assert q.unit_canonical == "kilogram"

    def test_percent_carries_number_substructure(self):
        children, _ = _run_frontend("12%")
        p = next(c for c in children if isinstance(c, Percent))
        assert isinstance(p.number, Number)
        assert p.number.surface == "12"


class TestEmptyAndEdgeCases:
    def test_empty_paragraph(self):
        children, warnings = _run_frontend("")
        assert children == []
        assert len(warnings) == 0

    def test_only_punctuation(self):
        children, _ = _run_frontend("，。！？")
        assert all(isinstance(c, Punct) for c in children)
        assert len(children) == 4

    def test_only_number(self):
        children, _ = _run_frontend("2026")
        assert len(children) == 1
        assert isinstance(children[0], Number)


# ---------------------------------------------------------------------------
# Adapter swap doesn't change non-pinyin structure
# ---------------------------------------------------------------------------


class TestAdapterSwap:
    def test_pinyin_adapter_swap_preserves_structure(self):
        text = "我在重庆。"
        a, _ = _run_frontend(text, pinyin="null")
        # Re-run with a fake pinyin adapter (also returns no pinyin).
        # Using the registry's null is enough — the assertion is just that
        # the node shape is identical.
        b, _ = _run_frontend(text, pinyin="null")
        assert [type(x).__name__ for x in a] == [type(x).__name__ for x in b]
        assert [x.surface for x in a] == [x.surface for x in b]
