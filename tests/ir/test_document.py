import json
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from brailix.core.span import Span
from brailix.ir.document import (
    _BLOCK_REGISTRY,
    _SUPPORTED_IR_VERSIONS,
    Block,
    CodeBlock,
    DocumentIR,
    Footnote,
    Heading,
    List,
    ListItem,
    MathBlock,
    MusicBlock,
    Paragraph,
    ScoreBlock,
    Table,
    TableCell,
    TableRow,
    block_for,
    block_from_dict,
)
from brailix.ir.inline import Punct, Word


@contextmanager
def _registered_block(cls):
    """Make a test-local ``Block`` subclass rebuildable by its type tag.

    ``block_from_dict`` dispatches through the registry, so a type declared
    inside a test has to be in it for the round trip; removed again on exit so
    it never leaks into another test's view of the registry.
    """
    _BLOCK_REGISTRY[cls.type] = cls
    try:
        yield
    finally:
        _BLOCK_REGISTRY.pop(cls.type, None)


class TestConstruction:
    def test_paragraph_with_text(self):
        p = Paragraph(text="我在重庆。")
        assert p.type == "paragraph"
        assert p.text == "我在重庆。"
        assert p.inlines == []

    def test_paragraph_with_children(self):
        p = Paragraph(inlines=[
            Word(surface="我", reading="wo3", span=Span(0, 1)),
            Word(surface="在", reading="zai4", span=Span(1, 2)),
            Punct(surface="。", span=Span(2, 3)),
        ])
        assert len(p.inlines) == 3

    def test_heading_with_level(self):
        h = Heading(level=2, text="第二章")
        assert h.level == 2

    def test_codeblock_with_language(self):
        c = CodeBlock(text="print(1)", language="python")
        assert c.language == "python"

    def test_math_block_with_source(self):
        m = MathBlock(text="x^2+y^2", source="latex")
        assert m.source == "latex"

    def test_score_block_with_source(self):
        # ScoreBlock holds only source — MusicXML tree lives in
        # inlines=[MusicInline(score=tree)], filled by the pipeline
        # mirrors MathBlock → MathInline.
        s = ScoreBlock(text="1=C 4/4 | 1 2 3 - |", source="jianpu")
        assert s.type == "score"
        assert s.source == "jianpu"
        assert s.inlines == []

    def test_music_block_with_source(self):
        m = MusicBlock(text="<music-error/>", source="musicxml")
        assert m.type == "music_block"
        assert m.source == "musicxml"

    def test_footnote_with_ref(self):
        f = Footnote(ref="note-1", text="脚注内容")
        assert f.ref == "note-1"


class TestSerializationParagraph:
    def test_text_only(self):
        d = Paragraph(text="hi", id="b1").to_dict()
        assert d == {"type": "paragraph", "id": "b1", "text": "hi"}

    def test_inlines_only(self):
        p = Paragraph(inlines=[Word(surface="我", reading="wo3")])
        d = p.to_dict()
        assert d["type"] == "paragraph"
        assert d["inlines"] == [{"type": "word", "surface": "我", "reading": "wo3"}]


class TestSerializationAlign:
    """``Block.align`` (source-declared centre / right) is omitted from the
    serialized form when unset — the default carries no key."""

    def test_align_absent_when_none(self):
        assert "align" not in Paragraph(text="x").to_dict()


class TestStructureKey:
    """``Block.structure_key()`` captures rendering-affecting shape beyond
    the text surface, so a text-only cache key (``block_hash``) can compose
    with it without same-text blocks of different shape colliding."""

    def test_type_distinguishes_same_text_blocks(self):
        assert (
            Heading(text="x", level=1).structure_key()
            != Paragraph(text="x").structure_key()
        )

    def test_heading_level_in_key(self):
        assert (
            Heading(text="x", level=1).structure_key()
            != Heading(text="x", level=2).structure_key()
        )

    def test_list_ordering_in_key(self):
        items = [ListItem(text="a"), ListItem(text="b")]
        assert (
            List(ordered=False, blocks=list(items)).structure_key()
            != List(ordered=True, blocks=list(items)).structure_key()
        )

    def test_container_shape_in_key(self):
        assert (
            List(blocks=[ListItem(text="a")]).structure_key()
            != List(blocks=[ListItem(text="a"), ListItem(text="b")]
            ).structure_key()
        )
        assert (
            Table(blocks=[TableRow()]).structure_key()
            != Table(blocks=[TableRow(), TableRow()]).structure_key()
        )

    def test_nested_container_shape_in_key(self):
        # Row count alone can't tell a 2-column row from a 1-column row; the
        # key recurses into nested blocks so column shape is captured too.
        two_cols = Table(blocks=[TableRow(blocks=[TableCell(), TableCell()])])
        one_col = Table(blocks=[TableRow(blocks=[TableCell()])])
        assert two_cols.structure_key() != one_col.structure_key()

    def test_align_and_source_in_key(self):
        assert (
            Paragraph(text="x").structure_key()
            != Paragraph(text="x", align="center").structure_key()
        )
        assert (
            MathBlock(text="E", source="latex").structure_key()
            != MathBlock(text="E", source="mathml").structure_key()
        )

    # Insensitivity to text / id / span (and call-to-call stability) is
    # property-tested over every block type in
    # test_serialization_properties.py::TestStructureKey; the examples
    # above pin the DISCRIMINATION side (which differences must change
    # the key).


class TestTypedChildValidation:
    """JSON round-trips must reject obviously wrong child types instead
    of silently swallowing them — otherwise downstream consumers walking a
    row's cells would crash mysteriously.

    What each entry must be is the owning class's ``child_type``; the field
    name is ``blocks`` whatever the container."""

    def test_list_with_non_listitem_item_raises(self):
        payload = {
            "type": "list",
            "ordered": False,
            # A Paragraph slipped into a list's items — must be rejected, not
            # silently kept as a ListItem-shaped impostor.
            "blocks": [{"type": "paragraph", "text": "wrong"}],
        }
        with pytest.raises(TypeError, match="ListItem"):
            block_from_dict(payload)

    def test_table_with_non_tablerow_row_raises(self):
        payload = {
            "type": "table",
            "blocks": [{"type": "paragraph", "text": "wrong"}],
        }
        with pytest.raises(TypeError, match="TableRow"):
            block_from_dict(payload)

    def test_tablerow_with_non_tablecell_raises(self):
        payload = {
            "type": "table",
            "blocks": [
                {"type": "table_row", "blocks": [{"type": "paragraph", "text": "x"}]},
            ],
        }
        with pytest.raises(TypeError, match="TableCell"):
            block_from_dict(payload)

    def test_a_block_that_declares_no_child_type_refuses_nested_blocks(self):
        # A paragraph has no nested blocks, so a payload giving it some says
        # nothing about what they are — and the loader has nothing to check
        # them against. Refuse rather than build a paragraph holding whatever
        # the payload's tags happened to name.
        with pytest.raises(TypeError, match="child_type"):
            block_from_dict(
                {"type": "paragraph", "blocks": [{"type": "paragraph"}]}
            )

    def test_block_inlines_with_block_entry_raises_on_to_dict(self):
        # ``inlines`` is typed list[InlineNode]; a nested Block belongs in
        # ``blocks``. to_dict can serialise a block (every Block has to_dict),
        # but block_from_dict rebuilds ``inlines`` via the *inline* registry
        # and would KeyError on the block tag — to_dict/from_dict would not be
        # inverses. Reject at the source so the breakage surfaces where the bad
        # tree is built, not on reload.
        p = Paragraph(inlines=[ListItem(text="wrong")])
        with pytest.raises(TypeError, match="InlineNode"):
            p.to_dict()


class TestBaseToDictSelfConsistency:
    """``Block.to_dict`` emits JSON-native values, the two content fields, and
    refuses anything else nested.

    A subclass that added a nested-block field of its own used to have it
    silently skipped: saving succeeded, the JSON was valid, and the field was
    gone after a reload. There is now one field for nested blocks, so there is
    nowhere else for them to be — and putting them anywhere else says so."""

    def test_a_nested_field_of_its_own_raises_instead_of_being_dropped(self):
        @dataclass(slots=True)
        class _Weird(Block):
            type: ClassVar[str] = "weird"
            kids: list = field(default_factory=list)

        with pytest.raises(TypeError, match="``blocks``"):
            _Weird(kids=[ListItem(text="x")]).to_dict()

    def test_an_empty_field_of_its_own_is_still_fine(self):
        # Nothing nested is present, so nothing can be lost: an empty list is
        # omitted as a default like any other, and the guard doesn't fire on a
        # field that merely *could* hold blocks.
        @dataclass(slots=True)
        class _Weird(Block):
            type: ClassVar[str] = "weird"
            kids: list = field(default_factory=list)

        assert "kids" not in _Weird().to_dict()

    def test_declaring_child_type_makes_blocks_round_trip(self):
        # One declaration drives both directions, so a new container type needs
        # no serializer of its own — and the entries come back typed.
        @dataclass(slots=True)
        class _Basket(Block):
            # ``type`` shadows the builtin inside a Block body, so a
            # ``type[Block]`` annotation written here would index the tag
            # string — the reason the IR module aliases it at module scope.
            type: ClassVar[str] = "basket"
            child_type: ClassVar = ListItem

        d = _Basket(blocks=[ListItem(text="x")]).to_dict()
        assert [k["text"] for k in d["blocks"]] == ["x"]
        json.dumps(d)
        with _registered_block(_Basket):
            back = block_from_dict(d)
        assert isinstance(back, _Basket)
        assert [type(k) for k in back.blocks] == [ListItem]
        assert back.blocks[0].text == "x"

    def test_blocks_holding_a_foreign_block_is_refused(self):
        # The declaration is enforced on the way back (``_typed_child``), so
        # enforcing it here too is what keeps the two directions agreeing: a
        # Paragraph among the ListItems used to serialise fine and then fail
        # to load, which puts the diagnostic on whoever opens the file rather
        # than on whoever built the tree.
        with pytest.raises(TypeError, match="expects ListItem"):
            List(blocks=[Paragraph(text="x")]).to_dict()

    def test_nested_blocks_with_no_child_type_are_refused(self):
        # Nothing says what they must be, and the loader would refuse to
        # rebuild them — so writing them is refused too.
        with pytest.raises(TypeError, match="child_type"):
            Paragraph(blocks=[ListItem(text="x")]).to_dict()

    def test_a_container_emits_and_is_json_native(self):
        d = List(blocks=[ListItem(text="a")]).to_dict()
        assert [it["text"] for it in d["blocks"]] == ["a"]
        json.dumps(d)

    def test_every_container_block_type_round_trips_its_nested_blocks(self):
        """Registry-driven, so a new container type is covered by existing here.

        Each block that declares a ``child_type`` is given one child of that
        class and round-tripped; the nesting has to survive with its entries'
        types intact.
        """
        containers = [
            (name, cls)
            for name, cls in _BLOCK_REGISTRY.items()
            if cls.child_type is not None
        ]
        assert {n for n, _ in containers} == {"list", "table", "table_row"}
        for name, cls in containers:
            block = cls(blocks=[cls.child_type(text="x")])
            back = block_from_dict(block.to_dict())
            assert [type(c) for c in back.blocks] == [cls.child_type], (
                f"{name} did not round-trip its nested blocks"
            )
            assert back.blocks[0].text == "x"


class TestDocumentIR:
    def test_default_construction(self):
        doc = DocumentIR()
        assert doc.version == "2.0"
        assert doc.metadata == {}
        assert doc.blocks == []

    def test_with_metadata(self):
        doc = DocumentIR(metadata={"language": "zh-CN", "profile": "cn_current"})
        assert doc.metadata["language"] == "zh-CN"

    def test_to_dict_shape(self):
        doc = DocumentIR(
            metadata={"language": "zh-CN"},
            blocks=[Heading(level=1, text="标题"), Paragraph(text="正文")],
        )
        d = doc.to_dict()
        assert d["version"] == "2.0"
        assert d["type"] == "document"
        assert d["metadata"] == {"language": "zh-CN"}
        assert len(d["blocks"]) == 2
        assert d["blocks"][0]["type"] == "heading"
        assert d["blocks"][1]["type"] == "paragraph"

    def test_round_trip_restores_version_and_blocks(self):
        doc = DocumentIR(
            metadata={"language": "zh-CN"},
            blocks=[Heading(level=1, text="标题"), Paragraph(text="正文")],
        )
        back = DocumentIR.from_dict(doc.to_dict())
        assert back.version == doc.version
        assert [type(b) for b in back.blocks] == [Heading, Paragraph]


class TestDocumentIRLoadBoundary:
    """``from_dict`` is a *boundary*: what it accepts, it must be able to
    represent. Two payload-level facts used to go unread — the root type tag
    and the format version — and each let a document load as something it was
    not."""

    def test_rejects_wrong_root_type(self):
        # A block payload is not a document, however parseable its shape.
        with pytest.raises(ValueError, match="document"):
            DocumentIR.from_dict(
                {
                    "version": "2.0",
                    "type": "paragraph",
                    "metadata": {},
                    "blocks": [],
                }
            )

    def test_rejects_missing_root_type(self):
        with pytest.raises(ValueError, match="document"):
            DocumentIR.from_dict(
                {"version": "2.0", "metadata": {}, "blocks": []}
            )

    def test_rejects_unsupported_version(self):
        """The bug this closes was not the *refusal* of a future payload but
        the acceptance of one: the version was stored verbatim, the fields it
        added were dropped by ``block_from_dict``, and ``to_dict`` wrote the
        version back out — a file still claiming a format whose content had
        been silently discarded."""
        with pytest.raises(ValueError, match="unsupported"):
            DocumentIR.from_dict(
                {
                    "version": "3.0",
                    "type": "document",
                    "metadata": {},
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": "x",
                            "semantic_annotations": {"reviewed": True},
                        }
                    ],
                }
            )

    def test_rejects_the_retired_1_0_format(self):
        """1.0 is refused by name, not migrated and not half-read.

        Its blocks put a math / music / graphic tree one level down, in a
        ``children`` list holding a carrier node, and no carrier node type
        exists any more. Nothing in the library reads a document-IR payload
        back — it is written as an export, and a ``.blx`` project stores source
        plus overrides and recompiles — so there is no in-tree reader for a
        migration to serve, and reading one anyway would drop the formula and
        say nothing."""
        with pytest.raises(ValueError, match="unsupported"):
            DocumentIR.from_dict(
                {"version": "1.0", "type": "document", "metadata": {}, "blocks": []}
            )

    @pytest.mark.parametrize(
        "version",
        [[], {}, 1, 1.0, True, None, ["1.0"], {"1.0": True}],
        ids=["list", "dict", "int", "float", "bool", "none", "list-of-str",
             "dict-keyed"],
    )
    def test_rejects_non_string_version_as_value_error(self, version):
        """A payload is arbitrary decoded JSON, so ``version`` can be an array
        or an object — and the membership test *hashes* what it is given, so
        those two left this boundary as ``TypeError: unhashable type: 'list'``
        rather than the ``ValueError`` the boundary documents (and callers
        catch). The hashable non-strings were already refused, but by accident
        of not matching a string; they are pinned here so the type rule is what
        holds, not the coincidence."""
        with pytest.raises(ValueError, match="must be a string"):
            DocumentIR.from_dict(
                {
                    "type": "document",
                    "version": version,
                    "metadata": {},
                    "blocks": [],
                }
            )
        with pytest.raises(ValueError, match="must be a string"):
            DocumentIR(version=version)

    def test_absent_version_still_defaults(self):
        """Only a *missing* key falls back to the default. An explicit
        ``null`` is a malformed payload, not an omission — the parametrized
        rejection above covers it."""
        doc = DocumentIR.from_dict(
            {"type": "document", "metadata": {}, "blocks": []}
        )
        assert doc.version == "2.0"

    def test_rejects_unsupported_version_at_construction_too(self):
        """Both directions, or the invariant only looks closed: a document
        built with an unloadable version would serialize to a payload its own
        ``from_dict`` refuses."""
        with pytest.raises(ValueError, match="unsupported"):
            DocumentIR(version="1.0")

    def test_every_supported_version_actually_loads(self):
        """Guard against the set and the loader drifting apart — an entry added
        here without a code path is a promise nothing keeps."""
        for version in _SUPPORTED_IR_VERSIONS:
            doc = DocumentIR.from_dict(
                {
                    "version": version,
                    "type": "document",
                    "metadata": {},
                    "blocks": [{"type": "paragraph", "text": "x"}],
                }
            )
            assert doc.version == version
            assert doc.to_dict()["version"] == version

    def test_unknown_block_fields_are_still_tolerated(self):
        """The version gate is what makes per-field tolerance safe, not a
        replacement for it: inside a supported version an unknown field is
        foreign data, and dropping it stays the documented behaviour."""
        doc = DocumentIR.from_dict(
            {
                "version": "2.0",
                "type": "document",
                "metadata": {},
                "blocks": [{"type": "paragraph", "text": "x", "future": "y"}],
            }
        )
        assert [type(b) for b in doc.blocks] == [Paragraph]
        assert doc.blocks[0].text == "x"


class TestRegistry:
    def test_lookup_known(self):
        assert block_for("heading") is Heading
        assert block_for("table") is Table
        assert block_for("score") is ScoreBlock
        assert block_for("music_block") is MusicBlock

    def test_lookup_unknown_raises(self):
        with pytest.raises(KeyError):
            block_for("nope")

    def test_block_from_dict_rejects_missing_type(self):
        with pytest.raises(ValueError):
            block_from_dict({"text": "x"})

    def test_block_from_dict_ignores_unknown_fields(self):
        b = block_from_dict({"type": "paragraph", "text": "x", "future": "y"})
        assert isinstance(b, Paragraph)

    def test_block_from_dict_rejects_malformed_span(self):
        # Block spans share the canonical Span.from_tuple boundary — a malformed
        # length raises rather than being stored raw as a list.
        with pytest.raises(ValueError):
            block_from_dict({"type": "paragraph", "text": "x", "span": [0, 1, 2]})


class TestDeserializeBlockGuard:
    """Block deserialization dispatches on field name; a nested IR payload
    (``dict`` / list of ``dict``) with no branch must raise rather than
    silently round-trip as raw dicts — the from_dict-side mirror of
    :class:`TestBaseToDictSelfConsistency`."""

    def test_unregistered_list_of_dict_field_raises(self):
        from brailix.ir.document import _deserialize_block_value

        with pytest.raises(ValueError, match="nested IR payload"):
            _deserialize_block_value(Paragraph, "kids", [{"type": "paragraph"}])

    def test_unregistered_dict_field_raises(self):
        from brailix.ir.document import _deserialize_block_value

        with pytest.raises(ValueError, match="nested IR payload"):
            _deserialize_block_value(Paragraph, "kid", {"type": "paragraph"})

    def test_scalar_field_passes_through(self):
        from brailix.ir.document import _deserialize_block_value

        assert _deserialize_block_value(Heading, "level", 3) == 3

    def test_the_blocks_field_still_works(self):
        from brailix.ir.document import _deserialize_block_value

        rows = _deserialize_block_value(Table, "blocks", [{"type": "table_row"}])
        assert isinstance(rows[0], TableRow)


class TestEmbeddedBlockTree:
    """A math / music / graphic block carries its parsed tree itself.

    It used to hang one level down, on a carrier inline node that was the
    block's only child — a node type per domain whose whole job was to move
    the tree from the frontend to the backend. These are the cases that used
    to be asserted on the inline side; they are the block's now, and they go
    through the same shared loader (``_serde.deserialize_xml_tree``) the one
    remaining inline tree field does.
    """

    def test_none_tree_is_omitted_from_the_payload(self):
        block = ScoreBlock(text="do re mi", source="plain")
        payload = block.to_dict()
        assert "tree" not in payload
        assert block_from_dict(payload).tree is None

    def test_tree_round_trips_as_xml_text(self):
        block = ScoreBlock(
            text="", source="musicxml", tree=ET.fromstring("<score-partwise/>")
        )
        payload = block.to_dict()
        assert payload["tree"] == "<score-partwise />"
        restored = block_from_dict(payload)
        assert isinstance(restored, ScoreBlock)
        assert restored.tree is not None
        assert restored.tree.tag == "score-partwise"

    def test_a_dict_tree_value_is_refused(self):
        # tree must be None / str / ET.Element. A dict raises so malformed
        # payloads fail loudly instead of silently storing junk.
        with pytest.raises(ValueError, match="ScoreBlock.tree"):
            block_from_dict(
                {"type": "score", "tree": {"kind": "note", "pitch": "C"}}
            )

    def test_a_malformed_xml_string_names_its_format(self):
        with pytest.raises(ValueError, match="not well-formed MusicXML"):
            block_from_dict({"type": "score", "tree": "<score-partwise>"})

    def test_the_format_named_is_the_block_s_own(self):
        with pytest.raises(ValueError, match="not well-formed SVG"):
            block_from_dict({"type": "graphic", "tree": "<svg>"})

    def test_an_explicit_none_is_accepted(self):
        assert block_from_dict({"type": "math_block", "tree": None}).tree is None

    def test_a_preparsed_element_passes_through_without_a_copy(self):
        tree = ET.fromstring("<math><mi>x</mi></math>")
        restored = block_from_dict({"type": "math_block", "tree": tree})
        assert restored.tree is tree

    @pytest.mark.parametrize(
        ("type_name", "root_tag"),
        [
            ("math_block", "math"),
            ("score", "score-partwise"),
            ("graphic", "svg"),
        ],
    )
    def test_a_preparsed_namespaced_tree_is_stripped(self, type_name, root_tag):
        # The backends dispatch on bare local names, so a Clark-notated tag
        # matches nothing and degrades to blank cells plus a misleading
        # "unsupported element" warning. A comment rides along because its
        # ``tag`` is a function rather than a string, which is what once made
        # the strip raise AttributeError — the one exception class the
        # soft-failure boundaries deliberately re-raise.
        root = ET.Element(f"{{urn:x}}{root_tag}")
        root.append(ET.Comment("vendor note"))
        ET.SubElement(root, "{urn:x}child")

        tree = block_from_dict({"type": type_name, "tree": root}).tree

        assert tree.tag == root_tag
        assert tree[0].text == "vendor note"
        assert tree[1].tag == "child"

    def test_the_tree_stays_out_of_the_structure_key(self):
        """``repr`` of an Element carries its memory address, so folding the
        tree into the cache key would mint a different key for every parse of
        the same formula and no cache would ever hit."""
        parsed = MathBlock(text="x", source="latex", tree=ET.fromstring("<math/>"))
        bare = MathBlock(text="x", source="latex")
        assert parsed.structure_key() == bare.structure_key()
