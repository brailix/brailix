"""Low-level dependency-free helpers shared by the config subpackage.

Anything in here is consumed by both :mod:`brailix.core.config.loader`
and :mod:`brailix.core.config.validator`. Keeping the shared utilities
in one place avoids cyclic imports between the loader and validator
modules.
"""

from __future__ import annotations

import copy
import html.entities
import json
import re
from collections.abc import Mapping
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
    dots = tuple(int(d) for d in value)
    # Validate at the single point every dot tuple is built, so a typo'd dot
    # (dot 7 mistyped as 9) or a duplicate fails loud at load instead of
    # rendering a non-braille glyph / crashing later in a raw dots_to_char
    # path that bypasses BrailleCell's own check.
    seen: set[int] = set()
    for d in dots:
        if not 1 <= d <= 8:
            raise ConfigurationError(
                f"braille dot {d} out of range (must be 1..8) in {value!r}"
            )
        if d in seen:
            raise ConfigurationError(f"braille dot {d} repeated in {value!r}")
        seen.add(d)
    return dots


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


# What a feature override may be set to. A flag is a **scalar**: the shipped
# profiles' feature leaves are all ``bool`` or ``str``, and the nesting a
# features dict has is *grouping* — which is exactly what the dotted key
# addresses. Two things follow from admitting only these, and both are the
# point:
#
# * A container value would be ambiguous by construction. ``_feature_lookup``
#   walks into a dict, so ``{"plugin.opt": {"enabled": False}}`` does not set
#   one flag to a structured value — it replaces the whole ``plugin.opt``
#   *group*, and ``feature("plugin.opt.enabled")`` then answers from it. An
#   override is documented as "the same standard with a named flag set
#   differently", not as a way to graft a subtree.
# * Scalars are immutable, which closes the aliasing hole. A ``dict`` or
#   ``list`` value stayed shared with whoever passed it: ``MappingProxyType``
#   freezes the mapping, not the objects inside it, and the merge below wrote
#   the value straight through. The caller could then mutate it after
#   construction and change what a built pipeline compiles — past a
#   fingerprint computed from the old contents, so the same ``source_hash``
#   now stood for two different outputs. That is the same hole
#   ``_freeze_seg_dict`` exists to close for the segmentation dictionary; here
#   there is no legitimate container to freeze, so the value is refused
#   instead.
_FEATURE_VALUE_TYPES = (str, int, float, bool, type(None))


def _feature_merge(
    features: dict[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a deep copy of ``features`` with each override key written in.

    The write side of :func:`_feature_lookup`, and keyed the same way: a
    dotted key walks into sub-dicts (``"zh.tone"`` sets
    ``features["zh"]["tone"]``), a plain key sets a top-level entry. Missing
    intermediate dicts are created, so a key can name a group the profile
    never declared.

    A **deep copy**, not a shallow one: the caller's profile keeps a nested
    dict per feature group, and mutating one group in place would reach back
    into the profile the override was derived from — which is exactly the
    object a caller may still be compiling with.

    Raises :class:`ConfigurationError` for either malformed override:

    * an intermediate segment that exists and is not a dict
      (``"zh.tone.strict"`` where ``zh.tone`` is a bool): silently replacing
      the scalar with a group would leave the original feature unreadable and
      the override looking like it took effect;
    * a value that is not a JSON scalar (see :data:`_FEATURE_VALUE_TYPES`).

    The single write point for feature overrides, which is why both checks
    live here rather than at one of the two entry points
    (:meth:`BrailleProfile.with_features` and the ``profile_features``
    pipeline field) — a check on one of those is a check the other is missing.
    """
    merged = copy.deepcopy(features)
    for key, value in overrides.items():
        if not isinstance(value, _FEATURE_VALUE_TYPES):
            raise ConfigurationError(
                f"feature override {key!r}: a feature flag is a scalar "
                f"(str / int / float / bool / None), got "
                f"{type(value).__name__}. A dotted key already addresses a "
                f"nested flag — write {key + '.<flag>'!r} for each one "
                f"instead of handing over a container"
            )
        *parents, leaf = key.split(".")
        node = merged
        for depth, segment in enumerate(parents):
            child = node.get(segment)
            if child is None:
                child = {}
                node[segment] = child
            elif not isinstance(child, dict):
                path = ".".join(parents[: depth + 1])
                raise ConfigurationError(
                    f"feature override {key!r}: {path!r} is a "
                    f"{type(child).__name__}, not a group of features"
                )
            node = child
        node[leaf] = value
    return merged
