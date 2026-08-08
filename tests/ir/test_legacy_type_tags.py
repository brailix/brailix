"""Retired inline-node type tags do not resolve — and nothing needs them to.

There was a compatibility table in :mod:`brailix.ir.inline` mapping
``hanzi_char`` onto :class:`~brailix.ir.inline.Word` and ``latin_acronym`` onto
:class:`~brailix.ir.inline.LatinWord`, on the reasoning that a project file
outlives the schema that produced it: retire a node type and every document
already on disk still carries its tag, so the tag has to keep resolving or the
work is in the file and the tool won't read it.

The premise is not true of any file this library reads. A front-end that
persists a project keeps the source text and the overrides applied to it, and
recompiles the inline IR every time it opens; ``proofread_json()`` is a
write-only export.
Across the whole tree, the only callers of the IR deserializers are the
deserializers themselves and their own tests — no path reads inline IR back
off disk, so no stored payload can name a retired tag.

The table had also stopped describing what its name claimed: ``Quantity`` and
``Percent`` were retired without entries, because neither could map losslessly
onto anything. "Retired types" was two of four.

So the tags are gone, and this file pins that they are gone rather than that
they work — the same shape as any other removal here. What a retired tag gets
is the error a misspelling gets, which is the honest answer: this library does
not know that type.
"""

from __future__ import annotations

import pytest

from brailix.ir.inline import LatinWord, Word, from_dict, inline_node_for


class TestRetiredTagsAreGone:
    @pytest.mark.parametrize("tag", ["hanzi_char", "latin_acronym"])
    def test_a_retired_tag_no_longer_resolves(self, tag: str) -> None:
        with pytest.raises(KeyError, match=tag):
            inline_node_for(tag)

    @pytest.mark.parametrize("tag", ["hanzi_char", "latin_acronym"])
    def test_a_payload_carrying_one_is_refused_by_name(self, tag: str) -> None:
        """Named in the error, so a caller holding IR JSON from an older build
        is told which tag stopped existing rather than shown a bare KeyError."""
        with pytest.raises(KeyError, match=tag):
            from_dict({"type": tag, "surface": "我"})

    def test_no_alias_table_is_left_behind(self) -> None:
        import brailix.ir.inline as inline_mod

        assert not hasattr(inline_mod, "_LEGACY_TYPE_ALIASES")


class TestTheReplacementTypesStillCarryEverything:
    """What the aliases were protecting is still true of the types themselves:
    neither retired type carried a field its replacement lacks. Kept so the
    claim above stays checked rather than remembered."""

    def test_a_single_character_is_a_one_character_word(self) -> None:
        node = from_dict(
            {"type": "word", "surface": "我", "span": [3, 4], "reading": "wo3"}
        )
        assert isinstance(node, Word)
        assert node.surface == "我"
        assert node.reading == "wo3"
        assert node.span is not None and node.span.to_tuple() == (3, 4)

    def test_an_all_caps_run_is_a_plain_latin_word(self) -> None:
        """The doubled capital sign comes from ``surface.isupper()``, never
        from a type, so an acronym needs no node of its own."""
        node = from_dict({"type": "latin_word", "surface": "CPU", "span": [0, 3]})
        assert isinstance(node, LatinWord)
        assert node.surface == "CPU"


def test_a_genuinely_unknown_tag_raises_the_same_way() -> None:
    """The removal must not have turned lookup into "accept anything", and a
    tag no build ever wrote must read the same as one that was retired — the
    message names what was actually in the payload."""
    with pytest.raises(KeyError, match="not_a_real_node"):
        inline_node_for("not_a_real_node")
