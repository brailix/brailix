"""The segmenter→normalizer mediator.

Lives beside the other core primitives rather than under ``tests/ir`` because
:class:`~brailix.core.segment.Segment` is not an IR node — see the module
docstring for why a type named by a core protocol signature cannot live in the
frontend that produces it.
"""

from brailix.core.segment import Segment
from brailix.core.span import Span


class TestSegment:
    def test_basic(self):
        s = Segment(type="hanzi_text", surface="我在", span=Span(0, 2))
        assert s.type == "hanzi_text"
        assert s.to_dict() == {"type": "hanzi_text", "surface": "我在", "span": [0, 2]}

    def test_no_span(self):
        s = Segment(type="math_inline", surface="x^2")
        assert s.to_dict() == {"type": "math_inline", "surface": "x^2"}


class TestNotIR:
    def test_segment_is_not_reachable_from_the_ir_facade(self):
        """``brailix.ir`` publishes IR nodes; a frontend mediator is not one.

        The rule this pins is the one ``ChineseToken`` already follows: a
        normalized format two adapters agree on is not part of the document
        model, and re-exporting it from the IR facade made the frontend look
        like it had an IR stage of its own.
        """
        import brailix.ir

        assert "Segment" not in brailix.ir.__all__
        assert not hasattr(brailix.ir, "Segment")
