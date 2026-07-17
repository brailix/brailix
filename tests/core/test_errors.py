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


class TestWarningCollectorAPI:
    """Collector conveniences with example value. The three-mode emit
    policy itself (strict raises / normal stores / lenient downgrades,
    string spellings included) is property-tested over generated warnings
    in ``test_warning_properties.py``."""

    def test_default_mode_is_normal(self):
        wc = WarningCollector()
        assert wc.mode is RunMode.NORMAL

    def test_warn_helper(self):
        wc = WarningCollector()
        wc.warn("X", "boom", surface="x", span=Span(0, 1))
        assert wc.warnings[0].level is WarningLevel.WARN
        assert wc.warnings[0].span == Span(0, 1)
        assert len(wc) == 1
        assert bool(wc) is True

    def test_error_helper_emits_error_level(self):
        wc = WarningCollector()
        wc.error("X", "boom")
        assert wc.warnings[0].level is WarningLevel.ERROR

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
