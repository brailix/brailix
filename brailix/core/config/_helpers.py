"""Low-level dependency-free helpers shared by the config subpackage.

Anything in here is consumed by both :mod:`brailix.core.config.loader`
and :mod:`brailix.core.config.validator`. Keeping the shared utilities
in one place avoids cyclic imports between the loader and validator
modules.
"""

from __future__ import annotations

import html.entities
import json
import re
from pathlib import Path
from typing import Any

from brailix.core.errors import ConfigurationError

# Reserved keys at the top of each table file — ignored as metadata.
_METADATA_KEYS: frozenset[str] = frozenset({
    "schema", "name", "cell", "status", "source", "version", "reference",
})


def _is_metadata_key(k: str) -> bool:
    """True if ``k`` should be skipped when iterating a table payload.

    Real entries (e.g. the ``_`` underscore character in the punctuation
    table) are single chars; metadata markers (``_note``, ``_n1_section_``,
    ``_ascii``) are always multi-char. So a length-1 ``_`` is never
    metadata.
    """
    if k in _METADATA_KEYS:
        return True
    return len(k) > 1 and k.startswith("_")


# Legacy flat names → dotted names for the features table. The old
# profile JSON used flat ``math_simplify_fraction``; new JSON groups
# them under ``features.math.simplify_fraction``. Callers can use
# either form via :meth:`BrailleProfile.feature`.
_FEATURE_FLAT_ALIASES: dict[str, str] = {
    "math_simplify_fraction":         "math.simplify_fraction",
    "math_simplify_script":           "math.simplify_script",
    "math_op_spacing":                "math.op_spacing",
    "tone":               "zh.tone",
    "tone_omit_neutral":  "zh.tone_omit_neutral",
    "number_sign":        "zh.number_sign",
}

# Reverse map (dotted → legacy flat) for O(1) reverse lookup.
_FEATURE_DOTTED_TO_FLAT: dict[str, str] = {
    v: k for k, v in _FEATURE_FLAT_ALIASES.items()
}


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from ``path``, normalising every failure mode
    into :class:`ConfigurationError` — the ``load_profile`` contract is
    "malformed profile or referenced table → ConfigurationError".

    Three raw exceptions used to escape here and dodge the framework's
    catch-all: ``json.JSONDecodeError`` for a syntax error (the single
    most common hand-authoring mistake), ``FileNotFoundError`` /
    ``OSError`` for a broken table reference, and — later, inside the
    loaders' ``.items()`` calls — ``AttributeError`` when the top level
    wasn't an object.  Every message carries the file path so the
    author can jump straight to the offending file.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise ConfigurationError(f"{path}: file not found") from e
    except OSError as e:
        raise ConfigurationError(f"{path}: unreadable ({e})") from e
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"{path}: invalid JSON ({e})") from e
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"{path}: top level must be a JSON object, "
            f"got {type(data).__name__}"
        )
    return data


def _to_dots(value: Any) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(d) for d in value)


def _extract_dots(value: Any) -> tuple[int, ...] | None:
    """Extract a dot tuple from either a bare list or a cell-spec object.

    Returns ``()`` for an empty list / empty dots field. Returns
    ``None`` if the value isn't recognizable as a cell spec (so the
    caller can skip it).
    """
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return ()
        if all(isinstance(x, int) for x in value):
            return _to_dots(value)
        return None
    if isinstance(value, dict) and "dots" in value:
        dots = value["dots"]
        return _to_dots(dots) if isinstance(dots, list) else None
    return None


def _dots_dict(payload: dict[str, Any]) -> dict[str, tuple[int, ...]]:
    """Convert a mapping of cell-name → dots into name → tuple.

    Accepts both bare-list (``[1, 2, 4]``) and cell-spec-object
    (``{"dots": [1, 2, 4], ...}``) values. Metadata keys (``_*`` or in
    :data:`_METADATA_KEYS`) are skipped.
    """
    out: dict[str, tuple[int, ...]] = {}
    for k, v in payload.items():
        if _is_metadata_key(k):
            continue
        dots = _extract_dots(v)
        if dots is not None:
            out[k] = dots
    return out


_CODEPOINT_RE = re.compile(r"^U\+([0-9A-Fa-f]{4,6})$")


def _entity_to_char(name: str, *, file: str | None = None) -> str:
    """Resolve a symbols-table key to a single Unicode char.

    Two key forms are accepted:

    * a standard HTML5 entity name (``plus`` / ``conint`` / ...), looked
      up in :data:`html.entities.html5` (which keys entries by
      ``"<name>;"``);
    * a ``U+XXXX`` codepoint literal (4–6 hex digits) — an escape hatch
      for math characters that have **no** HTML5 entity name (e.g.
      ``U+29F5`` ⧵, which is what latex2mathml emits for ``\\setminus``).
      The rest of the table stays on ASCII entity names per
      ``math-redesign.md`` §3.

    Raises :class:`ConfigurationError` if the entity is unknown, the
    codepoint literal is out of range / a surrogate, or the entity
    expands to anything other than a single Unicode codepoint (e.g.
    ``fjlig`` → ``"fj"``).

    ``file`` (optional) is the source path that gets folded into the
    error message so users can jump straight to the offending key.
    """
    m = _CODEPOINT_RE.match(name)
    if m:
        cp = int(m.group(1), 16)
        if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
            location = f"{file}: " if file else ""
            raise ConfigurationError(
                f"{location}codepoint literal {name!r} is not a valid "
                f"Unicode scalar value"
            )
        return chr(cp)
    expanded = html.entities.html5.get(f"{name};")
    if expanded is None:
        location = f"{file}: " if file else ""
        raise ConfigurationError(
            f"{location}unknown HTML5 entity {name!r} in symbols table; "
            f"see https://www.w3.org/TR/xml-entity-names/"
        )
    if len(expanded) != 1:
        location = f"{file}: " if file else ""
        raise ConfigurationError(
            f"{location}entity {name!r} expands to multi-character string "
            f"{expanded!r}; symbols.json keys must resolve to a single "
            f"Unicode character"
        )
    return expanded


def _feature_keys_to_try(key: str) -> list[str]:
    """Return the canonical + legacy variants of a feature key.

    For a key already in the legacy → dotted alias map, return both
    forms. For an unmapped key, return just itself plus its reverse
    alias if any.
    """
    alias = _FEATURE_FLAT_ALIASES.get(key) or _FEATURE_DOTTED_TO_FLAT.get(key)
    if alias is not None:
        return [key, alias]
    return [key]


def _feature_lookup(features: dict[str, Any], key: str, default: Any) -> Any:
    """Walk a (possibly) nested features dict by dotted path.

    A plain (no-dot) key is looked up directly at the top level.
    Dotted keys walk into sub-dicts. Returns ``default`` if any
    segment is missing or hits a non-dict intermediate.
    """
    if "." not in key:
        return features.get(key, default)
    node: Any = features
    for segment in key.split("."):
        if not isinstance(node, dict) or segment not in node:
            return default
        node = node[segment]
    return node
