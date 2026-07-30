"""Serialization plumbing shared by both halves of the IR.

:mod:`brailix.ir.document` and :mod:`brailix.ir.inline` each serialize a
dataclass tree to plain JSON-able values and rebuild it, and the *mechanics* of
that are the same on both sides: which fields to omit from a payload, what to
do with a field the deserializer has no branch for, and how to rebuild a typed
child without letting the wrong node class through. What differs is only the
node family — the factory that rebuilds one, the base type it must be, and how
a message names it.

They were two implementations of those mechanics, and they drifted exactly the
way two implementations of one idea do: the block side type-checked its
structural children from the start, the inline side did not, and a
``{"type": "quantity", "number": {"type": "word", ...}}`` payload round-tripped
into a ``Quantity`` holding a ``Word`` until that was found and repaired on its
own. Parameterising the difference is what stops the next repair from landing
on one side again.

Private to the IR layer: nothing here is API, and neither is the module's
existence — it holds no IR types, only the plumbing the two type modules share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


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
