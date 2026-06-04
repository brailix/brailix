"""Neutral letter table loader (latin / greek).

Letter tables live in ``resources/latin/letters.json`` and
``resources/greek/letters.json``. Each file is *neutral* — it only
stores the letter body's dot tuple, never any context prefix. The math
backend prepends the script-class prefix from ``math.structures``; the
future LatinBraille backend will apply its own rules to the same data.
"""

from __future__ import annotations

from pathlib import Path

from brailix.core.config._helpers import _read_json
from brailix.core.config.loader._refs import _resolve_dots_table


def _load_letters_table(
    base: Path,
    relative: str | None,
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """Load a neutral letter table.

    Returns ``{"lower": {...}, "upper": {...}}``. Each inner dict maps
    a single character to its dot tuple (no prefix applied). Cell-pool
    refs (``"a": "c_1"``), bare dot lists, and ``{"dots": [...]}`` are
    all accepted via :func:`_resolve_dots_table`. Returns empty subgroups
    when the file is absent or missing the ``letters`` section.
    """
    out: dict[str, dict[str, tuple[int, ...]]] = {"lower": {}, "upper": {}}
    if not relative:
        return out
    payload = _read_json(base / relative)
    letters = payload.get("letters")
    if not isinstance(letters, dict):
        return out
    for case in ("lower", "upper"):
        group = letters.get(case)
        if isinstance(group, dict):
            out[case] = _resolve_dots_table(group, cells_pool)
    return out
