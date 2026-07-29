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


class TestBindMode:
    """WarningCollector.bind_mode — adopt a run mode once, reject a conflict."""

    def test_first_bind_sets_mode(self):
        c = WarningCollector()
        c.bind_mode(RunMode.STRICT)
        assert c.mode is RunMode.STRICT

    def test_rebind_same_mode_is_idempotent(self):
        c = WarningCollector()
        c.bind_mode(RunMode.LENIENT)
        c.bind_mode("lenient")  # string form normalizes to the same mode
        assert c.mode is RunMode.LENIENT

    def test_rebind_different_mode_raises(self):
        c = WarningCollector()
        c.bind_mode(RunMode.STRICT)
        with pytest.raises(ValueError, match="already bound"):
            c.bind_mode(RunMode.NORMAL)

    def test_explicit_construction_mode_is_still_rebindable_once(self):
        # A collector constructed with an explicit mode is not yet "bound" —
        # the first adopting context may harmonize it (context is
        # authoritative). Only a second, different bind conflicts.
        c = WarningCollector(mode=RunMode.NORMAL)
        c.bind_mode(RunMode.LENIENT)
        assert c.mode is RunMode.LENIENT


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


class TestTheAnchorIsReallyFrozen:
    """``Warning`` is ``frozen=True``, and ``anchor`` was the hole in that.

    A frozen dataclass freezes the *fields*, not what a field points at, so the
    one mapping-valued field stayed writable from both sides: through the dict
    the caller passed in (still theirs, still shared), and through
    ``warning.anchor`` itself. Either rewrites a diagnostic that was already
    recorded — one a block cache, the editor's navigation and a test comparison
    all read as fixed — and neither raises.

    What must survive the fix matters as much: the field exists to be read out
    by a front-end, a log or a serialized report, so it has to stay JSON-
    encodable, copyable and picklable. That is why it is a ``dict`` subclass
    and not a ``MappingProxyType`` (which is none of the three).
    """

    def _anchored(self) -> tuple[Warning, dict]:
        source = {"part_id": "P1", "measure_number": "5"}
        return Warning(code="X", message="m", anchor=source), source

    def test_mutating_the_dict_you_passed_does_not_rewrite_the_record(self):
        w, source = self._anchored()
        source["measure_number"] = "99"
        assert w.anchor == {"part_id": "P1", "measure_number": "5"}

    def test_the_collector_helpers_copy_too(self):
        """``warn`` / ``error`` take the caller's dict straight through to the
        constructor, which is the path the aliasing bug actually travelled."""
        anchor = {"measure_number": "1"}
        wc = WarningCollector()
        wc.warn("A", "m", anchor=anchor)
        wc.error("B", "m", anchor=anchor)
        anchor["measure_number"] = "99"
        assert [w.anchor["measure_number"] for w in wc.warnings] == ["1", "1"]

    @pytest.mark.parametrize(
        "name,write",
        [
            ("__setitem__", lambda a: a.__setitem__("part_id", "P9")),
            ("__delitem__", lambda a: a.__delitem__("part_id")),
            ("__ior__", lambda a: a.__ior__({"part_id": "P9"})),
            ("update", lambda a: a.update({"part_id": "P9"})),
            ("pop", lambda a: a.pop("part_id")),
            ("popitem", lambda a: a.popitem()),
            ("clear", lambda a: a.clear()),
            ("setdefault", lambda a: a.setdefault("new", "v")),
        ],
    )
    def test_writing_through_the_field_is_refused(self, name, write):
        w, _ = self._anchored()
        with pytest.raises(TypeError, match="read-only"):
            write(w.anchor)
        assert w.anchor == {"part_id": "P1", "measure_number": "5"}

    def test_reinitialising_the_anchor_is_refused(self):
        """The hole every other override left open.

        ``__init__`` is a public method of the object, and ``dict.__init__``
        fills an *existing* mapping in C without going through
        ``__setitem__`` — so ``warning.anchor.__init__({...})`` rewrote a
        recorded diagnostic while every guarded spelling above raised. It is
        not a base-class call like ``dict.__setitem__(anchor, ...)``; it is
        the object's own constructor, offered by the object.
        """
        w, _ = self._anchored()
        with pytest.raises(TypeError, match="read-only"):
            w.anchor.__init__({"part_id": "P9"})
        assert w.anchor == {"part_id": "P1", "measure_number": "5"}

    def test_construction_itself_still_works(self):
        """The other half: sealing must not refuse the *first* fill, which is
        how the mapping is built at all."""
        from brailix.core.errors import _FrozenAnchor

        assert _FrozenAnchor({"a": "1"}) == {"a": "1"}
        assert _FrozenAnchor() == {}
        assert _FrozenAnchor(a="1") == {"a": "1"}

    def test_it_still_reads_as_an_ordinary_dict(self):
        w, _ = self._anchored()
        assert w.anchor == {"part_id": "P1", "measure_number": "5"}
        assert w.anchor.get("part_id") == "P1"
        assert "measure_number" in w.anchor
        assert dict(w.anchor) == w.anchor
        assert sorted(w.anchor.items()) == [("measure_number", "5"), ("part_id", "P1")]

    def test_it_survives_json_copy_and_pickle(self):
        """The reason this is a ``dict`` subclass. A ``MappingProxyType``
        would make every one of these raise, on a field whose whole purpose is
        being read out of the library."""
        import copy
        import json
        import pickle

        w, _ = self._anchored()
        assert json.loads(json.dumps(w.anchor)) == dict(w.anchor)
        assert copy.copy(w.anchor) == w.anchor
        assert copy.deepcopy(w).anchor == w.anchor
        assert pickle.loads(pickle.dumps(w.anchor)) == w.anchor
        assert json.dumps(w.to_dict())  # the serialized form, end to end

    def test_replace_keeps_the_anchor_frozen(self):
        """``dataclasses.replace`` rebuilds the record — the LENIENT-mode
        downgrade in ``emit`` does exactly this — so the copy must survive it
        rather than being applied only on the first construction."""
        import dataclasses

        w, _ = self._anchored()
        again = dataclasses.replace(w, level=WarningLevel.ERROR)
        with pytest.raises(TypeError):
            again.anchor["part_id"] = "P9"

    def test_to_dict_hands_out_a_writable_copy(self):
        """The serialized form is the caller's to do what they like with —
        freezing it there would be freezing someone else's data."""
        w, _ = self._anchored()
        payload = w.to_dict()
        payload["anchor"]["part_id"] = "P9"  # must not raise
        assert w.anchor["part_id"] == "P1"


# ``dict``'s own callables that only read. ``copy`` / ``__or__`` / ``__ror__``
# return a plain ``dict`` and leave the receiver alone; ``fromkeys`` builds a
# new instance. ``__init__`` is NOT on this list — it fills the mapping in C
# without passing through ``__setitem__``, which made it a write like any
# other, and it is overridden (and therefore not inherited) so that a second
# call is refused.
_READ_ONLY_DICT_API = frozenset({
    "__class_getitem__", "__contains__", "__eq__", "__ge__", "__getattribute__",
    "__getitem__", "__gt__", "__iter__", "__le__", "__len__",
    "__lt__", "__ne__", "__new__", "__or__", "__repr__", "__reversed__",
    "__ror__", "__sizeof__", "copy", "fromkeys", "get", "items", "keys",
    "values",
})


def test_no_dict_mutator_is_inherited_without_review() -> None:
    """The structural half, mirroring the guard on ``_BoundaryRegistry``.

    ``dict``'s mutators are C-level and independent — overriding
    ``__setitem__`` does nothing for ``update`` or ``|=`` — and the interpreter
    adds methods across versions. So an inherited name must be on the read-only
    list above: "unclassified" means "look at it", not "assume it reads".
    """
    from brailix.core.errors import _FrozenAnchor

    inherited = {
        name for name, value in vars(dict).items() if callable(value)
    } - set(vars(_FrozenAnchor))
    unreviewed = sorted(inherited - _READ_ONLY_DICT_API)
    assert not unreviewed, (
        f"dict methods inherited by _FrozenAnchor without review: "
        f"{unreviewed}. Override it to refuse the write, or add it to "
        f"_READ_ONLY_DICT_API."
    )


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
