"""End-to-end test of the frontend pipeline.

Walks the canonical demo sentence through the frontend **the Pipeline runs**,
by driving that Pipeline's own frontend driver:

    Paragraph.text
      → segmenter (auto → the profile language's)
      → normalizer (auto → the profile language's)
      → the LanguageFrontend registered for the profile's language
        (tokenize → pinyin → tokens_to_inline)
      → the language's boundary pass

…and asserts the resulting InlineNode list has the expected shape. This is the
contract every frontend implementation must honor; swapping the char/null
fallbacks for HanLP/g2pW must not change the *structure* of the output, only
the pinyin values and word boundaries inside hanzi runs.

**Driven, not re-implemented.** This file used to chain ``DefaultSegmenter`` →
``DefaultNormalizer`` → analyzer → resolver by hand and finish with a
token→inline converter of its own, which meant it asserted against a copy: the
copy emitted no word-boundary ``Space`` at all, so every assertion below held
while describing an output the library does not produce, and the
``language_frontend_registry`` / ``_ZhFrontend.process`` / boundary-pass /
auto-adapter path — everything a real caller gets — went untested here. It
reaches through ``pipe._frontend`` for the same reason the other integration
tests do: that is the production frontend, stopping short of the backend so the
warnings assertions stay about the frontend.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.document import Paragraph
from brailix.ir.inline import InlineNode, Number, Punct, Space
from brailix.pipeline import Pipeline


def _run_frontend(
    text: str, *, zh: str = "char", pinyin: str = "null"
) -> tuple[list[InlineNode], object]:
    """The production frontend over one paragraph. Returns (children, warnings)."""
    pipe = Pipeline(profile="cn_current", analyzer=zh, resolver=pinyin)
    block = Paragraph(text=text)
    ctx = FrontendContext(
        profile="cn_current", options=pipe._frontend.frontend_options()
    )
    pipe._frontend.populate_block(block, ctx)
    return block.inlines, ctx.warnings


# ---------------------------------------------------------------------------
# The big one
# ---------------------------------------------------------------------------


class TestCanonicalSentence:
    TEXT = "我在2026年5月17日去了重庆银行。"

    def test_structure_after_full_pipeline(self):
        children, warnings = _run_frontend(self.TEXT)

        # Expected ordering: 我 ⟂ 在 ⟂ [Date 2026年5月17日] ⟂ 去 ⟂ 了 ⟂
        # 重 ⟂ 庆 ⟂ 银 ⟂ 行 [Punct 。]
        kinds = [type(c).__name__ for c in children]
        assert "Date" in kinds
        assert "Punct" in kinds
        # The char analyzer emits one token per hanzi, so each of 我 在 去 了
        # 重 庆 银 行 is its own one-character Word.
        words = [c for c in children if type(c).__name__ == "Word"]
        assert len(words) == 8

        # Date sits where expected.
        date_idx = next(i for i, c in enumerate(children) if type(c).__name__ == "Date")
        assert children[date_idx].surface == "2026年5月17日"

        # No warnings under fallback adapters.
        assert len(warnings) == 0

    def test_word_boundaries_are_marked(self):
        """Chinese braille writes a word together and separates words with a
        blank cell, so the frontend's own output must already carry a separator
        between adjacent words — including across the Date, which is a whole
        word set off from the prose on both sides."""
        children = _run_frontend(self.TEXT)[0]
        kinds = [type(c).__name__ for c in children]
        # 8 words + 1 date = 9 word-level nodes in a row → 8 separators; the
        # trailing Punct takes none.
        assert kinds.count("Space") == 8
        date_idx = kinds.index("Date")
        assert kinds[date_idx - 1] == "Space"
        assert kinds[date_idx + 1] == "Space"

    def test_separators_are_zero_width_and_carry_no_surface(self):
        """A synthesised separator must not claim source text: it renders as a
        blank cell but stands at a boundary, so proofreading highlights of the
        words either side stay exact."""
        children = _run_frontend(self.TEXT)[0]
        synthetic = [c for c in children if isinstance(c, Space) and c.surface == ""]
        assert synthetic
        assert all(c.span is not None and c.span.is_empty() for c in synthetic)

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
    def test_paragraph_mixing_math_a_quantity_and_a_percentage(self):
        text = "看 算 $a+b$ 3.5kg 12% 完。"
        children, _ = _run_frontend(text)
        kinds = {type(c).__name__ for c in children}
        # Each protected pattern should produce its own node.
        assert "MathInline" in kinds
        assert "Punct" in kinds
        # Surface still round-trips.
        assert "".join(c.surface for c in children) == text

    def test_a_percentage_and_a_quantity_are_their_plain_parts(self):
        # Neither is a composite node: a quantity is a Number beside a
        # LatinWord, a percentage a Number beside a Punct. What sets each off
        # from the surrounding prose is a rule about the boundary — the
        # latin↔hanzi one, and ``%``'s own space_after — not a node.
        children, _ = _run_frontend("12%")
        assert [type(c).__name__ for c in children] == ["Number", "Punct"]
        children, _ = _run_frontend("3.5kg")
        assert [type(c).__name__ for c in children] == ["Number", "LatinWord"]

    def test_a_user_typed_space_is_not_doubled(self):
        """The boundary pass is idempotent: where the source already wrote a
        space, it stays one node and no synthetic separator joins it."""
        children, _ = _run_frontend("看 算")
        kinds = [type(c).__name__ for c in children]
        assert kinds == ["Word", "Space", "Word"]
        assert children[1].surface == " "


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

        # Re-run with a DIFFERENT resolver that actually sets a (dummy)
        # reading, so the two runs genuinely differ in pinyin — the contract
        # is that the node structure (types + surfaces) is identical
        # regardless of which resolver ran. (Re-running null vs null only
        # proved a deterministic call equals itself.)
        from dataclasses import replace

        class _StubResolver:
            name = "stub-swap-test"

            def resolve(self, tokens, ctx=None):
                return [replace(t, pinyin="xx") for t in tokens]

        with resolver_registry.overriding("stub-swap-test", _StubResolver):
            b, _ = _run_frontend(text, pinyin="stub-swap-test")

        # Structure invariant across the swap...
        assert [type(x).__name__ for x in a] == [type(x).__name__ for x in b]
        assert [x.surface for x in a] == [x.surface for x in b]
        # ...while the readings genuinely differed (proves a real swap).
        a_readings = [getattr(x, "reading", None) for x in a]
        b_readings = [getattr(x, "reading", None) for x in b]
        assert a_readings != b_readings
