"""What the user-facing docs tell you to type has to work.

The repository already guards documentation *structure*: that the extension
guide names every method a protocol requires, that the imports it prints
resolve, that the architecture trees list files that exist. None of that
executes anything, and the drift that got through was in the part a reader
actually copies. Four documents — the README, this CLI guide, Getting
Started, and the CLI module's own docstring and ``--help`` epilog — showed
the same first command:

    brailix "我在重庆。"

which has not translated anything since ``--profile`` became required: it
exits 2 as a usage error. One of them printed a ``--in-format`` with a
``--file``, a combination the implementation ignored. Getting Started called
``parse_markdown(text)`` when both keyword arguments are mandatory, so the
first thing a new user pasted raised ``TypeError``. The examples were copied
between documents, and no copy was the executable one.

So this file executes them, in the only two senses that don't need a shell or
an optional dependency:

* every ``brailix …`` command line is parsed and validated by the real
  parser — the same ``build_parser()`` + ``_validate()`` ``main()`` runs — so
  an example that would exit 2 fails here instead of in a reader's terminal;
* every call in a Python example whose callee the example itself imports from
  ``brailix`` is bound against the real signature, so a missing required
  argument (or a method that no longer exists) fails here rather than at the
  reader's first paste.

What it deliberately does not do is *run* the translations: that would need
the optional language engines this suite is built to work without, and the
library's own tests already cover what the calls do. The claim being pinned
is narrower and is exactly the one that broke — the command is a command the
CLI accepts, and the call is a call the function accepts.

The pages are *found*, not assumed: a checkout may keep the published set at
the top level or stage it a couple of directories down, and this guard has to
mean the same thing either way. Hence a glob per page, plus two checks that the
scan is still seeing anything — every named page turned up, and enough commands
and calls came out of them — because a pattern that silently stopped matching
would make every check below vacuous while still passing.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import io
import re
import shlex
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# The published set: a README plus four guide pages, named. This is the list
# the checks below mean by "the documentation", and the globs are derived from
# it — so a page that is renamed, moved out of the glob's reach, or deleted is
# reported *by name* rather than as a number that came up short. A count could
# not do that: with a floor of four, any one of the five could vanish and take
# every command and example inside it out of scope, silently, while the guard
# stayed green.
_EXPECTED_DOCS = (
    "README.md",
    "docs/index.md",
    "docs/getting-started.md",
    "docs/cli.md",
    "docs/extending.md",
)

# Each page as two globs: the same document is picked up whether it sits at the
# top level (the published repository) or is staged under a directory or two
# (here, where the shipped pages live in the export overlay).
_DOC_GLOBS = tuple(
    pattern for doc in _EXPECTED_DOCS for pattern in (doc, f"*/*/{doc}")
)

# Fences whose contents are shell commands, and the ones that are Python.
_SHELL_FENCE = re.compile(
    r"^```(?:bash|sh|shell|console)\n(.*?)^```", re.MULTILINE | re.DOTALL
)
_PYTHON_FENCE = re.compile(r"^```(?:python|py)\n(.*?)^```", re.MULTILINE | re.DOTALL)
# Inline code spans: `brailix --list-renderers` in the middle of a sentence is
# as much an instruction as a fenced one.
_INLINE_CODE = re.compile(r"`([^`\n]+)`")

# Two or more spaces separate a command from the description aligned after it
# — the shape every one of these documents uses, in fences (before a ``#``)
# and in the ``--help`` epilog (without one).
_DESCRIPTION_COLUMN = re.compile(r"\s{2,}")

_COMMAND_PREFIXES = (("brailix",), ("python", "-m", "brailix"))


def _docs() -> list[tuple[str, str]]:
    """Every published page that exists here, as ``(label, text)``.

    Dot-directories are skipped. A ``.tmp`` scratch tree or a build directory
    can hold a *copy* of a page — an old review checkout, an export staged for
    inspection — and the two-deep glob finds it as readily as the real one.
    Checking those says nothing about what is published and reads exactly
    backwards when it fails: a command that was fixed months ago is reported as
    broken documentation because a stale copy still carries it.
    """
    out: list[tuple[str, str]] = []
    for glob in _DOC_GLOBS:
        for path in sorted(_ROOT.glob(glob)):
            rel = path.relative_to(_ROOT)
            if not path.is_file() or any(
                part.startswith(".") for part in rel.parts
            ):
                continue
            out.append((rel.as_posix(), path.read_text(encoding="utf-8")))
    return out


def _published_page(rel: str) -> str:
    """Which entry of :data:`_EXPECTED_DOCS` the found path ``rel`` is a copy
    of — the same page whether it was found at the top level or staged."""
    for doc in _EXPECTED_DOCS:
        if rel == doc or rel.endswith(f"/{doc}"):
            return doc
    raise AssertionError(f"{rel} matched a glob but no expected page")


def _cli_text_sources() -> list[tuple[str, str]]:
    """The CLI's own prose: its module docstring and its ``--help`` epilog.

    Not Markdown, and not covered by any documentation check — but printed to
    every user who types ``brailix --help``, and drifted with the rest.
    """
    import brailix.cli as cli

    return [
        ("brailix/cli.py module docstring", cli.__doc__ or ""),
        ("brailix/cli.py --help epilog", cli._EPILOG),
    ]


def _sources() -> list[tuple[str, str]]:
    return _docs() + _cli_text_sources()


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _command_lines(label: str, text: str) -> list[tuple[int, str]]:
    """Candidate command lines: fenced shell blocks, inline code, and — for
    the CLI's own plain text — every line."""
    out: list[tuple[int, str]] = []
    if label.endswith(".md"):
        for match in _SHELL_FENCE.finditer(text):
            base = _line_of(text, match.start(1))
            for offset, line in enumerate(match.group(1).splitlines()):
                out.append((base + offset, line))
        for match in _INLINE_CODE.finditer(text):
            out.append((_line_of(text, match.start(1)), match.group(1)))
    else:
        out.extend(enumerate(text.splitlines(), start=1))
    return out


def _argv_of(line: str) -> list[str] | None:
    """The ``brailix`` argv a documentation line means, or ``None``.

    Handles the shapes these documents use: a description column aligned
    after the command, a trailing ``#`` comment, and a pipeline whose last
    stage is the brailix invocation (``echo … | brailix …``).
    """
    stripped = _DESCRIPTION_COLUMN.split(line.strip(), maxsplit=1)[0]
    if not stripped:
        return None
    try:
        tokens = shlex.split(stripped, comments=True)
    except ValueError:  # unbalanced quotes: not a command line we can read
        return None
    if "|" in tokens:  # take the stage after the last pipe
        tokens = tokens[len(tokens) - tokens[::-1].index("|"):]
    for prefix in _COMMAND_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            # A bare ``brailix`` is the program's *name* — "installing brailix
            # puts a `brailix` command on your PATH" — not an invocation
            # anyone is being told to type.
            return tokens[len(prefix):] or None
    return None


def _check_command(argv: list[str]) -> str | None:
    """``None`` if the CLI accepts ``argv``, else why it doesn't.

    Mirrors ``main()`` — parse, let a discovery flag short-circuit, then
    validate — by calling the same three functions rather than restating what
    they do, so a new discovery flag or a new rejected combination needs no
    edit here. Nothing is translated: the point is whether the invocation is
    one the CLI would go on to run.
    """
    from brailix.cli import _handle_discovery, _validate, build_parser

    parser = build_parser()
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured), contextlib.redirect_stdout(
            captured
        ):
            args = parser.parse_args(argv)
            if _handle_discovery(args) is not None:
                return None  # --list-* / --version printed and would exit 0
            _validate(args, parser)
    except SystemExit:
        message = captured.getvalue().strip().splitlines()
        return message[-1] if message else "rejected by the parser"
    return None


@pytest.mark.parametrize("source", _sources(), ids=lambda s: s[0])
def test_every_documented_command_is_one_the_cli_accepts(
    source: tuple[str, str],
) -> None:
    label, text = source
    broken: list[str] = []
    for lineno, line in _command_lines(label, text):
        argv = _argv_of(line)
        if argv is None:
            continue
        problem = _check_command(argv)
        if problem is not None:
            broken.append(f"{label}:{lineno}: {line.strip()}\n    → {problem}")
    assert not broken, (
        "documented commands the CLI rejects — a reader who copies one gets "
        "a usage error, not a translation:\n" + "\n".join(broken)
    )


def test_the_command_scan_actually_found_commands() -> None:
    found = sum(
        1
        for label, text in _sources()
        for _lineno, line in _command_lines(label, text)
        if _argv_of(line) is not None
    )
    assert found >= 15, (
        f"only extracted {found} documented commands — the fence or "
        f"description-column convention changed and the check above stopped "
        f"seeing anything"
    )


def test_every_published_page_was_actually_found() -> None:
    """The whole published set turned up — each page named, not counted.

    Everything above is parametrized over what this scan returns, so a page it
    stops finding is a page whose commands and examples are no longer checked
    at all. That has to fail here, saying which one, rather than leaving the
    suite green over a shrunken scope.
    """
    found = {_published_page(rel) for rel, _ in _docs()}
    assert found == set(_EXPECTED_DOCS), (
        f"published pages the scan did not find: "
        f"{sorted(set(_EXPECTED_DOCS) - found)} — either the page was renamed "
        f"or moved (fix _DOC_GLOBS / _EXPECTED_DOCS together) or it is gone, "
        f"in which case every command and example it held has quietly left "
        f"the checks above"
    )


# ---------------------------------------------------------------------------
# Python examples: the calls have to fit the signatures
# ---------------------------------------------------------------------------


class _Placeholder:
    """Stands in for an argument's value: only arity and names are checked."""


def _imported_names(tree: ast.Module) -> dict[str, object]:
    """``{local name: object}`` for everything the example imports from
    ``brailix``. An import that needs an uninstalled extra is skipped — the
    extension guide's own check is what reports those."""
    names: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("brailix"):
            continue
        try:
            module = importlib.import_module(node.module)
        except ImportError:
            continue
        for alias in node.names:
            obj = getattr(module, alias.name, None)
            if obj is not None:
                names[alias.asname or alias.name] = obj
    return names


def _instances(tree: ast.Module, names: dict[str, object]) -> dict[str, type]:
    """``{variable: class}`` for ``pipe = Pipeline(...)`` — which is how every
    guide reaches the methods a reader copies most (``translate_text``,
    ``translate_file``, ``translate_document``)."""
    out: dict[str, type] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        target = node.targets[0]
        if isinstance(target, ast.Name) and isinstance(func, ast.Name):
            obj = names.get(func.id)
            if inspect.isclass(obj):
                out[target.id] = obj
    return out


def _resolve_call(
    node: ast.Call, names: dict[str, object], instances: dict[str, type]
) -> tuple[str, object, int] | None:
    """``(label, callable, implicit positional args)``, or ``None`` when the
    callee isn't something this example told us how to find."""
    func = node.func
    if isinstance(func, ast.Name):
        obj = names.get(func.id)
        return (func.id, obj, 0) if obj is not None else None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        cls = instances.get(func.value.id)
        if cls is None:
            return None
        # ``self`` is the implicit first argument of a method read off its
        # class, so the binding below has to supply one.
        return (f"{cls.__name__}.{func.attr}", getattr(cls, func.attr, None), 1)
    return None


def _python_call_problems(label: str, text: str) -> tuple[list[str], int]:
    """Signature problems in ``text``'s Python examples, and how many calls
    were checked."""
    problems: list[str] = []
    checked = 0
    for match in _PYTHON_FENCE.finditer(text):
        base = _line_of(text, match.start(1)) - 1
        try:
            tree = ast.parse(match.group(1))
        except SyntaxError as exc:
            problems.append(f"{label}:{base + (exc.lineno or 1)}: does not parse ({exc})")
            continue
        names = _imported_names(tree)
        instances = _instances(tree, names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolve_call(node, names, instances)
            if resolved is None:
                continue
            name, target, implicit = resolved
            where = f"{label}:{base + node.lineno}: {name}(...)"
            if target is None:
                problems.append(f"{where}\n    → no such attribute")
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                kw.arg is None for kw in node.keywords
            ):
                continue  # *args / **kwargs: nothing to bind
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):  # a builtin without one
                continue
            checked += 1
            try:
                signature.bind(
                    *[_Placeholder()] * (implicit + len(node.args)),
                    **{kw.arg: _Placeholder() for kw in node.keywords if kw.arg},
                )
            except TypeError as exc:
                problems.append(f"{where}\n    → {exc}")
    return problems, checked


@pytest.mark.parametrize("doc", _docs(), ids=lambda d: d[0])
def test_every_documented_call_fits_the_signature(doc: tuple[str, str]) -> None:
    label, text = doc
    problems, _checked = _python_call_problems(label, text)
    assert not problems, (
        "documented calls that raise TypeError as written — the first thing a "
        "reader pastes:\n" + "\n".join(problems)
    )


def test_the_call_scan_actually_checked_calls() -> None:
    checked = sum(_python_call_problems(label, text)[1] for label, text in _docs())
    assert checked >= 6, (
        f"only bound {checked} documented calls — the Python fences or the "
        f"imports they resolve through changed shape, and the check above "
        f"stopped seeing them"
    )
