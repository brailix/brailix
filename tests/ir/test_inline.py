import xml.etree.ElementTree as ET

import pytest

from brailix.core.span import Span
from brailix.ir.inline import (
    ChineseToken,
    Date,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    Punct,
    Quantity,
    Segment,
    Space,
    Unknown,
    Word,
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

    def test_round_trip(self):
        original = Word(surface="重庆", reading="chong2 qing4", span=Span(0, 2), pos="ns")
        restored = from_dict(original.to_dict())
        assert isinstance(restored, Word)
        assert restored.surface == original.surface
        assert restored.reading == original.reading
        assert restored.span == original.span
        assert restored.pos == original.pos


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

    def test_quantity_round_trip(self):
        q = Quantity(
            surface="3.5kg",
            number=Number(surface="3.5"),
            unit="kg",
            unit_canonical="kilogram",
        )
        restored = from_dict(q.to_dict())
        assert isinstance(restored, Quantity)
        assert restored.number.surface == "3.5"
        assert restored.unit_canonical == "kilogram"


class TestSerializationMathInline:
    def test_math_tree_round_trip_restores_et_element(self):
        # MathInline.math holds an ET.Element directly; to_dict
        # serialises it to a MathML string and from_dict parses back.
        math_tree = ET.fromstring(
            "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
        )
        original = MathInline(surface="1/2", source="latex", math=math_tree)

        payload = original.to_dict()
        assert isinstance(payload["math"], str)
        assert payload["math"].startswith("<math>")

        restored = from_dict(payload)
        assert isinstance(restored, MathInline)
        assert isinstance(restored.math, ET.Element)
        assert restored.math.tag == "math"
        # Structural equivalence: same child shape and text content.
        original_frac = math_tree[0]
        restored_frac = restored.math[0]
        assert original_frac.tag == restored_frac.tag == "mfrac"
        assert [c.tag for c in original_frac] == [c.tag for c in restored_frac]
        assert [c.text for c in original_frac] == [c.text for c in restored_frac]

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

    def test_round_trip_preserves_attributes(self):
        # Attributes (mathvariant, displaystyle, ...) on any node must
        # survive the JSON detour — proofread tools and downstream
        # rendering depend on them.
        tree = ET.fromstring(
            '<math display="block"><mi mathvariant="bold">x</mi></math>'
        )
        node = MathInline(surface="x", source="latex", math=tree)
        restored = from_dict(node.to_dict())
        assert restored.math is not None
        assert restored.math.attrib.get("display") == "block"
        assert restored.math[0].attrib.get("mathvariant") == "bold"

    def test_round_trip_preserves_deeply_nested_tree(self):
        # Two levels of nesting plus mixed text/tail content. Confirms
        # we don't accidentally flatten siblings or drop the
        # closing-tag tail text.
        original = ET.fromstring(
            "<math>"
            "<mfrac><msup><mi>x</mi><mn>2</mn></msup>"
            "<mrow><mn>2</mn><mo>+</mo><mi>y</mi></mrow></mfrac>"
            "</math>"
        )
        node = MathInline(surface="x^2/(2+y)", source="latex", math=original)
        restored = from_dict(node.to_dict()).math
        assert restored is not None
        # Compare normalized string form — easiest structural equality.
        assert ET.tostring(original, encoding="unicode") == ET.tostring(
            restored, encoding="unicode"
        )

    def test_round_trip_preserves_merror(self):
        # MathML <merror> wraps parse-failure spans; the math subsystem
        # uses it for graceful-degradation, so round-trip must keep it.
        original = ET.fromstring(
            "<math><merror><mtext>unparsable: \\foobar</mtext></merror></math>"
        )
        node = MathInline(surface="\\foobar", source="latex", math=original)
        restored = from_dict(node.to_dict()).math
        assert restored is not None
        assert restored[0].tag == "merror"
        assert restored[0][0].tag == "mtext"
        assert restored[0][0].text == "unparsable: \\foobar"

    def test_round_trip_idempotent(self):
        # Two serialize/deserialize cycles must converge — the payload
        # is stable, not drifting on each pass.
        original = ET.fromstring(
            "<math><mfrac><mn>3</mn><mn>4</mn></mfrac></math>"
        )
        first = MathInline(surface="3/4", source="latex", math=original)
        once = from_dict(first.to_dict())
        twice = from_dict(once.to_dict())
        assert ET.tostring(once.math, encoding="unicode") == ET.tostring(
            twice.math, encoding="unicode"
        )

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
    def test_score_tree_round_trip_restores_et_element(self):
        # MusicInline.score holds an ET.Element directly; to_dict
        # serialises it to a MusicXML string and from_dict parses back —
        # symmetric to MathInline.math (see ARCHITECTURE.md).
        score_tree = ET.fromstring(
            "<score-partwise><part id='P1'>"
            "<measure number='1'><note><pitch><step>C</step>"
            "<octave>4</octave></pitch><duration>4</duration>"
            "<type>quarter</type></note></measure>"
            "</part></score-partwise>"
        )
        original = MusicInline(surface="C4", source="musicxml", score=score_tree)

        payload = original.to_dict()
        assert isinstance(payload["score"], str)
        assert payload["score"].startswith("<score-partwise>")

        restored = from_dict(payload)
        assert isinstance(restored, MusicInline)
        assert isinstance(restored.score, ET.Element)
        assert restored.score.tag == "score-partwise"
        # Structural equivalence: same child shape and text content.
        original_part = score_tree[0]
        restored_part = restored.score[0]
        assert original_part.tag == restored_part.tag == "part"
        assert original_part.attrib == restored_part.attrib

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

    def test_round_trip_preserves_attributes(self):
        # Attributes (version, id, ...) must survive the JSON detour —
        # MusicXML carries crucial metadata in attributes like
        # <score-partwise version="4.0"> and <part id="P1">.
        tree = ET.fromstring(
            '<score-partwise version="4.0">'
            '<part-list><score-part id="P1"><part-name>Piano</part-name>'
            '</score-part></part-list></score-partwise>'
        )
        node = MusicInline(surface="", source="musicxml", score=tree)
        restored = from_dict(node.to_dict())
        assert restored.score is not None
        assert restored.score.attrib.get("version") == "4.0"
        assert restored.score[0][0].attrib.get("id") == "P1"

    def test_round_trip_preserves_deeply_nested_tree(self):
        # Real MusicXML has 4+ nesting levels (score > part > measure >
        # note > pitch). Confirms we don't flatten or drop tail text.
        original = ET.fromstring(
            "<score-partwise><part id='P1'>"
            "<measure number='1'>"
            "<attributes><divisions>4</divisions>"
            "<key><fifths>0</fifths></key>"
            "<time><beats>4</beats><beat-type>4</beat-type></time>"
            "</attributes>"
            "<note><pitch><step>D</step><octave>5</octave></pitch>"
            "<duration>2</duration><type>eighth</type></note>"
            "</measure></part></score-partwise>"
        )
        node = MusicInline(surface="", source="musicxml", score=original)
        restored = from_dict(node.to_dict()).score
        assert restored is not None
        assert ET.tostring(original, encoding="unicode") == ET.tostring(
            restored, encoding="unicode"
        )

    def test_round_trip_idempotent(self):
        # Two serialize/deserialize cycles must converge.
        original = ET.fromstring(
            "<score-partwise><part id='P1'><measure number='1'/></part>"
            "</score-partwise>"
        )
        first = MusicInline(surface="", source="musicxml", score=original)
        once = from_dict(first.to_dict())
        twice = from_dict(once.to_dict())
        assert ET.tostring(once.score, encoding="unicode") == ET.tostring(
            twice.score, encoding="unicode"
        )

    def test_source_field_round_trips(self):
        # ``source`` distinguishes musicxml / jianpu / midi / abc / plain;
        # must survive serialisation even when score is None.
        for source in ("musicxml", "mxl", "jianpu", "midi", "abc", "plain"):
            node = MusicInline(surface="x", source=source)
            restored = from_dict(node.to_dict())
            assert restored.source == source


class TestAllTypesRoundTrip:
    @pytest.mark.parametrize(
        "node",
        [
            HanziChar(surface="字", reading="zi4"),
            Punct(surface="，"),
            LatinWord(surface="hello"),
            LatinAcronym(surface="CPU"),
            Space(surface=" "),
            Unknown(surface="??", reason="bad"),
            MathInline(surface="x^2", source="latex"),
            MusicInline(surface="do re mi", source="plain"),
        ],
    )
    def test_round_trip(self, node):
        payload = node.to_dict()
        restored = from_dict(payload)
        assert type(restored) is type(node)
        assert restored.surface == node.surface


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
