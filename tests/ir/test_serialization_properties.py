"""Property-based tests for IR serialization round-trips.

Documents survive as JSON (.blx payloads, caches, proofread exports), so
the one contract everything downstream leans on is: **serialization is a
lossless, stable, JSON-native bijection** over the IR's value space —

* ``from_dict(to_dict(x))`` rebuilds the same node type;
* re-serializing the rebuilt node yields the *identical* payload
  (stability — a document must not drift on every open/save cycle);
* every payload is JSON-native (``json.dumps`` succeeds — no raw IR
  objects, no ET elements, no bytes leak in);
* ``DocumentIR.assets`` (binary side-payloads) is deliberately EXCLUDED
  from the text-IR payload and therefore dropped by a round-trip.

These are checked over *generated* forests covering every registered
inline node type (composites and ET-tree carriers included), every block
type (containers included), and the braille IR. Boundary behaviours
(rejecting malformed spans, typed-child validation, xmlns stripping, the
deserialize guards) stay example-tested in test_inline.py /
test_document.py / test_braille.py — this module owns the round-trip
family those files no longer enumerate case by case.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import fields, is_dataclass

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core.span import Span
from brailix.ir.braille import BrailleBlock, BrailleCell, BrailleDocument, BrailleSequence
from brailix.ir.document import (
    Block,
    CodeBlock,
    DocumentIR,
    Footnote,
    GraphicBlock,
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
    block_from_dict,
)
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Date,
    GraphicInline,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    PhoneticInline,
    Punct,
    Quantity,
    Space,
    Unknown,
    Word,
    from_dict,
)

# --- shared field strategies ---------------------------------------------------

_surfaces = st.text(
    alphabet=st.sampled_from(list("我在重庆好中文abcXY012,。!? $%é")), max_size=8
)
_opt_short = st.one_of(st.none(), st.text(alphabet=st.sampled_from(list("abcn4声")), min_size=1, max_size=6))


@st.composite
def _spans(draw: st.DrawFn) -> Span | None:
    if draw(st.booleans()):
        return None
    start = draw(st.integers(0, 40))
    return Span(start, start + draw(st.integers(0, 10)))


@st.composite
def _trees(draw: st.DrawFn, root: str) -> ET.Element:
    # Small XML trees with attributes and text. No ``xmlns`` attribute on
    # purpose: the IR boundary strips namespaces on reparse (a documented,
    # example-tested normalization), so an xmlns-bearing payload is stable
    # only after one trip — out of scope for the stability property.
    elem = ET.Element(root)
    for _ in range(draw(st.integers(0, 2))):
        child = ET.SubElement(
            elem,
            draw(st.sampled_from(["mi", "mo", "part", "measure", "rect", "text"])),
            attrib=draw(st.sampled_from([{}, {"id": "P1"}, {"number": "1"}])),
        )
        child.text = draw(st.sampled_from([None, "x", "+", "C4", "标"]))
    return elem


# --- inline nodes ---------------------------------------------------------------


@st.composite
def _leaf_inline_nodes(draw: st.DrawFn) -> InlineNode:
    kind = draw(
        st.sampled_from(
            [
                "word", "hanzi_char", "number", "hanzi_marker", "punct",
                "latin_word", "latin_acronym", "code_inline", "phonetic_inline",
                "space", "connector", "unknown", "math", "music", "graphic",
            ]
        )
    )
    surface = draw(_surfaces)
    span = draw(_spans())
    if kind == "word":
        return Word(
            surface=surface,
            span=span,
            reading=draw(_opt_short),
            pos=draw(_opt_short),
            confidence=draw(st.one_of(st.none(), st.floats(0, 1, allow_nan=False))),
        )
    if kind == "hanzi_char":
        return HanziChar(surface=surface, span=span, reading=draw(_opt_short))
    if kind == "number":
        return Number(surface=surface, span=span, role=draw(_opt_short))
    if kind == "hanzi_marker":
        return HanziMarker(surface=surface, span=span, reading=draw(_opt_short))
    if kind == "punct":
        return Punct(surface=surface, span=span)
    if kind == "latin_word":
        return LatinWord(surface=surface, span=span)
    if kind == "latin_acronym":
        return LatinAcronym(surface=surface, span=span)
    if kind == "code_inline":
        return CodeInline(surface=surface, span=span)
    if kind == "phonetic_inline":
        return PhoneticInline(surface=surface, span=span)
    if kind == "space":
        return Space(surface=surface, span=span)
    if kind == "connector":
        return Connector(surface=surface, span=span)
    if kind == "unknown":
        return Unknown(surface=surface, span=span, reason=draw(_opt_short))
    if kind == "math":
        return MathInline(
            surface=surface,
            span=span,
            source=draw(st.sampled_from(["latex", "mathml", "plain"])),
            math=draw(st.one_of(st.none(), _trees("math"))),
        )
    if kind == "music":
        return MusicInline(
            surface=surface,
            span=span,
            source=draw(st.sampled_from(["musicxml", "mxl", "midi", "abc", "plain"])),
            score=draw(st.one_of(st.none(), _trees("score-partwise"))),
        )
    return GraphicInline(
        surface=surface,
        span=span,
        source=draw(st.sampled_from(["svg", "primitives", "figure", "image"])),
        svg=draw(st.one_of(st.none(), _trees("svg"))),
    )


@st.composite
def inline_nodes(draw: st.DrawFn) -> InlineNode:
    kind = draw(st.sampled_from(["leaf", "leaf", "date", "quantity", "percent"]))
    if kind == "leaf":
        return draw(_leaf_inline_nodes())
    surface = draw(_surfaces)
    if kind == "date":
        return Date(
            surface=surface,
            span=draw(_spans()),
            parts=draw(st.lists(_leaf_inline_nodes(), max_size=3)),
        )
    number = draw(st.one_of(st.none(), _leaf_inline_nodes().filter(lambda n: isinstance(n, Number))))
    if kind == "quantity":
        return Quantity(
            surface=surface,
            number=number,
            unit=draw(_opt_short),
            unit_canonical=draw(_opt_short),
        )
    return Percent(surface=surface, number=number)


# --- blocks ---------------------------------------------------------------------


@st.composite
def _leaf_blocks(draw: st.DrawFn) -> Block:
    kind = draw(
        st.sampled_from(
            [
                "heading", "paragraph", "list_item", "table_cell", "quote",
                "footnote", "code_block", "math_block", "score", "music_block",
                "image_alt", "graphic",
            ]
        )
    )
    common: dict = {
        "id": draw(st.one_of(st.none(), st.just("b1"))),
        "text": draw(st.one_of(st.none(), _surfaces)),
        "span": draw(_spans()),
        "align": draw(st.sampled_from([None, "center", "right"])),
        "children": draw(st.lists(inline_nodes(), max_size=2)),
    }
    if kind == "heading":
        return Heading(level=draw(st.integers(1, 6)), **common)
    if kind == "paragraph":
        return Paragraph(**common)
    if kind == "list_item":
        return ListItem(**common)
    if kind == "table_cell":
        return TableCell(**common)
    if kind == "quote":
        return Quote(**common)
    if kind == "footnote":
        return Footnote(ref=draw(_opt_short), **common)
    if kind == "code_block":
        return CodeBlock(language=draw(_opt_short), **common)
    if kind == "math_block":
        return MathBlock(source=draw(st.sampled_from(["latex", "mathml", "plain"])), **common)
    if kind == "score":
        return ScoreBlock(source=draw(st.sampled_from(["musicxml", "jianpu", "plain"])), **common)
    if kind == "music_block":
        return MusicBlock(source=draw(st.sampled_from(["musicxml", "plain"])), **common)
    if kind == "image_alt":
        return ImageAlt(target=draw(st.one_of(st.none(), st.just("media/image1.png"))), **common)
    return GraphicBlock(source=draw(st.sampled_from(["svg", "image"])), **common)


@st.composite
def blocks(draw: st.DrawFn) -> Block:
    kind = draw(st.sampled_from(["leaf", "leaf", "leaf", "list", "table"]))
    if kind == "leaf":
        return draw(_leaf_blocks())
    if kind == "list":
        return List(
            ordered=draw(st.booleans()),
            items=draw(
                st.lists(
                    _leaf_blocks().filter(lambda b: isinstance(b, ListItem)),
                    max_size=3,
                )
            ),
        )
    return Table(
        rows=draw(
            st.lists(
                st.builds(
                    TableRow,
                    cells=st.lists(
                        _leaf_blocks().filter(lambda b: isinstance(b, TableCell)),
                        max_size=2,
                    ),
                ),
                max_size=2,
            )
        )
    )


# --- braille IR -----------------------------------------------------------------


@st.composite
def _braille_cells(draw: st.DrawFn) -> BrailleCell:
    dots = tuple(draw(st.sets(st.integers(1, 8), max_size=5)))
    return BrailleCell(
        dots=dots,
        role=draw(st.sampled_from([None, "space", "zh_initial", "number_sign", "line_break"])),
        source_span=draw(_spans()),
        source_text=draw(st.one_of(st.none(), _surfaces)),
    )


@st.composite
def braille_documents(draw: st.DrawFn) -> BrailleDocument:
    return BrailleDocument(
        metadata=draw(st.dictionaries(st.sampled_from(["profile", "language"]), _surfaces, max_size=2)),
        blocks=draw(
            st.lists(
                st.builds(
                    BrailleBlock,
                    block_type=st.sampled_from(["paragraph", "heading", "list_item"]),
                    cells=st.lists(_braille_cells(), max_size=4),
                    id=st.one_of(st.none(), st.just("b1")),
                    heading_level=st.sampled_from([None, 1, 2]),
                    align=st.sampled_from([None, "center", "right"]),
                ),
                max_size=3,
            )
        ),
    )


# --- field-level equality --------------------------------------------------------


def _ir_equal(a: object, b: object) -> bool:
    """Deep field equality across IR dataclasses, lists and ET trees.

    Payload stability alone cannot catch a ``to_dict`` that silently
    forgets a field (the field never reaches the payload, so the round
    trip looks stable while data is lost); comparing the rebuilt node to
    the original field by field closes that hole. ET elements compare by
    serialized form (their ``==`` is identity); ``frontend_fingerprint``
    is in-memory provenance, excluded from serialization by design.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, ET.Element):
        assert isinstance(b, ET.Element)
        return ET.tostring(a, encoding="unicode") == ET.tostring(b, encoding="unicode")
    if isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple))
        return len(a) == len(b) and all(_ir_equal(x, y) for x, y in zip(a, b, strict=True))
    if is_dataclass(a) and not isinstance(a, type):
        return all(
            _ir_equal(getattr(a, f.name), getattr(b, f.name))
            for f in fields(a)
            if f.name != "frontend_fingerprint"
        )
    return a == b


# --- properties -----------------------------------------------------------------


class TestInlineRoundTrip:
    @settings(max_examples=150)
    @given(node=inline_nodes())
    def test_round_trip_is_stable_and_json_native(self, node: InlineNode) -> None:
        payload = node.to_dict()
        json.dumps(payload)
        restored = from_dict(payload)
        assert type(restored) is type(node)
        assert _ir_equal(restored, node)
        assert restored.to_dict() == payload
        # Determinism: serializing the same node twice can't differ.
        assert node.to_dict() == payload

    @settings(max_examples=60)
    @given(node=inline_nodes())
    def test_tree_fields_serialize_as_strings(self, node: InlineNode) -> None:
        # ET carriers ride the JSON as serialized XML strings — never as
        # live Element objects.
        payload = node.to_dict()
        for key in ("math", "score", "svg"):
            if key in payload:
                assert isinstance(payload[key], str)


class TestBlockRoundTrip:
    @settings(max_examples=150)
    @given(block=blocks())
    def test_round_trip_is_stable_and_json_native(self, block: Block) -> None:
        payload = block.to_dict()
        json.dumps(payload)
        restored = block_from_dict(payload)
        assert type(restored) is type(block)
        assert _ir_equal(restored, block)
        assert restored.to_dict() == payload


class TestDocumentRoundTrip:
    @settings(max_examples=60)
    @given(
        blocks_=st.lists(blocks(), max_size=3),
        metadata=st.dictionaries(
            st.sampled_from(["language", "profile", "title"]), _surfaces, max_size=3
        ),
        with_assets=st.booleans(),
    )
    def test_round_trip_is_stable_and_assets_stay_out(
        self, blocks_: list[Block], metadata: dict, with_assets: bool
    ) -> None:
        doc = DocumentIR(
            metadata=metadata,
            blocks=blocks_,
            assets={"media/image1.png": b"\x89PNG..."} if with_assets else {},
        )
        payload = doc.to_dict()
        json.dumps(payload)
        # The text IR carries no binary payload (ARCHITECTURE §1): assets are
        # deliberately not serialized, so a round-trip drops them.
        assert "assets" not in payload
        restored = DocumentIR.from_dict(payload)
        assert restored.assets == {}
        assert restored.to_dict() == payload
        assert _ir_equal(restored.blocks, doc.blocks)
        assert restored.metadata == doc.metadata


class TestStructureKey:
    @settings(max_examples=100)
    @given(block=blocks(), data=st.data())
    def test_ignores_surface_identity_and_location(
        self, block: Block, data: st.DataObject
    ) -> None:
        # structure_key composes with the text-surface hash in cache keys,
        # so it must be blind to exactly what that hash covers: the text,
        # plus id / span (an edit elsewhere shifts spans but must not bust
        # this block's cache entry). Generic over every block type — a new
        # structural field is covered automatically.
        import dataclasses

        span = None
        if data.draw(st.booleans()):
            start = data.draw(st.integers(0, 30))
            span = Span(start, start + data.draw(st.integers(0, 5)))
        clone = dataclasses.replace(
            block,
            text=data.draw(st.one_of(st.none(), _surfaces)),
            id=data.draw(st.sampled_from([None, "b1", "other"])),
            span=span,
        )
        assert clone.structure_key() == block.structure_key()
        # And it is a pure function of the block.
        assert block.structure_key() == block.structure_key()


class TestBrailleRoundTrip:
    @settings(max_examples=100)
    @given(doc=braille_documents())
    def test_document_round_trip_is_exact(self, doc: BrailleDocument) -> None:
        payload = doc.to_dict()
        json.dumps(payload)
        restored = BrailleDocument.from_dict(payload)
        # Frozen value-typed cells make deep dataclass equality exact.
        assert restored == doc
        assert restored.to_dict() == payload

    @settings(max_examples=100)
    @given(cells=st.lists(_braille_cells(), max_size=6))
    def test_sequence_round_trip_is_exact(self, cells: list[BrailleCell]) -> None:
        seq = BrailleSequence(cells=cells)
        restored = BrailleSequence.from_dict(seq.to_dict())
        assert restored.cells == seq.cells
