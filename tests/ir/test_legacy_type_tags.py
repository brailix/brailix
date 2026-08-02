"""Retired inline-node type tags still load from saved documents.

A project file outlives the schema that produced it. When a node type is
retired, every document already on disk still carries its tag — so the tag has
to keep resolving, or opening a file saved before the change fails outright.
That is the one failure a proofreader cannot work around: the work is in the
file and the tool won't read it.

Two tags exist today, both retired for the same reason — a node type that
carried no information its surface didn't already state:

* ``hanzi_char`` — single characters, now one-character
  :class:`~brailix.ir.inline.Word` nodes;
* ``latin_acronym`` — all-caps Latin runs, now plain
  :class:`~brailix.ir.inline.LatinWord` nodes (the doubled capital sign was
  always decided from ``surface.isupper()``, never from the type).

Neither carried a field its replacement lacks, so stored payloads
deserialize unchanged.
"""

from __future__ import annotations

import pytest

from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Paragraph
from brailix.ir.inline import LatinWord, Word, from_dict, inline_node_for


class TestRetiredTagResolves:
    def test_tag_maps_to_its_replacement(self) -> None:
        assert inline_node_for("hanzi_char") is Word

    def test_payload_deserializes_with_every_field_intact(self) -> None:
        node = from_dict(
            {
                "type": "hanzi_char",
                "surface": "我",
                "span": [3, 4],
                "reading": "wo3",
            }
        )
        assert isinstance(node, Word)
        assert node.surface == "我"
        assert node.reading == "wo3"
        assert node.span is not None
        assert node.span.to_tuple() == (3, 4)

    def test_latin_acronym_maps_to_the_plain_latin_word(self) -> None:
        assert inline_node_for("latin_acronym") is LatinWord

    def test_a_saved_acronym_loads_identical_to_a_fresh_node(self) -> None:
        """Equality of the whole node, not just its class.

        Every field being equal is what makes the rest follow: the doubled
        capital sign comes from ``surface.isupper()``, so a node that equals
        a freshly-built one cannot translate differently.
        """
        loaded = from_dict(
            {"type": "latin_acronym", "surface": "CPU", "span": [0, 3]}
        )
        assert loaded == LatinWord(surface="CPU", span=Span(0, 3))

    def test_a_genuinely_unknown_tag_still_raises(self) -> None:
        # The alias table must not turn into a blanket "accept anything":
        # a tag no build ever wrote is a real error, and the message names
        # what was actually in the file.
        with pytest.raises(KeyError, match="not_a_real_node"):
            inline_node_for("not_a_real_node")


class TestWholeDocumentRoundTrip:
    def test_a_document_saved_before_the_merge_still_loads(self) -> None:
        """The scenario as a user meets it: an existing project file.

        Built as the payload a pre-merge build would have written, rather
        than by serializing today's IR — serializing first would only prove
        the current writer agrees with the current reader.
        """
        legacy = {
            "type": "document",
            "metadata": {},
            "blocks": [
                {
                    "type": "paragraph",
                    "text": "我在重庆",
                    "children": [
                        {"type": "hanzi_char", "surface": "我", "span": [0, 1],
                         "reading": "wo3"},
                        {"type": "space", "surface": "", "span": [1, 1]},
                        {"type": "hanzi_char", "surface": "在", "span": [1, 2],
                         "reading": "zai4"},
                        {"type": "space", "surface": "", "span": [2, 2]},
                        {"type": "word", "surface": "重庆", "span": [2, 4],
                         "reading": "chong2 qing4"},
                    ],
                }
            ]
        }
        doc = DocumentIR.from_dict(legacy)

        block = doc.blocks[0]
        assert isinstance(block, Paragraph)
        assert [c.surface for c in block.children] == ["我", "", "在", "", "重庆"]
        words = [c for c in block.children if isinstance(c, Word)]
        assert [w.reading for w in words] == ["wo3", "zai4", "chong2 qing4"]

    def test_resaving_writes_the_current_tag(self) -> None:
        # Nothing emits the retired tag any more, so a file quietly migrates
        # the first time it is saved — the alias is a read-side bridge, not a
        # format the library keeps producing.
        node = from_dict({"type": "hanzi_char", "surface": "我"})
        assert node.to_dict()["type"] == "word"
