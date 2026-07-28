"""The ``brailix`` core library's layer boundaries must stay one-directional.

ARCHITECTURE §1 / §12: the compile pipeline flows Input → Frontend → IR →
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

**Input** is guarded by allowlist rather than by a flat ban. It has a
documented, narrow dependency on the frontend source registries for the
binary-decode exception — ``.mxl`` / ``.mid`` music and MTEF-in-docx math
(ARCHITECTURE §1 rule 2 / §7.3) — so an Input → Frontend edge is allowed, but
only from the modules that actually decode those containers
(:data:`_INPUT_FRONTEND_ALLOWLIST`). Everything downstream of it (Backend,
Renderer, Pipeline) stays banned outright. A text dialect like ``.abc`` does
not qualify: it is kept raw and deferred to the frontend (§1 rule 1),
importing no frontend from the input layer. A *new* input format that reaches
for the frontend fails here and has to justify the entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "brailix"


def _imported_modules(py: Path) -> set[str]:
    """Absolute module names imported anywhere in ``py`` — including inside
    function bodies (``ast.walk`` visits every node), so a lazy import can't
    smuggle a layer violation past this guard. Docstring cross-references
    don't count (only real import statements are AST nodes)."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def _runtime_imported_modules(py: Path) -> set[str]:
    """Like :func:`_imported_modules`, minus everything inside an
    ``if TYPE_CHECKING:`` block.

    A type-only import creates no runtime edge, which is what lets ``core``
    annotate against IR types without depending on IR. Nested statements are
    walked, so a ``TYPE_CHECKING`` block containing a ``try``/``with`` is still
    excluded; anything else — including a lazy import in a function body — is
    a real edge and counts."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    type_only: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in ast.walk(node):
                type_only.add(id(child))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in type_only:
            continue
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for ``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` guard tests."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


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
# deferred as text (ARCHITECTURE §1 rule 2). Both reach a frontend *source
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
            for mod in _imported_modules(py):
                if any(_matches(mod, f) for f in forbidden):
                    out.append(f"{py.relative_to(_PKG.parent)} imports {mod}")
    return out


def test_core_layer_dependencies_are_one_directional() -> None:
    offenders = _offenders()
    assert not offenders, (
        "core layer-boundary violations (§1/§12 — deps must point downstream; "
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
        for mod in _runtime_imported_modules(py)
        if _matches(mod, "brailix.ir")
    ]
    assert not offenders, "core → ir runtime edge:\n" + "\n".join(offenders)


def test_input_reaches_the_frontend_only_where_allowlisted() -> None:
    """Input's frontend dependency is the binary-decode exception, and it stays
    an exception: only the container decoders may take it."""
    offenders: list[str] = []
    for py in sorted((_PKG / "input").rglob("*.py")):
        rel = py.relative_to(_PKG).as_posix()
        for mod in _imported_modules(py):
            if _matches(mod, "brailix.frontend") and rel not in (
                _INPUT_FRONTEND_ALLOWLIST
            ):
                offenders.append(f"input/{rel} imports {mod}")
    assert not offenders, (
        "input → frontend outside the binary-decode allowlist (§1 rule 1: a "
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
        for mod in _imported_modules(py)
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
