"""Unified golden test runner.

Every JSON file in ``data/`` is a self-contained set of input → expected
braille cases plus optional warning-code assertions. This file is the
single Python entry point: it walks the data directory once at collect
time, flattens every ``case`` into a parametrize entry, and runs them
all through the same ``pipe`` fixture defined in :mod:`conftest`.

Non-coders edit the JSON files directly — see ``data/README.md`` for
the schema. New ``.json`` files dropped into ``data/`` are picked up
automatically on the next pytest run; no code change required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

DATA_DIR = Path(__file__).parent / "data"


def _load_cases() -> list[tuple[str, dict[str, Any]]]:
    """Walk ``data/*.json`` and yield ``(test_id, case)`` pairs.

    The pytest id is ``<file>/<group>/<case_id_or_src>`` so a failure
    points straight at the JSON entry that produced it.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for json_path in sorted(DATA_DIR.glob("*.json")):
        suite = json_path.stem
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        groups = payload.get("groups", {})
        if not isinstance(groups, dict):
            raise ValueError(
                f"{json_path}: top-level 'groups' must be an object"
            )
        for group_name, group in groups.items():
            cases = group.get("cases", []) if isinstance(group, dict) else []
            for case in cases:
                if not isinstance(case, dict):
                    raise ValueError(
                        f"{json_path}#{group_name}: every case must be an object"
                    )
                if "src" not in case:
                    raise ValueError(
                        f"{json_path}#{group_name}: case is missing required 'src' field"
                    )
                case_id = case.get("id") or case["src"]
                test_id = f"{suite}/{group_name}/{case_id}"
                if not any(
                    k in case
                    for k in (
                        "expected",
                        "warnings_include",
                        "warnings_exclude",
                        "warnings_exclude_prefix",
                    )
                ):
                    raise ValueError(
                        f"{json_path}#{group_name}: case {case_id!r} declares "
                        f"no assertion (need one of expected / warnings_include "
                        f"/ warnings_exclude / warnings_exclude_prefix) — it "
                        f"would pass without checking anything"
                    )
                out.append((test_id, case))
    return out


_CASES = _load_cases()
_IDS = [tid for tid, _ in _CASES]
_PAYLOADS = [case for _, case in _CASES]


@pytest.mark.parametrize("case", _PAYLOADS, ids=_IDS)
def test_golden(pipe, case: dict[str, Any]) -> None:
    """Run one case from the JSON corpus.

    Behaviour by case fields:

    * ``expected`` set → braille output must match exactly.
    * ``warnings_include`` set → every listed code must appear.
    * ``warnings_exclude`` set → none of the listed codes may appear.
    * ``warnings_exclude_prefix`` set → no code may start with the
      prefix (e.g. ``"MATH_"`` to ban every math-recovery warning).
    """
    src = case["src"]
    note = case.get("note", "")

    result = pipe.translate_text(src)
    actual = result.render()
    codes = {w.code for w in result.warnings}

    if "expected" in case:
        expected = case["expected"]
        assert actual == expected, (
            f"{note}\nsrc={src!r}\nexpected={expected!r}\nactual  ={actual!r}"
        )

    for code in case.get("warnings_include", []):
        assert code in codes, (
            f"{note}\nsrc={src!r}\n"
            f"expected warning {code!r}, got {sorted(codes)!r}"
        )

    for code in case.get("warnings_exclude", []):
        assert code not in codes, (
            f"{note}\nsrc={src!r}\n"
            f"forbidden warning {code!r} present in {sorted(codes)!r}"
        )

    prefix = case.get("warnings_exclude_prefix")
    if prefix:
        offenders = sorted(c for c in codes if c.startswith(prefix))
        assert not offenders, (
            f"{note}\nsrc={src!r}\n"
            f"no code may start with {prefix!r}, but got {offenders!r}"
        )
