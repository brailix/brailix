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
