"""Every whole-file read in the input layer goes through ``InputLimits``.

Two adapters had grown near-identical whole-file text readers — read bytes,
sniff the UTF-16 BOM, else decode ``utf-8-sig`` — and both took ``limits`` as a
*defaulted* parameter. A call site that forgot to pass the caller's policy
therefore read under :data:`~brailix.input.DEFAULT_INPUT_LIMITS` instead, with
no error and nothing to notice: the generic ``.xml`` route and the ``.abc``
deferred-score route both did, so a service's tightened ``max_file_bytes``
silently did not apply to either. That is security-policy code, not incidental
duplication, which is why it is now one method
(:meth:`~brailix.input.InputLimits.read_bounded_text`) and why these two
structural guards exist.

They are AST-shaped rather than behavioural on purpose: the behaviour is
already pinned by the replace-after-stat tests in ``test_limits.py``, one per
route. What those cannot do is fail for a route added *later*. These can.
"""

from __future__ import annotations

import ast
from pathlib import Path

_INPUT_DIR = Path(__file__).resolve().parents[2] / "brailix" / "input"

# The one module allowed to touch the filesystem: it *is* the budget.
_READER = "limits.py"

# Whole-file reads. ``Path.read_bytes`` / ``read_text`` and the builtin
# ``open`` are unbounded by construction; ``Path(...).open`` is the shape
# ``read_bounded`` itself uses.
_WHOLE_FILE_READS = frozenset({"read_bytes", "read_text"})


def _modules() -> list[Path]:
    return [
        p
        for p in sorted(_INPUT_DIR.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]


def _rel(path: Path) -> str:
    return path.relative_to(_INPUT_DIR.parents[1]).as_posix()


def _reads_the_filesystem(node: ast.AST) -> bool:
    """True for the call shapes that read a whole file off disk.

    ``zf.open(member)`` on a :class:`zipfile.ZipFile` is deliberately not one
    of them — it reads an archive member from bytes already in hand, which is
    the bounded path. Only ``Path(...).open(...)`` is matched for ``open`` as
    an attribute, so this guard catches the ordinary spelling rather than
    every possible alias; a helper that binds ``p = Path(x)`` first would slip
    through, and the behavioural per-route tests are what cover that.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open":
        return True
    if isinstance(func, ast.Attribute):
        if func.attr in _WHOLE_FILE_READS:
            return True
        if func.attr == "open":
            recv = func.value
            return (
                isinstance(recv, ast.Call)
                and isinstance(recv.func, ast.Name)
                and recv.func.id == "Path"
            )
    return False


def test_only_the_limits_module_reads_a_file_off_disk() -> None:
    offenders: list[str] = []
    for module in _modules():
        if module.name == _READER:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _reads_the_filesystem(node):
                offenders.append(f"{_rel(module)}:{node.lineno}")
    assert not offenders, (
        "input adapters must not read files themselves — the read is what the "
        "size budget binds to. Go through InputLimits.read_bounded / "
        ".read_bounded_text instead:\n" + "\n".join(offenders)
    )


def _is_public_parse_entry(node: ast.FunctionDef) -> bool:
    return node.name.startswith("parse_")


def test_no_private_helper_defaults_the_input_budget() -> None:
    """``DEFAULT_INPUT_LIMITS`` is the *caller's* fallback, not an internal one.

    A public ``parse_*`` may default to it — that is the documented "desktop
    caller opening its own file" behaviour. Anywhere else it is a way for a
    caller's tightened policy to be silently replaced by the generous default:
    a helper with ``limits: InputLimits = DEFAULT_INPUT_LIMITS`` looks correct
    at every call site, including the ones that pass nothing.
    """
    offenders: list[str] = []
    for module in _modules():
        if module.name == _READER:  # where the default is defined
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _is_public_parse_entry(node):
                for default in [
                    *node.args.defaults,
                    *[d for d in node.args.kw_defaults if d is not None],
                ]:
                    allowed.update(
                        id(sub) for sub in ast.walk(default)
                    )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and node.id == "DEFAULT_INPUT_LIMITS"
                and id(node) not in allowed
            ):
                offenders.append(f"{_rel(module)}:{node.lineno}")
    assert not offenders, (
        "DEFAULT_INPUT_LIMITS may only be the default of a public parse_* "
        "entry point. Elsewhere it lets a caller's policy be lost by "
        "omission — take ``limits`` as a required argument:\n"
        + "\n".join(offenders)
    )


def test_the_guards_are_looking_at_something() -> None:
    # A moved package or a renamed suffix would make both checks vacuous.
    modules = _modules()
    assert len(modules) >= 5, f"only found {len(modules)} input modules"
    assert any(m.name == _READER for m in modules)
