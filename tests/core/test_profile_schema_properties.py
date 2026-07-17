"""Schema contracts for profile JSON (tests/schemas/profile.schema.json).

The profile system's architecture decision — the braille standard as DATA —
gets a declared external structure. Three layers, each owning its rules
exactly once:

* the JSON Schema owns *structure* (which keys, what shapes);
* :mod:`brailix.core.config.validator` owns *semantics* (referenced table
  files exist, entities resolve, cycles rejected);
* this module wires the two: every shipped profile must conform to the
  schema, and any schema-shaped payload — generated adversarially by
  hypothesis-jsonschema — must either load or be rejected with the
  documented :class:`ConfigurationError`, never crash the loader with
  anything else.

The schema files themselves are also checked against the draft-07
meta-schema, so a broken schema fails loudly instead of vacuously
accepting everything.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")
pytest.importorskip("jsonschema")
pytest.importorskip("hypothesis_jsonschema")

from hypothesis import given, settings
from hypothesis_jsonschema import from_schema
from jsonschema import Draft7Validator

import brailix
from brailix.core.config import load_profile
from brailix.core.errors import ConfigurationError

# Schemas are VERIFICATION-layer contracts, owned by the test suite: no
# runtime code reads them (brailix.core.config.validator owns runtime
# validation, dependency-free). Promote one into the package only when a
# real runtime consumer appears — an editor validating user profiles live.
_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
_PROFILE_DIR = Path(brailix.__file__).parent / "profiles"

_PROFILE_SCHEMA = json.loads(
    (_SCHEMA_DIR / "profile.schema.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "schema_file",
    sorted(p.name for p in _SCHEMA_DIR.glob("*.schema.json")),
)
def test_every_declared_schema_is_a_valid_draft7_schema(schema_file: str) -> None:
    # A malformed schema can silently accept everything; check each one
    # against the metaschema so the guard itself is guarded.
    schema = json.loads((_SCHEMA_DIR / schema_file).read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)


@pytest.mark.parametrize(
    "profile_file",
    sorted(p.name for p in _PROFILE_DIR.glob("*.json")),
)
def test_every_builtin_profile_conforms(profile_file: str) -> None:
    payload = json.loads(
        (_PROFILE_DIR / profile_file).read_text(encoding="utf-8")
    )
    Draft7Validator(_PROFILE_SCHEMA).validate(payload)


class TestGeneratedProfiles:
    @settings(max_examples=25)
    @given(payload=from_schema(_PROFILE_SCHEMA))
    def test_loader_accepts_or_rejects_cleanly(self, payload: dict) -> None:
        # Schema-shaped ≠ loadable: generated table references point at
        # files that don't exist, feature values are junk, names are
        # arbitrary. The loader's contract is that ALL of that surfaces
        # as ConfigurationError — the documented business rejection —
        # never as a KeyError / TypeError / AttributeError crash.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "genprof.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                profile = load_profile("genprof", extra_search_paths=[Path(tmp)])
            except ConfigurationError:
                return
            # Accepted: the loaded profile reflects the payload's identity.
            assert profile.name == payload["name"]
