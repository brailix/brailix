import xml.etree.ElementTree as ET

import pytest

from brailix.core.span import Span
from brailix.ir.inline import (
    ChineseToken,
    Date,
    HanziMarker,
    InlineNode,
    MathInline,
    MusicInline,
    Number,
    Percent,
    Quantity,
    Segment,
    Unknown,
    Word,
    _deserialize_value,
    _serialize_value,
    from_dict,
    inline_node_for,
)


class TestConstruction:
    def test_word_minimal(self):
        w = Word(surface="重庆")
        assert w.type == "word"
        assert w.surface == "重庆"
        assert w.reading is None

    def test_word_full(self):
        w = Word(
            surface="重庆",
            reading="chong2 qing4",
            pos="ns",
            span=Span(0, 2),
            confidence=0.99,
        )
        assert w.reading == "chong2 qing4"

    def test_number_with_role(self):
        n = Number(surface="2026", role="year", span=Span(0, 4))
        assert n.role == "year"

    def test_unknown_with_reason(self):
        u = Unknown(surface="??", reason="bad encoding")
        assert u.reason == "bad encoding"


class TestCompositeStructures:
    def test_date_with_parts(self):
        d = Date(
            surface="2026年5月17日",
            span=Span(0, 11),
            parts=[
                Number(surface="2026", role="year"),
                HanziMarker(surface="年", reading="nian2"),
                Number(surface="5", role="month"),
                HanziMarker(surface="月", reading="yue4"),
                Number(surface="17", role="day"),
                HanziMarker(surface="日", reading="ri4"),
            ],
        )
        assert len(d.parts) == 6
        assert d.parts[0].surface == "2026"

    def test_quantity(self):
        q = Quantity(
            surface="3.5kg",
            number=Number(surface="3.5"),
            unit="kg",
            unit_canonical="kilogram",
        )
        assert q.number.surface == "3.5"
        assert q.unit_canonical == "kilogram"

    def test_percent(self):
        p = Percent(surface="12%", number=Number(surface="12"))
        assert p.number.surface == "12"


class TestSerializationWord:
    def test_to_dict_minimal(self):
        d = Word(surface="我").to_dict()
        assert d == {"type": "word", "surface": "我"}

    def test_to_dict_with_span_and_pinyin(self):
        d = Word(surface="重庆", reading="chong2 qing4", span=Span(0, 2)).to_dict()
        assert d == {
            "type": "word",
            "surface": "重庆",
            "span": [0, 2],
            "reading": "chong2 qing4",
        }


class TestSerializationComposite:
    def test_empty_parts_omitted_from_to_dict(self):
        # ``parts`` is a default_factory=list field (f.default == MISSING),
        # so an empty list must still be omitted from the JSON, not emitted
        # as "parts": [].
        d = Date(surface="2026")
        assert "parts" not in d.to_dict()

    def test_date_round_trip(self):
        d = Date(
            surface="2026年5月17日",
            span=Span(0, 11),
            parts=[
                Number(surface="2026", role="year"),
                HanziMarker(surface="年", reading="nian2"),
                Number(surface="5", role="month"),
                HanziMarker(surface="月", reading="yue4"),
                Number(surface="17", role="day"),
                HanziMarker(surface="日", reading="ri4"),
            ],
        )
        payload = d.to_dict()
        assert payload["type"] == "date"
        assert payload["span"] == [0, 11]
        assert len(payload["parts"]) == 6
        assert payload["parts"][0] == {"type": "number", "surface": "2026", "role": "year"}

        restored = from_dict(payload)
        assert isinstance(restored, Date)
        assert len(restored.parts) == 6
        assert isinstance(restored.parts[0], Number)
        assert restored.parts[0].role == "year"
        assert isinstance(restored.parts[1], HanziMarker)
        assert restored.parts[1].reading == "nian2"


class TestSerializationMathInline:
    def test_math_inline_with_none_serializes_without_math_key(self):
        node = MathInline(surface="x^2", source="latex", math=None)
        payload = node.to_dict()
        assert "math" not in payload
        restored = from_dict(payload)
        assert restored.math is None

    def test_from_dict_rejects_dict_math_value(self):
        # Old IR format used a nested dict for math; the new schema
        # only accepts a string or ET.Element. A dict should raise.
        with pytest.raises(ValueError):
            from_dict({
                "type": "math_inline",
                "surface": "x",
                "math": {"kind": "identifier", "value": "x"},
            })

    def test_from_dict_rejects_malformed_xml_string(self):
        # A non-well-formed XML string surfaces as ValueError at the IR
        # boundary (ET.ParseError is re-raised), not a raw ParseError.
        with pytest.raises(ValueError):
            from_dict({
                "type": "math_inline",
                "surface": "x",
                "math": "<math><mo>+</mo>",  # unclosed <math>
            })

    def test_from_dict_accepts_explicit_none_math_value(self):
        # ``from_dict`` should accept an explicit ``"math": None`` even
        # though ``to_dict`` strips it. This hits the
        # ``_deserialize_value("math", None)`` branch directly.
        restored = from_dict({
            "type": "math_inline",
            "surface": "x",
            "math": None,
        })
        assert isinstance(restored, MathInline)
        assert restored.math is None

    def test_from_dict_accepts_et_element_math_value(self):
        # If an upstream IR builder hands ``from_dict`` a pre-parsed
        # ET.Element directly, the deserializer should pass it through
        # unchanged (no re-serialization round-trip).
        tree = ET.fromstring("<math><mi>x</mi></math>")
        restored = from_dict({
            "type": "math_inline",
            "surface": "x",
            "math": tree,
        })
        assert isinstance(restored, MathInline)
        # Must be the *same* element object — pass-through, not a copy.
        assert restored.math is tree

    def test_round_trip_strips_xmlns_attribute_tree(self):
        # Regression: a producer (e.g. normalize._try_atomic's math_op
        # path) can build the tree with an ``xmlns`` attribute.  After
        # ET.tostring -> ET.fromstring the reparse Clark-notates every tag
        # ({http://...}math); the backend dispatches on bare local names,
        # so an un-stripped round-trip yielded blank cells + spurious
        # warnings.  The IR boundary must normalise back to bare tags.
        m = ET.Element("math", {"xmlns": "http://www.w3.org/1998/Math/MathML"})
        ET.SubElement(m, "mo").text = "+"
        node = MathInline(surface="+", source="mathml", math=m)
        restored = from_dict(node.to_dict()).math
        assert restored is not None
        assert restored.tag == "math"
        assert restored[0].tag == "mo"  # NOT '{http://...}mo'
        assert restored[0].text == "+"


class TestSerializationMusicInline:
    def test_music_inline_with_none_serializes_without_score_key(self):
        node = MusicInline(surface="do re mi", source="plain", score=None)
        payload = node.to_dict()
        assert "score" not in payload
        restored = from_dict(payload)
        assert restored.score is None

    def test_from_dict_rejects_dict_score_value(self):
        # score must be None / str / ET.Element. A dict should raise so
        # malformed payloads fail loudly instead of silently storing junk.
        with pytest.raises(ValueError):
            from_dict({
                "type": "music_inline",
                "surface": "x",
                "score": {"kind": "note", "pitch": "C"},
            })

    def test_from_dict_accepts_explicit_none_score_value(self):
        restored = from_dict({
            "type": "music_inline",
            "surface": "x",
            "score": None,
        })
        assert isinstance(restored, MusicInline)
        assert restored.score is None

    def test_from_dict_accepts_et_element_score_value(self):
        # Pass-through when an upstream frontend hands a pre-parsed tree.
        tree = ET.fromstring("<score-partwise/>")
        restored = from_dict({
            "type": "music_inline",
            "surface": "",
            "score": tree,
        })
        assert isinstance(restored, MusicInline)
        assert restored.score is tree


class TestRegistry:
    def test_lookup_known(self):
        assert inline_node_for("word") is Word
        assert inline_node_for("date") is Date
        assert inline_node_for("math_inline") is MathInline
        assert inline_node_for("music_inline") is MusicInline

    def test_lookup_unknown_raises(self):
        with pytest.raises(KeyError):
            inline_node_for("nonsense")

    def test_from_dict_rejects_missing_type(self):
        with pytest.raises(ValueError):
            from_dict({"surface": "x"})

    def test_from_dict_ignores_extra_fields(self):
        # Forward-compatibility: unknown fields shouldn't break old readers.
        d = from_dict({"type": "word", "surface": "我", "future_field": 123})
        assert isinstance(d, Word)


class TestSegment:
    def test_basic(self):
        s = Segment(type="hanzi_text", surface="我在", span=Span(0, 2))
        assert s.type == "hanzi_text"
        assert s.to_dict() == {"type": "hanzi_text", "surface": "我在", "span": [0, 2]}

    def test_no_span(self):
        s = Segment(type="math_inline", surface="x^2")
        assert s.to_dict() == {"type": "math_inline", "surface": "x^2"}


class TestChineseToken:
    def test_minimal(self):
        t = ChineseToken(surface="我")
        assert t.pinyin is None
        assert t.to_dict() == {"surface": "我"}

    def test_full(self):
        t = ChineseToken(
            surface="重庆",
            pos="ns",
            span=Span(0, 2),
            pinyin="chong2 qing4",
            confidence=0.99,
        )
        assert t.to_dict() == {
            "surface": "重庆",
            "pos": "ns",
            "span": [0, 2],
            "pinyin": "chong2 qing4",
            "confidence": 0.99,
        }


class TestBaseClass:
    def test_inline_node_is_abstract_in_spirit(self):
        # Direct instantiation works but type is the generic placeholder.
        n = InlineNode(surface="x")
        assert n.type == "inline"


class TestSerializeValueHelper:
    def test_span_value_becomes_list(self):
        # Defensive: a Span surfacing as a non-``span`` field value should
        # still serialize cleanly via the helper.
        assert _serialize_value(Span(2, 5)) == [2, 5]

    def test_passthrough_scalar(self):
        assert _serialize_value(42) == 42
        assert _serialize_value("x") == "x"

    def test_list_recurses(self):
        assert _serialize_value([Span(0, 1), 7]) == [[0, 1], 7]

    def test_inline_node_delegates_to_to_dict(self):
        node = Number(surface="42")
        assert _serialize_value(node) == node.to_dict()


class TestMalformedSpan:
    def test_from_dict_rejects_malformed_span(self):
        # A span round-trips as a 2-element list; a 3-element one is malformed
        # and must raise at the IR boundary, not be stored raw as a list.
        with pytest.raises(ValueError):
            from_dict({"type": "number", "surface": "1", "span": [0, 1, 2]})

    def test_from_dict_accepts_explicit_none_span(self):
        # An explicit null span is allowed (to_dict omits it, from_dict keeps None).
        node = from_dict({"type": "number", "surface": "1", "span": None})
        assert node.span is None


class TestDeserializeGuard:
    """``from_dict`` dispatches on field name, but serialization is type-driven.
    A nested IR payload (a ``dict`` / list of ``dict``) reaching the
    deserializer fall-through means an IR-node field nobody registered — it must
    raise, not silently round-trip as raw dicts. The from_dict-side mirror of
    ``TestBaseToDictSelfConsistency`` in test_document.py."""

    def test_unregistered_dict_field_raises(self):
        with pytest.raises(ValueError, match="nested IR payload"):
            _deserialize_value("kid", {"type": "number", "surface": "1"})

    def test_unregistered_list_of_dict_field_raises(self):
        with pytest.raises(ValueError, match="nested IR payload"):
            _deserialize_value("kids", [{"type": "number", "surface": "1"}])

    def test_scalar_field_passes_through(self):
        assert _deserialize_value("confidence", 0.9) == 0.9
        assert _deserialize_value("reason", "bad") == "bad"

    def test_list_of_scalars_passes_through(self):
        # A future list-of-str field carries no dicts → must not trip the guard.
        assert _deserialize_value("tags", ["a", "b"]) == ["a", "b"]

    def test_registered_branches_run_before_guard(self):
        # The guard sits after the real branches: span / parts still deserialize.
        assert _deserialize_value("span", [0, 2]) == Span(0, 2)
        assert _deserialize_value("parts", []) == []
