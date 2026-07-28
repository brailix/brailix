"""The ``brailix`` core library's layer boundaries must stay one-directional.

ARCHITECTURE#arch-layers / #arch-boundaries: the compile pipeline flows
Input → Frontend → IR →
Backend → Renderer, and the dependency edges only ever point *downstream*:

* **Core** (span, errors, contexts, protocols, the registry) is the base
  everything else sits on, so it imports no pipeline stage at all. Its
  ``protocols`` module does name IR types — but under ``TYPE_CHECKING`` only,
  since ``brailix.ir`` imports core and a runtime edge back would close a
  cycle; that is checked separately below.
* **Frontend** ("what is this?") never imports Input, Backend, or the
  Pipeline orchestrator.
* **Backend** ("write it by the rules") never reverse-imports Frontend or
  Input. Its one controlled exception — translating embedded prose in music
  ``<words>`` / chem conditions — is *dependency injection* via
  ``BackendContext.options`` (``InlineTextTranslator``), **not** an import,
  so no import edge is allowed here either.
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
"""

from __future__ import annotations

import ast
from pathlib import Path

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
    """
    if node.level == 0:
        return [node.module] if node.module else []
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


def _is_type_checking_test(test: ast.expr, aliases: set[str]) -> bool:
    """True for ``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` guard tests.

    Deliberately narrow on the attribute form: accepting *any* expression
    whose attribute happens to be named ``TYPE_CHECKING`` would let a stray
    ``config.TYPE_CHECKING`` mark a block type-only and hide the real runtime
    imports inside it. Only the ``typing`` module — under whatever name this
    file imported it as — can open that block.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return isinstance(test.value, ast.Name) and test.value.id in aliases
    return False


def _imports_in(
    source: str, package: str, *, runtime_only: bool = False
) -> set[str]:
    """Absolute module names ``source`` imports, relative forms resolved
    against ``package``.

    ``ast.walk`` visits every node, so a lazy import inside a function body
    counts too — invisible to a top-level grep, and the blind spot that has
    made a whole feature vanish from a packaged build before. Docstring
    cross-references don't count (only real import statements are AST nodes).

    ``runtime_only`` drops everything inside an ``if TYPE_CHECKING:`` block. A
    type-only import creates no runtime edge, which is what lets ``core``
    annotate against IR types without depending on IR. Nested statements are
    walked, so a ``TYPE_CHECKING`` block containing a ``try`` / ``with`` is
    still excluded; anything else is a real edge and counts.
    """
    tree = ast.parse(source)
    skip: set[int] = set()
    if runtime_only:
        aliases = _typing_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and _is_type_checking_test(
                node.test, aliases
            ):
                skip.update(id(child) for child in ast.walk(node))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.update(_from_targets(node, package))
    return mods


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


# layer dir -> packages it must never import
_RULES: dict[str, tuple[str, ...]] = {
    "core": (
        "brailix.input",
        "brailix.frontend",
        "brailix.backend",
        "brailix.renderer",
        "brailix.pipeline",
    ),
    "frontend": ("brailix.input", "brailix.backend", "brailix.pipeline"),
    "backend": ("brailix.frontend", "brailix.input", "brailix.pipeline"),
    "ir": (
        "brailix.frontend",
        "brailix.backend",
        "brailix.input",
        "brailix.pipeline",
        "brailix.renderer",
    ),
    "renderer": (
        "brailix.frontend",
        "brailix.backend",
        "brailix.input",
        "brailix.pipeline",
    ),
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
_INPUT_FORBIDDEN: tuple[str, ...] = (
    "brailix.backend",
    "brailix.renderer",
    "brailix.pipeline",
)


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
        assert _would_flag("import brailix.frontend.normalize")

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

    def test_the_default_scan_ignores_type_checking_entirely(self) -> None:
        source = "from typing import TYPE_CHECKING\n" + self._RUNTIME_EDGE.format(
            test="TYPE_CHECKING"
        )
        assert "brailix.ir.document" in _imports_in(source, "brailix.core")
