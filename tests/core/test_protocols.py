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
    # Declared by the language, not by core: a protocol whose signature names
    # Chinese types is Chinese's contract (Japanese declares its own the same
    # way). Checked here anyway — this file is where "every protocol is a
    # usable runtime check" is stated.
    from brailix.frontend.zh.analyzer import ChineseAnalyzer

    assert isinstance(GoodChineseAnalyzer(), ChineseAnalyzer)


def test_pinyin_resolver_isinstance():
    from brailix.frontend.zh.pinyin import PinyinResolver

    assert isinstance(GoodPinyinResolver(), PinyinResolver)


def test_language_protocols_are_not_on_the_core_surface():
    """core must not regrow one language's contracts.

    The asymmetry this closes was invisible from either side: Chinese's
    analyzer protocol sat in core while Japanese's sat in its own package,
    so an adapter author learned a different import path per language and a
    third language had no consistent example to copy.
    """
    assert not hasattr(protocols, "ChineseAnalyzer")
    assert not hasattr(protocols, "PinyinResolver")


def test_math_source_adapter_isinstance():
    assert isinstance(GoodMathSourceAdapter(), protocols.MathSourceAdapter)
    assert not isinstance(NoMethodMathAdapter(), protocols.MathSourceAdapter)


def test_renderer_isinstance():
    assert isinstance(GoodRenderer(), protocols.Renderer)


def test_all_protocols_are_runtime_checkable():
    # If any protocol forgets @runtime_checkable, isinstance() raises
    # TypeError; the smoke checks above would have caught that, but
    # this test states the invariant explicitly.
    from brailix.frontend.zh.analyzer import ChineseAnalyzer
    from brailix.frontend.zh.pinyin import PinyinResolver

    language_protocols = {
        "ChineseAnalyzer": ChineseAnalyzer,
        "PinyinResolver": PinyinResolver,
    }
    for name in (
        "Segmenter",
        "ChineseAnalyzer",
        "PinyinResolver",
        "MathSourceAdapter",
        "Renderer",
    ):
        cls = language_protocols.get(name) or getattr(protocols, name)
        # _is_runtime_protocol is the private flag set by @runtime_checkable
        assert getattr(cls, "_is_runtime_protocol", False), f"{name} not runtime_checkable"
