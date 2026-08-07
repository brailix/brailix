"""The extension guides must describe the contract the code enforces.

A wrong sentence here is not a typo: it is a broken public contract. The
registries validate an adapter against its Protocol at runtime, on first
resolution — so a third-party implementation written by following the guide
exactly is *rejected at* ``get()``, in the plugin author's own installation,
with nothing in the guide to explain why. That happened: both architecture
documents and the extension guide named only two of the three
``LanguageBackend`` methods required at the time, omitting
``translate_date_marker``.

Comparing prose against a Protocol is not generally mechanisable, but two parts
are, and both have broken. **Every required method name must appear where the
guide tells you to implement that protocol** — the omission above. And the
other direction: **the guide must not teach a method the protocol no longer
has.** A superfluous method does not fail a Protocol check, so when
``translate_hanzi_char`` was retired the guides kept teaching it and every
existing check stayed green; a plugin author would have written a method that
is never called, and taken away the belief that single characters still have an
IR type of their own. So the worked example's method set is compared for
*equality*, and the documents are scanned for the retired type names
themselves.

The documents are *found*, not assumed: a checkout may carry the architecture
overview in more than one language, and may keep the extension guide at the top
level or stage it a couple of directories down. Each is globbed, with a floor on
how many must turn up — a pattern that silently stopped matching would make
every check below vacuous while still passing.
"""

from __future__ import annotations

import contextlib
import inspect
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# The documents that tell an extender what to implement, as globs — see the
# module docstring for why they are found rather than listed.
_DOC_GLOBS = (
    "ARCHITECTURE*.md",
    "docs/extending.md",
    "*/*/docs/extending.md",
)

# Just the extension guide, not the architecture documents. Describing the
# internals *is* what those are for — ``ARCHITECTURE.md`` names
# ``brailix.core.errors`` and a hundred others because it explains how the
# library is built, and holding it to "supported paths only" would be holding
# it to the wrong contract. The guide is different: it tells a third party what
# to type.
_GUIDE_GLOBS = ("docs/extending.md", "*/*/docs/extending.md")

# An architecture overview plus the extension guide is the minimum any checkout
# carries. Below this the globs have gone stale rather than the docs having
# shrunk.
_MIN_DOCS = 2


def _read(globs: tuple[str, ...]) -> list[tuple[str, str]]:
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for glob in globs
        for path in sorted(_ROOT.glob(glob))
        if path.is_file()
    ]


def _extension_docs() -> list[tuple[str, str]]:
    return _read(_DOC_GLOBS)


def _guides() -> list[tuple[str, str]]:
    found = _read(_GUIDE_GLOBS)
    assert found, f"no extension guide found among {_GUIDE_GLOBS}"
    return found


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
        f"only found {found} — _DOC_GLOBS has gone stale (a doc was renamed "
        f"or moved), which would make every check below pass on nothing"
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
    single entry point. It is the map an extender reads before deciding
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
    date-marker method — a language with no special date rule still writes one
    out, which is exactly the sentence the guides were missing.
    """
    from brailix.core.protocols import LanguageBackend

    class DocumentedBackend:
        def translate_word(self, node, ctx, profile):  # noqa: ANN001, ANN201
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


def test_a_backend_missing_the_date_marker_method_is_rejected() -> None:
    """The other half, and the reason this file exists: the shape the guides
    used to describe does NOT satisfy the protocol. Without this, the check
    above would keep passing even if the requirement were quietly relaxed."""
    from brailix.core.protocols import LanguageBackend

    class OldGuideBackend:
        def translate_word(self, node, ctx, profile):  # noqa: ANN001, ANN201
            return []

    assert not isinstance(OldGuideBackend(), LanguageBackend)


# ---------------------------------------------------------------------------
# The guide's worked example is checked for EQUALITY, not just presence
# ---------------------------------------------------------------------------
#
# ``isinstance`` against a Protocol answers "does this have everything
# required", never "does it have anything extra" — so a guide that teaches a
# retired method produces an example that passes every check while being wrong.
# That is not hypothetical: after ``translate_hanzi_char`` was removed from the
# protocol, the guide, both architecture documents and the transcribed example
# above all kept it, and nothing went red.

# The guide's fenced code blocks. The worked backend is the one whose block
# registers a language backend — located by that line rather than by the
# example class's name, so renaming the example language doesn't silently stop
# the scan. Fences may be indented (the example sits inside a numbered list).
_CODE_FENCE = re.compile(
    r"^[ \t]*```(?:python)?\n(.*?)^[ \t]*```", re.DOTALL | re.MULTILINE
)
_EXAMPLE_METHOD = re.compile(r"^[ \t]+def\s+(\w+)\(", re.MULTILINE)


@pytest.mark.parametrize("doc", _guides(), ids=lambda d: d[0])
def test_the_guides_backend_example_implements_exactly_the_protocol(
    doc: tuple[str, str],
) -> None:
    """Every method the example writes is required, and every required method
    is written. A reader copies this block whole."""
    from brailix.core.protocols import LanguageBackend

    rel, text = doc
    blocks = [
        block
        for block in _CODE_FENCE.findall(text)
        if "language_backend_registry.register" in block
    ]
    assert len(blocks) == 1, (
        f"{rel}: found {len(blocks)} code blocks registering a language "
        f"backend, expected exactly one — the scan went stale, so this check "
        f"is passing on nothing"
    )
    written = set(_EXAMPLE_METHOD.findall(blocks[0]))
    required = _required_methods(LanguageBackend)
    assert written == required, (
        f"{rel}: the worked backend implements {sorted(written)} but "
        f"LanguageBackend requires exactly {sorted(required)}. A missing method "
        f"means the registry rejects a reader's adapter at get(); an extra one "
        f"means they write code that is never called and take away a wrong "
        f"model of the IR."
    )


# ---------------------------------------------------------------------------
# The current-architecture documents don't describe a retired type as current
# ---------------------------------------------------------------------------

# The retired IR type tags. Hand-listed, and that is a change: this used to be
# derived from the deserializer's compatibility table, on the reading that the
# table WAS the definition of "retired". It was not — a type only got an entry
# if it could map losslessly onto its replacement, so ``Quantity`` and
# ``Percent`` retired without one and this scan never looked for them. (The
# table has since been removed outright: nothing persists inline IR, so it
# bridged nothing.) A list has to be extended by hand when the next type folds
# away, which is the honest cost of naming a thing that no longer exists
# anywhere to be read off.
_RETIRED_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "HanziChar",  # single characters — now a one-character Word
        "LatinAcronym",  # all-caps runs — now a plain LatinWord
        "Quantity",  # number + unit — now a Number beside a LatinWord
        "Percent",  # number + % — now a Number beside a Punct
        # The LanguageBackend method HanziChar used to require of every
        # language. The other three were module functions, never protocol.
        "translate_hanzi_char",
    }
)


def _retired_names() -> set[str]:
    return set(_RETIRED_TYPE_NAMES)


@pytest.mark.parametrize("doc", _extension_docs(), ids=lambda d: d[0])
def test_no_extension_document_names_a_retired_type(doc: tuple[str, str]) -> None:
    """These documents describe the model as it is now.

    A retired tag still deserializes — ``ir/inline.py`` keeps that promise and
    says so — but that is a note about reading old files, and it lives with the
    code that reads them. Here, naming ``HanziChar`` beside ``Word`` says the
    distinction is part of the current model, which is what a reader designing
    an adapter (or a node type of their own) takes away.
    """
    rel, text = doc
    retired = _retired_names()
    assert retired, "no retired tags found — the scan broke"
    found = sorted(name for name in retired if name in text)
    assert not found, (
        f"{rel} names retired IR types as if they were current: {found}. "
        f"Nothing in the library carries them any more — see "
        f"_RETIRED_TYPE_NAMES — so a description of the model an extender "
        f"writes against should not either."
    )


# ---------------------------------------------------------------------------
# The guide's analyzer example is RUN, against the boundary it has to satisfy
# ---------------------------------------------------------------------------
#
# The backend example above is checked for the shape of its method set, which
# is all a Protocol can be checked against. An analyzer example needs more than
# that, because what makes one wrong is not its methods but the *tokens* it
# produces: the guide's version searched with ``text.find`` and built
# ``Span(start, start + len(w))`` from the result without ever testing it, so
# the first word the tokenizer normalised returned ``-1`` and the reader's
# adapter died inside ``Span.__post_init__`` — in library code, for a mistake
# the guide had handed them. Nothing here could see it: the checks read
# imports, method sets and signatures, none of which the example got wrong.
#
# So this one is executed, exactly as the document prints it, and driven
# through the real ``tokenize`` boundary with the inputs that break a naive
# alignment loop: a repeated word, a word the tokenizer rewrote, and a word
# running off the end of the text.


@contextlib.contextmanager
def _guide_analyzer_module(text: str, rel: str):  # noqa: ANN201
    """Execute the guide's analyzer example, yielding its namespace.

    ``_run_lac`` is the one name the example leaves to the reader (it stands
    for their tokenizer), so it is injected; everything else in the block —
    the imports, the class, and the ``analyzer_registry.register`` line — is
    the document's own code, run as written. That registration is real, which
    is why the whole exec sits inside ``Registry.overriding()``: the example's
    own line is what puts ``"lac"`` in the registry for the body of the test,
    and the snapshot takes it back out again afterwards.
    """
    from brailix.frontend.zh.analyzer.registry import analyzer_registry

    blocks = [b for b in _CODE_FENCE.findall(text) if "analyzer_registry.register" in b]
    assert len(blocks) == 1, (
        f"{rel}: found {len(blocks)} code blocks registering a Chinese "
        f"analyzer, expected exactly one — the scan went stale, so the check "
        f"below is passing on nothing"
    )
    with analyzer_registry.overriding():
        namespace: dict[str, object] = {"_run_lac": lambda t: []}
        exec(blocks[0], namespace)  # noqa: S102 — running the example IS the test
        assert analyzer_registry.has("lac"), (
            f"{rel}: the example's own registration line did not take effect"
        )
        yield namespace


@pytest.mark.parametrize("doc", _guides(), ids=lambda d: d[0])
def test_the_guides_analyzer_example_survives_its_own_alignment_cases(
    doc: tuple[str, str],
) -> None:
    """The example, run over the inputs that produce mis-aligned tokens.

    Each case asserts the *frontend contract* the library now enforces at
    ``tokenize``, so a future edit to the guide that reintroduces a naive
    ``find`` loop fails here rather than in a plugin author's installation.
    """
    from brailix.core.context import FrontendContext
    from brailix.core.errors import FrontendContractError
    from brailix.frontend.zh.analyzer import tokenize

    rel, text = doc
    cases = [
        # A repeated word: the second 很好 must resolve to its own occurrence,
        # which is what searching from the cursor buys.
        ("很好，很好", ["很好", "，", "很好"], [(0, 2), (2, 3), (3, 5)]),
        # A word the tokenizer rewrote (full-width to half-width): not in the
        # source as written, so ``find`` returns -1. The naive version built
        # Span(-1, 2) here and raised.
        ("ＡＢＣ好", ["ABC", "好"], [(0, 3), (3, 4)]),
        # A rewritten word at the very end, longer than what is left of the
        # text: the span has to be clamped, not run past it.
        ("好", ["好好好"], [(0, 1)]),
        # Nothing at all: an empty text must not produce a token either.
        ("", [], []),
    ]

    with _guide_analyzer_module(text, rel) as namespace:
        for source, words, expected in cases:
            namespace["_run_lac"] = lambda _t, _w=words: list(_w)
            ctx = FrontendContext("cn_current", options={"zh_analyzer": "lac"})
            try:
                tokens = tokenize(source, ctx)
            except FrontendContractError as exc:  # pragma: no cover - the bug
                pytest.fail(
                    f"{rel}: the guide's analyzer example violates the "
                    f"frontend contract on {source!r} → {words}: {exc}"
                )
            assert [t.span.to_tuple() for t in tokens] == expected, (
                f"{rel}: on {source!r} → {words}"
            )
            assert [t.surface for t in tokens] == words


@pytest.mark.parametrize("doc", _guides(), ids=lambda d: d[0])
def test_the_guides_analyzer_example_reports_what_it_could_not_place(
    doc: tuple[str, str],
) -> None:
    """Anchoring a rewritten word at the cursor is a guess, and the example
    has to say so — otherwise a proofreader is sent to those characters with
    no indication that the coordinates are approximate.

    Two warnings are expected and both are wanted: the adapter's own, which
    names the word its tokenizer invented, and brailix's ``TOKEN_SPAN_MISMATCH``
    from the boundary check, which is what a reader gets from *any* adapter
    whose surfaces do not match the source.
    """
    from brailix.core.context import FrontendContext
    from brailix.frontend.zh.analyzer import tokenize

    rel, text = doc
    ctx = FrontendContext("cn_current", options={"zh_analyzer": "lac"})
    with _guide_analyzer_module(text, rel) as namespace:
        namespace["_run_lac"] = lambda _t: ["ABC", "好"]
        tokenize("ＡＢＣ好", ctx)

    codes = [w.code for w in ctx.warnings]
    assert "TOKEN_SPAN_MISMATCH" in codes, f"{rel}: {codes}"
    assert any(w.code.endswith("_WORD_NOT_IN_TEXT") for w in ctx.warnings), (
        f"{rel}: the example placed a word it could not find without warning "
        f"about it: {codes}"
    )


def test_the_naive_alignment_loop_really_does_break() -> None:
    """The shape the guide used to print, kept as the reason the one above is
    written the way it is.

    Without this, the example could quietly go back to ``Span(start, start +
    len(w))`` on a checkout where no test input happens to be normalised, and
    every check would stay green.
    """
    from brailix.core.span import Span

    text, words = "ＡＢＣ好", ["ABC", "好"]
    cursor = 0
    with pytest.raises(ValueError):
        for w in words:
            start = text.find(w, cursor)
            Span(start, start + len(w))  # start == -1 on the first word
            cursor = start + len(w)


class TestTheDocumentScans:
    """Both scans above run on real files, where a clean tree and a scan that
    stopped matching produce the same green. These pin what each one sees."""

    def test_the_retired_name_scan_covers_every_folded_away_type(self) -> None:
        # The type names as a document writes them, plus the protocol method
        # one of them used to require. Listed rather than derived — see
        # _RETIRED_TYPE_NAMES for why — so this is what keeps the list from
        # silently shrinking.
        assert {
            "HanziChar", "LatinAcronym", "Quantity", "Percent",
            "translate_hanzi_char",
        } <= _retired_names()

    def test_the_retired_name_scan_reports_a_mention(self) -> None:
        retired = _retired_names()
        assert [n for n in retired if n in "the IR carries Word / HanziChar"]

    def test_the_fence_scan_reads_an_example_indented_under_a_list(self) -> None:
        doc = (
            "3. **Backend**:\n"
            "\n"
            "    ```python\n"
            "    class KoBackend:\n"
            "        def translate_word(self, node, ctx, profile):\n"
            "            return []\n"
            "\n"
            "    language_backend_registry.register('ko', KoBackend)\n"
            "    ```\n"
        )
        blocks = [
            b
            for b in _CODE_FENCE.findall(doc)
            if "language_backend_registry.register" in b
        ]
        assert len(blocks) == 1
        assert set(_EXAMPLE_METHOD.findall(blocks[0])) == {"translate_word"}

    def test_the_fence_scan_ignores_other_examples(self) -> None:
        doc = (
            "```python\n"
            "class KoAnalyzer:\n"
            "    def analyze(self, text, ctx):\n"
            "        return []\n"
            "```\n"
            "\n"
            "```python\n"
            "class KoBackend:\n"
            "    def translate_word(self, node, ctx, profile):\n"
            "        return []\n"
            "\n"
            "language_backend_registry.register('ko', KoBackend)\n"
            "```\n"
        )
        blocks = [
            b
            for b in _CODE_FENCE.findall(doc)
            if "language_backend_registry.register" in b
        ]
        assert len(blocks) == 1
        assert set(_EXAMPLE_METHOD.findall(blocks[0])) == {"translate_word"}


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
