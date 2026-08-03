"""Runtime wire-type validation degrades per field, never per class.

Every IR loader runs each field's already-converted value through
:func:`brailix.ir._serde.check_wire_value`, which compares it against the
dataclass's own declared type. The table it compares against is *derived* from
the annotations, so the derivation has to resolve them — and a class whose
annotations stop resolving used to switch that whole class's validation off and
say nothing. Payload shapes the guard exists to reject would load again: a
``MathBlock`` whose ``source`` is a list (accepted, ``unhashable type`` at a
registry lookup much later), a ``List`` whose ``ordered`` is the string
``"false"`` (accepted, truthy for ever after).

Two things are pinned here: that no IR dataclass currently has an annotation
that fails to resolve, and that if one ever does, only that field loses its
check.
"""

from __future__ import annotations

import dataclasses
import importlib
import typing
import warnings

import pytest

from brailix.ir._serde import _resolve_hints, _wire_types, check_wire_value

_IR_MODULES = (
    "brailix.ir.document",
    "brailix.ir.inline",
    "brailix.ir.braille",
    "brailix.ir.tactile",
)


def _ir_dataclasses() -> list[type]:
    """Every dataclass the IR declares, found by walking its modules.

    Discovered rather than listed: a hand-written list is one class out of date
    the first time anybody adds a node type, which is the failure mode this
    whole file is about.
    """
    found: dict[str, type] = {}
    for module_name in _IR_MODULES:
        module = importlib.import_module(module_name)
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and dataclasses.is_dataclass(obj)
                and obj.__module__ == module_name
            ):
                found[f"{module_name}.{name}"] = obj
    return [found[key] for key in sorted(found)]


IR_DATACLASSES = _ir_dataclasses()


def test_the_walk_actually_finds_the_ir() -> None:
    # A discovery bug would make every test below vacuously pass.
    names = {cls.__name__ for cls in IR_DATACLASSES}
    assert {"Paragraph", "MathBlock", "Word", "BrailleCell"} <= names


@pytest.mark.parametrize("cls", IR_DATACLASSES, ids=lambda c: c.__name__)
def test_every_ir_dataclass_resolves_its_annotations(cls: type) -> None:
    """The check that should catch a broken declaration before the degradation
    ever runs in production."""
    typing.get_type_hints(cls)


@pytest.mark.parametrize("cls", IR_DATACLASSES, ids=lambda c: c.__name__)
def test_resolution_needs_no_fallback_today(cls: type) -> None:
    # No IR class currently takes the per-field path, so no IR class is
    # currently loading with part of its validation off.
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        _resolve_hints(cls)


class TestOneBadAnnotationDoesNotDisarmTheClass:
    """The regression the fail-open ``return {}`` allowed."""

    @staticmethod
    def _broken() -> type:
        @dataclasses.dataclass
        class Broken:
            level: int
            ordered: bool
            # A name a refactor moved, or one only imported under
            # TYPE_CHECKING in the wrong place. Under PEP 563 this is just a
            # string until someone tries to resolve it.
            mystery: NoSuchTypeAnywhere  # noqa: F821 — deliberately unresolvable

        return Broken

    def test_whole_class_resolution_fails(self) -> None:
        # The premise: this is exactly the situation that used to return {}.
        with pytest.raises(NameError):
            typing.get_type_hints(self._broken())

    def test_the_resolvable_fields_keep_their_types(self) -> None:
        cls = self._broken()
        with pytest.warns(RuntimeWarning, match="could not be resolved"):
            wire = _wire_types(cls)
        assert wire["level"] == (int,)
        assert wire["ordered"] == (bool,)
        assert "mystery" not in wire

    def test_the_resolvable_fields_are_still_enforced(self) -> None:
        cls = self._broken()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            # The shape that used to load: a string where an int is declared.
            with pytest.raises(ValueError, match="must be int"):
                check_wire_value(cls, "level", "2", "Broken")
            # ``bool`` is refused for a field that does not declare it, and
            # accepted for one that does.
            with pytest.raises(ValueError, match="got a bool"):
                check_wire_value(cls, "level", True, "Broken")
            assert check_wire_value(cls, "ordered", True, "Broken") is True

    def test_the_unresolvable_field_is_the_only_one_left_open(self) -> None:
        cls = self._broken()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            # Unchecked, because nothing here can say what it should be —
            # which is the honest answer for that one field, and used to be
            # the answer given for all three.
            assert check_wire_value(cls, "mystery", object(), "Broken") is not None

    def test_the_degradation_is_reported(self) -> None:
        with pytest.warns(RuntimeWarning) as caught:
            _wire_types(self._broken())
        message = str(caught[0].message)
        assert "Broken" in message
        assert "NoSuchTypeAnywhere" in message
