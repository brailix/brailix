import pytest

from brailix.core.span import Span, merge_spans


class TestSpanConstruction:
    def test_basic(self):
        s = Span(0, 5)
        assert s.start == 0
        assert s.end == 5
        assert s.length == 5
        assert not s.is_empty()

    def test_empty(self):
        s = Span(3, 3)
        assert s.length == 0
        assert s.is_empty()

    def test_negative_start_rejected(self):
        with pytest.raises(ValueError):
            Span(-1, 5)

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError):
            Span(5, 3)

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        s = Span(0, 5)
        with pytest.raises(FrozenInstanceError):
            s.start = 1  # type: ignore[misc]


class TestSpanRelations:
    def test_contains_true(self):
        assert Span(0, 10).contains(Span(2, 5))
        assert Span(0, 10).contains(Span(0, 10))

    def test_contains_false_partial(self):
        assert not Span(0, 5).contains(Span(3, 8))

    def test_contains_false_disjoint(self):
        assert not Span(0, 5).contains(Span(6, 9))

    def test_overlaps(self):
        assert Span(0, 5).overlaps(Span(3, 8))
        assert Span(0, 5).overlaps(Span(4, 5))
        assert not Span(0, 5).overlaps(Span(5, 8))  # touching but not overlapping
        assert not Span(0, 5).overlaps(Span(6, 9))


class TestSpanTransforms:
    def test_merge(self):
        merged = Span(0, 3).merge(Span(5, 9))
        assert merged == Span(0, 9)

    def test_merge_overlapping(self):
        merged = Span(0, 5).merge(Span(3, 7))
        assert merged == Span(0, 7)

    def test_shift_positive(self):
        assert Span(2, 5).shift(10) == Span(12, 15)

    def test_shift_negative(self):
        assert Span(5, 10).shift(-2) == Span(3, 8)

    def test_shift_below_zero_rejected(self):
        with pytest.raises(ValueError):
            Span(0, 5).shift(-1)


class TestSpanSerialization:
    def test_round_trip(self):
        s = Span(3, 7)
        assert Span.from_tuple(s.to_tuple()) == s

    def test_to_tuple_format(self):
        assert Span(3, 7).to_tuple() == (3, 7)

    def test_from_tuple_accepts_json_list(self):
        # JSON round-trips a span as a list, not a tuple.
        assert Span.from_tuple([3, 7]) == Span(3, 7)

    def test_from_tuple_rejects_wrong_length(self):
        # The single canonical JSON-to-Span entry point must reject a malformed
        # span loudly rather than silently truncating or smuggling it through.
        with pytest.raises(ValueError):
            Span.from_tuple([0, 1, 2])
        with pytest.raises(ValueError):
            Span.from_tuple([0])

    def test_from_tuple_rejects_non_sequence(self):
        with pytest.raises(ValueError):
            Span.from_tuple(5)
        with pytest.raises(ValueError):
            Span.from_tuple(None)

    def test_from_tuple_rejects_float_offset(self):
        # A float is int-coercible but must be rejected, not truncated: turning
        # 3.9 into 3 would silently point the cell↔source map one char short.
        with pytest.raises(ValueError):
            Span.from_tuple([0.0, 3.9])

    def test_from_tuple_rejects_bool_offset(self):
        # bool is an int subclass; reject it explicitly rather than store
        # True/False as offsets 1/0.
        with pytest.raises(ValueError):
            Span.from_tuple([True, False])


class TestMergeSpans:
    def test_empty_iterable_returns_none(self):
        assert merge_spans([]) is None

    def test_single(self):
        assert merge_spans([Span(1, 4)]) == Span(1, 4)

    def test_multiple(self):
        spans = [Span(5, 7), Span(0, 2), Span(10, 12)]
        assert merge_spans(spans) == Span(0, 12)
