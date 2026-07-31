"""The architecture document's IR type lists must be the registries' contents.

Those two enumerations — block types and inline token types — are the only
place a reader is told what the *closed* set of IR nodes is (the set is
deliberately closed: adding a node is a coordinated change across dataclass,
registry, serialization, backend dispatch and schema, not a plugin seam). A
contributor works from the list, so a node missing from it is a node they will
forget in the dispatch table or the serializer. That happened: ``graphic`` and
``graphic_inline`` were registered, exported from the ``brailix.ir`` facade and
present in the JSON Schema while the documents still described the pre-graphics
set.

Prose cannot be checked mechanically, but a *declared complete enumeration*
can, so this pins exactly that much: which tags appear, not what is said about
them. The enumerations are found by shape rather than by the sentence that
introduces them: the overview is maintained in more than one language, each
copy organised on its own terms, so the introducing prose is not something to
match on — while the list itself keeps the same shape wherever it is written.

Scoped to ``ARCHITECTURE*.md`` on purpose: it is the document that claims to
describe the set as it *is*. Design notes written at an earlier stage enumerate
the set as of that stage, and holding a historical snapshot to today's registry
would either force rewriting history or make this test the reason not to write
one.
"""

from __future__ import annotations

import re
from pathlib import Path

from brailix.ir.document import _BLOCK_REGISTRY
from brailix.ir.inline import _INLINE_REGISTRY

_ROOT = Path(__file__).resolve().parents[1]
_ARCH_DOC_GLOB = "ARCHITECTURE*.md"

# A slash-separated enumeration of lowercase identifiers, as both documents
# write their type lists — inline in a sentence between backticks (the block
# list) or as a fenced block wrapping across lines (the inline list). ``\s``
# covers the wrap; backticks fall outside the token pattern and so act as
# separators like any other punctuation.
_TOKEN = r"[a-z][a-z0-9_]*"
_RUN = re.compile(rf"{_TOKEN}(?:\s*/\s*`?{_TOKEN}`?)+")
_TOKEN_RE = re.compile(_TOKEN)

# How many *known* type tags a slash-run needs before it counts as one of the
# type enumerations. Documents carry unrelated slash-runs (``auto / char /
# jieba / thulac / hanlp``, ``svg / primitives / figure / image``), and none of
# those tokens is a registered tag, so any threshold above one excludes them
# while a genuine list clears it many times over. It also means a two-tag
# fragment cannot masquerade as a complete list.
_MIN_KNOWN_TAGS = 3


def _arch_docs() -> list[tuple[str, str]]:
    """Every architecture overview in the checkout, as (relative path, text).

    Globbed rather than listed: the overview is maintained in more than one
    language and a checkout may carry any one of those copies, so a hard-coded
    filename pair would fail wherever the set differs.
    """
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(_ROOT.glob(_ARCH_DOC_GLOB))
        if path.is_file()
    ]


def _enumerated_tags(text: str) -> tuple[set[str], set[str], set[str]]:
    """Tags named by ``text``'s type enumerations, split three ways.

    Returns ``(block tags, inline tags, tags in neither registry)``. The two
    registries have disjoint tag sets, so a run's tokens sort themselves; the
    third bucket is what catches a list still naming a node that was renamed
    or removed.
    """
    blocks: set[str] = set()
    inlines: set[str] = set()
    unknown: set[str] = set()
    for run in _RUN.findall(text):
        tokens = _TOKEN_RE.findall(run)
        known = [
            t for t in tokens if t in _BLOCK_REGISTRY or t in _INLINE_REGISTRY
        ]
        if len(known) < _MIN_KNOWN_TAGS:
            continue  # not a type enumeration
        for token in tokens:
            if token in _BLOCK_REGISTRY:
                blocks.add(token)
            elif token in _INLINE_REGISTRY:
                inlines.add(token)
            else:
                unknown.add(token)
    return blocks, inlines, unknown


def test_an_architecture_document_was_found() -> None:
    """Without this, a renamed document would make every check below pass on
    nothing at all."""
    assert _arch_docs(), (
        f"no {_ARCH_DOC_GLOB} in {_ROOT} — the glob has gone stale, and the "
        f"type-list checks below would silently verify nothing"
    )


def test_the_run_scanner_still_finds_the_enumerations() -> None:
    """Regex-rot guard: if the documents reformat their lists into a shape the
    scanner no longer matches, the comparisons would come up empty-vs-empty on
    one side and merely look wrong on the other. Assert the scanner sees a
    plausible list before trusting what it reports."""
    for rel, text in _arch_docs():
        blocks, inlines, _ = _enumerated_tags(text)
        assert len(blocks) >= _MIN_KNOWN_TAGS, (
            f"{rel}: found no block-type enumeration — reformatted away from "
            f"a slash-separated list?"
        )
        assert len(inlines) >= _MIN_KNOWN_TAGS, (
            f"{rel}: found no inline-token enumeration — reformatted away "
            f"from a slash-separated list?"
        )


def _drift_report(
    rel: str, kind: str, documented: set[str], registered: set[str]
) -> str | None:
    if documented == registered:
        return None
    return (
        f"{rel}: {kind} list disagrees with the registry.\n"
        f"    registered but undocumented: {sorted(registered - documented)}\n"
        f"    documented but unregistered: {sorted(documented - registered)}"
    )


def test_block_type_list_matches_the_registry() -> None:
    registered = set(_BLOCK_REGISTRY)
    drift = [
        report
        for rel, text in _arch_docs()
        if (
            report := _drift_report(
                rel, "block-type", _enumerated_tags(text)[0], registered
            )
        )
        is not None
    ]
    assert not drift, "\n".join(drift)


def test_inline_token_list_matches_the_registry() -> None:
    registered = set(_INLINE_REGISTRY)
    drift = [
        report
        for rel, text in _arch_docs()
        if (
            report := _drift_report(
                rel, "inline-token", _enumerated_tags(text)[1], registered
            )
        )
        is not None
    ]
    assert not drift, "\n".join(drift)


def test_no_enumerated_tag_is_unregistered() -> None:
    """A tag inside a type enumeration that belongs to neither registry is
    either a node that was renamed out from under the document or a run the
    scanner misread as a type list — both worth a look."""
    stale = {
        rel: sorted(unknown)
        for rel, text in _arch_docs()
        if (unknown := _enumerated_tags(text)[2])
    }
    assert not stale, (
        f"type enumerations name tags no IR registry knows: {stale} — "
        f"renamed / removed node, or a slash-run that is not a type list "
        f"after all"
    )
