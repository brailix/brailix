"""Serialization plumbing shared across the IR.

:mod:`brailix.ir.document`, :mod:`brailix.ir.inline` and
:mod:`brailix.ir.braille` each serialize a dataclass tree to plain JSON-able
values and rebuild it, and the *mechanics* of that are the same everywhere:
which fields to omit from a payload, what to do with a field the deserializer
has no branch for, how to rebuild a typed child without letting the wrong node
class through, and how to answer a payload whose shape is simply not the one
the field declares. What differs is only the node family — the factory that
rebuilds one, the base type it must be, and how a message names it.

They were separate implementations of those mechanics, and they drifted
exactly the way separate implementations of one idea do: the block side
type-checked its structural children from the start, the inline side did not,
and a ``{"type": "quantity", "number": {"type": "word", ...}}`` payload
round-tripped into a ``Quantity`` holding a ``Word`` until that was found and
repaired on its own. Parameterising the difference is what stops the next
repair from landing on one side again.

**The deserialization boundary's contract.** A payload is arbitrary decoded
JSON — it comes off disk, off a wire, out of a hand-edited file — so no field
in it is known to have the type the dataclass declares. Every loader here
therefore answers any shape with exactly one of two outcomes: a built object,
or a :class:`ValueError` naming the field and what it should have been.
Anything else means the wrong shape travelled far enough to fail somewhere it
cannot be diagnosed — ``"blocks": null`` raising ``TypeError: 'NoneType'
object is not iterable`` from inside a comprehension, or a ``MathBlock`` whose
``source`` is a list reaching a registry lookup and raising ``unhashable
type``, three layers from the file that said so.

Private to the IR layer: nothing here is API, and neither is the module's
existence — it holds no IR types, only the plumbing the type modules share.
"""

from __future__ import annotations

import functools
import types
import typing
from dataclasses import fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence


def is_omittable(value: Any, default: Any) -> bool:
    """True if ``value`` should be omitted from a ``to_dict`` payload: it is
    ``None``, equal to its field ``default``, or an empty sequence.

    (``default_factory`` list/tuple fields report ``default`` == MISSING, so the
    equality check alone misses an empty list — the explicit empty-sequence test
    covers them.) Shared by the inline and block ``to_dict`` field loops.
    """
    return (
        value is None
        or value == default
        or (isinstance(value, (list, tuple)) and not value)
    )


def reject_unhandled_nested_payload(key: str, value: Any) -> None:
    """Guard a deserializer's fall-through against an IR payload nobody rebuilt.

    Serialization is type-driven — a serializer recurses into any IR node
    automatically. Deserialization dispatches on field *name*, so a newly added
    IR-node-valued field serializes correctly yet would fall through and be
    stored as a raw ``dict`` (or list of ``dict``): a silent round-trip
    corruption no per-type test catches.

    A serialized IR node is always a ``dict`` and a list of them a list of
    ``dict``; every scalar / span / XML-tree field deserializes from something
    else (a span is a 2-int list, ``math`` / ``score`` a string). So a
    fall-through ``dict`` or list-of-``dict`` means exactly "an IR-payload
    field nobody registered" — raise so the omission surfaces at its source
    instead of corrupting the tree. Mirrors the loud-drop guard on the
    ``to_dict`` side (:func:`brailix.ir.document._is_ir_payload`).
    """
    if isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(v, dict) for v in value)
    ):
        raise ValueError(
            f"field {key!r} carries a nested IR payload but has no "
            f"deserialization branch; register it in the deserializer — "
            f"serialization is type-driven while deserialization dispatches "
            f"on field name, so the two must be kept in sync"
        )


def typed_child[NodeT](
    payload: Any,
    *,
    expected: type[NodeT],
    factory: Callable[[dict[str, Any]], Any],
    label: str,
    kind: str,
) -> NodeT:
    """Rebuild ``payload`` through ``factory`` and verify it is an ``expected``.

    ``NodeT`` is the node family the call works in — a
    :class:`~brailix.ir.document.Block` or an
    :class:`~brailix.ir.inline.InlineNode`. Unbounded on purpose: this module
    must not import either type module, since both import *it*.

    A declared field type is not enforced by anything at runtime: the
    deserializer dispatches on the field *name* and rebuilds whatever the
    payload's own ``type`` tag says it is. So a payload can put a ``Paragraph``
    in a ``TableRow.cells`` list, or a ``Word`` in ``Quantity.number``, and the
    result type-checks at the dataclass level while breaking every consumer
    that reads the field — the backend writes the wrong cells for it, or none.

    A payload that is already a built node passes through, so a caller
    assembling a tree by hand can hand over the node rather than its dict form.

    ``label`` names the field in the error the way its own side names things
    (``"Table.rows"``, ``"inline field 'number'"``) and ``kind`` is the word for
    what the offending payload claimed to be (``"block"`` / ``"node"``), so one
    implementation still produces each side's diagnostic.
    """
    child = factory(payload) if isinstance(payload, dict) else payload
    if not isinstance(child, expected):
        tag = payload.get("type") if isinstance(payload, dict) else type(payload).__name__
        raise TypeError(
            f"{label} expects {expected.__name__}; got "
            f"{type(child).__name__} ({kind} type {tag!r})"
        )
    return child


# ---------------------------------------------------------------------------
# Wire-shape validation
# ---------------------------------------------------------------------------


def require_payload_object(payload: Any, what: str) -> dict[str, Any]:
    """Check a payload is a mapping at all, and return it.

    Every loader's first move is ``payload.get(...)``, so a payload that is a
    number or a list fails with ``AttributeError: 'int' object has no
    attribute 'get'`` — from whichever line happened to read first, naming
    neither the entry nor its container. Nested entries are where this
    actually bites: a ``cells`` list is walked entry by entry, and only the
    entry knows it was the fourth cell of the second block.

    ``what`` names the thing being loaded for the message (``"braille
    cell"``).
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"{what} payload must be an object, got {type(payload).__name__}"
        )
    return payload


def require_payload_type(payload: Any, expected: str, what: str) -> dict[str, Any]:
    """Check a payload is a mapping tagged ``expected``, and return it.

    Every IR root writes a ``"type"`` constant and every schema declares it,
    which only means something if the *loader* reads it back. Three braille-IR
    loaders did not, so any object of a similar shape loaded as any of them and
    was written back out under a different tag — a ``BrailleSequence`` payload
    became a ``BrailleDocument``, silently, on a round trip.

    ``what`` names the thing being loaded for the message
    (``"braille document"``).
    """
    mapping = require_payload_object(payload, what)
    tag = mapping.get("type")
    if tag != expected:
        raise ValueError(
            f"{what} payload must carry type {expected!r}, got {tag!r}"
        )
    return mapping


def payload_list(payload: Mapping[str, Any], key: str, what: str) -> Sequence[Any]:
    """``payload[key]`` as a list, defaulting to empty when absent.

    ``None`` is refused rather than treated as absent: a payload that spells a
    field ``null`` is saying something different from one that omits it, and
    the loaders used to let it through to a ``for`` loop that raised
    ``TypeError: 'NoneType' object is not iterable`` from a comprehension with
    no idea which field it was reading.
    """
    value = payload.get(key)
    if value is None and key not in payload:
        return ()
    if not isinstance(value, list):
        raise ValueError(
            f"{what} field {key!r} must be a list, got "
            f"{type(value).__name__}"
        )
    return value


def payload_mapping(payload: Mapping[str, Any], key: str, what: str) -> dict[str, Any]:
    """``payload[key]`` as a fresh dict, defaulting to empty when absent.

    The mapping counterpart of :func:`payload_list`, and it existed for the
    same reason: ``dict(None)`` and ``dict([1, 2])`` both raise a ``TypeError``
    that names neither the field nor the file.
    """
    value = payload.get(key)
    if value is None and key not in payload:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f"{what} field {key!r} must be an object, got "
            f"{type(value).__name__}"
        )
    return dict(value)


# Annotations this module cannot turn into a runtime check — either because
# there is nothing to check (``Any``) or because the declared type is not what
# the *converted* value is. Checked by name because the annotations are
# strings under ``from __future__ import annotations`` and resolving one that
# names a type this module must not import would close an import cycle.
_UNCHECKED_ANNOTATIONS = frozenset({"Any", "typing.Any"})


def _runtime_types(annotation: Any) -> tuple[type, ...] | None:
    """Runtime-checkable types for one resolved annotation, or None.

    Handles the shapes the IR dataclasses actually declare: a plain class, an
    optional / union of them, and a parameterised container (``list[Foo]`` →
    ``list``, whose *entries* are validated by the family's own typed-child
    check, not here). Anything else — ``Any``, a callable, a type variable —
    returns None and is left unchecked, because a guard that guesses is worse
    than one that declines.
    """
    if annotation is Any:
        return None
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        out: list[type] = []
        for arg in typing.get_args(annotation):
            resolved = _runtime_types(arg)
            if resolved is None:
                return None
            out.extend(resolved)
        return tuple(out)
    if origin is not None:
        return (origin,) if isinstance(origin, type) else None
    if annotation is type(None):
        return (type(None),)
    return (annotation,) if isinstance(annotation, type) else None


@functools.cache
def _wire_types(cls: type) -> dict[str, tuple[type, ...]]:
    """Per-field runtime types for ``cls``, derived from its annotations.

    Derived rather than hand-listed, so a field added to an IR dataclass is
    covered the moment it is declared — a hand-written table is a table that
    is one field out of date the first time anybody adds one. Cached per
    class: ``typing.get_type_hints`` re-resolves the module namespace on every
    call, and a document load asks this once per field per node.

    Fields whose annotation this module cannot check are simply absent from
    the result.
    """
    try:
        hints = typing.get_type_hints(cls)
    except Exception:  # noqa: BLE001 — an unresolvable hint just means no check
        return {}
    out: dict[str, tuple[type, ...]] = {}
    for f in fields(cls):
        if str(f.type) in _UNCHECKED_ANNOTATIONS:
            continue
        resolved = _runtime_types(hints.get(f.name, Any))
        if resolved:
            out[f.name] = resolved
    return out


def check_wire_value(cls: type, key: str, value: Any, what: str) -> Any:
    """Verify ``value`` matches the declared type of ``cls.key``; return it.

    Runs on the value the deserializer has already *converted* (a span is a
    :class:`~brailix.core.span.Span` by then, an XML-tree field an
    ``ET.Element``), so the dataclass's own declaration is the right thing to
    check against and the two can never disagree.

    ``bool`` is refused for a field that does not declare it, for the reason
    it is refused in :func:`brailix.core.measure.as_positive_finite`: it is an
    ``int`` subclass, so ``{"level": true}`` would otherwise build a level-1
    heading out of a payload that says no such thing.

    Raises :class:`ValueError` — the malformed-payload signal for this whole
    boundary. What it stops is not a crash but the shapes that used to load
    *successfully*: a ``MathBlock`` whose ``source`` was a list (accepted here,
    ``unhashable type`` at the registry lookup much later), a ``List`` whose
    ``ordered`` was the string ``"false"`` (accepted here, truthy everywhere
    after).
    """
    allowed = _wire_types(cls).get(key)
    if allowed is None:
        return value
    if isinstance(value, bool) and bool not in allowed:
        raise ValueError(
            f"{what} field {key!r} must be "
            f"{_type_names(allowed)}, got a bool ({value!r})"
        )
    if not isinstance(value, allowed):
        raise ValueError(
            f"{what} field {key!r} must be "
            f"{_type_names(allowed)}, got {type(value).__name__}"
        )
    return value


def _type_names(allowed: Iterable[type]) -> str:
    names = [t.__name__ if t is not type(None) else "null" for t in allowed]
    return " or ".join(dict.fromkeys(names))
