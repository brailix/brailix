"""Schema contracts for the serialized IR (document-ir / braille-ir).

Two directions, so the schema and the dataclasses can't drift apart:

* **code → schema**: every payload the IR produces (via the same
  generated forests the round-trip properties use) must validate against
  the shipped schema — including the rule that a document payload never
  carries an ``assets`` key;
* **schema → code**: braille-IR payloads generated FROM the schema by
  hypothesis-jsonschema must be accepted by ``from_dict`` (and re-emit a
  schema-valid payload) or rejected with the documented ``ValueError`` —
  never crash otherwise. (The document-IR schema is recursive, which
  hypothesis-jsonschema cannot generate from; its generation-direction
  fuzzing is covered by the round-trip properties' own generators.)

The second direction has a blind spot the generators cannot cover on either
side: they produce *well-shaped* payloads, so they can only ever ask whether a
valid document loads. What a payload actually is, once it comes off disk, is
arbitrary decoded JSON — a field may be ``null``, a list, an object, or the
string ``"false"``. ``TestMalformedPayloadShapesAreRejectedCleanly`` supplies
that: it takes a payload the code produced and rewrites one field to a shape
the schema forbids, asserting the loader answers with one of the boundary's
two documented rejections and never leaks an error raised somewhere too deep
to name the offending field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")
pytest.importorskip("jsonschema")
pytest.importorskip("hypothesis_jsonschema")

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import Draft7Validator

from brailix.ir.braille import (
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.ir.document import (
    _SUPPORTED_IR_VERSIONS,
    Block,
    DocumentIR,
    block_from_dict,
)
from brailix.ir.inline import InlineNode
from brailix.ir.inline import from_dict as inline_from_dict
from tests.ir.test_serialization_properties import (
    _ir_equal,
    blocks,
    braille_documents,
    inline_nodes,
)

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


_DOC_SCHEMA = _load("document-ir.schema.json")
_BRAILLE_SCHEMA = _load("braille-ir.schema.json")

_document_validator = Draft7Validator(_DOC_SCHEMA)
_braille_validator = Draft7Validator(_BRAILLE_SCHEMA)
# Sub-schema validators: same definitions, different root.
_block_validator = Draft7Validator(
    {"$ref": "#/definitions/block", "definitions": _DOC_SCHEMA["definitions"]}
)
_inline_validator = Draft7Validator(
    {"$ref": "#/definitions/inline_node", "definitions": _DOC_SCHEMA["definitions"]}
)


def test_schema_and_loader_agree_on_the_loadable_versions() -> None:
    """The schema's ``version`` enum and the loader's supported set are two
    statements of the same fact, and a reader has no way to tell which one is
    stale: a payload the schema validates must be a payload ``from_dict``
    accepts. Pin them to each other so a new version has to be added on both
    sides (with its migration) rather than on whichever one the author
    remembered."""
    documented = set(
        _DOC_SCHEMA["definitions"]["document"]["properties"]["version"]["enum"]
    )
    assert documented == set(_SUPPORTED_IR_VERSIONS)


class TestCodeProducesSchemaValidPayloads:
    @settings(max_examples=100)
    @given(node=inline_nodes())
    def test_inline_payloads_conform(self, node: InlineNode) -> None:
        _inline_validator.validate(node.to_dict())

    @settings(max_examples=100)
    @given(block=blocks())
    def test_block_payloads_conform(self, block: Block) -> None:
        _block_validator.validate(block.to_dict())

    @settings(max_examples=60)
    @given(
        blocks_=st.lists(blocks(), max_size=3),
        with_assets=st.booleans(),
    )
    def test_document_payloads_conform_and_never_carry_assets(
        self, blocks_: list[Block], with_assets: bool
    ) -> None:
        doc = DocumentIR(
            blocks=blocks_,
            assets={"media/image1.png": b"\x89PNG"} if with_assets else {},
        )
        payload = doc.to_dict()
        # additionalProperties: false on the document root makes an assets
        # leak a schema violation, not just a convention.
        _document_validator.validate(payload)

    @settings(max_examples=60)
    @given(doc=braille_documents())
    def test_braille_payloads_conform(self, doc: BrailleDocument) -> None:
        _braille_validator.validate(doc.to_dict())


class TestSchemaShapedPayloadsFeedFromDict:
    @settings(max_examples=25)
    @given(payload=from_schema(_BRAILLE_SCHEMA))
    def test_braille_from_dict_accepts_or_rejects_cleanly(self, payload: dict) -> None:
        # Schema-valid structure is necessary but not sufficient (a span
        # may be [5, 2]); from_dict must accept or raise the documented
        # ValueError — nothing else — and an accepted payload must
        # re-serialize schema-valid.
        try:
            doc = BrailleDocument.from_dict(payload)
        except ValueError:
            return
        _braille_validator.validate(doc.to_dict())


# ---------------------------------------------------------------------------
# The other direction the schema cannot generate: hostile field SHAPES
# ---------------------------------------------------------------------------

# Values a decoded JSON payload can legitimately hold and an IR field almost
# never can. ``"false"`` and ``"0"`` are here because they are the shapes that
# do damage *quietly*: a non-empty string is truthy, so a ``List`` whose
# ``ordered`` was the string ``"false"`` used to load as an ORDERED list and
# render the wrong marker, with nothing anywhere reporting a problem.
_HOSTILE_VALUES = st.sampled_from(
    [
        None,
        [],
        {},
        [1, 2],
        {"nested": True},
        "false",
        "0",
        0,
        1,
        True,
        1.5,
    ]
)


# The two rejections this boundary is allowed to make, and they mean different
# things: a ``ValueError`` says "that field's SHAPE is not one this payload may
# carry", a ``TypeError`` says "that nested node is the wrong CLASS" (see
# ``brailix.ir._serde.typed_child`` — the one check that is about node identity
# rather than wire shape, and the same signal a caller assembling the tree in
# code gets for the same mistake). Anything outside this pair is the failure
# mode being guarded against: an error raised somewhere too deep to name the
# field that caused it.
_DOCUMENTED_REJECTIONS = (ValueError, TypeError)


def _rebuild_or_reject(loader: object, payload: object) -> object | None:
    """Run ``loader(payload)``, returning None if it rejected the payload the
    way the boundary documents. Any other exception is re-raised, so the test
    reports the leak rather than swallowing it."""
    try:
        return loader(payload)  # type: ignore[operator]
    except _DOCUMENTED_REJECTIONS:
        return None


def _assert_round_trips(loader: object, obj: object) -> None:
    """What an ACCEPTED mutated payload has to satisfy: re-serializing it and
    loading it back returns an equal object.

    Deliberately not "the output validates against the schema". These payloads
    are *chosen* to be schema-invalid, and holding the loader to the schema
    would make it a schema validator — which it is not and does not claim to
    be here (the schemas are a test-layer artifact; nothing in the package
    reads them). What the loader does promise is that a value it accepts is a
    value of the field's declared type, and a round trip is the cheapest total
    check of that: a value that only *looks* like the field's type stops
    surviving one.

    Compared with :func:`_ir_equal` rather than ``==``, for the reason it
    exists: an ``ET.Element`` compares by identity, so a node carrying a tree
    never equals its own reload.
    """
    assert _ir_equal(loader(obj.to_dict()), obj)  # type: ignore[operator, attr-defined]


class TestMalformedPayloadShapesAreRejectedCleanly:
    """A payload's field is arbitrary decoded JSON until something checks it.

    This is the fuzz direction ``from_schema`` cannot supply for the document
    IR (its schema is recursive, so hypothesis-jsonschema declines it): take a
    payload the code itself produced — therefore schema-valid — and rewrite ONE
    field to a shape the schema forbids. The loader must either build something
    that still serializes schema-valid, or reject with one of the boundary's
    two documented signals (:data:`_DOCUMENTED_REJECTIONS`).

    "Or reject" is the weak half; the part with teeth is that nothing ELSE may
    escape. These payloads used to *load*: a ``MathBlock`` whose ``source`` was
    a list built fine and raised ``unhashable type: 'list'`` from a registry
    lookup much later, and ``"blocks": null`` raised ``TypeError: 'NoneType'
    object is not iterable`` from inside a comprehension. Both are real
    failures reported from somewhere that cannot name the file that caused
    them, which is the same as no diagnosis at all for whoever has to fix the
    file.
    """

    @settings(max_examples=200)
    @given(block=blocks(), value=_HOSTILE_VALUES, seed=st.integers(0, 999))
    def test_a_rewritten_block_field(
        self, block: Block, value: object, seed: int
    ) -> None:
        payload = block.to_dict()
        keys = [k for k in payload if k != "type"]
        if not keys:
            return
        payload[keys[seed % len(keys)]] = value
        rebuilt = _rebuild_or_reject(block_from_dict, payload)
        if rebuilt is None:
            return
        # Accepted → the value really is a legal shape for that field.
        assert isinstance(rebuilt, Block)
        _assert_round_trips(block_from_dict, rebuilt)

    @settings(max_examples=200)
    @given(node=inline_nodes(), value=_HOSTILE_VALUES, seed=st.integers(0, 999))
    def test_a_rewritten_inline_field(
        self, node: InlineNode, value: object, seed: int
    ) -> None:
        payload = node.to_dict()
        keys = [k for k in payload if k != "type"]
        if not keys:
            return
        payload[keys[seed % len(keys)]] = value
        rebuilt = _rebuild_or_reject(inline_from_dict, payload)
        if rebuilt is None:
            return
        assert isinstance(rebuilt, InlineNode)
        _assert_round_trips(inline_from_dict, rebuilt)

    @settings(max_examples=100)
    @given(value=_HOSTILE_VALUES, key=st.sampled_from(["metadata", "blocks"]))
    def test_a_rewritten_document_container(
        self, value: object, key: str
    ) -> None:
        payload = DocumentIR(blocks=[]).to_dict()
        payload[key] = value
        doc = _rebuild_or_reject(DocumentIR.from_dict, payload)
        if doc is None:
            return
        _assert_round_trips(DocumentIR.from_dict, doc)

    @settings(max_examples=200)
    @given(doc=braille_documents(), value=_HOSTILE_VALUES, seed=st.integers(0, 999))
    def test_a_rewritten_braille_field(
        self, doc: BrailleDocument, value: object, seed: int
    ) -> None:
        payload = doc.to_dict()
        # Rewrite a field somewhere in the braille tree: root, a block, or a
        # cell — the three loaders that had no shape checks at all.
        targets: list[dict] = [payload]
        for b in payload["blocks"]:
            targets.append(b)
            targets.extend(b["cells"])
        target = targets[seed % len(targets)]
        keys = [k for k in target if k != "type"]
        if not keys:
            return
        target[keys[seed % len(keys)]] = value
        rebuilt = _rebuild_or_reject(BrailleDocument.from_dict, payload)
        if rebuilt is None:
            return
        _assert_round_trips(BrailleDocument.from_dict, rebuilt)


class TestRootTagsAreRead:
    """Every IR root writes a ``type`` constant and every schema declares it
    a ``const``; a loader that does not read it back makes both statements
    decorative. The three braille loaders did not, so a payload of one kind
    loaded as another and was re-serialized under the *new* tag — a silent
    conversion, on a round trip that reports success."""

    @pytest.mark.parametrize(
        ("loader", "payload", "expected"),
        [
            (
                BrailleDocument.from_dict,
                {"type": "document", "metadata": {}, "blocks": []},
                "braille_document",
            ),
            (
                BrailleBlock.from_dict,
                {"type": "braille_document", "block_type": "paragraph", "cells": []},
                "braille_block",
            ),
            (
                BrailleSequence.from_dict,
                {"type": "braille_block", "cells": []},
                "braille_sequence",
            ),
            (
                DocumentIR.from_dict,
                {"type": "braille_document", "metadata": {}, "blocks": []},
                "document",
            ),
        ],
    )
    def test_a_foreign_tag_is_refused(
        self, loader: object, payload: dict, expected: str
    ) -> None:
        with pytest.raises(ValueError, match=expected):
            loader(payload)

    @pytest.mark.parametrize(
        "loader",
        [BrailleDocument.from_dict, BrailleBlock.from_dict, BrailleSequence.from_dict],
    )
    def test_a_missing_tag_is_refused(self, loader: object) -> None:
        with pytest.raises(ValueError, match="must carry type"):
            loader({"cells": [], "blocks": [], "metadata": {}})

    def test_every_braille_tag_round_trips(self) -> None:
        # The other half: the tag each loader demands is the tag its own
        # ``to_dict`` writes, so a round trip cannot be the thing that breaks.
        for obj in (
            BrailleDocument(blocks=[BrailleBlock(cells=[BrailleCell(dots=(1,))])]),
            BrailleBlock(cells=[BrailleCell(dots=(1,))]),
            BrailleSequence(cells=[BrailleCell(dots=(1,))]),
        ):
            assert type(obj).from_dict(obj.to_dict()) == obj
