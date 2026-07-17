"""Property-based tests for the Span algebra.

``Span`` is the provenance currency of the whole compiler: every IR node and
braille cell carries one, and layout / proofreading logic reasons in terms of
containment, overlap, merging and shifting. This module pins the *algebraic*
contract over generated inputs instead of enumerated boundary cases, so edge
regressions (empty spans, touching spans, zero shifts, degenerate payloads)
surface as shrunken counterexamples.

Example-based coverage of the same API lives in ``test_span.py``; here we
deliberately assert only relations *between* operations (bounding box,
symmetry, composition, round-trips), which stay valid however the concrete
implementation evolves.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st

from brailix.core.span import Span, merge_spans

# Offsets stay small: the algebra is offset-magnitude-independent, and small
# values shrink to readable counterexamples.
_MAX = 200


@st.composite
def spans(draw: st.DrawFn) -> Span:
    start = draw(st.integers(0, _MAX))
    length = draw(st.integers(0, 50))
    return Span(start, start + length)


class TestConstruction:
    @given(start=st.integers(-_MAX, _MAX), end=st.integers(-_MAX, _MAX))
    def test_valid_iff_ordered_and_nonnegative(self, start: int, end: int) -> None:
        # The constructor's whole contract: accept exactly the half-open
        # ranges ``0 <= start <= end``, reject everything else loudly.
        if 0 <= start <= end:
            s = Span(start, end)
            assert s.length == end - start
            assert s.is_empty() == (start == end)
        else:
            with pytest.raises(ValueError):
                Span(start, end)


class TestMerge:
    @given(a=spans(), b=spans())
    def test_merge_is_the_bounding_box(self, a: Span, b: Span) -> None:
        m = a.merge(b)
        assert m.start == min(a.start, b.start)
        assert m.end == max(a.end, b.end)
        assert m.contains(a) and m.contains(b)

    @given(a=spans(), b=spans())
    def test_merge_commutes(self, a: Span, b: Span) -> None:
        assert a.merge(b) == b.merge(a)

    @given(a=spans(), b=spans(), c=spans())
    def test_merge_associates(self, a: Span, b: Span, c: Span) -> None:
        assert a.merge(b).merge(c) == a.merge(b.merge(c))

    @given(a=spans())
    def test_merge_idempotent(self, a: Span) -> None:
        assert a.merge(a) == a

    @given(items=st.lists(spans(), max_size=8))
    def test_merge_spans_bounds_every_input(self, items: list[Span]) -> None:
        merged = merge_spans(items)
        if not items:
            assert merged is None
        else:
            assert merged is not None
            assert merged.start == min(s.start for s in items)
            assert merged.end == max(s.end for s in items)
            assert all(merged.contains(s) for s in items)


class TestRelations:
    @given(a=spans())
    def test_contains_is_reflexive(self, a: Span) -> None:
        assert a.contains(a)

    @given(a=spans(), b=spans(), c=spans())
    def test_contains_is_transitive(self, a: Span, b: Span, c: Span) -> None:
        if a.contains(b) and b.contains(c):
            assert a.contains(c)

    @given(a=spans(), b=spans())
    def test_contains_matches_interval_model(self, a: Span, b: Span) -> None:
        assert a.contains(b) == (a.start <= b.start and b.end <= a.end)

    @given(a=spans(), b=spans())
    def test_overlaps_is_symmetric(self, a: Span, b: Span) -> None:
        assert a.overlaps(b) == b.overlaps(a)

    @given(a=spans(), b=spans())
    def test_overlaps_matches_interval_model_for_nonempty(self, a: Span, b: Span) -> None:
        # Half-open semantics: touching spans ([0,5) and [5,9)) do NOT
        # overlap. Restricted to non-empty spans, where "shares at least
        # one position" is unambiguous.
        if not a.is_empty() and not b.is_empty():
            assert a.overlaps(b) == (max(a.start, b.start) < min(a.end, b.end))

    def test_empty_span_overlap_semantics_pinned(self) -> None:
        # Current (undocumented) behaviour for empty spans — an insertion
        # point *strictly inside* a range overlaps it, one on the boundary
        # does not. Mathematically the intersection is empty either way, but
        # the inside case reading as "belongs to the range" is load-bearing
        # for zero-width provenance (insertion-point cells). Pinned so any
        # future change to it is a deliberate decision, not an accident.
        assert Span(5, 5).overlaps(Span(3, 8))
        assert not Span(5, 5).overlaps(Span(5, 8))
        assert not Span(8, 8).overlaps(Span(5, 8))
        assert not Span(5, 5).overlaps(Span(5, 5))

    @given(a=spans(), b=spans())
    def test_containment_of_nonempty_implies_overlap(self, a: Span, b: Span) -> None:
        if a.contains(b) and not b.is_empty():
            assert a.overlaps(b)


class TestShift:
    @given(s=spans(), offset=st.integers(0, _MAX))
    def test_shift_preserves_length_and_round_trips(self, s: Span, offset: int) -> None:
        shifted = s.shift(offset)
        # Absolute position pinned, not just the relations: a shift that
        # moved by 2*offset would still round-trip and keep its length.
        assert shifted.start == s.start + offset
        assert shifted.length == s.length
        assert shifted.shift(-offset) == s

    @given(s=spans(), a=st.integers(0, _MAX), b=st.integers(0, _MAX))
    def test_shift_composes_additively(self, s: Span, a: int, b: int) -> None:
        assert s.shift(a).shift(b) == s.shift(a + b)

    @given(s=spans(), offset=st.integers(-3 * _MAX, 0))
    def test_shift_below_zero_rejected(self, s: Span, offset: int) -> None:
        # Shifting is rebasing between coordinate systems; a rebase that
        # lands before position 0 is a caller bug and must fail loudly.
        if s.start + offset < 0:
            with pytest.raises(ValueError):
                s.shift(offset)
        else:
            assert s.shift(offset).start == s.start + offset


# Payload soup for the JSON boundary: things a malformed persisted document
# could plausibly smuggle into a ``span`` field.
_junk_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-5, 5),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=3),
)
_payloads = st.one_of(
    _junk_scalars,
    st.lists(_junk_scalars, max_size=4),
    st.tuples(_junk_scalars, _junk_scalars),
)


class TestSerialization:
    @given(s=spans())
    def test_tuple_round_trip(self, s: Span) -> None:
        assert Span.from_tuple(s.to_tuple()) == s
        # JSON round-trips a tuple as a list; both shapes must parse.
        assert Span.from_tuple(list(s.to_tuple())) == s

    @given(payload=_payloads)
    def test_from_tuple_accepts_exactly_the_documented_shape(self, payload: object) -> None:
        # Documented contract: a 2-element sequence of genuine ints (bool is
        # rejected despite being an int subclass; floats are rejected rather
        # than truncated) forming a valid ``0 <= start <= end`` range.
        # Everything else raises ValueError instead of smuggling a bad span
        # into the IR.
        valid = (
            isinstance(payload, (list, tuple))
            and len(payload) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in payload)
            and 0 <= payload[0] <= payload[1]
        )
        if valid:
            span = Span.from_tuple(payload)
            assert (span.start, span.end) == tuple(payload)
        else:
            with pytest.raises(ValueError):
                Span.from_tuple(payload)
