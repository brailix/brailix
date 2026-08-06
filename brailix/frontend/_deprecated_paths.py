"""The registry addresses that moved, kept importable for one release cycle.

``frontend.segment`` and ``frontend.normalize`` were renamed to
``frontend.segmentation`` / ``frontend.normalization`` because a package
attribute and a submodule cannot both own one name and the facade's
:func:`~brailix.frontend.segment` function won every collision (see
:mod:`brailix.frontend`). The rename fixed the spelling that was broken and
broke the spelling that worked: ``from brailix.frontend.segment import
segmenter_registry`` resolved through :data:`sys.modules` rather than through
the package, so it had always worked, and the extension surface named it —
which is a promise to whoever wrote an adapter against it, not an
implementation detail to renumber.

So the old addresses stay resolvable, warn when read, and go away at 0.2.

**Why the shims are module objects rather than files.** A real
``frontend/segment.py`` would resolve the old import — and, the first time
anything imported it, the import system would set it as the ``segment``
attribute of ``brailix.frontend``, replacing the published *function* with a
module for the rest of the process. That is the collision the rename existed to
end, re-armed as a delayed trap. A module registered directly in
:data:`sys.modules` is found by
``importlib._bootstrap._find_and_load_unlocked``'s explicit re-check after the
parent package is imported (its "Crazy side-effects!" branch), and because
nothing is *loaded*, nothing is bound onto the parent: the function keeps the
name it publishes.
"""

from __future__ import annotations

import sys as _sys
import types as _types
import warnings as _warnings

from brailix.frontend import normalization as _normalization
from brailix.frontend import segmentation as _segmentation

# old address -> (module it moved to, the names it forwards). The destination
# is the imported module rather than its name on purpose: an
# ``import_module(computed_name)`` here would be a dynamic import the layering
# guard cannot read through (``tests/test_core_layering.py::
# test_no_layer_imports_a_module_this_guard_cannot_read``), and a compatibility
# alias is the last place worth spending an exemption on.
_MOVED: dict[str, tuple[_types.ModuleType, tuple[str, ...]]] = {
    "brailix.frontend.segment": (_segmentation, ("segmenter_registry",)),
    "brailix.frontend.normalize": (_normalization, ("normalizer_registry",)),
}

_REMOVED_IN = "0.2"


def _make_shim(
    old: str, new: _types.ModuleType, names: tuple[str, ...]
) -> _types.ModuleType:
    module = _types.ModuleType(old)
    module.__doc__ = (
        f"Deprecated alias for :mod:`{new.__name__}`, removed in brailix "
        f"{_REMOVED_IN}."
    )
    # Through ``__dict__`` rather than by attribute: what makes a module-level
    # ``__getattr__`` fire is its presence in the module's dict (PEP 562), and
    # assigning it as an attribute of a ``ModuleType`` *instance* reads as
    # overwriting a method to a type checker while meaning the same thing.
    module.__dict__["__all__"] = list(names)

    def __getattr__(name: str) -> object:
        if name not in names:
            raise AttributeError(
                f"module {old!r} has no attribute {name!r}; it is a "
                f"compatibility alias forwarding only {list(names)} to "
                f"{new.__name__}"
            )
        _warnings.warn(
            f"{old}.{name} moved to {new.__name__}.{name} and this alias is "
            f"removed in brailix {_REMOVED_IN}; import it from "
            f"{new.__name__}.",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(new, name)

    module.__dict__["__getattr__"] = __getattr__
    return module


def install() -> None:
    """Register the moved addresses. Called by :mod:`brailix.frontend`.

    From the package's own ``__init__``, deliberately: the import system looks
    the child up again in :data:`sys.modules` right after importing the parent,
    so anything reaching for an old address arrives here on the way through.
    """
    for old, (new, names) in _MOVED.items():
        if old not in _sys.modules:
            _sys.modules[old] = _make_shim(old, new, names)
