"""Property-based tests for the Warning record and collector mode policy.

The three run modes are a *diagnostics policy*, defined entirely by what
:meth:`WarningCollector.emit` does with a warning (the backends never
branch on mode — pinned by the math mode-contract properties). For ANY
warning, whatever its fields:

* **STRICT** raises :class:`StrictModeError` carrying that exact warning
  object, and stores nothing;
* **NORMAL** stores the warning object untouched;
* **LENIENT** downgrades exactly ERROR → WARN, preserving every other
  field (a hand-rebuilt copy once silently dropped fields added later —
  the property makes that class of regression impossible), and passes
  WARN / INFO through untouched.

String mode spellings ("strict" / "normal" / "lenient") must behave
identically to the enum values. ``to_dict`` must stay JSON-native and
omit unset optional fields. Collector API behaviours with example value
(discard predicates, by_code, iteration order) stay in test_errors.py.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core.errors import (
    RunMode,
    StrictModeError,
    Warning,
    WarningCollector,
    WarningLevel,
)
from brailix.core.span import Span

_short = st.text(alphabet=st.sampled_from(list("abcXY标记0,")), min_size=1, max_size=6)


@st.composite
def warnings_(draw: st.DrawFn) -> Warning:
    span = None
    if draw(st.booleans()):
        start = draw(st.integers(0, 30))
        span = Span(start, start + draw(st.integers(0, 5)))
    return Warning(
        code=draw(st.sampled_from(["UNKNOWN_PUNCT", "MATH_ERROR", "X"])),
        message=draw(_short),
        level=draw(st.sampled_from(list(WarningLevel))),
        surface=draw(st.one_of(st.none(), _short)),
        span=span,
        candidates=tuple(draw(st.lists(_short, max_size=2))),
        source=draw(st.one_of(st.none(), st.just("backend.test"))),
        anchor=draw(
            st.one_of(
                st.none(),
                st.dictionaries(st.sampled_from(["part_id", "measure_number"]), _short, max_size=2),
            )
        ),
    )


_modes_strict = st.sampled_from([RunMode.STRICT, "strict"])
_modes_normal = st.sampled_from([RunMode.NORMAL, "normal"])
_modes_lenient = st.sampled_from([RunMode.LENIENT, "lenient"])


def _fields_except_level(w: Warning) -> tuple:
    return (w.code, w.message, w.surface, w.span, w.candidates, w.source, w.anchor)


class TestModePolicy:
    @settings(max_examples=80)
    @given(warning=warnings_(), mode=_modes_strict)
    def test_strict_raises_the_exact_warning_and_stores_nothing(
        self, warning: Warning, mode: RunMode | str
    ) -> None:
        collector = WarningCollector(mode=mode)
        with pytest.raises(StrictModeError) as excinfo:
            collector.emit(warning)
        assert excinfo.value.warning is warning
        assert len(collector) == 0

    @settings(max_examples=80)
    @given(warning=warnings_(), mode=_modes_normal)
    def test_normal_stores_untouched(self, warning: Warning, mode: RunMode | str) -> None:
        collector = WarningCollector(mode=mode)
        collector.emit(warning)
        assert collector.warnings == [warning]
        assert collector.warnings[0] is warning

    @settings(max_examples=80)
    @given(warning=warnings_(), mode=_modes_lenient)
    def test_lenient_downgrades_only_the_level(
        self, warning: Warning, mode: RunMode | str
    ) -> None:
        collector = WarningCollector(mode=mode)
        collector.emit(warning)
        (stored,) = collector.warnings
        if warning.level is WarningLevel.ERROR:
            assert stored.level is WarningLevel.WARN
            # Every non-level field survives the downgrade bit for bit.
            assert _fields_except_level(stored) == _fields_except_level(warning)
        else:
            assert stored is warning


class TestSerializedShape:
    @settings(max_examples=100)
    @given(warning=warnings_())
    def test_to_dict_is_json_native_and_omits_unset(self, warning: Warning) -> None:
        payload = warning.to_dict()
        json.dumps(payload)
        assert payload["code"] == warning.code
        assert payload["message"] == warning.message
        assert payload["level"] == warning.level.value
        # Optional fields appear exactly when set.
        assert ("surface" in payload) == (warning.surface is not None)
        assert ("span" in payload) == (warning.span is not None)
        assert ("candidates" in payload) == bool(warning.candidates)
        assert ("source" in payload) == (warning.source is not None)
        # Truthiness, not None-ness: an EMPTY anchor dict carries no
        # information and is omitted, same as empty candidates.
        assert ("anchor" in payload) == bool(warning.anchor)
        if warning.span is not None:
            assert payload["span"] == [warning.span.start, warning.span.end]


class TestDiscardProperty:
    @settings(max_examples=60)
    @given(items=st.lists(warnings_(), max_size=6), doomed=st.sets(st.sampled_from(["UNKNOWN_PUNCT", "MATH_ERROR", "X"])))
    def test_discard_removes_exactly_the_matches_in_order(
        self, items: list[Warning], doomed: set[str]
    ) -> None:
        collector = WarningCollector(mode=RunMode.NORMAL)
        for w in items:
            collector.emit(w)
        removed = collector.discard(lambda w: w.code in doomed)
        assert removed == sum(1 for w in items if w.code in doomed)
        assert collector.warnings == [w for w in items if w.code not in doomed]
