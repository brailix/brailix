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
# The spelling this guard exists to keep out.
_SECTION_NUMBER = re.compile(r"ARCHITECTURE(?:\.md)?`{0,2}[ ]*§[ ]*\d")


def _docs() -> list[tuple[str, str]]:
    return [
        (name, (_ROOT / name).read_text(encoding="utf-8"))
        for name in _ARCH_DOCS
        if (_ROOT / name).is_file()
    ]


def _python_files() -> list[Path]:
    return [
        py
        for d in _CODE_DIRS
        for py in sorted((_ROOT / d).rglob("*.py"))
        if "__pycache__" not in py.parts
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


def test_no_code_cites_a_section_number() -> None:
    """The regression: a section number is not a reference that survives two
    independently-organised copies of the document."""
    offenders = [
        f"{py.relative_to(_ROOT).as_posix()}:{lineno}: {line.strip()[:80]}"
        for py in _python_files()
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1)
        if _SECTION_NUMBER.search(line)
    ]
    assert not offenders, (
        "ARCHITECTURE cited by section number — the Chinese and English copies "
        "number sections differently, so this is wrong in at least one of them. "
        "Cite the stable anchor instead (ARCHITECTURE#arch-…):\n"
        + "\n".join(offenders)
    )
