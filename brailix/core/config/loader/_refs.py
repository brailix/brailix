"""Loader-internal table / cell / ref resolvers.

This module is the "toolkit" layer of the loader subpackage: every other
``loader/*.py`` module imports its helpers from here.  Nothing here
depends on a specific resource topic — it's all generic spec / ref /
flag plumbing.

Grouped by responsibility:

* **Table indexing** — :func:`_section`, :func:`_table_ref`,
  :func:`_read_section`, :func:`_load_table` (single-cell tables)
* **Spec shape recognition** — :func:`_is_spec_object`,
  :func:`_coerce_dots_field`, :func:`_extract_cells`
* **Cell-sequence resolution** — :func:`_spec_to_cells`,
  :func:`_resolve_table`, :func:`_resolve_single`,
  :func:`_resolve_dots_table`, :func:`_resolve_digits`,
  :func:`_resolve_cell_refs` (flat ref-list resolver shared by the music
  + zh override loaders)
* **Symbol entity normalisation** — :func:`_normalise_symbols_payload`,
  :func:`_normalise_symbols_spec`, :func:`_normalise_one_ref`,
  :func:`_resolve_nested_structures`
* **Flag dicts** — :func:`_symbol_spacing_dict`, :func:`_flag_dict_bool`,
  :func:`_flag_dict_str`
* **Cells pool** — :func:`_load_cells_pool`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config._helpers import (
    _entity_to_char,
    _is_metadata_key,
    _read_json,
    _to_dots,
)
from brailix.core.errors import ConfigurationError

# ---------------------------------------------------------------------------
# Table indexing
# ---------------------------------------------------------------------------


def _section(tables: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a sub-section of the tables block.

    For the math block we only accept the dict form per design §3.7 —
    a string ``tables.math`` is rejected (returns ``{}``), and the
    loader proceeds with an empty math table.
    """
    sub = tables.get(key)
    if isinstance(sub, dict):
        return sub
    return {}


def _table_ref(tables: dict[str, Any], section: str, key: str) -> Any:
    """Look up a table reference, accepting either the nested form
    (``tables.<section>.<key>``) or the flat fallback (``tables.<key>``).

    The nested form is preferred; the flat form keeps older test
    fixtures and minimal profiles working without re-nesting every
    field. Returns ``None`` if neither shape contains the key.
    """
    sub = tables.get(section)
    if isinstance(sub, dict) and key in sub:
        return sub[key]
    return tables.get(key)


def _read_section(
    base: Path, relative: str | None, section: str
) -> dict[str, Any]:
    """Read a math sub-table file and return its ``section`` block.

    Returns ``{}`` when ``relative`` is missing or the file lacks the
    expected top-level section.
    """
    if not relative:
        return {}
    payload = _read_json(base / relative)
    sub = payload.get(section)
    return sub if isinstance(sub, dict) else {}


def _load_table(
    base: Path,
    relative: str | None,
    cells_pool: dict[str, tuple[int, ...]],
    group: str | None = None,
) -> dict[str, tuple[int, ...]]:
    """Load a generic single-cell table file.

    If the file uses the rich schema (entries nested under a named
    ``group`` like ``"initials"``), parse from there. Otherwise treat
    every non-metadata top-level key as a cell entry.

    Goes through :func:`_resolve_dots_table` so cell-pool refs like
    ``"b": "c_12"`` and inline ``{"dots": [...]}`` shapes are both
    accepted. Unknown refs raise at load time.
    """
    if not relative:
        return {}
    payload = _read_json(base / relative)
    if group and isinstance(payload.get(group), dict):
        return _resolve_dots_table(payload[group], cells_pool)
    return _resolve_dots_table(payload, cells_pool)


# ---------------------------------------------------------------------------
# Spec shape recognition
# ---------------------------------------------------------------------------


def _is_spec_object(node: dict[str, Any]) -> bool:
    """A dict counts as a spec (rather than a topic-group container) if
    it carries any of ``cells`` / ``dots`` / ``parts``."""
    return "cells" in node or "dots" in node or "parts" in node


def _coerce_dots_field(dots: Any) -> tuple[tuple[int, ...], ...] | None:
    if not isinstance(dots, list):
        return None
    if not dots:
        return ()
    if all(isinstance(x, int) for x in dots):
        return (_to_dots(dots),)
    out: list[tuple[int, ...]] = []
    for cell in dots:
        if not isinstance(cell, list):
            return None
        out.append(_to_dots(cell))
    return tuple(out)


def _extract_cells(value: Any) -> tuple[tuple[int, ...], ...] | None:
    """Promote a math-table value into a cell sequence.

    Accepted shapes (matching the zh ``{"dots": [...]}`` schema):

      * ``{"dots": [1, 2]}``          — single cell
      * ``{"dots": [[1, 2], [3]]}``   — multi-cell sequence
      * ``[1, 2]``                    — bare single cell
      * ``[[1, 2], [3]]``             — bare multi-cell sequence
    """
    if value is None:
        return None
    if isinstance(value, dict):
        if "dots" not in value:
            return None
        return _coerce_dots_field(value["dots"])
    if isinstance(value, list):
        return _coerce_dots_field(value)
    return None


# ---------------------------------------------------------------------------
# Cell-sequence resolution
# ---------------------------------------------------------------------------


def _resolve_table(
    payload: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
    *,
    file: str | None = None,
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Resolve a spec table to flat cell sequences.

    Spec shapes accepted (any combination across entries):

    * Bare string — single ref: ``"a": "c_1"``
    * Bare list — list of refs: ``"≠": ["c_4", "="]``
    * Object with ``cells``: ``"+": {"cells": ["c_235"], "space_before": true}``
    * Object with ``dots`` — inline literal cells (single ``[1,2]`` or
      multi-cell ``[[1,2],[3]]``). No ref lookup; escape hatch for
      cells without a name in the pool.

    A ref name is looked up first in ``cells_pool`` (returns a 1-cell
    sequence), then as another key in the same section (recursive
    resolve with cycle detection). Cross-table refs are not supported
    — a ``symbols`` entry's ref can only land in the cells pool or
    another ``symbols`` entry.

    Raises :class:`ConfigurationError` on cycles or unresolvable
    references — those are configuration bugs we want to surface
    loudly at startup. Specs the loader can't make sense of are
    silently skipped.

    ``file`` (optional) is folded into the error messages so a bad
    entry points at the source path.
    """
    # Strip metadata keys.
    merged: dict[str, Any] = {
        k: v for k, v in payload.items()
        if not _is_metadata_key(k)
    }
    out: dict[str, tuple[tuple[int, ...], ...]] = {}
    resolving: list[str] = []
    resolving_set: set[str] = set()

    def resolve(name: str) -> tuple[tuple[int, ...], ...] | None:
        if name in out:
            return out[name]
        if name in cells_pool:
            return (cells_pool[name],)
        spec = merged.get(name)
        if spec is None:
            return None
        if name in resolving_set:
            chain = " -> ".join(resolving + [name])
            location = f"{file}: " if file else ""
            raise ConfigurationError(
                f"{location}composition cycle at {name!r} (chain: {chain})"
            )
        resolving.append(name)
        resolving_set.add(name)
        try:
            cells = _spec_to_cells(spec, resolve, name, file=file)
        finally:
            resolving.pop()
            resolving_set.discard(name)
        if cells is not None:
            out[name] = cells
        return cells

    for key in merged:
        resolve(key)
    return out


def _spec_to_cells(
    spec: Any,
    resolve: Any,
    name: str,
    *,
    file: str | None = None,
) -> tuple[tuple[int, ...], ...] | None:
    """Turn a single spec into its cell sequence.

    Spec shapes:

    * String — single cell ref: ``"a": "c_1"``
    * List of strings — ref list: ``"≠": ["c_4", "="]``
    * List of ints — inline literal single cell: ``"b": [1, 2]``
      (legacy shape; equivalent to ``{"dots": [1, 2]}``)
    * Empty list ``[]`` — empty cell sequence (e.g. neutral tone)
    * Object with ``cells`` — ref list with extras: ``{"cells": [...], "space_before": true}``
    * Object with ``dots`` — inline literal (single ``[1,2]`` or
      multi-cell ``[[1,2],[3]]``); bypasses ref lookup entirely
    """
    refs: list[str] | None = None
    if isinstance(spec, str):
        refs = [spec]
    elif isinstance(spec, list):
        if not spec:
            return ()
        if all(isinstance(x, int) for x in spec):
            return (_to_dots(spec),)
        if all(isinstance(x, str) for x in spec):
            refs = list(spec)
        else:
            return None
    elif isinstance(spec, dict):
        if "cells" in spec:
            if not isinstance(spec["cells"], list):
                location = f"{file}: " if file else ""
                raise ConfigurationError(
                    f"{location}entry {name!r} has a non-list 'cells' value "
                    f"{spec['cells']!r}; wrap a single ref in a list, e.g. "
                    f'["c_235"]. (A bare string was silently dropped before, '
                    f"leaving a symbol with a role but no braille.)"
                )
            refs = [r for r in spec["cells"] if isinstance(r, str)]
        elif "dots" in spec:
            return _coerce_dots_field(spec["dots"])
    if refs is None:
        return None
    acc: list[tuple[int, ...]] = []
    for ref in refs:
        resolved = resolve(ref)
        if resolved is None:
            location = f"{file}: " if file else ""
            raise ConfigurationError(
                f"{location}entry {name!r} references unknown {ref!r}"
            )
        acc.extend(resolved)
    return tuple(acc)


def _resolve_cell_refs(
    refs: object,
    cells_pool: dict[str, tuple[int, ...]],
    *,
    err_ctx: str,
    file: str | None = None,
    required: bool = False,
    blank_ref: str | None = None,
) -> tuple[tuple[int, ...], ...] | None:
    """Resolve a *flat* list of cell-pool refs (``["c_145", ...]``) to dot
    tuples — the shared primitive behind the music + zh override loaders.

    Unlike :func:`_spec_to_cells` there is no recursion or spec-shape
    handling: every element must be a cells-pool ref string, or the
    ``blank_ref`` sentinel (resolves to an empty cell ``()`` — BANA music's
    internal-space groups). ``required`` controls whether a missing
    (``None``) ``refs`` raises or returns ``None``. ``err_ctx`` (e.g.
    ``entry 'sharp'`` / ``char_overrides['重'].cells``) and ``file`` are
    folded into every error so a bad entry points at its source.
    """
    location = f"{file}: " if file else ""
    if refs is None:
        if required:
            raise ConfigurationError(f"{location}{err_ctx} required")
        return None
    if not isinstance(refs, list):
        raise ConfigurationError(
            f"{location}{err_ctx} must be a list of cell-pool refs, "
            f"got {type(refs).__name__}"
        )
    out: list[tuple[int, ...]] = []
    for ref in refs:
        if not isinstance(ref, str):
            raise ConfigurationError(
                f"{location}{err_ctx} contains non-string ref {ref!r}"
            )
        if blank_ref is not None and ref == blank_ref:
            out.append(())
            continue
        dots = cells_pool.get(ref)
        if dots is None:
            raise ConfigurationError(
                f"{location}{err_ctx} references unknown cell {ref!r}"
            )
        out.append(dots)
    return tuple(out)


def _resolve_dots_table(
    payload: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, tuple[int, ...]]:
    """Single-cell table convenience over :func:`_resolve_table`.

    Returns ``{name: dot_tuple}`` — each entry's resolved cell sequence
    is unwrapped to a single tuple. Empty sequences (e.g. the neutral
    tone) are preserved as ``()``. Multi-cell entries are dropped (this
    helper is for tables where every entry is one cell)."""
    resolved = _resolve_table(payload, cells_pool)
    out: dict[str, tuple[int, ...]] = {}
    for k, v in resolved.items():
        if len(v) > 1:
            raise ConfigurationError(
                f"entry {k!r} resolved to {len(v)} cells, but this is a "
                f"single-cell table — each entry must be one cell or empty. "
                f"(A multi-cell entry was silently dropped before, leaving "
                f"the slot missing.)"
            )
        out[k] = v[0] if v else ()
    return out


def _resolve_single(
    spec: Any,
    cells_pool: dict[str, tuple[int, ...]],
) -> tuple[int, ...]:
    """Resolve a single spec to one dot tuple.

    Accepts the same spec shapes as :func:`_spec_to_cells` but expects
    the result to be a single cell. Useful for top-level entries like
    ``number_sign`` / ``decimal_point`` that aren't part of a dict
    table. Returns ``()`` for missing or multi-cell specs.
    """
    if spec is None:
        return ()
    # Placeholder key must NOT start with "_" or be in _METADATA_KEYS,
    # otherwise it'd be filtered as metadata.
    resolved = _resolve_table({"single": spec}, cells_pool)
    seq = resolved.get("single", ())
    if len(seq) > 1:
        raise ConfigurationError(
            f"single-cell config value resolved to {len(seq)} cells: {spec!r} "
            f"— a multi-cell value here was silently dropped before."
        )
    return seq[0] if seq else ()


def _resolve_digits(
    payload: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
    *,
    file: str | None = None,
) -> dict[str, tuple[int, ...]]:
    """digits_lower entries are always single-cell. Resolve cell refs
    (or literal dots) and unwrap the 1-element cell sequence so the
    backend receives a flat ``dict[str, tuple[int, ...]]`` — matching
    the field shape :attr:`BrailleProfile.math_digits_lower` exposes."""
    resolved = _resolve_table(payload, cells_pool, file=file)
    out: dict[str, tuple[int, ...]] = {}
    for name, seq in resolved.items():
        if seq and len(seq) == 1:
            out[name] = seq[0]
    return out


# ---------------------------------------------------------------------------
# Symbol entity normalisation
# ---------------------------------------------------------------------------


def _normalise_symbols_payload(
    payload: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
    *,
    file: str | None = None,
) -> dict[str, Any]:
    """Rewrite a symbols-table payload so all entity-name keys (and
    sibling refs inside ``cells`` arrays) are replaced by their
    resolved Unicode characters.

    The result is shape-compatible with what :func:`_resolve_table`
    expects: ``{<char>: {"cells": [<cell_or_char>, ...], ...}, ...}``.
    Cells-pool refs like ``"c_235"`` are passed through untouched —
    only names that *aren't* in the pool are entity-normalised.

    Raises :class:`ConfigurationError` if any entity name (key or
    sibling ref) is unknown or doesn't resolve to a single Unicode
    character.
    """
    out: dict[str, Any] = {}
    for raw_key, spec in payload.items():
        if _is_metadata_key(raw_key):
            continue
        char = _entity_to_char(raw_key, file=file)
        out[char] = _normalise_symbols_spec(spec, cells_pool, file=file)
    return out


def _normalise_symbols_spec(
    spec: Any, cells_pool: dict[str, tuple[int, ...]], *, file: str | None = None,
) -> Any:
    """Walk one symbol spec and rewrite sibling refs (entity names) to
    their Unicode characters. Cell-pool refs (``c_*``) and structural
    refs that already happen to be single chars are left alone."""
    if isinstance(spec, str):
        return _normalise_one_ref(spec, cells_pool, file=file)
    if isinstance(spec, list):
        return [_normalise_one_ref(ref, cells_pool, file=file) for ref in spec]
    if isinstance(spec, dict):
        out = dict(spec)
        if "cells" in out and isinstance(out["cells"], list):
            out["cells"] = [
                _normalise_one_ref(ref, cells_pool, file=file) for ref in out["cells"]
            ]
        return out
    return spec


def _normalise_one_ref(
    ref: Any, cells_pool: dict[str, tuple[int, ...]], *, file: str | None = None,
) -> Any:
    """Resolve one ref string: pass cell-pool refs through, normalise
    everything else as an entity name."""
    if not isinstance(ref, str):
        return ref
    if ref in cells_pool:
        return ref
    return _entity_to_char(ref, file=file)


def _resolve_nested_structures(
    payload: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
    *,
    file: str | None = None,
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Walk a nested structures payload (``{"fraction": {"bar": [...], ...},
    ...}``) and return a flat dotted-name dict (``{"fraction.bar": ((1,2,5,6),)}``).
    """
    flat_input: dict[str, Any] = {}

    def walk(node: dict[str, Any], prefix: str) -> None:
        for k, v in node.items():
            if _is_metadata_key(k):
                continue
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and not _is_spec_object(v):
                walk(v, full)
            else:
                flat_input[full] = v

    walk(payload, "")
    return _resolve_table(flat_input, cells_pool, file=file)


# ---------------------------------------------------------------------------
# Flag dicts
# ---------------------------------------------------------------------------


def _symbol_spacing_dict(payload: dict[str, Any]) -> dict[str, tuple[bool, bool]]:
    """Extract (space_before, space_after) flags from each symbol entry.

    Only entries that explicitly set at least one of the two flags are
    recorded; everything else returns the default (False, False) at
    lookup time via :meth:`BrailleProfile.math_symbol_spaces`.
    """
    out: dict[str, tuple[bool, bool]] = {}
    for k, v in payload.items():
        if _is_metadata_key(k):
            continue
        if not isinstance(v, dict):
            continue
        before = v.get("space_before")
        after = v.get("space_after")
        if before is None and after is None:
            continue
        out[k] = (bool(before), bool(after))
    return out


def _flag_dict_bool(
    payload: dict[str, Any], flag: str
) -> dict[str, bool]:
    """Extract a boolean flag from each spec entry. Only records keys
    where the flag is explicitly ``True``."""
    out: dict[str, bool] = {}
    for k, v in payload.items():
        if _is_metadata_key(k):
            continue
        if isinstance(v, dict) and v.get(flag) is True:
            out[k] = True
    return out


def _flag_dict_str(
    payload: dict[str, Any], flag: str
) -> dict[str, str]:
    """Extract a string flag from each spec entry."""
    out: dict[str, str] = {}
    for k, v in payload.items():
        if _is_metadata_key(k):
            continue
        if isinstance(v, dict) and isinstance(v.get(flag), str):
            out[k] = v[flag]
    return out


# ---------------------------------------------------------------------------
# Cells pool
# ---------------------------------------------------------------------------


def _load_cells_pool(
    base: Path,
    relative: str | None,
) -> dict[str, tuple[int, ...]]:
    """Load the shared 63-cell pool (``resources/cells.json``).

    Each entry is just a bare list of dot indices — no spec object, no
    metadata other than the standard ``_*`` skipped keys. Returns
    ``{name: tuple_of_int_dots}``; empty if the profile doesn't include
    the pool (callers can still use the inline ``dots`` form per spec).
    """
    if not relative:
        return {}
    payload = _read_json(base / relative)
    out: dict[str, tuple[int, ...]] = {}
    for k, v in payload.items():
        if _is_metadata_key(k):
            continue
        if isinstance(v, list) and all(isinstance(x, int) for x in v):
            out[k] = _to_dots(v)
    return out
