"""Code cites the architecture document by stable anchor, not by section number.

Section numbers were never a working reference. The overview is maintained in
more than one language, each organised on its own terms and numbering its
sections differently — the section that is "§12" in one is a different section
under that number in another. So every ``ARCHITECTURE §N`` in the tree was
right in at most one copy, and wrong in the copy a given reader opens. It got
worse than that: ``§7.6`` was cited from twelve places while no section by that
number existed in any copy.

The fix is one name per invariant, declared as an ``<a id="...">`` above the
section it names in **every** copy, and cited from code as
``ARCHITECTURE#arch-boundaries`` — a working link, and a string that can be
searched. Renumbering or reordering sections is then free; only moving an
invariant means moving its anchor.

Two checks:

* every anchor cited from code is declared in every architecture document
  present — so adding a citation without an anchor, or dropping an anchor a
  citation depends on, fails here rather than in a reader's browser;
* no ``ARCHITECTURE §N`` citation comes back. Bare ``§N`` is untouched and out
  of scope: most of them cite BANA's braille-music rules, RFC 8032, or a design
  note, and those numbers are stable in a way these were not.

The documents are globbed rather than listed: which copies a checkout carries
varies, and every one of them present is held to the same anchor set.

A file that has to spell the forbidden citation forms out — because it *tests*
the detector, so its examples would each be reported as a violation of the rule
they document — declares itself exempt with the marker in
:data:`_QUOTES_THE_FORMS_AS_DATA`. A marker rather than a list of filenames:
the exemption then travels with the file that needs it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

_ARCH_DOC_GLOB = "ARCHITECTURE*.md"
_CODE_DIRS = ("brailix", "tests")

# ``<a id="arch-layers"></a>`` — the anchor declaration in a document.
_DECLARED = re.compile(r'<a\s+id="(arch-[a-z0-9-]+)"\s*>')
# ``ARCHITECTURE#arch-layers`` and a chained ``/ #arch-boundaries``.
_CITED = re.compile(r"ARCHITECTURE#(arch-[a-z0-9-]+)|(?<![\w#])#(arch-[a-z0-9-]+)")
# The spelling this guard exists to keep out — in either order and across a
# line wrap, since a comment reflows and "§14 of ARCHITECTURE.md" is at least
# as natural to write as "ARCHITECTURE.md §14". Matching only the forward,
# single-line form meant the guard's promise held for one phrasing out of
# several.
#
# The window between the two halves is bounded and must not contain another
# document's name. That is what keeps the legitimate citations out of it:
# the design notes under ``docs/`` are cited by section number *deliberately*
# — they are single documents whose numbering is stable, unlike the
# independently-organised architecture copies — and several of them sit a line
# or two away from an ARCHITECTURE mention.
#
# ``_LANGUAGE_SUFFIXED`` spares the *filename* of one particular copy
# (``ARCHITECTURE.xx.md``): naming a file is not citing the invariant, and a
# copy's own name is how one copy points at another.
_OTHER_DOC = r"[\w/-]+\.md|[\w-]+-plan\b|BANA|RFC"
_LANGUAGE_SUFFIXED = r"(?!\.[a-z]{2}\.)"
_SECTION_NUMBER = re.compile(
    # ARCHITECTURE ... §N
    r"ARCHITECTURE" + _LANGUAGE_SUFFIXED
    + r"(?:\.md)?(?!#)(?:(?!" + _OTHER_DOC + r")[\s\S]){0,80}?§\s*\d"
    # §N ... ARCHITECTURE
    r"|§\s*\d[\d.]*(?:(?!" + _OTHER_DOC + r")[\s\S]){0,80}?ARCHITECTURE"
    + _LANGUAGE_SUFFIXED + r"(?!#)",
)

# A *citation* — code pointing at the document to justify what it does — as
# opposed to naming the file in prose ("``ARCHITECTURE.md`` explains the
# design"). Replacing section numbers with anchors left the citations that
# carried no locator at all untouched, and those are the same problem one step
# further along: "(ARCHITECTURE.md, adapter pattern)" sends a reader to a
# thousand-line document to find which paragraph is meant, and nothing fails
# when the paragraph moves. The two spellings that mark a citation are a
# parenthesis and a "see", which is how every one of them in the tree was
# written; a bare mention in running prose is left alone deliberately.
#
# The trailing lookahead spares an enumeration of *files* — "(ARCHITECTURE.md +
# docs/extending.md)" lists paths as data, it does not cite one as an
# authority. Cost: a genuine citation that happens to sit on a line naming
# another document goes unreported, the same trade the section-number detector
# makes with ``_OTHER_DOC``.
_UNANCHORED_CITATION = re.compile(
    r"(?:\(|\b[Ss]ee\s+)`{0,2}ARCHITECTURE" + _LANGUAGE_SUFFIXED
    + r"(?:\.md)?`{0,2}(?!#)"
    r"(?![^)\n]{0,60}\.md)"
)


def _docs() -> list[tuple[str, str]]:
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(_ROOT.glob(_ARCH_DOC_GLOB))
        if path.is_file()
    ]


# The self-declared exemption for a file whose *fixtures* are the forbidden
# citation forms: a detector cannot be tested without writing down what it
# detects, and every example would otherwise be reported as a violation of the
# rule it demonstrates. Written as a marker the exempt file carries rather than
# a list of filenames kept here, so the exemption travels with the file — a
# list has to be edited from a distance by whoever adds or renames such a test,
# and is silently wrong until someone notices the guard went quiet.
_QUOTES_THE_FORMS_AS_DATA = "architecture-citation-forms: quoted as data"


def _python_files() -> list[Path]:
    return [
        py
        for d in _CODE_DIRS
        for py in sorted((_ROOT / d).rglob("*.py"))
        if "__pycache__" not in py.parts
        and _QUOTES_THE_FORMS_AS_DATA not in py.read_text(encoding="utf-8")
    ]


def _citations() -> dict[str, list[str]]:
    """anchor -> the ``file:line`` sites citing it."""
    out: dict[str, list[str]] = {}
    for py in _python_files():
        for lineno, line in enumerate(
            py.read_text(encoding="utf-8").splitlines(), 1
        ):
            for m in _CITED.finditer(line):
                anchor = m.group(1) or m.group(2)
                out.setdefault(anchor, []).append(
                    f"{py.relative_to(_ROOT).as_posix()}:{lineno}"
                )
    return out


def test_the_architecture_documents_were_found() -> None:
    # A rename would make every check below vacuous.
    assert _docs(), f"no architecture document matched {_ARCH_DOC_GLOB!r}"


def test_there_are_citations_to_check() -> None:
    cited = _citations()
    assert len(cited) >= 5, (
        f"only {len(cited)} distinct anchors cited — the citation regex has "
        f"stopped matching the convention"
    )


def test_every_cited_anchor_is_declared_in_every_document() -> None:
    """An anchor cited by code but missing from one copy is the original bug in
    miniature: the citation resolves for whoever reads that copy and dangles for
    everyone reading the other."""
    cited = _citations()
    missing: list[str] = []
    for name, text in _docs():
        declared = set(_DECLARED.findall(text))
        for anchor, sites in sorted(cited.items()):
            if anchor not in declared:
                missing.append(f"{name} declares no {anchor!r} (cited from {sites[0]})")
    assert not missing, (
        "architecture anchors cited from code but not declared:\n"
        + "\n".join(missing)
        + '\n\nAdd `<a id="…"></a>` above the section in EVERY architecture '
        "document — the anchor is the one name both copies share."
    )


def test_every_document_declares_the_same_anchor_set() -> None:
    """Beyond what code happens to cite: the copies present must stay a
    translation of one another at the anchor level, or the next citation added
    against one copy silently dangles in the other.

    Every copy against the first, not the first two against each other: a
    comparison that stops at ``docs[1]`` leaves a third copy unchecked, which is
    the same hole one level along.

    A checkout carrying a single copy has nothing to compare, and that is a
    legitimate shape — which copies ship varies. It **skips** rather than
    returning green, because a passing test named for a comparison that never
    ran is how "the copies agree" comes to read as verified: the report should
    say the check did not apply.
    """
    docs = _docs()
    assert docs, f"no architecture document matched {_ARCH_DOC_GLOB!r}"
    if len(docs) < 2:
        pytest.skip(
            f"one architecture document present ({docs[0][0]}) — no second "
            f"anchor set to compare it against"
        )
    (base_name, base_text), *others = docs
    base = set(_DECLARED.findall(base_text))
    differ = [
        f"only in {base_name}: {sorted(base - other)}; "
        f"only in {name}: {sorted(other - base)}"
        for name, text in others
        if (other := set(_DECLARED.findall(text))) != base
    ]
    assert not differ, "anchor sets differ — " + "; ".join(differ)


class TestTheSectionNumberDetector:
    """What the detector must catch, and what it must leave alone.

    It matched only ``ARCHITECTURE ... §N``, forward and on one line — so the
    rule held for one phrasing out of several. A reflowed docstring or a
    reversed clause slipped through while the guard reported success.

    The other half matters as much: design notes under ``docs/`` **are** cited
    by section number on purpose. They are single documents with stable
    numbering, unlike the two independently-organised architecture copies, and
    several sit within a line of an ARCHITECTURE mention. A detector that
    flagged those would be reverted within a day.
    """

    def test_catches_the_forward_form(self) -> None:
        assert _SECTION_NUMBER.search("see ARCHITECTURE.md §14 for the rule")

    def test_catches_the_reverse_form(self) -> None:
        assert _SECTION_NUMBER.search("see §14 of ARCHITECTURE.md")

    def test_catches_a_reference_split_across_lines(self) -> None:
        assert _SECTION_NUMBER.search(
            "the component responsibilities in ARCHITECTURE.md\n    §14 apply"
        )

    def test_accepts_the_anchor_form(self) -> None:
        assert not _SECTION_NUMBER.search(
            "the layering rule (ARCHITECTURE#arch-layers) applies here"
        )

    def test_leaves_other_documents_citations_alone(self) -> None:
        """Section numbers that belong to some *other* document are fine.

        The examples deliberately cite a *published* document. A design note
        that some checkouts do not carry would make the fixture mean one thing
        here and another there, and a fixture is supposed to mean one thing.
        ``docs/extending.md`` is published everywhere, and BANA / RFC are
        untouched either way.
        """
        for legitimate in (
            "the adapter contract in ``docs/extending.md``\n    §2 explains it",
            "see §2 of ``docs/extending.md`` for the walkthrough",
            "bar-over-bar layout (BANA §28) splits on it",
            "RFC 8032 §7.1 published test vectors",
            "single_line format (BANA §24.1) for one melodic part",
        ):
            assert not _SECTION_NUMBER.search(legitimate), (
                f"flagged another document's citation: {legitimate!r}"
            )

    def test_does_not_reach_across_an_intervening_document(self) -> None:
        """The window stops at another document's name, so an ARCHITECTURE
        mention and an unrelated note's section number nearby stay separate."""
        assert not _SECTION_NUMBER.search(
            "layering lives in ARCHITECTURE#arch-layers; the geometry "
            "is in ``docs/extending.md`` §2"
        )


class TestTheUnanchoredCitationDetector:
    """What counts as a citation, and what is just naming the file."""

    def test_catches_a_parenthesised_citation(self) -> None:
        assert _UNANCHORED_CITATION.search(
            "one more entry, no new branch (ARCHITECTURE.md, adapter pattern)"
        )

    def test_catches_a_see_citation(self) -> None:
        assert _UNANCHORED_CITATION.search("layering rules: see ARCHITECTURE.md")

    def test_catches_the_backticked_form(self) -> None:
        assert _UNANCHORED_CITATION.search("(see ``ARCHITECTURE.md``)")

    def test_accepts_an_anchored_citation(self) -> None:
        assert not _UNANCHORED_CITATION.search(
            "one direction only (ARCHITECTURE#arch-layers)"
        )
        assert not _UNANCHORED_CITATION.search("see ``ARCHITECTURE#arch-adapters``")

    def test_leaves_a_prose_mention_alone(self) -> None:
        """Naming the document as a whole is not a citation — there is no
        paragraph to point at, so there is no anchor to demand."""
        for prose in (
            "``ARCHITECTURE.md`` explains the design; the guide is the how-to",
            "the export collapses an unpublished note to ARCHITECTURE.md and "
            "takes the section number with it",
        ):
            assert not _UNANCHORED_CITATION.search(prose), prose

    def test_leaves_a_list_of_document_paths_alone(self) -> None:
        assert not _UNANCHORED_CITATION.search(
            "the published set is two documents (ARCHITECTURE.md + "
            "docs/extending.md)"
        )

    def test_leaves_another_copys_filename_alone(self) -> None:
        # One copy naming another by filename points at a translation; there is
        # no paragraph being meant, so there is no anchor to demand.
        assert not _UNANCHORED_CITATION.search("(ARCHITECTURE.fr.md)")
        assert not _UNANCHORED_CITATION.search("see ARCHITECTURE.ja.md")


def test_no_code_cites_the_document_without_an_anchor() -> None:
    """The other half of the same rule the section numbers taught.

    An anchor is a locator that survives every copy being reorganised. A
    citation with *no* locator survives it too — by pointing at nothing in
    particular, which is worse: the reader has the whole document to search and
    no test notices when the paragraph being cited is gone.

    Unconditional, in every checkout. It was once skipped where only one
    architecture copy was present, and a skip written that way also skips every
    citation a contributor to *that* checkout writes — the ones whose pull
    request this suite is meant to be checking.
    """
    offenders: list[str] = []
    for py in _python_files():
        text = py.read_text(encoding="utf-8")
        for match in _UNANCHORED_CITATION.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{py.relative_to(_ROOT).as_posix()}:{lineno}: "
                f"{' '.join(match.group(0).split())}"
            )
    assert not offenders, (
        "ARCHITECTURE cited without an anchor — name the invariant so the "
        "citation survives a reorganisation and so a moved anchor fails a "
        "test (ARCHITECTURE#arch-…):\n" + "\n".join(offenders)
    )


def test_no_code_cites_a_section_number() -> None:
    """The regression: a section number is not a reference that survives two
    independently-organised copies of the document.

    Scanned over the **whole file**, not line by line: a docstring wraps, and
    ``ARCHITECTURE.md`` can easily end a line with its ``§3.1`` starting the
    next. A per-line search reads that as two innocent fragments.
    """
    offenders: list[str] = []
    for py in _python_files():
        text = py.read_text(encoding="utf-8")
        for match in _SECTION_NUMBER.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            excerpt = " ".join(match.group(0).split())[:80]
            offenders.append(
                f"{py.relative_to(_ROOT).as_posix()}:{lineno}: {excerpt}"
            )
    assert not offenders, (
        "ARCHITECTURE cited by section number — the Chinese and English copies "
        "number sections differently, so this is wrong in at least one of them. "
        "Cite the stable anchor instead (ARCHITECTURE#arch-…):\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# The directory tree in the document describes the repo that exists
# ---------------------------------------------------------------------------
#
# Both copies introduce their tree with a promise — "文件名以仓库实际为准" /
# "File names below follow what is actually in the repo" — and the Chinese one
# goes further, listing by name the entries an older draft carried that never
# existed (``ir/schema.py``, ``frontend/punctuation.py``, ...). That is a
# promise nothing was keeping: ``core/paths.py`` landed and the tree did not
# move, so the one place a maintainer looks up "where does path safety live?"
# answered "nowhere".
#
# Two directions, and only one of them can be checked everywhere. That every
# named file *exists* holds for the whole tree, which is why it is checked over
# all of it. The converse — that every file is *named* — is true only of the
# parts the tree enumerates exhaustively; it deliberately summarises adapter
# folders as ``adapters/  # latex / mathml / omml / ...``. ``core/`` is
# enumerated, and is where the drift happened, so that is where the reverse
# check is pinned.

# ``│   ├── name.py    # comment`` → indent, then the entry, then a comment.
_TREE_ENTRY = re.compile(r"^(?P<indent>(?:[│ ]   )*)(?:├──|└──) (?P<rest>.+)$")
_TREE_EXHAUSTIVE = ("brailix/core",)


def _tree_paths(text: str) -> dict[str, str]:
    """Every ``*.py`` the document's tree names → the line it was named on.

    Paths are rebuilt from the box-drawing indentation, so a file is checked
    where the tree says it is rather than merely somewhere in the repo. Two
    spellings the tree uses need handling: a trailing ``/`` marks a directory,
    and ``a.py / b.py`` names two siblings on one line.
    """
    stack: list[str] = []
    found: dict[str, str] = {}
    for line in text.split("\n"):
        match = _TREE_ENTRY.match(line)
        if match is None:
            continue
        depth = len(match.group("indent")) // 4
        entry = match.group("rest").split("#")[0].strip()
        del stack[depth:]
        if entry.endswith("/"):
            stack.append(entry.rstrip("/"))
            continue
        for name in (n.strip() for n in entry.split(" / ")):
            if name.endswith(".py"):
                found["/".join([*stack, name])] = line.strip()
    return found


def test_every_file_the_tree_names_exists() -> None:
    """A renamed or deleted module must move the tree with it."""
    missing: list[str] = []
    for doc, text in _docs():
        for path, line in _tree_paths(text).items():
            if not (_ROOT / path).exists():
                missing.append(f"{doc}: {path}   (from: {line})")
    assert not missing, (
        "the architecture tree names files that are not in the repo — it "
        "promises to follow what is actually there:\n" + "\n".join(missing)
    )


def test_the_exhaustively_listed_directories_are_complete() -> None:
    """And the other direction, where the tree claims to be complete.

    ``core/`` lists every module it contains, which is what makes it usable as
    a map. A new one that isn't added here is invisible exactly to the reader
    who went looking for it.
    """
    unlisted: list[str] = []
    for doc, text in _docs():
        named = set(_tree_paths(text))
        for directory in _TREE_EXHAUSTIVE:
            for py in sorted((_ROOT / directory).glob("*.py")):
                if py.name == "__init__.py":
                    continue
                path = f"{directory}/{py.name}"
                if path not in named:
                    unlisted.append(f"{doc}: {path}")
    assert not unlisted, (
        "modules missing from a part of the architecture tree that lists its "
        "directory exhaustively — add the entry, or (if the directory has "
        "outgrown a full listing) summarise it and drop it from "
        "_TREE_EXHAUSTIVE:\n" + "\n".join(unlisted)
    )
