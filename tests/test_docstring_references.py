"""Cross-references in ``brailix`` docstrings must point at something real.

The docstrings are the API reference: it is *generated* from them, so a
docstring that names a moved or deleted API is a wrong published page, and one
that names a method which never existed sends a reader looking for it. Both had
happened — a class docstring advertised two ``Pipeline`` bridge methods when
only one of the names still existed, and a module comment pointed at a helper
that had since been renamed and moved.

Two mechanical checks over every reST cross-reference role
(``:class:`` / ``:meth:`` / ``:func:`` / ``:data:`` / ``:attr:`` / ``:mod:`` /
``:exc:``) whose target is fully qualified with ``brailix.``:

* the role sits on ONE line — a target broken across a line wrap resolves to
  nothing, and the reference builder degrades it to plain text, so it silently
  stops being a link;
* the target resolves — importable module plus an attribute walk for the rest.

Scope and its limits. Only fully-qualified targets are checked: a bare
``:meth:`Pipeline.translate_text``` would need name resolution against the
enclosing scope, and guessing that is how a guard starts reporting things that
are fine. Identifiers named in prose backticks are out of scope for the same
reason. So this is a floor, not a proof — but it is the floor that covers the
~220 references a rename would break, and it costs one AST-free regex pass.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "brailix"

# Mirrors the role set the reference builder rewrites into Markdown links —
# the roles a reader actually follows.
_ROLE = re.compile(
    r":(?:class|meth|func|data|attr|mod|exc|obj|const):`\s*(~?)(brailix\.[^`]+?)\s*`"
)

_WS = re.compile(r"\s+")


def _roles() -> list[tuple[Path, int, str]]:
    """``(file, line, raw target)`` for every fully-qualified brailix role."""
    out: list[tuple[Path, int, str]] = []
    for py in sorted(_PKG.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for m in _ROLE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append((py.relative_to(_PKG.parent), line, m.group(2)))
    return out


def _resolves(dotted: str) -> bool:
    """True if ``dotted`` names an importable module or an attribute reachable
    from one.

    Tries the longest importable prefix first, then walks the remainder with
    ``getattr`` — so ``brailix.ir.document.ImageAlt.target`` resolves through
    the module, the class and the field alike.
    """
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:  # noqa: BLE001 — not importable at this depth
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def test_there_are_references_to_check() -> None:
    # A regex that stopped matching would make both checks below vacuous.
    assert len(_roles()) > 100


def test_role_targets_are_not_split_across_lines() -> None:
    """A wrapped target silently stops being a link in the reference; reflow
    the surrounding prose so the whole role fits on one line."""
    wrapped = [
        f"{path}:{line} -> {target!r}"
        for path, line, target in _roles()
        if "\n" in target
    ]
    assert not wrapped, "cross-reference targets broken by a line wrap:\n" + "\n".join(
        wrapped
    )


def test_role_targets_resolve() -> None:
    """Every fully-qualified target names something that exists."""
    dangling = [
        f"{path}:{line} -> {target}"
        for path, line, target in _roles()
        if not _resolves(_WS.sub("", target))
    ]
    assert not dangling, (
        "docstring cross-references pointing at nothing (renamed? moved? "
        "deleted?) — the published reference is generated from these:\n"
        + "\n".join(dangling)
    )
