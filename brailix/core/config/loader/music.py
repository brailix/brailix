"""Music tables loader (BANA 2015 Music Braille Code).

Music resources live in ``resources/music/<topic>.json``. Each file has
one body key (the topic name, e.g. ``"notes"``) whose value is a dict
of entity name -> entry. Each entry's ``cells`` is a list of cell refs
from ``resources/cells.json`` (e.g. ``"c_13456"``) plus the sentinel
``"c_blank"`` for an empty cell (BANA prints two cell groups with an
internal space character).

Subdirectories: ``tables.music`` values can be either a file path
(``"resources/music/notes.json"``) or a directory path (e.g.
``"resources/music/instruments/"``) — in the latter case every JSON
file inside is loaded under the ``<prefix>.<filename>`` topic key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brailix.core.config._helpers import _is_metadata_key, _read_json
from brailix.core.config.loader._refs import _resolve_cell_refs


def _load_music_tables(
    base: Path,
    entry: dict[str, Any],
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, dict[str, tuple[tuple[int, ...], ...]]]:
    """Load every music resource referenced from ``tables.music``.

    Returns a nested dict: ``music[topic][entity_name] -> cells``.
    Topic keys are the file stem (``"notes"``, ``"octaves"``, ...) or
    ``"<dir>.<stem>"`` for files under a subdirectory.

    A value in ``entry`` may point at either:

    * A single JSON file: ``"resources/music/notes.json"`` -> topic
      ``"notes"`` (or matches the dict key — whichever loaded last wins,
      but in practice the two agree).
    * A directory: ``"resources/music/instruments/"`` -> every
      ``*.json`` inside is loaded under topic ``"<key>.<stem>"``.
    """
    if not entry:
        return {}
    out: dict[str, dict[str, tuple[tuple[int, ...], ...]]] = {}
    for key, relative in entry.items():
        if not isinstance(relative, str) or not relative:
            continue
        target = base / relative
        if target.is_dir():
            for child in sorted(target.glob("*.json")):
                topic = f"{key}.{child.stem}"
                out[topic] = _load_one_music_file(child, cells_pool)
        else:
            out[key] = _load_one_music_file(target, cells_pool)
    return out


def _load_one_music_file(
    path: Path,
    cells_pool: dict[str, tuple[int, ...]],
) -> dict[str, tuple[tuple[int, ...], ...]]:
    """Parse one music resource JSON into ``{entity_name: cells_tuple}``.

    The file has one body key (anything besides schema/name/cell/status/
    source); its value maps entity name to an entry with a ``cells`` list.
    Each ref resolves through ``cells_pool``; ``c_blank`` is the empty-
    cell sentinel (stored as the empty dot tuple ``()`` inside the cell
    sequence).
    """
    payload = _read_json(path)
    body = _music_body(payload)
    out: dict[str, tuple[tuple[int, ...], ...]] = {}
    for entity_name, entry in body.items():
        if not isinstance(entry, dict):
            continue
        refs = entry.get("cells")
        if not isinstance(refs, list):
            continue
        cells = _resolve_music_cells(refs, cells_pool, entity_name, path)
        out[entity_name] = cells
    return out


def _music_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the single non-metadata body key from a music resource."""
    META = {"schema", "name", "cell", "status", "source"}
    for k, v in payload.items():
        if k in META:
            continue
        if _is_metadata_key(k):
            continue
        if isinstance(v, dict):
            return v
    return {}


def _resolve_music_cells(
    refs: list[Any],
    cells_pool: dict[str, tuple[int, ...]],
    entity_name: str,
    path: Path,
) -> tuple[tuple[int, ...], ...]:
    """Resolve a music entry's ``cells`` list to a cell sequence.

    Accepts cell-pool refs (``"c_13456"``) and the ``"c_blank"`` sentinel
    (an empty cell — BANA prints two cell groups with an internal space).
    Delegates to the shared :func:`_resolve_cell_refs`; unknown refs raise.
    """
    return (
        _resolve_cell_refs(
            refs,
            cells_pool,
            err_ctx=f"entry {entity_name!r}",
            file=str(path),
            blank_ref="c_blank",
        )
        or ()
    )


def _load_music_specs(
    base: Path, entry: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Load auxiliary, non-cells *spec* sections from music resources.

    A music file's body is the single cells topic (entity -> ``{cells}``).
    Some topics also carry a declarative rule section — a top-level
    ``_``-prefixed dict the cells loader deliberately skips (it's metadata
    to ``_music_body``).  Example: ``chord_symbols.json``'s ``_kind_spec``
    maps a MusicXML chord ``<kind>`` to its emit recipe (entity refs +
    letter/digit runs), so the backend drives chord spelling from data
    instead of a hard-coded table.

    Returns ``{topic: {section: raw_dict}}`` (section name with its
    leading underscores stripped), mirroring :func:`_load_music_tables`'s
    topic-keying (``"<dir>.<stem>"`` for subdirectory files).
    """
    if not entry:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, relative in entry.items():
        if not isinstance(relative, str) or not relative:
            continue
        target = base / relative
        files = sorted(target.glob("*.json")) if target.is_dir() else [target]
        for child in files:
            specs = _music_specs_from(child)
            if specs:
                topic = f"{key}.{child.stem}" if target.is_dir() else key
                out[topic] = specs
    return out


def _music_specs_from(path: Path) -> dict[str, Any]:
    """Return a music file's top-level ``_``-prefixed dict sections, raw.

    Keyed by the section name with leading underscores stripped (so
    ``"_kind_spec"`` is exposed as ``"kind_spec"``).  Cell-bearing
    metadata lives *inside* entries (``_print`` / ``_ascii``), never at
    the top level, so only genuine spec sections match here.
    """
    payload = _read_json(path)
    meta = {"schema", "name", "cell", "status", "source"}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in meta or not k.startswith("_") or not isinstance(v, dict):
            continue
        out[k.lstrip("_")] = v
    return out
