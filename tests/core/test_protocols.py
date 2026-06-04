"""Protocols are structural contracts.

We verify three properties:

1. They are :func:`runtime_checkable` so registries can validate at
   registration time.
2. A minimal correct implementation passes ``isinstance``.
3. A class missing a required method does NOT pass ``isinstance``.

We deliberately do not assert signature compatibility — Python's
``runtime_checkable`` only checks method names, and type-level
correctness is enforced by static type checkers and per-adapter tests.
"""

from brailix.core import protocols

# --- Minimal correct implementations ----------------------------------


class GoodSegmenter:
    name = "good"
    def segment(self, block, ctx): return []


class GoodChineseAnalyzer:
    name = "good"
    def analyze(self, text, ctx): return []


class GoodPinyinResolver:
    name = "good"
    def resolve(self, tokens, ctx): return tokens


class GoodMathSourceAdapter:
    source = "latex"
    def to_mathml(self, formula, ctx): return "<math/>"


class GoodRenderer:
    name = "good"
    def render(self, bir): return ""


# --- Bad implementations ----------------------------------------------


class NoMethodSegmenter:
    name = "bad"  # missing .segment


class NoMethodMathAdapter:
    source = "latex"  # missing .to_mathml


# --- Tests -------------------------------------------------------------


def test_segmenter_isinstance():
    assert isinstance(GoodSegmenter(), protocols.Segmenter)
    assert not isinstance(NoMethodSegmenter(), protocols.Segmenter)


def test_chinese_analyzer_isinstance():
    assert isinstance(GoodChineseAnalyzer(), protocols.ChineseAnalyzer)


def test_pinyin_resolver_isinstance():
    assert isinstance(GoodPinyinResolver(), protocols.PinyinResolver)


def test_math_source_adapter_isinstance():
    assert isinstance(GoodMathSourceAdapter(), protocols.MathSourceAdapter)
    assert not isinstance(NoMethodMathAdapter(), protocols.MathSourceAdapter)


def test_renderer_isinstance():
    assert isinstance(GoodRenderer(), protocols.Renderer)


def test_all_protocols_are_runtime_checkable():
    # If any protocol forgets @runtime_checkable, isinstance() raises
    # TypeError; the smoke checks above would have caught that, but
    # this test states the invariant explicitly.
    for name in (
        "Segmenter",
        "ChineseAnalyzer",
        "PinyinResolver",
        "MathSourceAdapter",
        "Renderer",
    ):
        cls = getattr(protocols, name)
        # _is_runtime_protocol is the private flag set by @runtime_checkable
        assert getattr(cls, "_is_runtime_protocol", False), f"{name} not runtime_checkable"
