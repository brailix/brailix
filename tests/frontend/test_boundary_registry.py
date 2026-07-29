"""``boundary_registry``'s generation counts every way its contents change.

A boundary handler changes the braille — it inserts the space between a hanzi
run and a Latin word, the connector before a number — so
:attr:`brailix.pipeline.Pipeline.fingerprint` folds the registry's
``generation`` in, and every ``CompiledBlock.source_hash`` is salted with that
fingerprint. The whole chain rests on one thing: **no way to change the table
leaves the generation behind.**

That is not free, because the registry is a ``dict`` subclass and ``dict``'s
mutators are C-level and independent — overriding ``__setitem__`` does not make
``update`` or ``|=`` route through it. ``__ior__`` was the one that had been
missed, and it is a documented spelling::

    boundary_registry |= {"zh": my_handler}

which swapped the handler while the generation, the fingerprint and every
``source_hash`` stood still — the "same key, different braille" a cache cannot
defend against, and exactly what folding the generation in was meant to stop.

Two guards here. The first walks every mutating spelling and asserts the whole
chain moves. The second is structural: any ``dict`` method the subclass does
*not* override has to be on a reviewed read-only list, so the next one cannot
be missed by omission the way ``__ior__`` was.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from brailix import Pipeline
from brailix.frontend import _BoundaryRegistry, boundary_registry
from brailix.ir.document import Paragraph


@contextmanager
def _registry_restored() -> Iterator[None]:
    """The registry is process-global; put it back byte for byte afterwards."""
    saved = dict(boundary_registry)
    try:
        yield
    finally:
        boundary_registry.clear()
        boundary_registry.update(saved)


def _passthrough(nodes: list[object], profile: object) -> list[object]:
    return list(nodes)


# Every way a caller can change what the table holds. Each one really changes
# the contents — the assertions below check that against a snapshot, so an
# entry that turned into a no-op would fail rather than pass vacuously.
_MUTATIONS: list[tuple[str, Callable[[_BoundaryRegistry], None]]] = [
    ("setitem", lambda r: r.__setitem__("zh", _passthrough)),
    ("delitem", lambda r: r.__delitem__("zh")),
    ("update-mapping", lambda r: r.update({"zh": _passthrough})),
    ("update-kwargs", lambda r: r.update(zh=_passthrough)),
    ("ior", lambda r: r.__ior__({"zh": _passthrough})),
    ("pop", lambda r: r.pop("zh")),
    ("popitem", lambda r: r.popitem()),
    ("clear", lambda r: r.clear()),
    ("setdefault-new-key", lambda r: r.setdefault("qq", _passthrough)),
]


@pytest.mark.parametrize("name,mutate", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_every_mutation_moves_generation_fingerprint_and_source_hash(
    name: str, mutate: Callable[[_BoundaryRegistry], None]
) -> None:
    with _registry_restored():
        pipe = Pipeline(profile="cn_current", resolver="null")
        before_contents = dict(boundary_registry)
        before_generation = boundary_registry.generation
        before_fingerprint = pipe.fingerprint
        before_hash = pipe.translate_block(Paragraph(text="x轴")).source_hash

        mutate(boundary_registry)

        assert dict(boundary_registry) != before_contents, (
            f"{name!r} did not actually change the table — the assertions "
            f"below would pass without proving anything"
        )
        assert boundary_registry.generation != before_generation, (
            f"{name!r} changed the handler table without moving the "
            f"generation: the compile that follows it is indistinguishable "
            f"from one under the old handlers"
        )
        assert pipe.fingerprint != before_fingerprint
        after_hash = pipe.translate_block(Paragraph(text="x轴")).source_hash
        assert after_hash != before_hash


def test_setdefault_on_an_existing_key_is_not_a_mutation() -> None:
    """The deliberate exception: ``setdefault`` on a key already present
    returns the existing handler and changes nothing, so it must *not* move the
    generation — an invalidation with no cause would drop every live cache."""
    with _registry_restored():
        pipe = Pipeline(profile="cn_current", resolver="null")
        before_generation = boundary_registry.generation
        before_fingerprint = pipe.fingerprint

        kept = boundary_registry.setdefault("zh", _passthrough)

        assert kept is not _passthrough
        assert boundary_registry.generation == before_generation
        assert pipe.fingerprint == before_fingerprint


# The other half of the same rule ``setdefault`` was already held to: a call
# that leaves the table exactly as it was is not a mutation, whatever its name.
# Each entry below really is a no-op — the assertions check the contents are
# unchanged too, so a case that started doing something would fail rather than
# pass vacuously.
_NO_OPS: list[tuple[str, Callable[[_BoundaryRegistry], None]]] = [
    ("update-empty-mapping", lambda r: r.update({})),
    ("update-no-args", lambda r: r.update()),
    ("ior-empty", lambda r: r.__ior__({})),
    ("pop-missing-with-default", lambda r: r.pop("nope", None)),
    ("reassign-the-same-handler", lambda r: r.__setitem__("zh", r["zh"])),
    ("update-with-the-same-handlers", lambda r: r.update(dict(r))),
    ("ior-with-the-same-handler", lambda r: r.__ior__({"zh": r["zh"]})),
]


@pytest.mark.parametrize("name,call", _NO_OPS, ids=[n[0] for n in _NO_OPS])
def test_a_call_that_changes_nothing_does_not_invalidate(
    name: str, call: Callable[[_BoundaryRegistry], None]
) -> None:
    """The generation is what drops every cached block, so bumping it without
    cause is a full recompile of the open document for nothing.

    Re-registering the handler that is already there is the realistic one: a
    front-end that re-runs its registration on reload (or an import executed
    twice) invalidated the whole cache while the braille it produces could not
    possibly differ.
    """
    with _registry_restored():
        pipe = Pipeline(profile="cn_current", resolver="null")
        before_contents = dict(boundary_registry)
        before_generation = boundary_registry.generation
        before_fingerprint = pipe.fingerprint

        call(boundary_registry)

        assert dict(boundary_registry) == before_contents, (
            f"{name!r} changed the table — it is not the no-op this case "
            f"claims to exercise"
        )
        assert boundary_registry.generation == before_generation, (
            f"{name!r} advanced the generation without changing anything: "
            f"every cached block is discarded for a table that still holds "
            f"the same handlers"
        )
        assert pipe.fingerprint == before_fingerprint


def test_clearing_an_already_empty_table_is_not_a_mutation() -> None:
    """``clear()`` twice: the first really empties the table, the second has
    nothing left to remove."""
    with _registry_restored():
        boundary_registry.clear()
        after_first = boundary_registry.generation
        boundary_registry.clear()
        assert boundary_registry.generation == after_first


def test_a_different_handler_under_an_existing_key_still_invalidates() -> None:
    """The other direction, since "unchanged" is decided by identity: a
    *different* object under a key that already exists is a real change, and
    the sameness check must not swallow it."""
    with _registry_restored():
        boundary_registry["zh"] = _passthrough
        before = boundary_registry.generation

        def other(nodes: list[object], profile: object) -> list[object]:
            return list(nodes)

        boundary_registry["zh"] = other
        assert boundary_registry.generation == before + 1


def test_ior_swaps_the_handler_it_is_asked_to_swap() -> None:
    """``|=`` must remain a working way to register — the fix bumps the
    generation, it does not neuter the operator."""
    with _registry_restored():
        # Through a local alias: ``boundary_registry |= ...`` inside a function
        # would make the module global a local. The alias is the same object,
        # so the augmented assignment still mutates the live registry in place.
        registry = boundary_registry
        registry |= {"zh": _passthrough}
        assert boundary_registry["zh"] is _passthrough
        assert registry is boundary_registry
        assert isinstance(boundary_registry, _BoundaryRegistry)


# ``dict``'s own callables that read rather than write. ``copy`` / ``__or__`` /
# ``__ror__`` return a plain ``dict`` and leave the receiver alone; ``fromkeys``
# is a classmethod that builds a *new* instance (through the overridden
# ``__setitem__``, so that instance counts its own generation correctly).
_READ_ONLY_DICT_API = frozenset({
    "__class_getitem__",
    "__contains__",
    "__eq__",
    "__ge__",
    "__getattribute__",
    "__getitem__",
    "__gt__",
    "__iter__",
    "__le__",
    "__len__",
    "__lt__",
    "__ne__",
    "__new__",
    "__or__",
    "__repr__",
    "__reversed__",
    "__ror__",
    "__sizeof__",
    "copy",
    "fromkeys",
    "get",
    "items",
    "keys",
    "values",
})


def test_no_dict_method_is_inherited_without_review() -> None:
    """The structural half: reviewing mutators one at a time is how ``__ior__``
    was missed.

    ``dict`` gains methods across interpreter versions, and nothing marks which
    of them write. So the rule is inverted here: a name the subclass does not
    override must be on the read-only list above, and anything else fails —
    "unclassified" defaults to "must be looked at", not to "assumed safe".
    """
    own = {name for name, value in vars(dict).items() if callable(value)}
    inherited = own - set(vars(_BoundaryRegistry))
    unreviewed = sorted(inherited - _READ_ONLY_DICT_API)
    assert not unreviewed, (
        f"dict methods inherited by _BoundaryRegistry without review: "
        f"{unreviewed}. If one of them can change the table, override it so "
        f"it advances the generation (the fingerprint, and every source_hash, "
        f"depend on that); if it only reads, add it to _READ_ONLY_DICT_API."
    )


def test_the_mutation_table_covers_every_overridden_mutator() -> None:
    """The other direction: an override that no case exercises is an override
    nobody has checked actually bumps."""
    overridden = {
        name
        for name in vars(_BoundaryRegistry)
        if name in vars(dict) and callable(vars(dict)[name])
    }
    # ``__init__`` builds an empty registry rather than mutating a live one.
    exercised = {"__setitem__", "__delitem__", "update", "__ior__",
                 "pop", "popitem", "clear", "setdefault", "__init__"}
    assert overridden <= exercised, (
        f"overridden dict methods with no case in _MUTATIONS: "
        f"{sorted(overridden - exercised)}"
    )
