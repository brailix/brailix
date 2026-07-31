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

What is checked is only what can be: which *names* appear, not what is said
about them, and only in the documents whose job is to describe the domains as
they are (the protocol, the architecture overviews, the extension guide). Design
notes written at an earlier stage describe an earlier stage, and holding those
to today's registry would make this test the reason not to write one.
"""

from __future__ import annotations

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
