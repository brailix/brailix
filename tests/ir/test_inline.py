import xml.etree.ElementTree as ET

import pytest

from brailix.core.span import Span
from brailix.frontend.zh.tokens import ChineseToken
from brailix.ir.inline import (
    Date,
    HanziMarker,
    InlineNode,
    MathInline,
    MusicInline,
    Number,
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
        w = Word(surface="重庆", reading="chong2 qing4", span=Span(0, 2))
        assert w.reading == "chong2 qing4"

    def test_unknown_with_reason(self):
        u = Unknown(surface="??", reason="bad encoding")
        assert u.reason == "bad encoding"


class TestCompositeStructures:
    def test_date_with_parts(self):
        d = Date(
            surface="2026年5月17日",
            span=Span(0, 11),
            parts=[
                Number(surface="2026"),
                HanziMarker(surface="年", reading="nian2"),
                Number(surface="5"),
                HanziMarker(surface="月", reading="yue4"),
                Number(surface="17"),
                HanziMarker(surface="日", reading="ri4"),
            ],
        )
        assert len(d.parts) == 6
        assert d.parts[0].surface == "2026"


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
                Number(surface="2026"),
                HanziMarker(surface="年", reading="nian2"),
                Number(surface="5"),
                HanziMarker(surface="月", reading="yue4"),
                Number(surface="17"),
                HanziMarker(surface="日", reading="ri4"),
            ],
        )
        payload = d.to_dict()
        assert payload["type"] == "date"
        assert payload["span"] == [0, 11]
        assert len(payload["parts"]) == 6
        assert payload["parts"][0] == {"type": "number", "surface": "2026"}

        restored = from_dict(payload)
        assert isinstance(restored, Date)
        assert len(restored.parts) == 6
        assert isinstance(restored.parts[0], Number)
        assert restored.parts[0].surface == "2026"
        assert isinstance(restored.parts[1], HanziMarker)
        assert restored.parts[1].reading == "nian2"


class TestNestedChildTypesAreEnforced:
    """``Date.parts`` is declared ``list[InlineNode]``, and deserialization
    must hold the declaration.

    It dispatches on the field *name* and rebuilds whatever each entry claims
    to be, so a malformed document could put something that is not a node in
    the list: IR that satisfies the dataclass and breaks every consumer
    walking it, silently and at a distance. The block side already verified
    its nested children (``TableRow.cells``, ``Table.rows``, ``List.items``);
    the inline side is the same idea, and had drifted.
    """

    def test_a_non_node_in_parts_is_refused(self) -> None:
        """``Date.parts`` takes any inline node, so what is checked there is
        that an entry IS one — instead of an ``AttributeError`` from deep
        inside the rebuild."""
        with pytest.raises(TypeError, match="expects InlineNode"):
            from_dict({"type": "date", "surface": "2026", "parts": [42]})


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
        # ET.Element directly, the deserializer takes it as-is — no
        # re-serialization round-trip, and no copy.
        tree = ET.fromstring("<math><mi>x</mi></math>")
        restored = from_dict({
            "type": "math_inline",
            "surface": "x",
            "math": tree,
        })
        assert isinstance(restored, MathInline)
        # Must be the *same* element object — pass-through, not a copy.
        assert restored.math is tree
        assert restored.math[0].tag == "mi"  # already bare: untouched

    def test_namespaced_preparsed_math_element_is_normalized(self):
        # The two legal shapes of one payload — a serialized string and a
        # pre-parsed Element — must deserialize to the same IR. The string
        # branch stripped Clark notation and the Element branch did not, so
        # this XML compiled to ⠰⠭ through one and to a blank cell plus a
        # spurious MATH_UNSUPPORTED_ELEMENT through the other.
        src = "<math xmlns='http://www.w3.org/1998/Math/MathML'><mi>x</mi></math>"
        payload = {"type": "math_inline", "surface": "x", "source": "mathml"}
        from_string = from_dict({**payload, "math": src})
        from_element = from_dict({**payload, "math": ET.fromstring(src)})

        assert from_string.math.tag == from_element.math.tag == "math"
        assert from_string.math[0].tag == from_element.math[0].tag == "mi"

    def test_namespaced_preparsed_music_and_svg_elements_are_normalized(self):
        # Same rule, same implementation, for the other two tree fields:
        # the deserializer must not normalize whichever field someone
        # happened to write a test for.
        from brailix.ir.inline import GraphicInline

        score = ET.fromstring(
            "<score-partwise xmlns='http://www.musicxml.org/ns'>"
            "<part id='P1'/></score-partwise>"
        )
        svg = ET.fromstring(
            "<svg xmlns='http://www.w3.org/2000/svg'><line x1='0'/></svg>"
        )
        music = from_dict({"type": "music_inline", "surface": "", "score": score})
        graphic = from_dict({"type": "graphic_inline", "surface": "", "svg": svg})

        assert isinstance(music, MusicInline)
        assert music.score.tag == "score-partwise"
        assert music.score[0].tag == "part"
        assert isinstance(graphic, GraphicInline)
        assert graphic.svg.tag == "svg"
        assert graphic.svg[0].tag == "line"

    @pytest.mark.parametrize(
        "type_name, field, root_tag",
        [
            ("math_inline", "math", "math"),
            ("music_inline", "score", "score-partwise"),
            ("graphic_inline", "svg", "svg"),
        ],
    )
    def test_preparsed_element_carrying_a_comment_deserializes(
        self, type_name, field, root_tag
    ):
        # A comment / processing instruction is a child node whose ``tag`` is
        # a function, not a string, so the namespace strip this boundary runs
        # raised AttributeError on it — and AttributeError is what the
        # soft-failure boundaries deliberately re-raise, so a third-party
        # adapter handing over a pre-parsed tree with a vendor comment in it
        # crashed the compile. ET.fromstring drops comments, which is why only
        # the (equally supported) pre-parsed shape ever hit this.
        root = ET.Element(f"{{urn:x}}{root_tag}")
        root.append(ET.Comment("vendor note"))
        ET.SubElement(root, "{urn:x}child")

        node = from_dict({"type": type_name, "surface": "", field: root})

        tree = getattr(node, field)
        assert tree.tag == root_tag
        assert tree[0].text == "vendor note"  # comment kept, body intact
        assert tree[1].tag == "child"  # sibling below it still stripped

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


class TestTheHierarchyDiagramMatchesTheClasses:
    """The module docstring draws the node hierarchy, and a reader takes it
    for the class layout — it is the only place the shape is stated.

    It had drifted into saying something false: ``Number`` was drawn indented
    under ``Word``, as though a numeric literal were a kind of prose word,
    while both subclass ``InlineNode`` directly. ``HanziMarker`` was missing
    from it entirely. Prose in general cannot be checked, but "this list is
    the set of node types, all at one level" is a claim with a fact behind
    it, so it is checked here rather than re-read.
    """

    @staticmethod
    def _diagram_rows() -> list[tuple[int, str]]:
        """``(indent, class name)`` for every branch the diagram draws.

        The diagram runs from its root line to the end of the docstring: it is
        the last thing the module says about itself, and pinning the end to
        whatever prose used to follow it made this guard fail with a
        ``ValueError`` the day that prose was removed — reporting the wrong
        problem about the wrong file.
        """
        import re

        import brailix.ir.inline as mod

        doc = mod.__doc__ or ""
        diagram = doc[doc.index("InlineNode (abstract)") :]
        return [
            (len(m.group(1)), m.group(2))
            for line in diagram.splitlines()
            if (m := re.match(r"^(\s*)[├└]── (\w+)", line))
        ]

    def _diagram_names(self) -> list[str]:
        return [name for _indent, name in self._diagram_rows()]

    def _diagram_indents(self) -> set[int]:
        return {indent for indent, _name in self._diagram_rows()}

    def test_it_lists_exactly_the_registered_node_classes(self) -> None:
        from brailix.ir.inline import _INLINE_REGISTRY

        drawn = set(self._diagram_names())
        registered = {cls.__name__ for cls in _INLINE_REGISTRY.values()}
        assert drawn == registered, (
            f"hierarchy diagram and registry disagree — "
            f"only drawn: {sorted(drawn - registered)}; "
            f"only registered: {sorted(registered - drawn)}"
        )

    def test_every_drawn_node_is_a_direct_subclass(self) -> None:
        from brailix.ir import inline as mod
        from brailix.ir.inline import InlineNode

        for name in self._diagram_names():
            cls = getattr(mod, name)
            assert cls.__bases__ == (InlineNode,), (
                f"{name} is drawn as a direct child of InlineNode but its "
                f"bases are {cls.__bases__}"
            )

    def test_the_diagram_is_drawn_flat(self) -> None:
        # One indent level, because there is one level. An indented entry is
        # how the diagram claimed Number was a kind of Word.
        assert len(self._diagram_indents()) == 1
