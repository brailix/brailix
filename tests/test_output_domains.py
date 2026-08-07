"""The product-domain manifest: two output domains, and who says so.

A renderer is not selected by the caller alone — the result object first checks
that the renderer's ``consumes`` matches the IR it is about to hand over, so the
set of output domains is a real, enforced partition of ``renderer_registry``,
not a way of speaking. This module pins that partition and the places a reader
learns it from.

The drift it exists to catch is quiet in a way a wrong sentence usually is not.
``pdf`` was registered as a fourth tactile renderer and reached through
``GraphicResult.render("pdf")``, while the ``Renderer`` protocol — the one
docstring a third-party implementer reads before writing one — still listed
three. Nothing failed: the protocol is structural, the registry is by name, and
a renderer omitted from a docstring works exactly as well as one included. The
cost lands on the next author, who writes to the list they were given.

What is checked is mostly what can be: which *names* appear, not what is said
about them, and only in the documents whose job is to describe the domains as
they are (the protocol, the architecture overviews, the extension guide). Design
notes written at an earlier stage describe an earlier stage, and holding those
to today's registry would make this test the reason not to write one.

**A whole-file token search cannot catch a document disagreeing with itself**,
and that is what it missed: the overview opened by naming two output domains
and closed with a summary that described one, compiling source documents into
braille in five moves with no figure, no raster and no second path anywhere in
it. Every token this file demanded was present — several sections earlier — so
the check passed while the last thing a reader read was two years out of date.
A maintainer who reads only the summary gets only the summary.

So the section-scoped checks below ask a narrower question of the two sections
whose whole job is to state the system as it is now: the summary
(``ARCHITECTURE#arch-summary``) and the testing strategy
(``ARCHITECTURE#arch-testing``). By anchor, not by section number or heading
text, because the copies are organised and numbered independently — the same
reason ``tests/test_architecture_anchors.py`` exists.

Still not a semantic check, and not pretending to be: it asks whether the
paragraph that must mention a thing mentions it. Whole-prose matching against
either copy would break on every honest rewording, which is how a guard becomes
the reason not to improve the document.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from brailix.core.errors import BrailixError
from brailix.core.protocols import Renderer
from brailix.pipeline._results import _BRAILLE_DOMAIN, _TACTILE_DOMAIN
from brailix.renderer import renderer_registry

_ROOT = Path(__file__).resolve().parents[1]

# The documents that describe the domains rather than merely use one. Globbed
# for the same reasons as the other document checks: the overview is maintained
# in more than one language, and a checkout may keep the extension guide at the
# top level or stage it a couple of directories down.
_DOC_GLOBS = ("ARCHITECTURE*.md", "docs/extending.md", "*/*/docs/extending.md")
_MIN_DOCS = 2

# The two product domains, and the output-domain IR each one carries. This pair
# IS the manifest: a third entry is a new product vertical, which is a
# coordinated change across the backend, the registry, a result type and every
# document below — so it should arrive by editing this list, not by discovering
# afterwards which of those were missed.
_DOMAIN_IR = {
    "braille": "BrailleDocument",
    "tactile_raster": "TactileRaster",
}


def _domain_partition() -> dict[str, set[str]]:
    """Registered renderer names, grouped by the domain each self-describes.

    A renderer whose loader fails (an optional dependency that isn't installed)
    is *not* skipped the way ``braille_renderer_names`` skips it: this is the
    roster check, so a renderer that stopped resolving should be visible here
    rather than silently shrink the set every assertion below is made against.
    """
    partition: dict[str, set[str]] = defaultdict(set)
    unresolvable: list[str] = []
    for name in renderer_registry.names():
        try:
            renderer = renderer_registry.get(name)
        except BrailixError as exc:  # pragma: no cover - all builtins are stdlib
            unresolvable.append(f"{name}: {exc}")
            continue
        partition[getattr(renderer, "consumes", _BRAILLE_DOMAIN)].add(name)
    assert not unresolvable, (
        "registered renderers that will not load, so the domain roster below "
        "is incomplete: " + "; ".join(unresolvable)
    )
    return dict(partition)


def _domain_docs() -> list[tuple[str, str]]:
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for glob in _DOC_GLOBS
        for path in sorted(_ROOT.glob(glob))
        if path.is_file()
    ]


# The architecture overviews only — the extension guide has no such sections,
# and demanding them of it would be demanding it be a different document.
_OVERVIEW_GLOB = "ARCHITECTURE*.md"


def _overviews() -> list[tuple[str, str]]:
    return [
        (path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(_ROOT.glob(_OVERVIEW_GLOB))
        if path.is_file()
    ]


def _section(text: str, anchor: str) -> str:
    """The body of the section ``anchor`` names, or ``""`` if it has none.

    An anchor sits on its own line immediately above the heading it names, so
    the section runs from that heading to the next one — which is what makes
    this work across copies that number and title their sections differently.

    Anything but the anchor's own closing tag between it and that heading means
    the convention has broken, and the honest answer is "not found": scanning
    on to the next heading would quietly assert against a *different* section,
    and the check would go on passing while measuring the wrong paragraph.
    """
    match = re.search(rf'<a\s+id="{re.escape(anchor)}"\s*>', text)
    if match is None:
        return ""
    rest = text[match.end() :]
    heading = re.search(r"^## .*$", rest, re.MULTILINE)
    if heading is None or rest[: heading.start()].strip() not in ("", "</a>"):
        return ""
    body = rest[heading.end() :]
    end = body.find("\n## ")
    return body if end < 0 else body[:end]


# The layers the library actually has, read off the package rather than listed
# here. A testing-strategy section is a map of the suite, and the drift it
# suffered was by omission: it described four layers for years after Input, the
# IR and the Renderer had grown test domains of their own, so the one document
# a contributor consults to find where a test belongs sent them to a tree that
# had moved on. Derived, so the next layer added has to be written into the
# section rather than quietly missing from it.
def _library_layers() -> set[str]:
    return {
        p.name
        for p in (_ROOT / "brailix").iterdir()
        if p.is_dir() and p.name != "__pycache__" and any(p.rglob("*.py"))
    }


def test_the_document_set_was_actually_found() -> None:
    """A glob that stopped matching would make every document check below pass
    on nothing at all."""
    found = _domain_docs()
    assert len(found) >= _MIN_DOCS, (
        f"expected at least {_MIN_DOCS} documents from {_DOC_GLOBS}, found "
        f"{[rel for rel, _ in found]} — the globs have gone stale"
    )


def test_the_output_domains_are_the_two_in_the_manifest() -> None:
    """Exactly two domains exist, and each has renderers.

    A third one appearing here is the signal to do the whole pass: the
    architecture overviews, the ``Renderer`` protocol, the extension guide and
    the result type that owns the new domain all describe the set as closed.
    """
    partition = _domain_partition()
    assert set(partition) == set(_DOMAIN_IR), (
        f"renderer domains are {sorted(partition)}, the manifest says "
        f"{sorted(_DOMAIN_IR)} — a new output domain is a documented, "
        f"coordinated change (see this module's docstring)"
    )
    empty = [domain for domain, names in partition.items() if not names]
    assert not empty, f"output domains with no renderer: {empty}"


def test_the_result_types_speak_the_manifest_vocabulary() -> None:
    """The domain strings the result objects check against are the same two
    strings the renderers declare. They are compared, never derived from each
    other, so a typo on either side turns every render of that domain into an
    ``IncompatibleRendererError`` — with both spellings looking right in
    isolation."""
    assert {_BRAILLE_DOMAIN, _TACTILE_DOMAIN} == set(_DOMAIN_IR)


def test_each_domains_ir_type_is_nameable_from_the_ir_facade() -> None:
    """Every output-domain IR is a published name: it is what a caller holds
    between the backend and the renderer, and what a renderer annotates."""
    import brailix.ir

    for domain, type_name in _DOMAIN_IR.items():
        assert type_name in brailix.ir.__all__, (
            f"the {domain} domain's IR type {type_name} is not exported from "
            f"brailix.ir"
        )
        assert isinstance(getattr(brailix.ir, type_name, None), type)


def test_the_renderer_protocol_names_every_registered_renderer() -> None:
    """The ``Renderer`` docstring is where an implementer meets both domains.

    It is the only place that says "these are the two kinds of IR a renderer can
    read, and here is who reads each" — so a renderer missing from its lists is
    a domain that looks smaller than it is. This is the check ``pdf`` failed.
    """
    doc = Renderer.__doc__ or ""
    assert doc, "Renderer lost its docstring (running under -OO?)"
    missing = {
        domain: sorted(name for name in names if f"``{name}``" not in doc)
        for domain, names in _domain_partition().items()
    }
    missing = {domain: names for domain, names in missing.items() if names}
    assert not missing, (
        f"registered renderers the Renderer protocol docstring never names: "
        f"{missing} — an implementer reads that list as the whole domain"
    )


# What each shipped renderer actually hands back. Written out per renderer
# rather than as "one of these types", because the loose form is what the
# documents had: "output-domain IR → bytes", said of a layer where ``unicode``
# returns a ``str``, ``cells`` a list or a dict, and ``tactile_preview`` a page
# of U+2800 characters. Three of eight, stated as all eight, in the sentence a
# reader meets the layer through — and nothing could contradict it, because the
# ``Renderer`` protocol's return type is deliberately ``Any`` and every one of
# these conformed.
#
# So this is the executable half of that sentence. It is not a claim that these
# types can never change: it is a claim that changing one is a change to the
# published output contract, which lands here and in the documents together.
_RENDERER_OUTPUT: dict[str, tuple[type, ...]] = {
    "unicode": (str,),
    "brf": (bytes,),
    # JSON-serialisable by design: a flat cell list for a sequence, a document
    # object for a document.
    "cells": (list, dict),
    # ``str`` unless an encoding was configured, hence both.
    "layout": (str, bytes),
    "bmp": (bytes,),
    "png": (bytes,),
    "pdf": (bytes,),
    # A U+2800 page for a refreshable display — text, not an image file.
    "tactile_preview": (str,),
}


def _sample_ir(domain: str) -> object:
    """The smallest IR of ``domain`` a renderer can be handed."""
    from brailix.ir.braille import BrailleBlock, BrailleCell, BrailleDocument
    from brailix.ir.tactile import TactileRaster

    if domain == _TACTILE_DOMAIN:
        return TactileRaster(
            width=4, height=4, dpi=20.0, page_width_mm=5.0, page_height_mm=5.0
        )
    return BrailleDocument(
        metadata={},
        blocks=[
            BrailleBlock(
                block_type="paragraph",
                cells=[BrailleCell(dots=frozenset({1}), source_span=None)],
            )
        ],
    )


def test_every_renderer_produces_what_the_output_contract_says() -> None:
    """The layer is a *dumb encoder*, not a byte encoder.

    Rendered for real, because the type is the whole claim and a docstring
    saying ``bytes`` renders exactly as well as one saying ``str``. The
    manifest above is compared as an exact set too: a new renderer arrives with
    its output category written down, rather than being discovered later by
    whoever assumed the layer emits bytes.
    """
    partition = _domain_partition()
    registered = {name for names in partition.values() for name in names}
    assert registered == set(_RENDERER_OUTPUT), (
        f"renderers with no declared output category: "
        f"{sorted(registered - set(_RENDERER_OUTPUT))}; categories declared "
        f"for renderers that no longer exist: "
        f"{sorted(set(_RENDERER_OUTPUT) - registered)}"
    )

    wrong: list[str] = []
    for domain, names in partition.items():
        ir = _sample_ir(domain)
        for name in sorted(names):
            produced = renderer_registry.get(name).render(ir)
            expected = _RENDERER_OUTPUT[name]
            if not isinstance(produced, expected):
                wrong.append(
                    f"{name} returned {type(produced).__name__}, the contract "
                    f"says {'/'.join(t.__name__ for t in expected)}"
                )
    assert not wrong, (
        "renderers whose output type no longer matches the published "
        "contract:\n  " + "\n  ".join(wrong)
    )


def test_the_renderers_do_not_all_produce_bytes() -> None:
    """The sentence this pins, stated as the fact it stands on.

    Reads the manifest rather than rendering again: what it guards is a future
    edit *to the manifest* that quietly makes every entry ``bytes`` — at which
    point the check above would pass on a claim the documents are once more
    entitled to make, and nobody would have looked at the documents.
    """
    categories = {t for types in _RENDERER_OUTPUT.values() for t in types}
    assert categories - {bytes}, (
        "every renderer now produces bytes — if that is really true, the "
        "architecture overviews and docs/index.md may go back to saying so"
    )


def test_the_documents_describe_both_output_domains() -> None:
    """Each document that describes the architecture names both domains.

    Four tokens, because each carries a different half of the contract and each
    has drifted on its own: the two IR type names (what a renderer receives),
    ``consumes`` (that a renderer must say which), and the literal
    ``tactile_raster`` (the value it says it with — the default is what a
    braille renderer gets for free, so this is the one an author has to copy).
    """
    required = (*_DOMAIN_IR.values(), "consumes", "tactile_raster")
    gaps = {
        rel: [token for token in required if token not in text]
        for rel, text in _domain_docs()
    }
    gaps = {rel: tokens for rel, tokens in gaps.items() if tokens}
    assert not gaps, (
        f"documents describing the pipeline without the vocabulary of its "
        f"second output domain: {gaps}"
    )


# ---------------------------------------------------------------------------
# The same question, asked of the sections that have to answer it
# ---------------------------------------------------------------------------


def test_the_scoped_sections_exist_in_every_overview() -> None:
    """Both anchors resolve in every copy, or the two checks below assert
    against an empty string and pass on nothing — which is the failure this
    whole file is about, one level down."""
    missing = [
        f"{rel}: {anchor}"
        for rel, text in _overviews()
        for anchor in ("arch-summary", "arch-testing")
        if not _section(text, anchor).strip()
    ]
    assert not missing, (
        "architecture sections the scoped checks cannot find (the anchor is "
        f"gone, or its section is empty): {missing}"
    )


def test_the_summary_carries_both_output_domains() -> None:
    """The last section a reader reads must describe the system they have.

    Both IR type names, because naming one of them is how the summary read
    while the product had two: a five-step pipeline ending in braille, with the
    tactile path — a second backend, a second IR, four renderers and mixed
    pages — absent from the one paragraph most likely to be read alone.
    """
    required = (*_DOMAIN_IR.values(), "tactile")
    gaps = {
        rel: [token for token in required if token not in summary]
        for rel, text in _overviews()
        if (summary := _section(text, "arch-summary"))
    }
    gaps = {rel: tokens for rel, tokens in gaps.items() if tokens}
    assert not gaps, (
        f"the architecture summary describes fewer output domains than the "
        f"product has: {gaps} — a reader who reads only the summary gets only "
        f"the summary, so both compilation paths belong in it"
    )


def test_the_testing_strategy_names_every_layer_and_both_domains() -> None:
    """The suite's map must show the whole tree.

    Layers derived from the package, so this extends itself; both domain names
    required outright, because a layer can be listed while only half of what it
    holds is described — the backend row said "IR to BrailleIR" for as long as
    the tactile backend had existed beside it.

    Layer names match on a word boundary and domain names as plain substrings,
    and the difference is not fussiness: ``ir`` needs the boundary or it
    matches inside "their", while ``tactile_raster`` needs to go without one,
    since the word it is looking for is spelled with an underscore and sits
    inside prose written in a language that has no word boundaries to speak of.
    """
    layers = sorted(_library_layers())
    gaps = {}
    for rel, text in _overviews():
        section = _section(text, "arch-testing")
        if not section:
            continue
        absent = [
            name
            for name in layers
            if not re.search(rf"\b{re.escape(name)}\b", section, re.IGNORECASE)
        ] + [domain for domain in sorted(_DOMAIN_IR) if domain not in section]
        if absent:
            gaps[rel] = absent
    assert not gaps, (
        f"the testing-strategy section never mentions {gaps} — it is the map a "
        f"contributor uses to find where a test belongs, so a layer missing "
        f"from it is a layer they will not think to test"
    )


class TestTheSectionScopedChecksBite:
    """The two checks above pass on the documents as they stand — which is
    either because the documents are current or because the checks measure
    nothing, and those look identical from the outside.

    So the sections *as they were when the drift was found* are replayed here
    and asserted reported. This is the same reasoning that made the two checks
    necessary in the first place: a whole-file token search also passed, right
    up until someone read the summary.
    """

    _OLD_SUMMARY = (
        '<a id="arch-summary"></a>\n'
        "## 15. Summary\n\n"
        "`brailix` compiles a source document into braille in five moves: the "
        "frontend recognizes and structures the input; the IR holds that "
        "meaning in a unified form; the backend applies profile-driven braille "
        "rules; BrailleIR records the result as a traceable cell sequence; and "
        "the renderer encodes it as Unicode, BRF, or a laid-out page.\n\n"
        "## 16. Next\n"
    )
    _OLD_TESTING = (
        '<a id="arch-testing"></a>\n'
        "## 13. Testing strategy\n\n"
        "Four layers, each runnable on its own.\n\n"
        "| Layer | What it tests | Independent of |\n"
        "|---|---|---|\n"
        "| Frontend | type recognition, segmentation, pinyin | the Backend |\n"
        "| MathParser | LaTeX to MathML equivalence | the Backend |\n"
        "| Backend | fixed IR to fixed BrailleIR | segmentation models |\n"
        "| Pipeline | end-to-end golden tests | human-proofread samples |\n\n"
        "## 14. Next\n"
    )

    def test_the_old_summary_is_reported(self) -> None:
        summary = _section(self._OLD_SUMMARY, "arch-summary")
        assert summary, "the extractor found no section to judge"
        missing = [t for t in _DOMAIN_IR.values() if t not in summary]
        assert missing == sorted(_DOMAIN_IR.values()), (
            "the single-domain summary passed the check written to catch it"
        )

    def test_the_old_testing_strategy_is_reported(self) -> None:
        section = _section(self._OLD_TESTING, "arch-testing")
        assert section, "the extractor found no section to judge"
        absent = {
            name
            for name in _library_layers()
            if not re.search(rf"\b{re.escape(name)}\b", section, re.IGNORECASE)
        }
        # Layers the four-layer table never mentioned at all.
        assert {"core", "input", "renderer"} <= absent
        # And the limit of a token check, written down rather than assumed
        # away: ``ir`` is NOT reported, because the old Backend row read "fixed
        # IR to fixed BrailleIR" — the word was there, the row was not. This
        # catches a layer nobody named; it cannot catch one named in passing.
        # Reading "does this section have a row per layer?" would mean parsing
        # a table that one copy writes differently from the other.
        assert "ir" not in absent

    def test_the_old_testing_strategy_is_reported_missing_both_domains(self) -> None:
        section = _section(self._OLD_TESTING, "arch-testing")
        assert [d for d in sorted(_DOMAIN_IR) if d not in section] == sorted(
            _DOMAIN_IR
        ), "the single-domain testing strategy passed the domain check"

    def test_the_extractor_stops_at_the_next_heading(self) -> None:
        """Otherwise a check "scoped" to the summary reads the rest of the
        document, and every token it wants is somewhere further down — which is
        the whole-file search it was written to replace."""
        assert "## 16." not in _section(self._OLD_SUMMARY, "arch-summary")
        assert "Next" not in _section(self._OLD_TESTING, "arch-testing")

    def test_an_anchor_that_names_no_heading_is_not_found(self) -> None:
        """A stray anchor must not silently annex the next section: the checks
        would then hold one section to another section's contract, and report
        on a paragraph nobody chose."""
        assert not _section(
            '<a id="arch-summary"></a>\n\nloose prose\n\n## 9. Something else\n',
            "arch-summary",
        )

    def test_a_missing_anchor_is_not_found(self) -> None:
        assert not _section("## 15. Summary\n\nno anchor here\n", "arch-summary")
