import pytest

from brailix.core.span import Span
from brailix.ir.document import (
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
