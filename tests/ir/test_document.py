import json
from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from brailix.core.span import Span
from brailix.ir.document import (
    Block,
    CodeBlock,
    DocumentIR,
    Footnote,
    Heading,
    ImageAlt,
    List,
    ListItem,
    MathBlock,
    MusicBlock,
    Paragraph,
    Quote,
    ScoreBlock,
    Table,
    TableCell,
    TableRow,
    block_for,
    block_from_dict,
)
from brailix.ir.inline import HanziMarker, MusicInline, Number, Punct, Word


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
        # (mirrors MathBlock → MathInline, see ARCHITECTURE.md).
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

    def test_round_trip(self):
        p = Paragraph(
            id="b2",
            span=Span(0, 5),
            children=[Word(surface="我", reading="wo3", span=Span(0, 1))],
        )
        restored = block_from_dict(p.to_dict())
        assert isinstance(restored, Paragraph)
        assert restored.id == "b2"
        assert restored.span == Span(0, 5)
        assert len(restored.children) == 1
        assert isinstance(restored.children[0], Word)


class TestSerializationHeading:
    def test_round_trip(self):
        h = Heading(level=3, text="标题", id="h1")
        restored = block_from_dict(h.to_dict())
        assert isinstance(restored, Heading)
        assert restored.level == 3
        assert restored.text == "标题"


class TestSerializationAlign:
    """``Block.align`` (source-declared centre / right) round-trips and is
    omitted from the serialized form when unset (the default)."""

    def test_center_round_trip(self):
        p = Paragraph(text="居中", align="center")
        d = p.to_dict()
        assert d["align"] == "center"
        restored = block_from_dict(d)
        assert isinstance(restored, Paragraph)
        assert restored.align == "center"

    def test_heading_align_round_trip(self):
        h = Heading(level=2, text="右对齐", align="right")
        restored = block_from_dict(h.to_dict())
        assert isinstance(restored, Heading)
        assert restored.level == 2
        assert restored.align == "right"

    def test_align_absent_when_none(self):
        assert "align" not in Paragraph(text="x").to_dict()


class TestSerializationStructures:
    def test_list_round_trip(self):
        lst = List(
            ordered=True,
            items=[
                ListItem(text="一"),
                ListItem(text="二"),
            ],
        )
        d = lst.to_dict()
        assert d["ordered"] is True
        assert len(d["items"]) == 2
        restored = block_from_dict(d)
        assert isinstance(restored, List)
        assert restored.ordered is True
        assert len(restored.items) == 2
        assert isinstance(restored.items[0], ListItem)

    def test_table_round_trip(self):
        t = Table(rows=[
            TableRow(cells=[TableCell(text="a"), TableCell(text="b")]),
            TableRow(cells=[TableCell(text="c"), TableCell(text="d")]),
        ])
        d = t.to_dict()
        assert len(d["rows"]) == 2
        restored = block_from_dict(d)
        assert isinstance(restored, Table)
        assert len(restored.rows) == 2
        assert isinstance(restored.rows[0], TableRow)
        assert restored.rows[0].cells[1].text == "b"

    def test_quote(self):
        q = Quote(text="名言")
        restored = block_from_dict(q.to_dict())
        assert isinstance(restored, Quote)
        assert restored.text == "名言"


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

    def test_excludes_text_id_and_span(self):
        # The surface hash owns text; id / span must not bust the cache.
        a = Paragraph(text="hello", id="b1", span=Span(0, 5))
        b = Paragraph(text="world", id="b2", span=Span(7, 12))
        assert a.structure_key() == b.structure_key()

    def test_stable_across_calls(self):
        h = Heading(text="x", level=3)
        assert h.structure_key() == h.structure_key()


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
    """The generic ``Block.to_dict`` loop must never emit a raw IR object: a
    subclass that adds a structural field but forgets to override ``to_dict``
    drops that field instead of producing a payload that explodes at
    ``json.dumps``. The shipped containers (List/Table/TableRow) override
    ``to_dict`` and so still emit their structural children."""

    def test_unoverridden_subclass_drops_raw_ir_field(self):
        @dataclass(slots=True)
        class _Weird(Block):
            type: ClassVar[str] = "weird"
            kids: list = field(default_factory=list)

        d = _Weird(kids=[ListItem(text="x")]).to_dict()
        assert "kids" not in d  # skipped, not emitted as a raw ListItem
        json.dumps(d)  # and the payload stays JSON-native

    def test_overridden_container_still_emits_and_is_json_native(self):
        d = List(items=[ListItem(text="a")]).to_dict()
        assert [it["text"] for it in d["items"]] == ["a"]
        json.dumps(d)


class TestSerializationAllBlocks:
    @pytest.mark.parametrize(
        "block",
        [
            Heading(level=1, text="h"),
            Paragraph(text="p"),
            ListItem(text="li"),
            TableCell(text="c"),
            Quote(text="q"),
            Footnote(text="fn", ref="r"),
            CodeBlock(text="code", language="py"),
            MathBlock(text="x+1", source="latex"),
            ScoreBlock(text="1=C | 1 2 3 |", source="jianpu"),
            MusicBlock(text="<music/>", source="musicxml"),
            ImageAlt(text="alt"),
        ],
    )
    def test_round_trip(self, block):
        restored = block_from_dict(block.to_dict())
        assert type(restored) is type(block)
        assert restored.text == block.text


class TestSerializationMusicBlocks:
    def test_score_block_round_trip_preserves_source(self):
        s = ScoreBlock(text="1=C 4/4 | 1 2 3 - |", source="jianpu", id="s1")
        restored = block_from_dict(s.to_dict())
        assert isinstance(restored, ScoreBlock)
        assert restored.id == "s1"
        assert restored.source == "jianpu"
        assert restored.text == "1=C 4/4 | 1 2 3 - |"

    def test_score_block_with_music_inline_child_round_trip(self):
        # End-to-end shape that the pipeline will produce: ScoreBlock
        # whose children=[MusicInline(score=tree)]. Verifies the block
        # registry hands the MusicInline child through inline_from_dict
        # and the ET.Element round-trips cleanly.
        import xml.etree.ElementTree as ET

        tree = ET.fromstring(
            "<score-partwise><part id='P1'><measure number='1'/></part>"
            "</score-partwise>"
        )
        s = ScoreBlock(
            source="musicxml",
            children=[MusicInline(surface="", source="musicxml", score=tree)],
        )
        restored = block_from_dict(s.to_dict())
        assert isinstance(restored, ScoreBlock)
        assert len(restored.children) == 1
        child = restored.children[0]
        assert isinstance(child, MusicInline)
        assert isinstance(child.score, ET.Element)
        assert child.score.tag == "score-partwise"

    def test_music_block_round_trip_preserves_source(self):
        m = MusicBlock(text="raw musicxml here", source="musicxml", id="m1")
        restored = block_from_dict(m.to_dict())
        assert isinstance(restored, MusicBlock)
        assert restored.id == "m1"
        assert restored.source == "musicxml"


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

    def test_round_trip(self):
        original = DocumentIR(
            metadata={"language": "zh-CN", "profile": "cn_current"},
            blocks=[
                Heading(level=1, text="第一章", id="h1"),
                Paragraph(
                    id="p1",
                    children=[
                        Word(surface="我", reading="wo3"),
                        Word(surface="在", reading="zai4"),
                    ],
                ),
            ],
        )
        restored = DocumentIR.from_dict(original.to_dict())
        assert restored.version == "1.0"
        assert restored.metadata == original.metadata
        assert len(restored.blocks) == 2
        assert isinstance(restored.blocks[0], Heading)
        assert restored.blocks[0].level == 1
        assert isinstance(restored.blocks[1], Paragraph)
        assert len(restored.blocks[1].children) == 2
        assert isinstance(restored.blocks[1].children[0], Word)

    def test_nested_structures_round_trip(self):
        """End-to-end: composite inline (Date) inside a Paragraph inside a Doc."""
        from brailix.ir.inline import Date

        doc = DocumentIR(
            blocks=[
                Paragraph(children=[
                    Word(surface="今天"),
                    Date(surface="2026年5月17日", parts=[
                        Number(surface="2026", role="year"),
                        HanziMarker(surface="年", reading="nian2"),
                        Number(surface="5", role="month"),
                        HanziMarker(surface="月", reading="yue4"),
                        Number(surface="17", role="day"),
                        HanziMarker(surface="日", reading="ri4"),
                    ]),
                ])
            ]
        )
        restored = DocumentIR.from_dict(doc.to_dict())
        p = restored.blocks[0]
        assert isinstance(p, Paragraph)
        date_node = p.children[1]
        assert isinstance(date_node, Date)
        assert len(date_node.parts) == 6
        assert date_node.parts[0].role == "year"


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
