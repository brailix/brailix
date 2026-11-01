"""Low-level dependency-free helpers shared by the config subpackage.

Anything in here is consumed by both :mod:`brailix.core.config.loader`
and :mod:`brailix.core.config.validator`. Keeping the shared utilities
in one place avoids cyclic imports between the loader and validator
modules.
"""

from __future__ import annotations

import copy as _copy
import html.entities as _html_entities
import json as _json
import re as _re
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.errors import ConfigurationError

if _TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import Any

    from brailix.core.errors import WarningCollector

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
            data = _json.load(f)
    except FileNotFoundError as e:
        raise ConfigurationError(f"{path}: file not found") from e
    except OSError as e:
        raise ConfigurationError(f"{path}: unreadable ({e})") from e
    except _json.JSONDecodeError as e:
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


_CODEPOINT_RE = _re.compile(r"^U\+([0-9A-Fa-f]{4,6})$")


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
    expanded = _html_entities.html5.get(f"{name};")
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


def _feature_lookup(features: dict[str, Any], key: str, default: Any) -> Any:
    """Walk a (possibly) nested features dict by dotted path.

    A plain (no-dot) key is looked up directly at the top level.
    Dotted keys walk into sub-dicts. Returns ``default`` if any
    segment is missing or hits a non-dict intermediate.

    One key, one place to look. Six flags used to have a second, flat
    spelling as well (``tone`` for ``zh.tone``, ``math_op_spacing`` for
    ``math.op_spacing``, ...), tried in turn — which meant a flag could be
    read under a name the shipped profiles never write, and it was: the
    Chinese tone policy asked for ``"tone"`` *deliberately*, because the flat
    form is tried first and a test setting a top-level ``features["tone"]``
    would otherwise not be seen. Production code choosing its lookup key so a
    test's override lands is the alias map earning its keep by breaking the
    thing it was supposed to be invisible to. The group a flag lives in is
    part of its name now, on both sides of the read.
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

# The value type every feature key the library itself reads declares —
# enumerated exhaustively (a reader's ``feature(key, default)`` call is the
# declaration; a key shipped in a profile JSON but read by nobody is NOT
# here, and neither is a plugin's own flag). This is what lets
# :func:`_feature_merge` reject ``{"zh.tone": "false"}`` — the string a
# serialized checkbox often carries, truthy where a bool was meant, so the
# override used to be silently accepted while the reader's ``if`` kept the
# flag ON. Keys absent from the table are still overridable when the
# profile itself declares them (a plugin flag lives in the profile's own
# ``features``); only a key that is neither read by the library nor
# declared by the profile is unknown.
_FEATURE_TYPES: dict[str, tuple[type, ...]] = {
    "input.docx.detect_chemistry": (bool,),
    "input.docx.mathtype_fallback": (str,),
    "list.ordered_marker": (str,),
    "list.unordered_marker": (str,),
    "math.atomic_script_lower_digit": (bool,),
    "math.number_sign": (bool,),
    "math.op_spacing": (bool,),
    "math.simplify_fraction": (bool,),
    "math.simplify_script": (bool,),
    "music.accidental_persist_in_measure": (bool,),
    "music.bar_line_print": (str,),
    "music.dot_form": (str,),
    "music.dynamics_form": (str,),
    "music.expand_repeats": (bool,),
    "music.in_accord_form": (str,),
    "music.in_accord_marker": (bool,),
    "music.lyrics_form": (str,),
    "music.measure_separator": (str,),
    "music.octave_rule": (str,),
    "music.show_chord_symbols": (bool,),
    "music.show_clef": (bool,),
    "music.show_dynamics": (bool,),
    "music.show_fingering": (bool,),
    "music.show_key_signature": (bool,),
    "music.show_lyrics": (bool,),
    "music.show_ornaments": (bool,),
    "music.show_string_techniques": (bool,),
    "music.show_time_signature": (bool,),
    "music.show_words": (bool,),
    "music.tuplet_form": (str,),
    "music.value_signs": (bool,),
    "music.volta_style": (str,),
    "number.sign": (bool,),
    "zh.tone": (bool,),
    "zh.tone_omit_neutral": (bool,),
    "zh.tone_strategy": (str,),
}

# Closed value sets, for the (rare) string flags whose reader enumerates its
# accepted values. ``zh.tone_strategy`` is deliberately NOT here: the
# strategy registry is open (third parties register their own), so any
# string is structurally valid and an unknown name fails loudly at the
# reader with the registered list in the message.
_FEATURE_VALUE_ENUMS: dict[str, frozenset[str]] = {
    # Mirrors ``brailix.backend.music._VALID_OCTAVE_RULES`` — core cannot
    # import the backend (layering), so the copy is deliberate; an unknown
    # value is warned-and-rejected here either way, never silently kept.
    "music.octave_rule": frozenset({"interval16", "every_measure", "always"}),
}

# The two codes a rejected feature override reports under, declared in the
# enumerable form (name == value) the i18n scanner reads — the emit site
# passes them through a variable, which is invisible to the literal scan,
# so without this declaration the bilingual-entry guard would miss them.
CONFIG_FEATURE_UNKNOWN_KEY = "CONFIG_FEATURE_UNKNOWN_KEY"
CONFIG_FEATURE_BAD_VALUE = "CONFIG_FEATURE_BAD_VALUE"


def _feature_override_problem(
    features: dict[str, Any],
    key: str,
    value: Any,
) -> tuple[str, str, bool] | None:
    """What is questionable about ``value`` as an override for ``key``, as a
    ``(warning code, reason, reject)`` triple, or ``None`` when it is
    unremarkable.

    Three verdicts, in order:

    * a key in :data:`_FEATURE_TYPES` must get a value of its declared
      type (and inside a closed value set, where one is declared) —
      anything else is ``CONFIG_FEATURE_BAD_VALUE`` **and rejected**;
    * otherwise the key should be **declared by the profile itself** —
      resolvable inside ``features`` — and a declared non-null value pins
      the override to its own type (a plugin flag the profile ships as a
      bool stays bool-typed; ``None`` pins nothing, being "unset") — a
      type clash is ``CONFIG_FEATURE_BAD_VALUE`` **and rejected**;
    * otherwise the key is unknown: no library reader and no profile
      declaration. That is ``CONFIG_FEATURE_UNKNOWN_KEY``, **warned but
      still written** — the features table is a documented open extension
      point (a plugin's own flag is addressed by its bare name), so an
      unknown key is not refused; what used to be missing was the *hint*,
      because the classic case is a typo (``"zh.ton"`` for ``"zh.tone"``)
      that silently did nothing.

    The value itself is never coerced ("false" → False): a silent guess is
    exactly the failure mode these checks exist to replace.
    """
    allowed = _FEATURE_TYPES.get(key)
    if allowed is not None:
        if isinstance(value, bool) and bool not in allowed:
            return (
                CONFIG_FEATURE_BAD_VALUE,
                f"flag {key!r} takes "
                f"{'/'.join(t.__name__ for t in allowed)}, got bool",
                True,
            )
        if not isinstance(value, allowed):
            return (
                CONFIG_FEATURE_BAD_VALUE,
                f"flag {key!r} takes "
                f"{'/'.join(t.__name__ for t in allowed)}, got "
                f"{type(value).__name__} {value!r}",
                True,
            )
        enum = _FEATURE_VALUE_ENUMS.get(key)
        if enum is not None and value not in enum:
            return (
                CONFIG_FEATURE_BAD_VALUE,
                f"flag {key!r} takes one of {sorted(enum)}, got {value!r}",
                True,
            )
        return None
    sentinel = object()
    declared = _feature_lookup(features, key, sentinel)
    if declared is not sentinel:
        if declared is not None and type(value) is not type(declared):
            return (
                CONFIG_FEATURE_BAD_VALUE,
                f"flag {key!r} is declared as "
                f"{type(declared).__name__} by this profile, got "
                f"{type(value).__name__} {value!r}",
                True,
            )
        return None
    return (
        CONFIG_FEATURE_UNKNOWN_KEY,
        f"unknown feature key {key!r}: no library reader knows it and the "
        f"loaded profile does not declare it (typo in a dotted name?); the "
        f"value is written but nothing reads it",
        False,
    )


def _feature_merge(
    features: dict[str, Any],
    overrides: Mapping[str, Any],
    *,
    warnings: WarningCollector | None = None,
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

    Raises :class:`ConfigurationError` for a malformed override — an
    intermediate segment that exists and is not a dict
    (``"zh.tone.strict"`` where ``zh.tone`` is a bool): silently replacing
    the scalar with a group would leave the original feature unreadable and
    the override looking like it took effect; or a value that is not a JSON
    scalar (see :data:`_FEATURE_VALUE_TYPES`).

    A value the flag's type table rejects (:data:`_FEATURE_TYPES` /
    :func:`_feature_override_problem`) — ``{"zh.tone": "false"}``, the
    string a serialized checkbox often carries, where a bool is expected —
    is **dropped with a warning** rather than applied or raised on: the
    pipeline this merge feeds must still build over a recoverable mistake,
    but the mistake must announce itself, and the flag keeps the value the
    profile's readers actually understand. An override under a key nobody
    knows is warned (the typo hint) and still written, because the features
    table is an open extension point. ``warnings`` is the
    :class:`~brailix.core.errors.WarningCollector` to report to (the
    Pipeline path passes its construction-time collector, so the
    diagnostics ride every compile's results); without one the diagnostics
    are skipped and the semantics alone apply.

    The single write point for feature overrides, which is why all the
    checks live here rather than at one of the two entry points
    (:meth:`BrailleProfile.with_features` and the ``profile_features``
    pipeline field) — a check on one of those is a check the other is missing.
    """
    merged = _copy.deepcopy(features)
    for key, value in overrides.items():
        if not isinstance(value, _FEATURE_VALUE_TYPES):
            raise ConfigurationError(
                f"feature override {key!r}: a feature flag is a scalar "
                f"(str / int / float / bool / None), got "
                f"{type(value).__name__}. A dotted key already addresses a "
                f"nested flag — write {key + '.<flag>'!r} for each one "
                f"instead of handing over a container"
            )
        problem = _feature_override_problem(features, key, value)
        if problem is not None:
            code, reason, reject = problem
            if warnings is not None:
                verb = "rejected" if reject else "written anyway"
                warnings.warn(
                    code=code,
                    message=f"feature override {verb}: {reason}",
                    surface=key,
                    source="config",
                )
            if reject:
                continue
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
