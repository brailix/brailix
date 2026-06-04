"""Numbers table loader (number_sign + digits + decimal / thousands).

The numbers resource lives at ``resources/numbers.json`` — universal
(the number sign + a-j digits are shared across braille systems), used
by both the zh and math subsystems: math's ``<mn>`` handler queries the
same digit cells that zh's :class:`Number` translator uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config._helpers import _read_json
from brailix.core.config.loader._refs import (
    _resolve_dots_table,
    _resolve_single,
)


def _load_numbers_table(
    base: Path, relative: str | None, cells_pool: dict[str, tuple[int, ...]]
) -> dict[str, Any]:
    """Parse the numbers table.

    Schema (with cell-pool ref support)::

        {"schema": "...",
         "number_sign": "c_3456" | {"dots": [3,4,5,6]},
         "digits": {"1": "c_1" | {"dots": [1], "brf": "a"}, ...},
         "punctuation": {"decimal_point": "c_46" | {"dots": [4,6]},
                         "thousands_sep": "c_3" | {"dots": [3]}}}

    Values support bare cell-pool refs (``"c_46"``), bare dot lists
    (``[4,6]``), and cell-spec objects (``{"dots": [4,6], ...}``).
    Decimal / thousands may live at the top level or under
    ``punctuation`` (latter preferred).
    """
    if not relative:
        return {
            "number_sign": (), "digits": {},
            "decimal_point": (), "thousands_sep": (),
        }
    payload = _read_json(base / relative)
    digits = _resolve_dots_table(payload.get("digits", {}), cells_pool)

    punct_group = payload.get("punctuation", {}) if isinstance(payload.get("punctuation"), dict) else {}
    decimal = _resolve_single(
        punct_group.get("decimal_point")
        if "decimal_point" in punct_group
        else payload.get("decimal_point"),
        cells_pool,
    )
    thousands = _resolve_single(
        punct_group.get("thousands_sep")
        if "thousands_sep" in punct_group
        else payload.get("thousands_sep"),
        cells_pool,
    )
    return {
        "number_sign": _resolve_single(payload.get("number_sign"), cells_pool),
        "digits": digits,
        "decimal_point": decimal,
        "thousands_sep": thousands,
    }
