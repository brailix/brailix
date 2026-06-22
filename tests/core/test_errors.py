import pytest

from brailix.core.errors import (
    BrailixError,
    MissingExtraError,
    ParseError,
    RunMode,
    StrictModeError,
    Warning,
    WarningCollector,
    WarningLevel,
)
from brailix.core.span import Span


class TestDiscard:
    """WarningCollector.discard — retract stored warnings by predicate."""

    def test_removes_matching_and_counts(self):
        c = WarningCollector()
        c.emit(Warning(code="A", message="m", surface="重庆"))
        c.emit(Warning(code="B", message="m", surface="银行"))
        c.emit(Warning(code="A", message="m", surface="北京"))
        removed = c.discard(lambda w: w.code == "A")
        assert removed == 2
        assert [w.code for w in c.warnings] == ["B"]

    def test_no_match_returns_zero(self):
        c = WarningCollector()
        c.emit(Warning(code="A", message="m"))
        assert c.discard(lambda w: w.code == "Z") == 0
        assert len(c.warnings) == 1

    def test_predicate_on_surface(self):
        c = WarningCollector()
        c.emit(Warning(code="LOW_CONFIDENCE_PINYIN", message="m", surface="重庆"))
        c.emit(Warning(code="LOW_CONFIDENCE_PINYIN", message="m", surface="银行"))
        dict_words = {"重庆": "chong2 qing4"}
        c.discard(
            lambda w: w.code == "LOW_CONFIDENCE_PINYIN"
            and w.surface in dict_words
        )
        assert [w.surface for w in c.warnings] == ["银行"]


class TestWarningRecord:
    def test_minimal(self):
        w = Warning(code="X", message="m")
        assert w.level is WarningLevel.WARN
        assert w.span is None
        assert w.candidates == ()

    def test_with_span_and_candidates(self):
        w = Warning(
            code="LOW_CONFIDENCE_PINYIN",
            message="多音字",
            surface="单于",
            span=Span(20, 22),
            candidates=("chan2 yu2", "dan1 yu2"),
            source="pinyin.g2pw",
        )
        d = w.to_dict()
        assert d == {
            "code": "LOW_CONFIDENCE_PINYIN",
            "level": "warn",
            "message": "多音字",
            "surface": "单于",
            "span": [20, 22],
            "candidates": ["chan2 yu2", "dan1 yu2"],
            "source": "pinyin.g2pw",
        }

    def test_to_dict_omits_unset(self):
        d = Warning(code="X", message="m").to_dict()
        assert d == {"code": "X", "level": "warn", "message": "m"}

    def test_anchor_round_trips_to_dict(self):
        """``anchor`` is the structural-provenance slot for inputs with
        no usable text span (music: part/measure labels)."""
        w = Warning(
            code="MUSIC_UNKNOWN_NOTE",
            message="m",
            anchor={"part_id": "P1", "measure_number": "5"},
        )
        assert w.anchor == {"part_id": "P1", "measure_number": "5"}
        assert w.to_dict()["anchor"] == {
            "part_id": "P1",
            "measure_number": "5",
        }
        # Default stays None and is omitted from the dict form.
        assert Warning(code="X", message="m").anchor is None
        assert "anchor" not in Warning(code="X", message="m").to_dict()


class TestWarningCollectorNormal:
    def test_default_mode_is_normal(self):
        wc = WarningCollector()
        assert wc.mode is RunMode.NORMAL

    def test_emit_stores(self):
        wc = WarningCollector()
        wc.emit(Warning(code="A", message="a"))
        assert len(wc) == 1
        assert bool(wc) is True

    def test_warn_helper(self):
        wc = WarningCollector()
        wc.warn("X", "boom", surface="x", span=Span(0, 1))
        assert wc.warnings[0].level is WarningLevel.WARN
        assert wc.warnings[0].span == Span(0, 1)

    def test_lenient_downgrade_preserves_all_fields(self):
        # LENIENT drops ERROR→WARN but must keep every other field. The old
        # hand-listed rebuild would silently lose any field added to Warning
        # later; dataclasses.replace can't drift.
        wc = WarningCollector(mode=RunMode.LENIENT)
        wc.emit(
            Warning(
                code="E",
                message="boom",
                level=WarningLevel.ERROR,
                surface="x",
                span=Span(2, 5),
                candidates=("a", "b"),
                source="frontend.zh",
                anchor={"part_id": "P1"},
            )
        )
        stored = wc.warnings[0]
        assert stored.level is WarningLevel.WARN
        assert stored.surface == "x"
        assert stored.span == Span(2, 5)
        assert stored.candidates == ("a", "b")
        assert stored.source == "frontend.zh"
        assert stored.anchor == {"part_id": "P1"}

    def test_iterable(self):
        wc = WarningCollector()
        wc.warn("A", "a")
        wc.warn("B", "b")
        codes = [w.code for w in wc]
        assert codes == ["A", "B"]

    def test_by_code(self):
        wc = WarningCollector()
        wc.warn("A", "1")
        wc.warn("B", "2")
        wc.warn("A", "3")
        assert [w.message for w in wc.by_code("A")] == ["1", "3"]

    def test_to_list_serializes(self):
        wc = WarningCollector()
        wc.warn("A", "a")
        items = wc.to_list()
        assert isinstance(items, list) and items[0]["code"] == "A"


class TestWarningCollectorStrict:
    def test_string_mode_is_normalized(self):
        wc = WarningCollector(mode="strict")
        assert wc.mode is RunMode.STRICT
        with pytest.raises(StrictModeError):
            wc.warn("OOPS", "kaboom")

    def test_emit_raises(self):
        wc = WarningCollector(mode=RunMode.STRICT)
        with pytest.raises(StrictModeError) as ei:
            wc.warn("OOPS", "kaboom")
        assert ei.value.warning.code == "OOPS"

    def test_strict_does_not_store_on_raise(self):
        wc = WarningCollector(mode=RunMode.STRICT)
        with pytest.raises(StrictModeError):
            wc.warn("X", "x")
        assert len(wc) == 0


class TestWarningCollectorLenient:
    def test_string_mode_downgrades_error(self):
        wc = WarningCollector(mode="lenient")
        wc.emit(Warning(code="X", message="m", level=WarningLevel.ERROR))
        assert wc.mode is RunMode.LENIENT
        assert wc.warnings[0].level is WarningLevel.WARN

    def test_error_downgraded(self):
        wc = WarningCollector(mode=RunMode.LENIENT)
        wc.emit(Warning(code="X", message="m", level=WarningLevel.ERROR))
        assert wc.warnings[0].level is WarningLevel.WARN

    def test_warn_passthrough(self):
        wc = WarningCollector(mode=RunMode.LENIENT)
        wc.warn("X", "m")
        assert wc.warnings[0].level is WarningLevel.WARN

    def test_info_passthrough(self):
        wc = WarningCollector(mode=RunMode.LENIENT)
        wc.emit(Warning(code="X", message="m", level=WarningLevel.INFO))
        assert wc.warnings[0].level is WarningLevel.INFO


class TestExceptions:
    def test_parse_error_inherits_base(self):
        assert issubclass(ParseError, BrailixError)

    def test_strict_mode_error_inherits_base(self):
        assert issubclass(StrictModeError, BrailixError)

    def test_missing_extra_message(self):
        err = MissingExtraError(adapter="hanlp", extra="hanlp")
        assert "pip install brailix[hanlp]" in str(err)
        assert err.adapter == "hanlp"
        assert err.extra == "hanlp"

    def test_missing_extra_with_hint(self):
        err = MissingExtraError(adapter="latex2mathml", extra="latex", hint="see docs/p3")
        assert "see docs/p3" in str(err)
