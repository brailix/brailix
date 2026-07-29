"""Code cites the architecture document by stable anchor, not by section number.

Section numbers were never a working reference. The canonical ``ARCHITECTURE.md``
is Chinese and the public mirror ships the independently-organised English
rewrite as its ``ARCHITECTURE.md``, and the two number their sections
differently — "§12" is 不变的边界 in one and "Adding a language" in the other.
So every ``ARCHITECTURE §N`` in the tree was right in at most one copy, and
wrong in the copy a third party actually reads. It got worse than that:
``§7.6`` was cited from twelve places (and from the Chinese document itself)
while no section by that number existed in either copy.

The fix is one name per invariant, declared as an ``<a id="...">`` above the
section it names in **both** copies, and cited from code as
``ARCHITECTURE#arch-boundaries`` — a working link, and a string that can be
searched. Renumbering or reordering sections is then free; only moving an
invariant means moving its anchor.

Two checks:

* every anchor cited from code is declared in every architecture document
  present — so adding a citation without an anchor, or dropping an anchor a
  citation depends on, fails here rather than in a reader's browser;
* no ``ARCHITECTURE §N`` citation comes back. Bare ``§N`` is untouched and out
  of scope: most of them cite BANA's braille-music rules, RFC 8032, or a plan
  document under ``docs/``, and those numbers are stable in a way these were
  not.

Path-probed, like ``test_extension_docs.py``: this guard ships to the public
mirror, where only the English copy exists and is named ``ARCHITECTURE.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_ARCH_DOCS = ("ARCHITECTURE.md", "ARCHITECTURE.en.md")
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
# ``ARCHITECTURE.md`` and the other design notes are cited
# by section number *deliberately* — they are single documents whose numbering
# is stable, unlike the two independently-organised architecture copies — and
# several of them sit a line or two away from an ARCHITECTURE mention.
_OTHER_DOC = r"[\w/-]+\.md|[\w-]+-plan\b|BANA|RFC"
_SECTION_NUMBER = re.compile(
    # ARCHITECTURE ... §N
    r"ARCHITECTURE(?!\.en)(?:\.md)?(?!#)(?:(?!" + _OTHER_DOC + r")[\s\S]){0,80}?§\s*\d"
    # §N ... ARCHITECTURE
    r"|§\s*\d[\d.]*(?:(?!" + _OTHER_DOC + r")[\s\S]){0,80}?ARCHITECTURE(?!\.en)(?!#)",
)


def _docs() -> list[tuple[str, str]]:
    return [
        (name, (_ROOT / name).read_text(encoding="utf-8"))
        for name in _ARCH_DOCS
        if (_ROOT / name).is_file()
    ]


_SELF = Path(__file__).resolve()


def _python_files() -> list[Path]:
    return [
        py
        for d in _CODE_DIRS
        for py in sorted((_ROOT / d).rglob("*.py"))
        if "__pycache__" not in py.parts
        # This file has to spell out the forbidden forms to explain and to test
        # them — every example below would otherwise be reported as a
        # violation of the rule it documents.
        and py != _SELF
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
    assert _docs(), f"no architecture document found among {_ARCH_DOCS}"


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


def test_both_documents_declare_the_same_anchor_set() -> None:
    """Beyond what code happens to cite: the two copies must stay a translation
    of one another at the anchor level, or the next citation added against one
    copy silently dangles in the other."""
    docs = _docs()
    if len(docs) < 2:  # the public mirror ships one copy — nothing to compare
        return
    (name_a, text_a), (name_b, text_b) = docs[0], docs[1]
    a, b = set(_DECLARED.findall(text_a)), set(_DECLARED.findall(text_b))
    assert a == b, (
        f"anchor sets differ — only in {name_a}: {sorted(a - b)}; "
        f"only in {name_b}: {sorted(b - a)}"
    )


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

        The examples deliberately avoid naming an unpublished ``docs/*-plan.md``
        note. The export rewrites those references to ``ARCHITECTURE.md`` for
        the mirror — including ones written inside a test — so using one here
        would make this file mean something different on each side.
        ``docs/extending.md`` ships, and BANA / RFC are untouched either way.
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
