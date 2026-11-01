"""proofread_json() output contract, schema-checked over real compiles.

``TranslationResult.proofread_json`` is the wire format proofreading
front-ends consume. Its contract: exactly the four documented keys, an
``ir`` payload valid against the document-IR schema (which also proves no
binary assets leak out), a ``braille_ir`` payload valid against the
braille-IR schema, and every entry of ``warnings`` valid against the
warning schema — over generated inputs, including ones that produce
warnings (unknown characters) and inline math.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")
pytest.importorskip("jsonschema")

from hypothesis import example, given, settings
from hypothesis import strategies as st
from jsonschema import Draft7Validator

from brailix.pipeline import Pipeline

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _validator(name: str) -> Draft7Validator:
    return Draft7Validator(
        json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    )


_document_validator = _validator("document-ir.schema.json")
_braille_validator = _validator("braille-ir.schema.json")
_warning_validator = _validator("warning.schema.json")

_PIPE = Pipeline(profile="cn_current")

_texts = st.text(
    alphabet=st.sampled_from(list("我在重庆好中文abcX012,。!? ©☂")), max_size=20
)


class TestProofreadJson:
    @settings(max_examples=30)
    @given(text=_texts)
    @example(text="得分＝95©")  # guaranteed warning-bearing case
    @example(text="")
    def test_shape_and_subschemas(self, text: str) -> None:
        payload = _PIPE.translate_text(text).proofread_json()
        # Exactly the documented keys — a proofreading front-end keys on
        # this set, and an accidental fifth key is a contract change.
        assert set(payload) == {"text", "ir", "braille_ir", "warnings"}
        assert payload["text"] == text
        _document_validator.validate(payload["ir"])
        _braille_validator.validate(payload["braille_ir"])
        for warning in payload["warnings"]:
            _warning_validator.validate(warning)
        # And the whole thing is JSON-transportable as promised.
        json.dumps(payload)


class TestTableProofreadSpansSliceThePayloadText:
    """The §8.3 consumption over a TABLE (review R2-02).

    A proofreading front-end highlights / jumps by pairing this payload's
    ``text`` with the spans in ``ir`` (and in ``warnings``). Inside a table
    every span is row-local — offsets into the two-space join of the row's
    cells (§4.4) — so the payload text has to BE that join. It used to join
    cells with ``" | "``, three characters no span accounted for, and every
    column past the first sliced out the wrong text with no diagnostic.
    """

    def test_text_is_the_row_join_and_spans_slice_it(self) -> None:
        from brailix.ir.document import DocumentIR, Table, TableCell, TableRow

        table = Table(
            blocks=[
                TableRow(
                    blocks=[
                        TableCell(text="重庆"),
                        TableCell(text="银行"),
                        TableCell(text="长安"),
                    ]
                )
            ]
        )
        result = _PIPE.translate_document(DocumentIR(blocks=[table]))
        row_text = "  ".join(c.text for c in table.blocks[0].blocks)
        payload = result.proofread_json()
        assert payload["text"] == row_text

        # Every inline-node span in the payload's ir slices its surface
        # out of the payload's text.
        def walk(node) -> None:
            for child in getattr(node, "inlines", ()) or ():
                assert child.span is not None
                assert payload["text"][child.span.start : child.span.end] == (
                    child.surface
                ), f"{child.type} at {child.span} misses {child.surface!r}"
                walk(child)

        for block in payload["ir"]["blocks"]:
            for row in block.get("blocks", []):
                for cell in row.get("blocks", []):
                    walk(cell)

        # And so does every spanned warning.
        for warning in payload["warnings"]:
            if warning.get("span") is None:
                continue
            start, end = warning["span"]
            assert payload["text"][start:end] == warning["surface"]

    def test_warning_free_columns_are_not_required(self) -> None:
        # The invariant above must hold vacuously too: a warning-free
        # table (the common case) simply has nothing to slice, and the
        # text contract alone is what's pinned.
        from brailix.ir.document import DocumentIR, Table, TableCell, TableRow

        table = Table(
            blocks=[TableRow(blocks=[TableCell(text="甲"), TableCell(text="乙")])]
        )
        payload = _PIPE.translate_document(
            DocumentIR(blocks=[table])
        ).proofread_json()
        assert payload["text"] == "甲  乙"
