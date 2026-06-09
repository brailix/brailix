"""Math tables loader (symbols / functions / structures / digits_lower).

The single entry point :func:`_load_math_table` reads four files from
``resources/cn/<scheme>/math/`` and returns a flat dict ready to slot
into :class:`BrailleProfile`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config.loader._refs import (
    _flag_dict_bool,
    _flag_dict_str,
    _normalise_symbols_payload,
    _read_section,
    _resolve_digits,
    _resolve_nested_structures,
    _resolve_table,
    _symbol_spacing_dict,
)

_EMPTY_MATH: dict[str, Any] = {
    "symbols": {},
    "functions": {},
    "structures": {},
    "digits_lower": {},
    "symbol_spacing": {},
    "symbol_roles": {},
    "symbol_accent_mark": {},
    "symbol_script_prefix": {},
    "symbol_provisional": {},
    "symbol_indicator": {},
    "function_big_op": {},
    "function_script_prefix": {},
}


def _load_math_table(
    base: Path,
    entry: Any,
    cells_pool: dict[str, tuple[int, ...]] | None = None,
) -> dict[str, Any]:
    """Load the math tables (dict of section_name → relative path).

    Only the split-file shape from design §3.7 is supported now:
    ``entry`` is a dict mapping ``symbols`` / ``functions`` /
    ``structures`` / ``digits_lower`` to a JSON file. Each file holds
    exactly one top-level section.
    """
    pool = cells_pool or {}
    if not entry or not isinstance(entry, dict):
        return _EMPTY_MATH

    sym_rel = entry.get("symbols")
    fn_rel = entry.get("functions")
    st_rel = entry.get("structures")
    dl_rel = entry.get("digits_lower")

    sym_payload = _read_section(base, sym_rel, "symbols")
    fn_payload = _read_section(base, fn_rel, "functions")
    st_payload = _read_section(base, st_rel, "structures")
    dl_payload = _read_section(base, dl_rel, "digits_lower")

    # --- Symbols: normalise entity-name keys (and entity refs inside
    # `cells` arrays) before resolving. -----------------------------------
    sym_dict = _normalise_symbols_payload(sym_payload, pool, file=sym_rel)
    symbols = _resolve_table(sym_dict, pool, file=sym_rel)
    symbol_spacing = _symbol_spacing_dict(sym_dict)
    symbol_roles = _flag_dict_str(sym_dict, "role")
    # Contextual accent kind ("arrow" / "bar"): a char tagged here renders
    # as a vector mark (over-arrow / short bar) when it sits in an accent
    # over/under position — independent of its global role (→/← stay
    # role=rel for ordinary relation use). Cells live in structures
    # (accent.mark.<kind>.{single,double}); this map only says which kind.
    symbol_accent_mark = _flag_dict_str(sym_dict, "accent_mark")
    symbol_script_prefix = _flag_dict_bool(sym_dict, "script_prefix")
    symbol_provisional = _flag_dict_bool(sym_dict, "provisional")
    symbol_indicator = _flag_dict_str(sym_dict, "indicator")

    # --- Functions: NO entity normalisation (function names are
    # natural English keys, not entity names). ---------------------------
    functions = _resolve_table(fn_payload, pool, file=fn_rel)
    function_big_op = _flag_dict_bool(fn_payload, "big_op")
    function_script_prefix = _flag_dict_bool(fn_payload, "script_prefix")

    # --- Structures: nested dotted form. -------------------------------
    structures = _resolve_nested_structures(st_payload, pool, file=st_rel)

    # --- Digits lower: flat single-cell table. -------------------------
    digits_lower = _resolve_digits(dl_payload, pool, file=dl_rel)

    return {
        "symbols": symbols,
        "functions": functions,
        "structures": structures,
        "digits_lower": digits_lower,
        "symbol_spacing": symbol_spacing,
        "symbol_roles": symbol_roles,
        "symbol_accent_mark": symbol_accent_mark,
        "symbol_script_prefix": symbol_script_prefix,
        "symbol_provisional": symbol_provisional,
        "symbol_indicator": symbol_indicator,
        "function_big_op": function_big_op,
        "function_script_prefix": function_script_prefix,
    }
