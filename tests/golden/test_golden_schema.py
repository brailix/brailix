"""Every golden data file conforms to the declared golden-case schema.

The golden suite is the human-reviewed output authority, and its files are
edited by hand — no Python involved. The schema (additionalProperties:
false at every level) is what turns a typo'd field name into a loud
failure instead of a silently skipped check: an ``expcted`` key would
otherwise make the case pass forever while checking nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

from jsonschema import Draft7Validator

_DATA_DIR = Path(__file__).parent / "data"
_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "schemas" / "golden-case.schema.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    "data_file",
    sorted(p.name for p in _DATA_DIR.glob("*.json")),
)
def test_golden_file_conforms(data_file: str) -> None:
    payload = json.loads((_DATA_DIR / data_file).read_text(encoding="utf-8"))
    Draft7Validator(_SCHEMA).validate(payload)
