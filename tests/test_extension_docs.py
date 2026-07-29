"""The extension guides must describe the contract the code enforces.

A wrong sentence here is not a typo: it is a broken public contract. The
registries validate an adapter against its Protocol at runtime, on first
resolution — so a third-party implementation written by following the guide
exactly is *rejected at* ``get()``, in the plugin author's own installation,
with nothing in the guide to explain why. That happened: both architecture
documents and the extension guide named two of ``LanguageBackend``'s three
required methods, omitting ``translate_date_marker``.

Comparing prose against a Protocol is not generally mechanisable, but the part
that broke is: **every required method name must appear where the guide tells
you to implement that protocol.** That is what these checks pin. They cannot
tell you the surrounding sentence is right; they can guarantee no required
method goes unmentioned.

Both repositories are covered by one file. This is a whole-package guard that
ships to the public mirror, where the layout differs — the Chinese
``ARCHITECTURE.md`` is the private canonical copy and never exports, and the
mirror's ``ARCHITECTURE.md`` is what ``ARCHITECTURE.en.md`` becomes, while the
guide lives under ``scripts/public_overlay/docs/`` here and ``docs/`` there. So
the candidates are probed for existence, with a floor on how many must have
been found: a path list that silently stopped matching would make every check
below vacuous.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# (repo-relative path, human label). Probed for existence — see the module
# docstring for why the two repositories disagree on these paths.
_DOC_CANDIDATES = (
    "ARCHITECTURE.md",
    "ARCHITECTURE.en.md",
    "docs/extending.md",
    "scripts/public_overlay/docs/extending.md",
)

# Below this, the path list has gone stale rather than the docs having shrunk:
# the public mirror carries two (ARCHITECTURE.md + docs/extending.md), this
# repository three.
_MIN_DOCS = 2


def _extension_docs() -> list[tuple[str, str]]:
    return [
        (rel, (_ROOT / rel).read_text(encoding="utf-8"))
        for rel in _DOC_CANDIDATES
        if (_ROOT / rel).is_file()
    ]


def _required_methods(protocol: type) -> set[str]:
    """The method names an implementation of ``protocol`` must provide.

    Read off the class body rather than through ``typing`` internals, which
    have moved twice across the versions brailix supports.
    """
    return {
        name
        for name, value in vars(protocol).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }


def test_the_document_set_was_actually_found() -> None:
    found = [rel for rel, _ in _extension_docs()]
    assert len(found) >= _MIN_DOCS, (
        f"only found {found} — _DOC_CANDIDATES has gone stale (a doc was "
        f"renamed or moved), which would make every check below pass on "
        f"nothing"
    )


def test_every_language_backend_method_is_documented() -> None:
    """``LanguageBackend`` is the seam a new language plugs into, and the one
    where the omission actually bit: ``translate_date_marker`` carries a
    language's date-marker readings *and* its number→marker joiner rule, so it
    has no sensible inherited default and the protocol makes it required."""
    from brailix.core.protocols import LanguageBackend

    required = _required_methods(LanguageBackend)
    assert required, "LanguageBackend has no methods — the scan broke"

    missing: list[str] = []
    for rel, text in _extension_docs():
        absent = sorted(m for m in required if m not in text)
        if absent:
            missing.append(f"{rel}: {absent}")
    assert not missing, (
        "LanguageBackend methods a language guide never names — an "
        "implementation written from this guide is rejected by the registry's "
        "runtime protocol check at get():\n" + "\n".join(missing)
    )


@pytest.mark.parametrize(
    "protocol_name",
    ["MathSourceAdapter", "MusicSourceAdapter", "GraphicSourceAdapter"],
)
def test_every_source_adapter_method_is_documented(protocol_name: str) -> None:
    """The three verticals' adapter protocols get the same treatment, so the
    next one to grow a method can't repeat the omission."""
    import brailix.core.protocols as protocols

    protocol = getattr(protocols, protocol_name)
    required = _required_methods(protocol)
    assert required, f"{protocol_name} has no methods — the scan broke"

    missing = [
        f"{rel}: {sorted(m for m in required if m not in text)}"
        for rel, text in _extension_docs()
        if any(m not in text for m in required)
    ]
    assert not missing, (
        f"{protocol_name} methods no extension guide names:\n"
        + "\n".join(missing)
    )


def test_the_frontend_subsystem_table_matches_reality() -> None:
    """``brailix.frontend``'s module docstring tabulates each subsystem's
    single public entry point. It is the map an extender reads before deciding
    where their adapter plugs in, and it had drifted both ways: it listed
    entries this facade does not export (reading as if it described
    ``brailix.frontend``'s own surface), while the graphics subsystem — a
    shipped vertical with a registered adapter protocol — was absent entirely.

    Each row is resolved: the module imports and really does define that
    callable. Row count is floored too, since a table format change that
    stopped matching would otherwise turn this into a no-op.
    """
    import importlib
    import re

    import brailix.frontend as frontend

    rows = re.findall(
        r"``(frontend\.[\w.]+)``\s+:func:`(\w+)`", frontend.__doc__ or ""
    )
    assert len(rows) >= 8, (
        f"only parsed {len(rows)} subsystem rows — the table's format changed "
        f"and this check stopped seeing it"
    )

    broken: list[str] = []
    for dotted, func in rows:
        module = f"brailix.{dotted}"
        try:
            mod = importlib.import_module(module)
        except ImportError as exc:  # pragma: no cover — a rename would hit this
            broken.append(f"{module} does not import ({exc})")
            continue
        if not hasattr(mod, func):
            broken.append(f"{module} has no {func}")
    assert not broken, (
        "the frontend subsystem table names entries that don't exist:\n"
        + "\n".join(broken)
    )


def test_a_backend_written_from_the_guide_satisfies_the_protocol() -> None:
    """The guide's own worked example, transcribed.

    The check the documents cannot make on themselves: the shape they describe
    really does pass the runtime protocol check the registry applies. Note the
    third method — a language with no special date rule still writes one out,
    which is exactly the sentence the guides were missing.
    """
    from brailix.core.protocols import LanguageBackend

    class DocumentedBackend:
        def translate_word(self, node, ctx, profile):  # noqa: ANN001, ANN201
            return []

        def translate_hanzi_char(self, node, ctx, profile):  # noqa: ANN001, ANN201
            return []

        def translate_date_marker(  # noqa: ANN201
            self,
            marker,  # noqa: ANN001
            follows_number,  # noqa: ANN001
            ctx,  # noqa: ANN001
            profile,  # noqa: ANN001
        ):
            return []

    assert isinstance(DocumentedBackend(), LanguageBackend)


def test_the_two_method_backend_the_old_guide_described_is_rejected() -> None:
    """The other half, and the reason this file exists: what the guides used to
    describe does NOT satisfy the protocol. Without this, the check above would
    keep passing even if the requirement were quietly relaxed."""
    from brailix.core.protocols import LanguageBackend

    class OldGuideBackend:
        def translate_word(self, node, ctx, profile):  # noqa: ANN001, ANN201
            return []

        def translate_hanzi_char(self, node, ctx, profile):  # noqa: ANN001, ANN201
            return []

    assert not isinstance(OldGuideBackend(), LanguageBackend)


# ---------------------------------------------------------------------------
# The import paths the guide prints are the ones it promises to support
# ---------------------------------------------------------------------------
#
# The guide states the rule itself: import IR and core types from the shallow
# facades, and take the protocols and registries from the deeper paths the
# extension manifest pins. Everything else under those subsystems is internal
# and free to move.
#
# It was breaking its own rule in the same paragraph — naming `ChineseToken`
# as coming "from `brailix.ir.inline`" one line above an example that correctly
# writes `from brailix.ir import ChineseToken`. Both spellings import, so
# nothing failed; a plugin author following the prose simply pinned their
# adapter to a path the policy calls internal.
#
# The anchor guard next door scans Python only, which is why a drift in a
# Markdown file that *is* a public contract could sit there. This scans the
# guides.

_MODULE_MENTION = re.compile(r"`(brailix(?:\.[a-z_][a-z0-9_]*)+)`")

# Only the extension guide, not the architecture documents. Describing the
# internals *is* what those are for — ``ARCHITECTURE.md`` names
# ``brailix.core.errors`` and a hundred others because it explains how the
# library is built, and holding it to "supported paths only" would be holding
# it to the wrong contract. The guide is different: it tells a third party what
# to type.
_GUIDE_CANDIDATES = (
    "docs/extending.md",
    "scripts/public_overlay/docs/extending.md",
)


def _guides() -> list[tuple[str, str]]:
    found = [
        (rel, (_ROOT / rel).read_text(encoding="utf-8"))
        for rel in _GUIDE_CANDIDATES
        if (_ROOT / rel).is_file()
    ]
    assert found, f"no extension guide found among {_GUIDE_CANDIDATES}"
    return found


def _supported_paths() -> set[str]:
    """Every ``brailix`` address a third party is told to import from, plus
    the published names reachable at each.

    Taken from the two manifests rather than restated, so publishing a new
    address stays one edit in one place. The names are included because the
    guide writes both forms — ``brailix.input`` as a module and
    ``brailix.input.parse_file`` as the function in it — and a fully qualified
    published name is as supported as the module holding it.
    """
    from tests.test_public_api import _EXTENSION_SURFACE, _FACADE

    manifests = {**_FACADE, **_EXTENSION_SURFACE}
    return set(manifests) | {
        f"{module}.{name}"
        for module, names in manifests.items()
        for name in names
    }


@pytest.mark.parametrize("doc", _guides(), ids=lambda d: d[0])
def test_the_guide_names_only_supported_paths(doc: tuple[str, str]) -> None:
    """A path in the guide is an instruction, whether or not it sits in a code
    block — a reader copies either one."""
    rel, text = doc
    supported = _supported_paths()
    offenders = sorted(
        {path for path in _MODULE_MENTION.findall(text) if path not in supported}
    )
    assert not offenders, (
        f"{rel} names paths that are not on a supported surface: "
        f"{offenders} — point readers at the facade or the extension-manifest "
        f"path instead, or publish it (in _FACADE / _EXTENSION_SURFACE) as a "
        f"deliberate promise"
    )


@pytest.mark.parametrize("doc", _extension_docs(), ids=lambda d: d[0])
def test_the_imports_the_guides_print_actually_work(doc: tuple[str, str]) -> None:
    """And the names really resolve there. A guide is the one piece of
    documentation a reader executes verbatim."""
    import importlib

    rel, text = doc
    broken: list[str] = []
    for module, names in re.findall(
        r"^from (brailix[\w.]*) import (.+)$", text, re.MULTILINE
    ):
        try:
            mod = importlib.import_module(module)
        except ImportError as e:
            broken.append(f"{module}: {e}")
            continue
        for name in (n.strip() for n in names.split(",")):
            if name and not hasattr(mod, name):
                broken.append(f"{module}.{name} does not exist")
    assert not broken, f"{rel} prints imports that fail:\n" + "\n".join(broken)
