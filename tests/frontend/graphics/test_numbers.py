"""The graphics frontend's one answer to "can this number be drawn?".

Two modules read numbers out of a decoded spec, and each used to ask only
whether ``float()`` accepted the value. Neither rejected a non-finite one, so
the same oversight surfaced twice in unrelated symptoms — a coordinate written
into an SVG attribute as the literal ``inf``, and an ``OverflowError`` thrown
out of a tick generator. Tested here rather than only through its two callers,
because a rule with one implementation and two consumers is a rule that has to
mean the same thing to both.
"""

from __future__ import annotations

import json
import math

import pytest

from brailix.frontend.graphics._numbers import as_finite, non_finite_paths


class TestAsFinite:
    def test_reads_what_float_reads(self):
        assert as_finite(3) == 3.0
        assert as_finite(-2.5) == -2.5
        # A quoted number is a legitimate way to spell one in a hand-edited
        # JSON file, the same latitude core.measure allows.
        assert as_finite("100") == 100.0

    def test_zero_and_negatives_are_ordinary_coordinates(self):
        """The line between this and ``core.measure.as_positive_finite``: a
        point at x = -40 mm is a normal place to be, and 0 is the origin. That
        is why these are two functions and not one with a flag."""
        assert as_finite(0) == 0.0
        assert as_finite(-40.0) == -40.0

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_non_finite_is_not_a_number(self, value):
        assert as_finite(value, 7.0) == 7.0
        assert as_finite(value, None) is None

    def test_unreadable_values_take_the_default(self):
        for value in (None, "abc", {}, [1], object()):
            assert as_finite(value, 7.0) == 7.0

    def test_bool_is_refused_like_a_measurement(self):
        """``True`` is an ``int``, so a spec field set to it would otherwise
        silently mean 1."""
        assert as_finite(True, 7.0) == 7.0
        assert as_finite(False, 7.0) == 7.0

    def test_the_default_is_what_the_caller_passed(self):
        assert as_finite("x") == 0.0  # documented default
        assert as_finite("x", None) is None


class TestNonFinitePaths:
    def test_a_clean_spec_reports_nothing(self):
        assert non_finite_paths({"kind": "bar", "width": 100, "data": [1, 2]}) == []
        assert non_finite_paths({}) == []
        assert non_finite_paths([]) == []

    def test_a_nested_value_is_named_by_path(self):
        """The point of paths over a bare "invalid": an author working without
        a canvas needs to know which number to go and fix."""
        spec = {"kind": "line", "points": [[0, 0], [1, math.inf]]}
        assert non_finite_paths(spec) == ["points[1][1]=inf"]

    def test_a_key_at_the_top_level(self):
        assert non_finite_paths({"max": math.nan}) == ["max=nan"]

    def test_a_bare_value_names_the_spec_itself(self):
        assert non_finite_paths(math.inf) == ["<spec>=inf"]

    def test_several_values_are_reported_together(self):
        spec = {"a": math.inf, "b": math.nan, "c": 1}
        assert non_finite_paths(spec) == ["a=inf", "b=nan"]

    def test_the_report_is_bounded_and_stable(self):
        """A spec with a thousand bad values has one mistake, not a thousand,
        and the message gets read aloud. Sorted, so quoting it in a bug report
        means something — the walk visits siblings back to front."""
        spec = {f"k{i:02d}": math.inf for i in range(50)}
        found = non_finite_paths(spec)
        assert len(found) == 5
        assert found == sorted(found)
        assert found == non_finite_paths(spec)

    def test_the_limit_is_the_callers(self):
        spec = {f"k{i:02d}": math.inf for i in range(50)}
        assert len(non_finite_paths(spec, limit=2)) == 2

    def test_integers_and_strings_are_not_scanned_as_floats(self):
        assert non_finite_paths({"n": 10, "s": "inf", "b": True}) == []

    def test_json_carries_these_literals_by_default(self):
        """Not a code-only hazard, which is the reason the check sits at the
        source boundary: a plain ``json.loads`` of an ordinary document hands
        back a float that is not a number."""
        spec = json.loads('{"kind": "number_line", "max": Infinity}')
        assert non_finite_paths(spec) == ["max=inf"]

    def test_deep_nesting_does_not_exhaust_the_stack(self):
        """The walk is iterative, because its argument's nesting depth is the
        *input's* to choose. A recursive one would answer a hostile spec with a
        ``RecursionError`` raised from the guard meant to keep hostile specs
        out of the drawing code.
        """
        spec: object = math.inf
        for _ in range(20_000):
            spec = [spec]
        assert non_finite_paths(spec) == ["[0]" * 20_000 + "=inf"]
