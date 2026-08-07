"""The ``brailix`` core library's layer boundaries must stay one-directional.

ARCHITECTURE#arch-layers / #arch-boundaries: the compile pipeline flows
Input → Frontend → IR →
Backend → Renderer, and the dependency edges only ever point *downstream*:

* **Core** (span, errors, contexts, protocols, the registry) is the base
  everything else sits on, so it imports no pipeline stage at all. Its
  ``protocols`` module does name IR types — but under ``TYPE_CHECKING`` only,
  since ``brailix.ir`` imports core and a runtime edge back would close a
  cycle; that is checked separately below.
* **Frontend** ("what is this?") never imports Input, Backend, Renderer, or
  the Pipeline orchestrator.
* **Backend** ("write it by the rules") never reverse-imports Frontend or
  Input, and never reaches *forward* into Renderer either: it produces IR,
  and how those cells become bytes is the next layer's business — a backend
  that imported an encoder would make the two replaceable only together. Its
  one controlled exception — translating embedded prose in music ``<words>``
  / chem conditions — is *dependency injection* via ``BackendContext.options``
  (``InlineTextTranslator``), **not** an import, so no import edge is allowed
  here either.
* **IR** (the shared mediator types — DocumentIR, BrailleIR, the inline /
  block nodes) never imports Frontend, Backend, Renderer, or the Pipeline. It
  is the neutral currency those layers exchange, so it must stay loadable on
  its own — in an editor, an alternate compiler, or another process — without
  dragging any of them in. (It carries only ``brailix.core`` primitives.)
* **Renderer** ("encode the cells") understands no language and imports none
  of Frontend / Backend / Input / Pipeline.

These edges are currently clean but were only held by convention + review, and
one convenient import is all it takes: an ordinary unit test would still pass
with ``backend`` reaching into ``frontend``, because the code *works* — what
breaks is the promise that any single layer can be replaced or loaded on its
own. The check is AST-based and walks *every* node, so a lazy ``import`` inside
a handler / adapter function body is caught too — invisible to a top-level grep,
and the exact blind spot that has bitten packaged builds before, where a lazy
import the packager never saw made a whole feature vanish at runtime.

**Relative imports count.** ``from ..frontend import normalize`` in a backend
module is the same layer edge as ``import brailix.frontend``, and it is the
*more* likely spelling from inside the package — so every relative import is
resolved against its own file's package before matching (:func:`_imports_in`).
A guard that only saw absolute imports would have advertised whole-repo
coverage while one ordinary ``from ..`` walked straight past it;
:class:`TestGuardCatchesEveryImportForm` pins each form it must catch.

**The bans are derived, not listed.** :data:`_ALLOWED_EDGES` names what each
layer may import and :func:`_forbidden` inverts it, so a pair nobody thought
about is forbidden rather than unguarded — which is what a hand-written
blacklist cannot promise, and what it had already failed to deliver: no rule
banned Frontend or Backend from importing :mod:`brailix.renderer`, and the tree
was clean only by habit.

**Input** is guarded by allowlist rather than by a flat ban. It has a
documented, narrow dependency on the frontend source registries for the
binary-decode exception — ``.mxl`` / ``.mid`` music and MTEF-in-docx math
(ARCHITECTURE#arch-layers rule 2 / #arch-registries) — so an Input →
Frontend edge is allowed, but
only from the modules that actually decode those containers
(:data:`_INPUT_FRONTEND_ALLOWLIST`). Everything downstream of it (Backend,
Renderer, Pipeline) stays banned outright. A text dialect like ``.abc`` does
not qualify: it is kept raw and deferred to the frontend
(ARCHITECTURE#arch-layers rule 1),
importing no frontend from the input layer. A *new* input format that reaches
for the frontend fails here and has to justify the entry.

**The source graph is not the runtime graph**, and only one of them is what a
consumer gets. Every check above reads import statements inside a layer
directory, while Python runs a package's ``__init__`` before any of its
submodules — so ``brailix/__init__.py``, which is not a layer and which nothing
here scans, could reconnect the whole compiler behind ``import brailix.ir``
with every rule below still satisfied. It did exactly that: the root facade
imported :mod:`brailix.pipeline` eagerly, so importing the neutral mediator
layer that promises to load carrying core primitives alone pulled in the
frontend, the backend, the renderers and the input layer — 135 modules for a
package whose own ``__init__`` names two — and this file went on reporting a
one-directional matrix.
:func:`test_importing_a_layer_loads_only_what_it_may_import` asks the question
an AST pass cannot answer: it imports each layer in a *fresh* interpreter and
holds the resulting ``sys.modules`` to the same matrix.

**What it does not cover**, said plainly, because "the layers are clean" is
easy to read as "the repository is clean". Two things are outside the scan by
design, and each would be a category error to include:

* ``brailix/cli.py`` — an application entry point, not a layer. Composing the
  library is its job: it names the pipeline, the registries and the renderers
  on purpose, and a matrix row for it would either forbid what it exists to do
  or allow everything and check nothing. It is *exempt*, not overlooked:
  ``test_every_top_level_module_is_classified_or_exempt`` names it and the two
  other root modules, so the next ``.py`` dropped beside them has to be argued
  for rather than quietly landing outside every rule.
* ``scripts/`` — build and export tooling that ships to nobody. It is not
  installed, imports nothing at runtime, and its own guard is
  ``tests/scripts/``.

So a leak reported as absent is absent from the guarded layers. Anywhere else,
this file is silent rather than reassuring.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1] / "brailix"


def _package_of(py: Path) -> str:
    """The package a relative import written inside ``py`` resolves against.

    For a module that is ``brailix/backend/dispatch.py`` that is
    ``brailix.backend``; for a package's own ``__init__.py`` it is the package
    itself, since ``from . import x`` there means the very same package.
    """
    parts = list(py.relative_to(_PKG.parent).with_suffix("").parts)
    # ``a/b/__init__.py`` IS package ``a.b``; ``a/b/c.py`` sits in ``a.b``.
    return ".".join(parts[:-1])


def _from_targets(node: ast.ImportFrom, package: str) -> list[str]:
    """Absolute module names one ``from ... import ...`` statement reaches.

    ``node.level`` is the leading-dot count: 0 is an absolute import, 1 is the
    file's own package, and each further dot walks one package up. Spelling
    the relative forms out absolutely is what lets a single :func:`_matches`
    check cover both spellings of the same layer edge.

    An absolute import contributes its alias names too, not just
    ``node.module``: ``from brailix import frontend`` records the *layer* in
    the alias list and only ``brailix`` in ``node.module``, so a check reading
    ``node.module`` alone saw an import of the root package and matched no
    forbidden layer — the same shape as the relative ``from .. import
    frontend`` handled below, which was resolved while its absolute twin
    walked past. Only for ``brailix``-rooted modules, so an ordinary ``from
    typing import TYPE_CHECKING`` still contributes one name; and it cannot
    over-report, since ``brailix.<pkg>.<alias>`` can only match a forbidden
    layer when ``brailix.<pkg>`` already did.
    """
    if node.level == 0:
        if not node.module:
            return []
        if node.module.split(".")[0] == "brailix":
            return [
                node.module,
                *(f"{node.module}.{a.name}" for a in node.names),
            ]
        return [node.module]
    parts = package.split(".") if package else []
    if node.level - 1 > len(parts):
        return []  # walks off the package root; not a brailix edge
    base = ".".join(parts[: len(parts) - (node.level - 1)])
    if node.module:
        return [f"{base}.{node.module}" if base else node.module]
    # ``from .. import frontend`` — the imported submodule is named in the
    # alias list, not in ``node.module``. A plain value import (``from .
    # import CONST``) also lands here and yields a name that resolves to
    # nothing, which is harmless: it only ever matters if it happens to spell
    # a forbidden layer.
    return [f"{base}.{a.name}" if base else a.name for a in node.names]


def _typing_aliases(tree: ast.Module) -> set[str]:
    """Every name bound to the ``typing`` module in this file.

    ``typing`` itself plus any ``import typing as t``, so a guard written
    ``if t.TYPE_CHECKING:`` is still recognised as type-only.
    """
    names = {"typing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                a.asname for a in node.names if a.name == "typing" and a.asname
            )
    return names


def _type_checking_flag_names(tree: ast.Module) -> set[str]:
    """Every name bound to ``typing.TYPE_CHECKING`` itself in this file.

    The package imports it as ``TYPE_CHECKING as _TYPE_CHECKING`` — no module
    may hold a name from outside brailix under its plain spelling
    (``tests/test_public_api.py``) — so the guard is written
    ``if _TYPE_CHECKING:``. Reading the binding rather than accepting any
    name that *looks* like the flag keeps this as narrow as the attribute
    form below: a local ``_TYPE_CHECKING = False`` in some other file cannot
    open a type-only block it never imported.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing":
            names.update(
                (a.asname or a.name) for a in node.names if a.name == "TYPE_CHECKING"
            )
    return names


def _is_type_checking_test(
    test: ast.expr, aliases: set[str], flags: set[str] = frozenset({"TYPE_CHECKING"})
) -> bool:
    """True for ``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` guard tests.

    Deliberately narrow in both forms, and for the same reason: accepting any
    expression whose attribute happens to be named ``TYPE_CHECKING``, or any
    bare name that looks like the flag, would let a stray
    ``config.TYPE_CHECKING`` (or a local ``_TYPE_CHECKING = False``) mark a
    block type-only and hide the real runtime imports inside it. Only the
    ``typing`` module — and only under a name *this file* imported it as —
    can open that block, which is what ``aliases`` / ``flags`` carry.
    """
    if isinstance(test, ast.Name):
        return test.id in flags
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return isinstance(test.value, ast.Name) and test.value.id in aliases
    return False


_IMPORTER_NAMES = frozenset({"__import__", "import_module"})
# Names that run source text nobody can resolve statically. Banned outright in
# the guarded layers rather than parsed: ``exec("import brailix.backend.x")``
# is an import edge written as a string, and a guard that tried to read the
# string would be a second, worse parser.
_DYNAMIC_EXEC_NAMES = frozenset({"exec", "eval"})


def _fetched_attr(call: ast.Call) -> str | None:
    """The attribute name a ``getattr(obj, "name")`` call fetches, if literal."""
    if (
        isinstance(call.func, ast.Name)
        and call.func.id == "getattr"
        and len(call.args) >= 2
    ):
        arg = call.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _importer_aliases(tree: ast.Module) -> frozenset[str]:
    """Local names that *are* ``import_module`` / ``__import__``.

    ``loader = importlib.import_module`` and ``from importlib import
    import_module as loader`` both make ``loader("brailix.backend")`` the same
    import edge, with the callable reached through a name of the file's own
    choosing. The call-shape test below matches on the name being called, so
    without this the alias walked past both — a rename was all it took to leave
    the guard reporting a clean tree.

    ``loader = getattr(importlib, "import_module")`` is the third spelling, and
    it was the one left out. The *inline* fetch —
    ``getattr(importlib, "import_module")("brailix.frontend")`` — was already
    caught, because there the callee is visibly a ``getattr`` call; split the
    two lines apart and the binding was an ordinary ``Call`` on the right-hand
    side, which this walk skipped, so the second line called something the
    guard knew nothing about. Both halves failed silently: the literal target
    went unreported as an edge, and a computed one went unreported as opaque.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "importlib":
                aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in _IMPORTER_NAMES
                )
            continue
    # Then the assignments, to a fixed point: ``loader = importlib.import_module``
    # makes ``loader`` one, and ``fn = loader`` makes ``fn`` one too. A single
    # pass over the tree answered only the first — an alias of an alias called a
    # name this walk did not know, so the call was neither an edge nor an opaque
    # import, and re-binding once was all it took to be invisible. Iterating is
    # what makes "an alias assignment is followed precisely" true rather than
    # true of the first hop.
    while True:
        found = set(aliases)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            # ``x = importlib.import_module`` / ``x = import_module`` / ``x =
            # <an alias>`` — the function object itself, not a call to it — and
            # ``x = getattr(...)``, a call whose *result* is that same object.
            if not (
                (
                    isinstance(value, ast.Name)
                    and (value.id in _IMPORTER_NAMES or value.id in found)
                )
                or (
                    isinstance(value, ast.Attribute)
                    and value.attr in _IMPORTER_NAMES
                )
                or (
                    isinstance(value, ast.Call)
                    and _fetched_attr(value) in _IMPORTER_NAMES
                )
            ):
                continue
            found.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if found == aliases:
            return frozenset(aliases)
        aliases = found


def _uncalled_importer_fetches(tree: ast.Module) -> list[int]:
    """Lines fetching an importer by ``getattr`` without calling it there.

    The conservative half of the rule above, and the reason the alias walk
    does not have to be exhaustive to be trusted. Binding the fetch to a name
    is only the readable spelling; it can also be stashed in a dict, passed to
    a helper, or returned from a factory, and each of those is a place this
    file would have to learn to follow. So the *fetch* is refused by shape
    inside the guarded layers — the same treatment ``exec`` / ``eval`` get, for
    the same reason: reaching the importer through a string is a way of
    spelling an import that no static pass can follow to its target.

    Only ``getattr``, and only when the result is not called on the spot: a
    plain ``importlib.import_module(...)`` or an inline
    ``getattr(importlib, "import_module")("brailix.ir")`` names its target
    where the checks above can read it, and reporting those here would say
    "unreadable" about the two forms that are readable.
    """
    invoked = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Call)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _fetched_attr(node) in _IMPORTER_NAMES
        and id(node) not in invoked
    ]


def _stored_importer_references(tree: ast.Module, aliases: frozenset[str]) -> list[int]:
    """Lines where an importer is used as a *value* rather than called.

    The last hiding place, and the one the guard used to admit to: an importer
    reached as a plain attribute and kept somewhere — ``handlers = {"m":
    importlib.import_module}``, ``register(import_module)``, ``return loader``
    — is followed by neither the alias walk (which reads only ``name = …``) nor
    :func:`_uncalled_importer_fetches` (which reads only the ``getattr``
    spelling). Stored that way, the call site is ``handlers["m"](computed)``,
    where nothing names an importer at all, and the module reached is decided
    at runtime by a value this pass cannot see.

    Two shapes are *not* reported, and between them they are why this can be a
    flat refusal without costing precision:

    * the callee of a call — ``importlib.import_module("brailix.ir")`` names
      its target where :func:`_dynamic_import_target` reads it, and a computed
      one is already refused as opaque;
    * the right-hand side of ``name = …`` — the readable binding, which
      :func:`_importer_aliases` follows to every call made through it (and now
      through an alias of it), reporting the module by name.

    Everything else is refused by shape, exactly as ``exec`` / ``eval`` and the
    ``getattr`` fetch are, and for the same reason: reaching the importer
    through a container, an argument or a return value is a way of spelling an
    import that no static pass can follow to its target. Nothing in the library
    does any of it; a use with a real justification gets argued into an
    allowlist here.
    """
    called = {
        id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    bound = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and all(isinstance(t, ast.Name) for t in node.targets)
    }
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            is_importer = node.id in _IMPORTER_NAMES or node.id in aliases
        elif isinstance(node, ast.Attribute):
            is_importer = node.attr in _IMPORTER_NAMES
        else:
            continue
        # Reads only. The ``loader`` in ``loader = importlib.import_module`` is
        # a ``Name`` carrying an alias too, in ``Store`` context — the binding
        # being made, not a use of it, and reporting it would refuse the one
        # form the exemption below exists to allow.
        if not isinstance(node.ctx, ast.Load):
            continue
        if is_importer and id(node) not in called and id(node) not in bound:
            out.append(node.lineno)
    return out


def _is_importer_call(node: ast.AST, aliases: frozenset[str]) -> bool:
    """True if ``node`` calls something that imports a module by name.

    Four spellings reach the same function: the plain name, an attribute
    (``importlib.import_module``), a local alias of either
    (:func:`_importer_aliases`), and a ``getattr`` fetch —
    ``getattr(importlib, "import_module")("brailix.backend.x")``, whose callee
    is itself a call.
    """
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _IMPORTER_NAMES or func.id in aliases
    if isinstance(func, ast.Attribute):
        return func.attr in _IMPORTER_NAMES
    if isinstance(func, ast.Call):
        return _fetched_attr(func) in _IMPORTER_NAMES
    return False


def _dynamic_import_target(node: ast.AST, aliases: frozenset[str]) -> str | None:
    """The module a dynamic-import call names, if it names one literally.

    ``importlib.import_module("brailix.frontend")`` and
    ``__import__("brailix.frontend")`` are import statements written as
    function calls: they build exactly the same edge, and the ``ast.Import`` /
    ``ast.ImportFrom`` walk above cannot see either. No production module uses
    them today — this is here so the first one to reach for a layer it may not
    have is reported rather than discovered later.

    Only a literal argument can be resolved; a computed name is caught by
    :func:`_dynamic_import_is_opaque` instead, which is the honest split — this
    guard cannot follow a name it does not know.
    """
    if not _is_importer_call(node, aliases):
        return None
    assert isinstance(node, ast.Call)  # narrowed by _is_importer_call
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _dynamic_import_is_opaque(node: ast.AST, aliases: frozenset[str]) -> bool:
    """True for a dynamic import whose target this guard cannot read.

    A layer that assembles a module name at runtime can reach anywhere, and no
    static check can say otherwise — so the *shape* is what gets refused,
    inside the guarded layers, rather than being silently filed as "no edge
    found". Nothing in the library does this; a use with a real justification
    belongs in an allowlist here, argued case by case.
    """
    if not _is_importer_call(node, aliases):
        return False
    assert isinstance(node, ast.Call)  # narrowed by _is_importer_call
    first = node.args[0]
    return not (isinstance(first, ast.Constant) and isinstance(first.value, str))


def _dynamic_code_execution(tree: ast.Module) -> list[int]:
    """Line numbers of ``exec`` / ``eval`` calls — imports this cannot read.

    The dynamic-import checks resolve a module *name*; source text passed to
    ``exec`` can carry any import statement at all, and reading it would mean
    parsing a string this guard has no reason to trust. So the call is refused
    in the guarded layers instead: the alternative is a guard that reports a
    clean tree beside ``exec("import brailix.backend.x")``.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _DYNAMIC_EXEC_NAMES
    ]


def _imports_in(
    source: str, package: str, *, runtime_only: bool = False
) -> set[str]:
    """Absolute module names ``source`` imports, relative forms resolved
    against ``package``.

    ``ast.walk`` visits every node, so a lazy import inside a function body
    counts too — invisible to a top-level grep, and the blind spot that has
    made a whole feature vanish from a packaged build before. Docstring
    cross-references don't count (only real import statements are AST nodes).

    Dynamic imports count as well (:func:`_dynamic_import_target`):
    ``importlib.import_module("brailix.backend")`` is the same edge as the
    statement, written as a call.

    ``runtime_only`` drops everything inside an ``if TYPE_CHECKING:`` block. A
    type-only import creates no runtime edge, which is what lets ``core``
    annotate against IR types without depending on IR. Nested statements are
    walked, so a ``TYPE_CHECKING`` block containing a ``try`` / ``with`` is
    still excluded; anything else is a real edge and counts.
    """
    tree = ast.parse(source)
    # Two alias sets, and they must not share a name: the importer aliases
    # decide which *calls* are import edges, the typing aliases which ``if``
    # opens a type-only block. One variable for both left ``runtime_only``
    # scanning with the typing set — so an aliased ``import_module`` was an
    # edge on the default scan and invisible on the one that guards core → ir.
    importer_aliases = _importer_aliases(tree)
    skip: set[int] = set()
    if runtime_only:
        typing_aliases = _typing_aliases(tree)
        flag_names = _type_checking_flag_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and _is_type_checking_test(
                node.test, typing_aliases, flag_names
            ):
                # ``node.body`` ONLY — not ``ast.walk(node)``. Walking the whole
                # ``If`` also swallows ``orelse``, which is the ``else`` branch
                # (and any ``elif`` chain): code that runs precisely when
                # TYPE_CHECKING is False, i.e. at runtime. An
                # ``else: from brailix.ir... import ...`` would have been filed
                # as type-only — the exact edge this guard exists to catch,
                # exempted by the exemption.
                for statement in node.body:
                    skip.update(id(child) for child in ast.walk(statement))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.update(_from_targets(node, package))
        else:
            target = _dynamic_import_target(node, importer_aliases)
            if target is not None:
                mods.add(target)
    return mods


def _opaque_dynamic_imports(py: Path) -> list[str]:
    """``file:line`` of every dynamic import in ``py`` with an unreadable
    target, of every ``exec`` / ``eval`` — a target this cannot read at all —
    of every importer fetched by ``getattr`` and kept rather than called, and
    of every importer used as a value rather than called."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    aliases = _importer_aliases(tree)
    rel = py.relative_to(_PKG.parent)
    return (
        [
            f"{rel}:{node.lineno}"
            for node in ast.walk(tree)
            if _dynamic_import_is_opaque(node, aliases)
        ]
        + [f"{rel}:{line} (exec/eval)" for line in _dynamic_code_execution(tree)]
        + [
            f"{rel}:{line} (getattr importer)"
            for line in _uncalled_importer_fetches(tree)
        ]
        + [
            f"{rel}:{line} (importer kept as a value)"
            for line in _stored_importer_references(tree, aliases)
        ]
    )


def _imports(py: Path, *, runtime_only: bool = False) -> set[str]:
    """:func:`_imports_in` for a file on disk, keyed to its own package."""
    return _imports_in(
        py.read_text(encoding="utf-8"),
        _package_of(py),
        runtime_only=runtime_only,
    )


def _matches(mod: str, forbidden: str) -> bool:
    """True if ``mod`` is the forbidden package or a submodule of it."""
    return mod == forbidden or mod.startswith(forbidden + ".")


# Every layer, and for each one the brailix layers it MAY import. The bans are
# *generated* from this (:func:`_forbidden`) rather than written out per layer,
# because a hand-kept ban list is only as complete as whoever edited it last
# remembered to be — and two entries were already missing: neither ``frontend``
# nor ``backend`` forbade ``brailix.renderer``, so a frontend importing an
# encoder would have passed a guard whose own docstring promises each layer can
# be replaced on its own. One table of allowed edges cannot have that kind of
# hole: an unlisted pair is forbidden by construction, and a *new* layer has to
# be classified before it is scanned at all
# (:func:`test_every_layer_directory_is_classified`).
_LAYERS: tuple[str, ...] = (
    "core",
    "ir",
    "input",
    "frontend",
    "backend",
    "renderer",
    "pipeline",
)

_ALLOWED_EDGES: dict[str, frozenset[str]] = {
    # The base everything sits on. It may *name* IR types — but only under
    # TYPE_CHECKING, since a runtime edge back would close a cycle; that is
    # what ``test_core_does_not_import_ir_at_runtime`` checks separately, with
    # ``runtime_only=True``.
    "core": frozenset({"ir"}),
    # The neutral currency the stages exchange: loadable on its own, carrying
    # only core primitives.
    "ir": frozenset({"core"}),
    # Input's frontend edge is the binary-decode exception, narrowed further —
    # per module — by :data:`_INPUT_FRONTEND_ALLOWLIST`.
    "input": frozenset({"core", "ir", "frontend"}),
    "frontend": frozenset({"core", "ir"}),
    "backend": frozenset({"core", "ir"}),
    "renderer": frozenset({"core", "ir"}),
    # The orchestrator drives every stage, so it is the one layer with no
    # restriction — and, being what everything else must stay independent of,
    # the one nothing else may import.
    "pipeline": frozenset(_LAYERS),
}


def _forbidden(layer: str) -> tuple[str, ...]:
    """The packages ``layer`` must never import, derived from the matrix."""
    allowed = _ALLOWED_EDGES[layer]
    return tuple(
        f"brailix.{other}"
        for other in _LAYERS
        if other != layer and other not in allowed
    )


# layer dir -> packages it must never import. ``input`` and ``pipeline`` are
# out of the flat sweep: input has its own pair of tests (the allowlisted
# frontend edge, then everything downstream), and pipeline is allowed
# everything.
_RULES: dict[str, tuple[str, ...]] = {
    layer: _forbidden(layer)
    for layer in _LAYERS
    if layer not in ("input", "pipeline")
}


# The only ``brailix.input`` modules allowed a frontend import: the binary
# containers whose payload must be decoded at read time because it can't be
# deferred as text (ARCHITECTURE#arch-layers rule 2). Both reach a frontend *source
# registry* to hand the decoded fragment to the right adapter.
_INPUT_FRONTEND_ALLOWLIST: frozenset[str] = frozenset(
    {
        "input/music_xml.py",  # .mxl / .mid → the music source registry
        "input/docx/_ole.py",  # MTEF-in-docx → the math source registry
    }
)

# Downstream of Input, with no exception at all: nothing in the input layer has
# any business writing braille, encoding cells, or driving the orchestrator.
_INPUT_FORBIDDEN: tuple[str, ...] = _forbidden("input")


def _offenders() -> list[str]:
    out: list[str] = []
    for layer, forbidden in _RULES.items():
        for py in sorted((_PKG / layer).rglob("*.py")):
            for mod in _imports(py):
                if any(_matches(mod, f) for f in forbidden):
                    out.append(f"{py.relative_to(_PKG.parent)} imports {mod}")
    return out


def test_core_layer_dependencies_are_one_directional() -> None:
    offenders = _offenders()
    assert not offenders, (
        "core layer-boundary violations (ARCHITECTURE#arch-layers / "
        "#arch-boundaries — deps must point downstream; "
        "backend's prose-translator exception is DI, not import):\n"
        + "\n".join(offenders)
    )


def test_no_layer_imports_a_module_this_guard_cannot_read() -> None:
    """A module name assembled at runtime is an edge to anywhere.

    Every check above resolves a *named* target: the statements, their relative
    spellings, and the literal-argument dynamic calls — including the ones
    reached through an alias or a ``getattr`` fetch. A computed one —
    ``import_module(f"brailix.{layer}.{name}")`` — resolves to nothing a static
    pass can classify, so filing it as "no edge found" would report a clean
    tree while the edge exists. The shape is refused inside the guarded layers
    instead, and so is ``exec`` / ``eval``, which can carry an import statement
    as text. Nothing in the library needs any of it today; a use with a real
    justification gets argued into an allowlist here.

    Third shape, and the one that says why this test exists beside the
    alias-following above rather than being replaced by it: an importer
    **fetched by** ``getattr`` and not called on the spot
    (:func:`_uncalled_importer_fetches`). Following aliases is following one
    hiding place — a local name — and a fetched function can be kept in a
    dict, handed to a helper, or returned from a factory just as easily. Each
    of those is another spelling the alias walk would have to learn, so what
    gets refused is reaching the importer through a string at all.

    Fourth, and it closes the gap this test used to describe rather than
    check: an importer reached as a plain attribute and *kept* rather than
    called — ``handlers = {"m": importlib.import_module}``, then
    ``handlers[k](computed)``, where the call site names no importer at all
    (:func:`_stored_importer_references`). The argument for leaving it was that
    refusing every mention of the attribute would also refuse ``loader =
    importlib.import_module``, the form the alias walk reports *precisely*,
    with the module it reaches. That is true and it is not a reason to refuse
    nothing: the two exemptions the check does make — the callee of a call, and
    the right-hand side of ``name = …`` — are exactly the two forms something
    else here reads, so the flat refusal costs no precision at all. What is
    left is every way of moving the importer somewhere this pass cannot
    follow, and none of them has a legitimate use inside a guarded layer.
    """
    opaque = [
        site
        for layer in [*_RULES, "input"]
        for py in sorted((_PKG / layer).rglob("*.py"))
        for site in _opaque_dynamic_imports(py)
    ]
    assert not opaque, (
        "dynamic import with a computed target inside a guarded layer — the "
        "layering check cannot see where it points:\n" + "\n".join(opaque)
    )


def test_no_guarded_layer_imports_the_root_package() -> None:
    """A layer imports the layer it needs, never ``brailix`` itself.

    ``import brailix`` matches no forbidden package — it names the root — and
    what it reaches is decided later, by attribute access: ``brailix.frontend``
    on the next line is a layer edge no import statement records, so no static
    check can see it. Banning the *import* is what makes the attribute
    unreachable.

    It is also the widest edge available, not the narrowest. The root package
    re-exports :class:`~brailix.Pipeline`, so importing it runs the
    orchestrator's imports, which run every layer's — a backend that reached
    for one helper would depend on the whole library, and on the orchestrator
    that is supposed to depend on *it*. ``brailix.pipeline`` is exempt for that
    same reason (it may import anything, and reads ``__version__`` from the
    root); ``brailix/cli.py`` is a front-end, not a layer, and is not scanned.
    """
    offenders = [
        f"{py.relative_to(_PKG.parent)} imports the root package"
        for layer in [*_RULES, "input"]
        for py in sorted((_PKG / layer).rglob("*.py"))
        if "brailix" in _imports(py)
    ]
    assert not offenders, (
        "a guarded layer imports ``brailix`` itself — import the exact layer "
        "(``brailix.core`` / ``brailix.ir``) instead, so the edge is written "
        "down where this guard can read it:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# The same matrix, asked of a running interpreter
# ---------------------------------------------------------------------------


def _runtime_closure(layer: str) -> frozenset[str]:
    """Every layer ``brailix.<layer>`` may end up loading, from the matrix.

    Transitive, because loading is: ``input`` may import ``frontend``, so
    whatever ``frontend`` may import is fair game too. Derived from
    :data:`_ALLOWED_EDGES` for the same reason the bans are — a closure written
    out by hand is a second copy of the rule, and the copies drift.

    It is a *permission*, not a prediction: ``core`` may name ``ir`` and does
    not import it at runtime, which is checked separately and stays true here
    because the assertion below is "nothing outside the closure", never
    "everything in it".
    """
    seen = {layer}
    stack = [layer]
    while stack:
        for nxt in _ALLOWED_EDGES[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return frozenset(seen)


def _layers_loaded_by(module: str) -> set[str]:
    """The brailix layers a **fresh** interpreter loads for ``import module``.

    A subprocess, not this one: by the time any test runs, pytest has imported
    the whole library, so ``sys.modules`` here answers a different question
    than the one being asked. ``PYTHONPATH`` points at the package's own parent
    so the working tree is what gets imported, installed or not.
    """
    code = (
        f"import {module}, sys\n"
        "print(' '.join(sorted({m.split('.')[1] for m in sys.modules "
        "if m.startswith('brailix.')})))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(_PKG.parent)},
        check=False,
    )
    assert proc.returncode == 0, (
        f"a fresh interpreter could not `import {module}`:\n{proc.stderr}"
    )
    return set(proc.stdout.split())


def test_importing_the_root_package_loads_no_layer() -> None:
    """``import brailix`` is the facade and nothing else.

    Every published name resolves lazily (PEP 562, ``brailix.__getattr__``),
    so the cost of touching the package is this one module — and, more to the
    point, the cost of ``import brailix.ir`` is not the whole compiler, since
    that runs this ``__init__`` first. The facade used to import
    :mod:`brailix.pipeline`, which imports every layer there is.
    """
    loaded = _layers_loaded_by("brailix")
    assert not loaded, (
        f"importing the root package loaded {sorted(loaded)} — every name in "
        f"``brailix.__all__`` must resolve through ``__getattr__``, or a "
        f"consumer of any single layer pays for all of them (and the layer "
        f"boundaries hold only in the source)"
    )


@pytest.mark.parametrize("layer", sorted(set(_LAYERS) - {"pipeline"}))
def test_importing_a_layer_loads_only_what_it_may_import(layer: str) -> None:
    """A layer, imported on its own, drags in nothing the matrix forbids it.

    This is the promise the file's docstring makes in prose — "loadable on its
    own, in an editor, an alternate compiler, or another process" — measured
    rather than inferred. The AST sweep above cannot make it: it reads the
    layer's own imports, and what a *parent package* runs on the way in is
    invisible to it.

    ``pipeline`` is excluded because it may import everything, so the check
    would assert nothing.
    """
    allowed = _runtime_closure(layer)
    loaded = _layers_loaded_by(f"brailix.{layer}")
    forbidden = sorted(loaded - allowed)
    assert not forbidden, (
        f"`import brailix.{layer}` also loaded {forbidden} in a fresh "
        f"interpreter, which the matrix does not allow it to import "
        f"(allowed: {sorted(allowed)}). The edge is probably not in "
        f"brailix/{layer}/ at all — check what brailix/__init__.py imports, "
        f"since it runs first."
    )


def test_the_runtime_closure_is_derived_from_the_matrix() -> None:
    """The derivation itself, so a mistake in it cannot silently widen the
    check above into one that permits everything."""
    assert _runtime_closure("ir") == frozenset({"ir", "core"})
    assert _runtime_closure("renderer") == frozenset({"renderer", "core", "ir"})
    # Transitive: input may take frontend, so it may take frontend's own edges.
    assert _runtime_closure("input") == frozenset(
        {"input", "core", "ir", "frontend"}
    )
    assert "pipeline" not in _runtime_closure("backend")


def test_core_does_not_import_ir_at_runtime() -> None:
    """``brailix.core`` may *annotate* against IR types but must not depend on
    them: ``brailix.ir`` imports core primitives, so a runtime edge back would
    make the two packages unloadable apart (and mutually importable only by
    luck of ordering). ``core.protocols`` is where this keeps almost happening
    — every plugin Protocol signature names IR types."""
    offenders = [
        f"{py.relative_to(_PKG.parent)} imports {mod} outside TYPE_CHECKING"
        for py in sorted((_PKG / "core").rglob("*.py"))
        for mod in _imports(py, runtime_only=True)
        if _matches(mod, "brailix.ir")
    ]
    assert not offenders, "core → ir runtime edge:\n" + "\n".join(offenders)


def test_input_reaches_the_frontend_only_where_allowlisted() -> None:
    """Input's frontend dependency is the binary-decode exception, and it stays
    an exception: only the container decoders may take it."""
    offenders: list[str] = []
    for py in sorted((_PKG / "input").rglob("*.py")):
        # Relative to the package root, so it already reads ``input/...`` —
        # the allowlist is spelled the same way, and the message must not
        # prefix a second ``input/`` onto a path the reader will try to open.
        rel = py.relative_to(_PKG).as_posix()
        for mod in _imports(py):
            if (
                _matches(mod, "brailix.frontend")
                and rel not in _INPUT_FRONTEND_ALLOWLIST
            ):
                offenders.append(f"{rel} imports {mod}")
    assert not offenders, (
        "input → frontend outside the binary-decode allowlist "
        "(ARCHITECTURE#arch-layers rule 1: a "
        "text dialect is kept raw and deferred, not converted at read time). "
        "If this really is a binary container, add it to "
        "_INPUT_FRONTEND_ALLOWLIST with the reason:\n" + "\n".join(offenders)
    )


def test_input_does_not_import_downstream_layers() -> None:
    """No allowlist here: reading a document never needs the backend, a
    renderer, or the orchestrator."""
    offenders = [
        f"{py.relative_to(_PKG.parent)} imports {mod}"
        for py in sorted((_PKG / "input").rglob("*.py"))
        for mod in _imports(py)
        if any(_matches(mod, f) for f in _INPUT_FORBIDDEN)
    ]
    assert not offenders, "input layer-boundary violations:\n" + "\n".join(
        offenders
    )


def test_the_cli_knows_no_individual_language() -> None:
    """The CLI composes the library, but it must not know which languages exist.

    Not a layer rule — ``cli.py`` is an application entry point and is allowed
    to name the pipeline, the registries and the renderers. This is the
    *extensibility* rule from ARCHITECTURE#arch-language-slots: adding a
    language is registration, so nothing that a user discovers a language
    through may be written per language. It was: the CLI imported
    ``frontend.zh.analyzer``, ``frontend.ja.analyzer`` and
    ``frontend.zh.pinyin`` and printed two hard-coded headings, so a third
    language could register its segmenter, frontend and backend and still be
    absent from ``--list-analyzers`` and refused by ``--analyzer``.

    The languages come from ``language_frontend_registry`` and each language's
    own declaration now (:func:`brailix.frontend.list_language_adapters`), and
    this keeps it that way.
    """
    # The only import in this file: every other check reads source text, and
    # deliberately so. Which languages exist is the one fact that cannot be
    # read off the tree — ``frontend/`` also holds math, music and graphics,
    # and telling those from a language would mean writing the list down here,
    # which is the very thing being forbidden.
    from brailix.frontend import language_frontend_registry

    cli = _PKG / "cli.py"
    offenders = sorted(
        mod
        for mod in _imports(cli)
        if any(
            _matches(mod, f"brailix.frontend.{lang}")
            for lang in language_frontend_registry.names()
        )
    )
    assert not offenders, (
        "brailix/cli.py imports a specific language's frontend: "
        f"{offenders} — ask the registry what languages exist and each "
        "language what it offers (brailix.frontend.list_language_adapters / "
        "language_display_name) so a third language needs no edit here"
    )


def test_lexical_constants_are_not_duplicated_across_layers() -> None:
    """A fact about the input belongs in one place, not one copy per layer.

    The percent signs were two hand-kept literals — ``frozenset`` in the
    frontend, ``tuple`` in the backend — with a comment asking whoever edits
    one to remember the other. The motive was right (a backend → frontend
    import would be a real layer violation) but the mechanism was a note: add a
    third spelling on the frontend side and it builds a valid ``Percent`` that
    the backend then rejects as malformed IR, with both layers "correct" by
    their own definition.

    ``core`` is where such a constant lives — the same reasoning that put
    :mod:`brailix.core.chars` there, so frontend and backend can share without
    either importing the other. This checks the literal has not been reinstated
    on either side.
    """
    import re

    # An ASSIGNMENT to the name, not a use of it: ``x not in PERCENT_CHARS``
    # contains an ``=`` (inside ``!=``) and is exactly what should be there.
    assignment = re.compile(r"^\s*_?PERCENT_CHARS\s*(?::[^=]+)?=")
    offenders: list[str] = []
    for layer in ("frontend", "backend"):
        for py in sorted((_PKG / layer).rglob("*.py")):
            for lineno, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), 1
            ):
                if assignment.match(line):
                    offenders.append(
                        f"{py.relative_to(_PKG.parent).as_posix()}:{lineno}"
                    )
    assert not offenders, (
        "percent-sign set redefined outside brailix.core.chars — import "
        "PERCENT_CHARS from there so the frontend's idea of what makes a "
        "Percent and the backend's idea of how to write one cannot drift:\n"
        + "\n".join(offenders)
    )


def test_every_layer_directory_is_classified() -> None:
    """A new package under ``brailix/`` must land in the matrix.

    The sweep only walks the layers :data:`_ALLOWED_EDGES` names, so an
    unclassified one is not "allowed everything" — it is *unscanned*, and the
    suite would go on reporting clean layering for a tree it never read. Both
    directions are checked: a stale entry naming a package that no longer
    exists would quietly stop covering anything.
    """
    on_disk = {
        p.name
        for p in _PKG.iterdir()
        if p.is_dir() and p.name != "__pycache__" and any(p.rglob("*.py"))
    }
    assert on_disk == set(_LAYERS), (
        f"unclassified package(s) under brailix/: {sorted(on_disk - set(_LAYERS))}; "
        f"matrix entries with no package: {sorted(set(_LAYERS) - on_disk)} — "
        f"add the layer to _ALLOWED_EDGES (deciding what it may import) so it "
        f"is scanned at all"
    )


# The modules that may sit at the root of ``brailix/`` without being a layer,
# each because it is an *application* rather than a stage of the compiler.
# Listed rather than pattern-matched: what makes these acceptable is a
# judgement about what they are for, and the next root module deserves the same
# judgement instead of inheriting theirs.
_ROOT_MODULE_EXEMPTIONS: dict[str, str] = {
    "__init__.py": "the facade — lazy re-exports, no logic",
    "cli.py": (
        "an application entry point. Composing the library is its job: it "
        "names the pipeline, the registries and the renderers on purpose, so "
        "a matrix row for it would either forbid what it exists to do or "
        "allow everything and check nothing"
    ),
    "__main__.py": "``python -m brailix`` — hands straight to cli",
}


def test_every_top_level_module_is_classified_or_exempt() -> None:
    """A new module at the root of ``brailix/`` must be a layer or be named.

    :func:`test_every_layer_directory_is_classified` keeps *packages* honest,
    and the sweep walks layer directories — so a plain ``.py`` dropped beside
    ``cli.py`` is in neither. It would be unscanned, exactly like an
    unclassified package, and the difference is that nothing would say so: the
    suite would keep reporting clean layering for a file it never read. A
    root-level helper is also the shape most likely to *become* a cross-layer
    channel, since sitting above every layer is what makes it convenient.

    The answer for a new one is normally to move it into the layer it belongs
    to (ARCHITECTURE#arch-layers: shared plumbing is ``core``, "what is this
    input" is ``frontend``, "write it as braille" is ``backend``). Adding a
    line here is the other answer, and it should have to be argued for.
    """
    on_disk = {p.name for p in _PKG.glob("*.py")}
    unclassified = sorted(on_disk - set(_ROOT_MODULE_EXEMPTIONS))
    assert not unclassified, (
        f"unclassified module(s) at the root of brailix/: {unclassified} — "
        f"no layer rule covers them and the layering sweep never reads them. "
        f"Move each into the layer it belongs to, or add it to "
        f"_ROOT_MODULE_EXEMPTIONS with the reason it is not one."
    )
    stale = sorted(set(_ROOT_MODULE_EXEMPTIONS) - on_disk)
    assert not stale, (
        f"_ROOT_MODULE_EXEMPTIONS names modules that no longer exist: {stale}"
    )


def test_forbidden_edges_are_the_complement_of_the_allowed_ones() -> None:
    """The derivation itself, spot-checked at the two edges that matter.

    ``_forbidden`` is what turns the matrix into the rule the sweep applies, so
    a mistake in it would silently widen every layer's licence.
    """
    assert "brailix.renderer" in _forbidden("backend")
    assert "brailix.renderer" in _forbidden("frontend")
    assert "brailix.ir" not in _forbidden("backend")
    assert "brailix.core" not in _forbidden("renderer")
    # A layer never forbids itself, and pipeline forbids nothing.
    assert not any(m == "brailix.backend" for m in _forbidden("backend"))
    assert _forbidden("pipeline") == ()


def test_allowlisted_input_modules_still_exist() -> None:
    """A stale allowlist entry would silently widen the rule (a renamed
    decoder's new path would be un-allowlisted, but so would nothing —
    the check would just stop covering the old name)."""
    missing = sorted(
        rel for rel in _INPUT_FRONTEND_ALLOWLIST if not (_PKG / rel).is_file()
    )
    assert not missing, f"_INPUT_FRONTEND_ALLOWLIST names missing files: {missing}"


# ---------------------------------------------------------------------------
# The guard's own self-test
# ---------------------------------------------------------------------------


def _would_flag(source: str, package: str = "brailix.backend") -> bool:
    """Whether the checks above would report ``source`` (written in
    ``package``) as reaching into the frontend."""
    return any(
        _matches(mod, "brailix.frontend") for mod in _imports_in(source, package)
    )


class TestGuardCatchesEveryImportForm:
    """The tests above pass on a clean tree either because the tree is clean
    or because the scanner is blind — and those look identical from the
    outside. So each import form a violation could take is injected here and
    asserted caught.

    The blindness was real: until relative imports were resolved, everything
    in the ``from ..frontend`` family below walked straight past a guard whose
    docstring claimed it walked *every* import.
    """

    def test_absolute_import(self) -> None:
        assert _would_flag("import brailix.frontend.normalization")

    def test_absolute_from(self) -> None:
        assert _would_flag("from brailix.frontend import normalize")

    def test_relative_sibling_package(self) -> None:
        assert _would_flag("from ..frontend import normalize")

    def test_relative_deeper_path(self) -> None:
        assert _would_flag(
            "from ..frontend.math import parse_math_tree",
            "brailix.backend",
        )

    def test_relative_from_a_nested_module(self) -> None:
        # ``brailix/backend/math/handlers/leaves.py`` reaching back up: four
        # dots from ``brailix.backend.math.handlers`` land on ``brailix``.
        assert _would_flag(
            "from ....frontend.math import parse_math_tree",
            "brailix.backend.math.handlers",
        )

    def test_a_shallower_relative_import_is_anchored_correctly(self) -> None:
        """One dot short lands inside ``backend``, not on the frontend — the
        resolver must not report an edge that isn't there."""
        assert not _would_flag(
            "from ...frontend.math import parse_math_tree",
            "brailix.backend.math.handlers",
        )

    def test_relative_bare_submodule(self) -> None:
        # ``from .. import frontend`` names the module in the alias list, not
        # in ``node.module`` — the form a level-only resolver drops.
        assert _would_flag("from .. import frontend")

    def test_absolute_bare_submodule(self) -> None:
        """The absolute twin of the case above, and the one that was missed:
        ``node.module`` is ``brailix``, which matches no forbidden layer, so
        the edge was recorded as an import of the root package."""
        assert _would_flag("from brailix import frontend")

    def test_absolute_bare_submodule_among_others(self) -> None:
        assert _would_flag("from brailix import ir, frontend")

    def test_absolute_bare_submodule_renamed(self) -> None:
        """``as`` binds a different name; the edge is the same one."""
        assert _would_flag("from brailix import frontend as f")

    def test_importing_an_allowed_layer_by_that_form_is_not_flagged(self) -> None:
        """The other half — expanding alias names must not start reporting the
        edges a layer is allowed to have."""
        assert not _would_flag("from brailix import ir")
        assert not _would_flag("from brailix.core import Span")
        assert not _would_flag("from brailix.ir.document import Paragraph")

    def test_the_root_package_import_is_recorded(self) -> None:
        """``import brailix`` + ``brailix.frontend.normalization(...)`` names no
        layer in any import statement — the attribute is where the edge is.
        The layer sweep cannot see it, which is why the root package is banned
        outright (``test_no_guarded_layer_imports_the_root_package``); this
        pins that the scanner at least records the import it bans."""
        assert "brailix" in _imports_in(
            "import brailix\ndef f():\n    return brailix.frontend.normalization\n",
            "brailix.backend",
        )
        assert "brailix" in _imports_in(
            "from brailix import frontend", "brailix.backend"
        )
        # An exact-layer import is not the root package.
        assert "brailix" not in _imports_in(
            "import brailix.core.span", "brailix.backend"
        )

    def test_lazy_relative_import_inside_a_function(self) -> None:
        assert _would_flag(
            "def handler(node, ctx):\n"
            "    from ..frontend import normalize\n"
            "    return normalize(node)\n"
        )

    def test_relative_import_inside_type_checking_still_counts_here(self) -> None:
        """The flat layer ban has no type-only exemption: ``backend`` must not
        name ``frontend`` even in an annotation, because the promise is that
        the layer is *replaceable*, and a type-checked signature is part of
        the contract. (Only the core → ir rule exempts TYPE_CHECKING, and it
        is checked with ``runtime_only=True``.)"""
        assert _would_flag(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from ..frontend import normalize\n"
        )

    def test_a_clean_module_is_not_flagged(self) -> None:
        """The other half: a guard that flags everything proves nothing."""
        assert not _would_flag(
            "from typing import TYPE_CHECKING\n"
            "from ..core.span import Span\n"
            "from ..ir.document import Paragraph\n"
            "from . import dispatch\n"
            "import brailix.ir.braille\n"
        )

    def test_import_walking_off_the_package_root_is_ignored(self) -> None:
        """More dots than packages can't name a brailix module; the resolver
        must return nothing rather than mis-anchor onto a shorter prefix."""
        assert _imports_in("from ..... import frontend", "brailix.backend") == set()

    def test_importlib_import_module(self) -> None:
        assert _would_flag(
            "import importlib\n"
            "def handler(node):\n"
            "    return importlib.import_module('brailix.frontend').normalize\n"
        )

    def test_bare_import_module(self) -> None:
        assert _would_flag(
            "from importlib import import_module\n"
            "normalize = import_module('brailix.frontend.normalization')\n"
        )

    def test_dunder_import(self) -> None:
        assert _would_flag("__import__('brailix.frontend')")

    def test_a_dynamic_import_of_an_allowed_module_is_not_flagged(self) -> None:
        assert not _would_flag("__import__('brailix.ir.document')")

    def test_import_module_bound_to_a_local_name(self) -> None:
        # ``loader`` is the importer itself, so the call is the same edge — the
        # call-name test saw only a call to something called ``loader``.
        assert _would_flag(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "normalize = loader('brailix.frontend.normalization')\n"
        )

    def test_import_module_renamed_at_import(self) -> None:
        assert _would_flag(
            "from importlib import import_module as _load\n"
            "normalize = _load('brailix.frontend.normalization')\n"
        )

    def test_import_module_fetched_with_getattr(self) -> None:
        assert _would_flag(
            "import importlib\n"
            "getattr(importlib, 'import_module')('brailix.frontend.normalization')\n"
        )

    def test_import_module_fetched_with_getattr_then_bound(self) -> None:
        """The form above split across two statements, and the one that walked
        past: the binding's right-hand side is an ordinary ``Call``, which the
        alias walk skipped, so the next line called a name the guard had never
        heard of. ``getattr`` had run by then, but nothing was imported yet —
        the edge is on the second line, where there is no ``getattr`` left to
        recognise."""
        assert _would_flag(
            "import importlib\n"
            "loader = getattr(importlib, 'import_module')\n"
            "normalize = loader('brailix.frontend.normalization')\n"
        )

    def test_dunder_import_fetched_with_getattr_then_bound(self) -> None:
        assert _would_flag(
            "import builtins\n"
            "loader = getattr(builtins, '__import__')\n"
            "loader('brailix.frontend.normalization')\n"
        )

    def test_a_getattr_of_something_else_does_not_bind_an_importer(self) -> None:
        """The other half: only the two importers make an alias, or every
        ``getattr``-bound callable in the tree would start reading as one."""
        assert not _would_flag(
            "import importlib\n"
            "reloader = getattr(importlib, 'reload')\n"
            "reloader('brailix.frontend')\n"
        )

    def test_an_unrelated_local_callable_is_not_an_importer(self) -> None:
        """The alias walk must bind the importer, not every name assigned a
        function — otherwise ordinary code starts reading as dynamic imports."""
        assert not _would_flag(
            "loader = dict\nloader('brailix.frontend')\n"
        )


class TestOpaqueDynamicImportDetection:
    """A computed module name is refused by shape, since no static pass can
    say where it points."""

    @staticmethod
    def _opaque(source: str) -> bool:
        tree = ast.parse(source)
        aliases = _importer_aliases(tree)
        return any(
            _dynamic_import_is_opaque(node, aliases) for node in ast.walk(tree)
        )

    @staticmethod
    def _exec_lines(source: str) -> list[int]:
        return _dynamic_code_execution(ast.parse(source))

    def test_a_computed_target_through_an_alias_is_opaque(self) -> None:
        assert self._opaque(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "mod = loader(f'brailix.{layer}')\n"
        )

    def test_a_computed_target_through_getattr_is_opaque(self) -> None:
        assert self._opaque(
            "getattr(importlib, 'import_module')('brailix.' + layer)"
        )

    def test_a_computed_target_through_a_getattr_bound_alias_is_opaque(self) -> None:
        """The other half of the alias hole: with the binding invisible, a
        computed target through it was not merely unresolved — it was not
        recognised as a dynamic import at all, so neither check reported it."""
        assert self._opaque(
            "import importlib\n"
            "loader = getattr(importlib, 'import_module')\n"
            "mod = loader(f'brailix.{layer}')\n"
        )

    def test_exec_and_eval_are_refused_by_shape(self) -> None:
        """An import can travel as source text, and reading that text would
        mean this guard parsing a string — so the call itself is the finding."""
        assert self._exec_lines("exec('import brailix.backend.x')") == [1]
        assert self._exec_lines("eval(compile(src, '<s>', 'exec'))") == [1]
        assert self._exec_lines("normalize(text, ctx)") == []

    def test_fstring_target_is_opaque(self) -> None:
        assert self._opaque("import_module(f'brailix.{layer}.normalize')")

    def test_variable_target_is_opaque(self) -> None:
        assert self._opaque("importlib.import_module(name)")

    def test_concatenated_target_is_opaque(self) -> None:
        assert self._opaque("import_module('brailix.' + layer)")

    def test_a_literal_target_is_readable(self) -> None:
        assert not self._opaque("import_module('brailix.frontend')")

    def test_an_ordinary_call_is_not_a_dynamic_import(self) -> None:
        assert not self._opaque("normalize(text, ctx)")
        assert not self._opaque("metadata.version('brailix')")


class TestUncalledImporterFetchDetection:
    """Reaching the importer through a string, then keeping it.

    Refused by shape rather than followed, because "kept" has no fixed
    spelling: a local name is the readable one, and a dict entry or a function
    argument works exactly as well. The alias walk handles the readable case
    precisely — naming the module that gets imported — and this is what keeps
    the other spellings from being a silent gap behind it.
    """

    @staticmethod
    def _fetches(source: str) -> list[int]:
        return _uncalled_importer_fetches(ast.parse(source))

    def test_a_bound_fetch_is_refused(self) -> None:
        assert self._fetches(
            "import importlib\nloader = getattr(importlib, 'import_module')\n"
        ) == [2]

    def test_a_fetch_kept_somewhere_the_alias_walk_cannot_see(self) -> None:
        assert self._fetches(
            "handlers = {'m': getattr(importlib, 'import_module')}\n"
        ) == [1]
        assert self._fetches("register(getattr(builtins, '__import__'))\n") == [1]

    def test_an_inline_fetch_and_call_is_not_reported_here(self) -> None:
        """It names its target on the same line, so the forbidden-edge sweep
        reads it. Reporting it as unreadable would say the opposite of what is
        true about the one dynamic form that is fully resolvable."""
        assert self._fetches(
            "getattr(importlib, 'import_module')('brailix.frontend')\n"
        ) == []

    def test_an_unrelated_getattr_is_left_alone(self) -> None:
        assert self._fetches("fn = getattr(obj, 'render')\n") == []
        # A computed attribute name is not a fetch this can identify; the call
        # it produces is caught as an opaque import if it turns out to be one.
        assert self._fetches("fn = getattr(obj, name)\n") == []


class TestStoredImporterDetection:
    """An importer kept as a value, rather than called where it can be read.

    The blind spot this file used to describe in prose: stored in a container
    or handed on, the call site is ``handlers[k](name)`` — nothing there names
    an importer, so neither the alias walk nor the ``getattr`` refusal sees the
    edge, and the guard reports a clean tree.
    """

    @staticmethod
    def _stored(source: str) -> list[int]:
        tree = ast.parse(source)
        return _stored_importer_references(tree, _importer_aliases(tree))

    def test_kept_in_a_container(self) -> None:
        assert self._stored(
            "import importlib\n"
            "handlers = {'m': importlib.import_module}\n"
            "handlers['m'](f'brailix.{layer}')\n"
        ) == [2]

    def test_handed_to_a_helper(self) -> None:
        assert self._stored(
            "from importlib import import_module\nregister(import_module)\n"
        ) == [2]

    def test_returned_from_a_factory(self) -> None:
        assert self._stored(
            "import importlib\n"
            "def loader():\n"
            "    return importlib.import_module\n"
        ) == [3]

    def test_an_alias_of_an_alias_is_kept_too(self) -> None:
        """The alias walk is transitive now, so the second name is an importer
        as well — and keeping *it* is the same hiding place."""
        assert self._stored(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "fn = loader\n"
            "handlers = [fn]\n"
        ) == [4]

    def test_the_builtin_counts(self) -> None:
        assert self._stored("handlers = (__import__,)\n") == [1]

    def test_a_call_is_not_a_stored_reference(self) -> None:
        """Called on the spot, it names its target where the edge sweep reads
        it — and a computed one is already refused as opaque. Reporting it here
        would say "unreadable" about the readable form."""
        assert self._stored("importlib.import_module('brailix.frontend')\n") == []
        assert self._stored("import_module('brailix.ir')\n") == []

    def test_a_plain_alias_binding_is_not_a_stored_reference(self) -> None:
        """``loader = importlib.import_module`` is the form the alias walk
        follows to every call made through it, reporting the module by name.
        Refusing it too would trade a precise finding for a shape complaint."""
        assert self._stored(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "loader('brailix.frontend')\n"
        ) == []

    def test_ordinary_code_is_left_alone(self) -> None:
        assert self._stored("handlers = {'m': normalize}\nregister(segment)\n") == []
        assert self._stored("import importlib.util\n") == []
        assert self._stored("from importlib import metadata\n") == []


class TestTheAliasWalkIsTransitive:
    def test_an_alias_of_an_alias_is_still_an_importer(self) -> None:
        """One pass answered only the first hop, so re-binding once made every
        call through the second name invisible to both halves of the guard."""
        aliases = _importer_aliases(
            ast.parse(
                "import importlib\n"
                "loader = importlib.import_module\n"
                "fn = loader\n"
            )
        )
        assert {"loader", "fn"} <= aliases

    def test_a_call_through_the_second_name_is_an_edge(self) -> None:
        assert _would_flag(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "fn = loader\n"
            "fn('brailix.frontend')\n"
        )

    def test_a_computed_call_through_the_second_name_is_opaque(self) -> None:
        tree = ast.parse(
            "import importlib\n"
            "loader = importlib.import_module\n"
            "fn = loader\n"
            "fn(name)\n"
        )
        aliases = _importer_aliases(tree)
        assert any(
            _dynamic_import_is_opaque(node, aliases) for node in ast.walk(tree)
        )

    def test_an_unrelated_chain_is_not_followed(self) -> None:
        assert not _importer_aliases(
            ast.parse("loader = dict\nfn = loader\n")
        )


class TestTypeCheckingDetection:
    """``runtime_only`` skips ``if TYPE_CHECKING:`` blocks, so whatever decides
    what counts as one decides what the core → ir guard can see."""

    _RUNTIME_EDGE = "if {test}:\n    from brailix.ir.document import Paragraph\n"

    def test_bare_name_opens_a_type_only_block(self) -> None:
        source = "from typing import TYPE_CHECKING\n" + self._RUNTIME_EDGE.format(
            test="TYPE_CHECKING"
        )
        assert _imports_in(source, "brailix.core", runtime_only=True) == {"typing"}

    def test_typing_attribute_opens_a_type_only_block(self) -> None:
        source = "import typing\n" + self._RUNTIME_EDGE.format(
            test="typing.TYPE_CHECKING"
        )
        assert _imports_in(source, "brailix.core", runtime_only=True) == {"typing"}

    def test_typing_alias_opens_a_type_only_block(self) -> None:
        """``import typing as t`` is legitimate; the narrowed check must not
        start reporting it as a runtime edge."""
        source = "import typing as t\n" + self._RUNTIME_EDGE.format(
            test="t.TYPE_CHECKING"
        )
        assert _imports_in(source, "brailix.core", runtime_only=True) == {"typing"}

    def test_an_unrelated_attribute_does_not_open_one(self) -> None:
        """The regression: matching any ``*.TYPE_CHECKING`` attribute let an
        object that merely *has* such a field hide real runtime imports behind
        a block the guard then skipped."""
        source = "import config\n" + self._RUNTIME_EDGE.format(
            test="config.TYPE_CHECKING"
        )
        runtime = _imports_in(source, "brailix.core", runtime_only=True)
        assert "brailix.ir.document" in runtime

    def test_an_else_branch_is_runtime(self) -> None:
        """``else`` runs precisely when TYPE_CHECKING is False — at runtime.

        The skip set was built from ``ast.walk(node)``, which covers the whole
        ``If`` including ``orelse``, so an import placed there was filed as
        type-only. The exemption for type-only code exempted the one branch
        guaranteed to execute.
        """
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    pass\n"
            "else:\n"
            "    from brailix.ir.document import Paragraph\n"
        )
        runtime = _imports_in(source, "brailix.core", runtime_only=True)
        assert "brailix.ir.document" in runtime, (
            "an import in the else branch was treated as type-only"
        )

    def test_an_elif_branch_is_runtime(self) -> None:
        """Same for an ``elif`` chain: it lives in ``orelse`` too."""
        source = (
            "import sys\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    pass\n"
            "elif sys.version_info >= (3, 13):\n"
            "    from brailix.ir.document import Paragraph\n"
        )
        runtime = _imports_in(source, "brailix.core", runtime_only=True)
        assert "brailix.ir.document" in runtime

    def test_the_type_checking_body_is_still_exempt(self) -> None:
        """The other half — narrowing the skip must not start reporting the
        type-only imports the exemption exists for."""
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from brailix.ir.document import Paragraph\n"
            "    from brailix.ir.inline import Word\n"
        )
        runtime = _imports_in(source, "brailix.core", runtime_only=True)
        assert not any(m.startswith("brailix.ir") for m in runtime)

    def test_the_default_scan_ignores_type_checking_entirely(self) -> None:
        source = "from typing import TYPE_CHECKING\n" + self._RUNTIME_EDGE.format(
            test="TYPE_CHECKING"
        )
        assert "brailix.ir.document" in _imports_in(source, "brailix.core")

    def test_this_scan_still_reads_an_aliased_dynamic_import(self) -> None:
        """Two alias sets meet here, and they are not the same set.

        ``runtime_only`` needs the typing aliases *and* the importer ones, and
        keeping both in one variable let the second overwrite the first: an
        aliased ``import_module`` counted as an edge on the default scan and
        vanished on this one — the scan that keeps core off ir at runtime, and
        the only one where a missed edge closes a cycle.
        """
        source = (
            "import importlib\n"
            "loader = importlib.import_module\n"
            "loader('brailix.ir.document')\n"
        )
        assert "brailix.ir.document" in _imports_in(
            source, "brailix.core", runtime_only=True
        )
