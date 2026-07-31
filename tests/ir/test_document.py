import json
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
        assert p.children == []

    def test_paragraph_with_children(self):
        p = Paragraph(children=[
            Word(surface="我", reading="wo3", span=Span(0, 1)),
            Word(surface="在", reading="zai4", span=Span(1, 2)),
            Punct(surface="。", span=Span(2, 3)),
        ])
        assert len(p.children) == 3

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
        # children=[MusicInline(score=tree)], filled by the pipeline
        # mirrors MathBlock → MathInline.
        s = ScoreBlock(text="1=C 4/4 | 1 2 3 - |", source="jianpu")
        assert s.type == "score"
        assert s.source == "jianpu"
        assert s.children == []

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

    def test_children_only(self):
        p = Paragraph(children=[Word(surface="我", reading="wo3")])
        d = p.to_dict()
        assert d["type"] == "paragraph"
        assert d["children"] == [{"type": "word", "surface": "我", "reading": "wo3"}]


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
            List(ordered=False, items=list(items)).structure_key()
            != List(ordered=True, items=list(items)).structure_key()
        )

    def test_container_shape_in_key(self):
        assert (
            List(items=[ListItem(text="a")]).structure_key()
            != List(
                items=[ListItem(text="a"), ListItem(text="b")]
            ).structure_key()
        )
        assert (
            Table(rows=[TableRow()]).structure_key()
            != Table(rows=[TableRow(), TableRow()]).structure_key()
        )

    def test_nested_container_shape_in_key(self):
        # Row count alone can't tell a 2-column row from a 1-column row; the
        # key recurses into nested blocks so column shape is captured too.
        two_cols = Table(rows=[TableRow(cells=[TableCell(), TableCell()])])
        one_col = Table(rows=[TableRow(cells=[TableCell()])])
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
    of silently swallowing them — otherwise downstream consumers
    introspecting ``cells[i]`` or ``items[i]`` would crash mysteriously."""

    def test_list_with_non_listitem_item_raises(self):
        payload = {
            "type": "list",
            "ordered": False,
            # A Paragraph slipped into items[] — must be rejected, not
            # silently kept as a ListItem-shaped impostor.
            "items": [{"type": "paragraph", "text": "wrong"}],
        }
        with pytest.raises(TypeError, match="ListItem"):
            block_from_dict(payload)

    def test_table_with_non_tablerow_row_raises(self):
        payload = {
            "type": "table",
            "rows": [{"type": "paragraph", "text": "wrong"}],
        }
        with pytest.raises(TypeError, match="TableRow"):
            block_from_dict(payload)

    def test_tablerow_with_non_tablecell_raises(self):
        payload = {
            "type": "table",
            "rows": [
                {"type": "table_row", "cells": [{"type": "paragraph", "text": "x"}]},
            ],
        }
        with pytest.raises(TypeError, match="TableCell"):
            block_from_dict(payload)

    def test_block_children_with_block_entry_raises_on_to_dict(self):
        # ``children`` is typed list[InlineNode]; a structural Block (e.g.
        # ListItem) belongs in items/cells/rows. to_dict can serialise a
        # block child (every Block has to_dict), but block_from_dict rebuilds
        # children via the *inline* registry and would KeyError on the block
        # tag — to_dict/from_dict would not be inverses. Reject at the source
        # so the breakage surfaces where the bad tree is built, not on reload.
        p = Paragraph(children=[ListItem(text="wrong")])
        with pytest.raises(TypeError, match="InlineNode"):
            p.to_dict()


class TestBaseToDictSelfConsistency:
    """``Block.to_dict`` emits JSON-native values and *declared* nested blocks —
    and refuses anything else nested.

    A subclass that adds a nested-block field and declares nothing used to have
    that field silently skipped: saving succeeded, the JSON was valid, and the
    field was gone after a reload. The deserializer was already loud about the
    mirror case (a nested payload with no branch raises), so the pair could only
    fail in the direction that loses data. Now the omission surfaces where the
    tree is built."""

    def test_undeclared_nested_field_raises_instead_of_being_dropped(self):
        @dataclass(slots=True)
        class _Weird(Block):
            type: ClassVar[str] = "weird"
            kids: list = field(default_factory=list)

        with pytest.raises(TypeError, match="structural_fields"):
            _Weird(kids=[ListItem(text="x")]).to_dict()

    def test_an_empty_undeclared_field_is_still_fine(self):
        # Nothing nested is present, so nothing can be lost: an empty list is
        # omitted as a default like any other, and the guard doesn't fire on a
        # field that merely *could* hold blocks.
        @dataclass(slots=True)
        class _Weird(Block):
            type: ClassVar[str] = "weird"
            kids: list = field(default_factory=list)

        assert "kids" not in _Weird().to_dict()

    def test_declaring_the_field_makes_it_round_trip(self):
        # The declaration drives both directions, so a new container type needs
        # no serializer of its own — and the entries come back typed.
        @dataclass(slots=True)
        class _Basket(Block):
            type: ClassVar[str] = "basket"
            structural_fields: ClassVar[dict] = {"kids": ListItem}
            kids: list[ListItem] = field(default_factory=list)

        d = _Basket(kids=[ListItem(text="x")]).to_dict()
        assert [k["text"] for k in d["kids"]] == ["x"]
        json.dumps(d)
        with _registered_block(_Basket):
            back = block_from_dict(d)
        assert isinstance(back, _Basket)
        assert [type(k) for k in back.kids] == [ListItem]
        assert back.kids[0].text == "x"

    def test_a_declared_field_holding_a_foreign_block_is_refused(self):
        # The declaration is enforced on the way back (``_typed_child``), so
        # enforcing it here too is what keeps the two directions agreeing: a
        # Paragraph among the ListItems used to serialise fine and then fail
        # to load, which puts the diagnostic on whoever opens the file rather
        # than on whoever built the tree.
        with pytest.raises(TypeError, match="expects ListItem"):
            List(items=[Paragraph(text="x")]).to_dict()

    def test_a_declared_field_holding_one_block_is_refused(self):
        # The declaration promises a list the deserializer can type-check entry
        # by entry; a bare block would serialise to something it cannot rebuild.
        @dataclass(slots=True)
        class _Odd(Block):
            type: ClassVar[str] = "odd"
            structural_fields: ClassVar[dict] = {"kid": ListItem}
            kid: object = None

        with pytest.raises(TypeError, match="not a list of blocks"):
            _Odd(kid=ListItem(text="x")).to_dict()

    def test_declared_container_emits_and_is_json_native(self):
        d = List(items=[ListItem(text="a")]).to_dict()
        assert [it["text"] for it in d["items"]] == ["a"]
        json.dumps(d)

    def test_every_registered_block_type_round_trips_its_nested_fields(self):
        """Registry-driven, so a new block type is covered by existing here.

        Each declared nested field is filled with one child of the declared
        class and the block is round-tripped; the field has to survive with its
        entries' types intact.
        """
        for name, cls in _BLOCK_REGISTRY.items():
            for field_name, child_cls in cls.structural_fields.items():
                block = cls(**{field_name: [child_cls(text="x")]})
                back = block_from_dict(block.to_dict())
                rebuilt = getattr(back, field_name)
                assert [type(c) for c in rebuilt] == [child_cls], (
                    f"{name}.{field_name} did not round-trip"
                )
                assert rebuilt[0].text == "x"


class TestDocumentIR:
    def test_default_construction(self):
        doc = DocumentIR()
        assert doc.version == "1.0"
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
        assert d["version"] == "1.0"
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
                    "version": "1.0",
                    "type": "paragraph",
                    "metadata": {},
                    "blocks": [],
                }
            )

    def test_rejects_missing_root_type(self):
        with pytest.raises(ValueError, match="document"):
            DocumentIR.from_dict(
                {"version": "1.0", "metadata": {}, "blocks": []}
            )

    def test_rejects_unsupported_version(self):
        """The bug this closes was not the *refusal* of a 2.0 payload but the
        acceptance of one: the version was stored verbatim, the fields 2.0
        added were dropped by ``block_from_dict``, and ``to_dict`` wrote
        ``"2.0"`` back out — a file still claiming a format whose content had
        been silently discarded."""
        with pytest.raises(ValueError, match="unsupported"):
            DocumentIR.from_dict(
                {
                    "version": "2.0",
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
        assert doc.version == "1.0"

    def test_rejects_unsupported_version_at_construction_too(self):
        """Both directions, or the invariant only looks closed: a document
        built with an unloadable version would serialize to a payload its own
        ``from_dict`` refuses."""
        with pytest.raises(ValueError, match="unsupported"):
            DocumentIR(version="2.0")

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
                "version": "1.0",
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

    def test_registered_structural_field_still_works(self):
        from brailix.ir.document import _deserialize_block_value

        rows = _deserialize_block_value(Table, "rows", [{"type": "table_row"}])
        assert isinstance(rows[0], TableRow)
