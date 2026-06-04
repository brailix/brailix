"""Punctuation table loader.

Two helpers:

* :func:`_load_punct_table` — punctuation cell sequences (used by the
  punct backend).
* :func:`_load_punct_spacing` — the (space_before, space_after) flags
  attached to each punct entry.

Both accept the same JSON file; the loader is split so callers can ask
for cells without the spacing dict and vice versa.
"""

from __future__ import annotations

from pathlib import Path

from brailix.core.config._helpers import _read_json
from brailix.core.config.loader._refs import (
    _resolve_table,
    _symbol_spacing_dict,
)


def _load_punct_spacing(base: Path, relative: str | None) -> dict[str, tuple[bool, bool]]:
    """Load (space_before, space_after) flags from the punctuation table.

    Same schema as :func:`_symbol_spacing_dict`: read the same payload as
    the cells table, but pull out the spacing flags so the punct backend
    can append blank cells after the comma, period, etc.
    """
    if not relative:
        return {}
    payload = _read_json(base / relative)
    group = payload.get("punctuation")
    if isinstance(group, dict):
        return _symbol_spacing_dict(group)
    return _symbol_spacing_dict(payload)


def _load_punct_table(
    base: Path,
    relative: str | None,
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Load the punctuation table as cell sequences.

    Goes through :func:`_resolve_table` so cell-pool refs (``"c_46"``),
    sibling refs, bare dot lists, and ``{"cells": [...]}`` /
    ``{"dots": [...]}`` spec objects are all accepted. Multi-cell
    entries become multi-cell tuples; the backend emits one BrailleCell
    per inner tuple.
    """
    if not relative:
        return {}
    payload = _read_json(base / relative)
    group = payload.get("punctuation")
    if isinstance(group, dict):
        return _resolve_table(group, cells_pool)
    return _resolve_table(payload, cells_pool)
