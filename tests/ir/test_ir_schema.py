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

from brailix.ir.braille import BrailleDocument
from brailix.ir.document import Block, DocumentIR
from brailix.ir.inline import InlineNode
from tests.ir.test_serialization_properties import (
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
