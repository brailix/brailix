"""The stable public import surface of ``brailix``.

The library exposes a shallow facade — the top-level package plus the
``brailix.ir`` / ``brailix.core`` / ``brailix.core.models`` /
``brailix.renderer`` sub-packages, and the ``brailix.input`` /
``brailix.frontend`` entry points — so downstream callers (a proofreading
front-end, CLI front-ends, ...) import from there rather than reaching into
concrete internal modules (``brailix.ir.inline``, ``brailix.core.span``, ...).

:data:`_FACADE` is the single manifest of that surface, and the check is
**exact set equality**, in both directions:

* a name that goes missing (a refactor drops or renames a re-export) fails
  here instead of silently breaking every downstream import site;
* a name that appears *without* being added here fails too. That direction
  is the one a presence-only check can't give: ``__all__`` is a promise of
  support, and something that lands in it by accident — a private helper
  pulled up for convenience, a test-only reset — becomes a compatibility
  burden the moment a third party runs ``import *`` or generates an API
  listing from it. Deciding to publish a name should be a deliberate edit
  of this list.

The published API reference is *generated* from these modules' docstrings
(pdoc honours ``__all__``), so there is no hand-written page to keep in
step: this manifest decides what the reference contains.

:data:`_EXTENSION_SURFACE` further down is the second, narrower promise: what
an adapter author imports (the Protocols and the per-subsystem registries).
Those names sit deeper than any facade and have no ``__all__`` of their own, so
they need a manifest of their own — kept separate because an adapter author and
an integrator are different audiences whose surfaces should move independently.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_FACADE: dict[str, list[str]] = {
    "brailix": [
        "Pipeline",
        "translate_graphic",
        "TranslationResult",
        "GraphicResult",
        "TactilePageResult",
        "CompiledBlock",
        "TreeSubcache",
        "block_hash",
        # The tactile vertical's one configuration type, beside the graphics
        # entry point that takes it: ``translate_graphic`` documents an
        # already-loaded profile as a legal argument, so the type and its
        # loader must be reachable without importing
        # ``brailix.backend.tactile.profile`` — a path the policy calls
        # internal and free to move.
        "TactileProfile",
        "load_tactile_profile",
        "list_tactile_profiles",
        "InputLimits",
        "InputTooLargeError",
        "DEFAULT_INPUT_LIMITS",
    ],
    "brailix.ir": [
        # braille (the layout control sentinels are backend↔renderer wire
        # protocol, not data model — they stay in brailix.ir.braille)
        "BLANK_CELL",
        "BrailleBlock",
        "BrailleCell",
        "BrailleDocument",
        "BrailleSequence",
        # document (block-level)
        "Block",
        "CodeBlock",
        "DocumentIR",
        "Footnote",
        "GraphicBlock",
        "Heading",
        "ImageAlt",
        "List",
        "ListItem",
        "MathBlock",
        "MusicBlock",
        "Paragraph",
        "Quote",
        "ScoreBlock",
        "Table",
        "TableCell",
        "TableRow",
        # inline
        "ChineseToken",
        "CodeInline",
        "Connector",
        "Date",
        "GraphicInline",
        "HanziChar",
        "HanziMarker",
        "InlineNode",
        "LatinAcronym",
        "LatinWord",
        "MathInline",
        "MusicInline",
        "Number",
        "Percent",
        "PhoneticInline",
        "Punct",
        "Quantity",
        "Segment",
        "Space",
        "Unknown",
        "Word",
        # tactile Product IR — public result types expose it
        # (GraphicResult.raster, TactilePageResult.pages, CompiledBlock.raster)
        "TactileRaster",
    ],
    "brailix.core": [
        "Span",
        "merge_spans",
        "BackendContext",
        "FrontendContext",
        "GraphicsContext",
        "MathContext",
        "MusicContext",
        "DEFAULT_NORMALIZER",
        "DEFAULT_PINYIN_RESOLVER",
        "DEFAULT_RENDERER",
        "DEFAULT_SEGMENTER",
        "DEFAULT_ZH_ANALYZER",
        "BackendContractError",
        "BrailixError",
        "ConfigurationError",
        "IncompatibleDependencyError",
        "IncompatibleRendererError",
        "MissingExtraError",
        "ModelNotInstalledError",
        "ParseError",
        "RunMode",
        "StrictModeError",
        "UnknownAdapterError",
        "Warning",
        "WarningCollector",
        "WarningLevel",
        "normalize_run_mode",
    ],
    "brailix.core.models": [
        "ModelAsset",
        "all_assets",
        "get_asset",
        "register_asset",
        "is_managed_download",
        "set_managed_download",
        "get_model_dir",
        "get_models_root",
    ],
    "brailix.renderer": [
        "renderer_registry",
        "braille_renderer_names",
        "LayoutOptions",
        "LayoutRenderer",
        "cell_to_char",
    ],
    "brailix.input": [
        "parse_plain",
        "parse_markdown",
        "parse_docx",
        "parse_doc",
        "parse_musicxml",
        "parse_score_file",
        "parse_deferred_score",
        "parse_file",
        "InputLimits",
        "InputTooLargeError",
        "DEFAULT_INPUT_LIMITS",
    ],
    "brailix.frontend": [
        "segment",
        "normalize",
        "tokenize_zh",
        "annotate_pinyin",
        "parse_math_tree",
        "language_frontend_registry",
        # The language-keyed boundary pass is an extension point (a new
        # language registers its handler here, and the architecture doc points
        # extenders at it); the function that *runs* the registered handler is
        # orchestration the compiler calls once per run, and is
        # ``_apply_boundary`` — the two used to be exported the wrong way
        # round, and then the wrong one stayed publicly named for a while.
        "boundary_registry",
    ],
}


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_facade_all_is_exactly_the_manifest(module: str) -> None:
    mod = importlib.import_module(module)
    declared = set(getattr(mod, "__all__", ()))
    expected = set(_FACADE[module])
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    assert not missing, f"{module}.__all__ lost: {missing}"
    assert not extra, (
        f"{module}.__all__ gained {extra} — publishing a name is a deliberate "
        f"decision: add it to _FACADE here (which puts it in the generated "
        f"reference) with a docstring worth publishing, or take it back out "
        f"of __all__"
    )


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_every_published_name_actually_resolves(module: str) -> None:
    """``__all__`` may not name something the module doesn't have — that
    would only surface as an ImportError on a downstream ``import *``."""
    mod = importlib.import_module(module)
    for name in _FACADE[module]:
        assert hasattr(mod, name), f"{module}.{name} missing from facade"


def test_facade_reexports_are_the_same_objects() -> None:
    """The facade must re-export the *same* object, not a copy/alias."""
    from brailix.core import Span
    from brailix.core.span import Span as ConcreteSpan
    from brailix.ir import Block, InlineNode
    from brailix.ir.document import Block as ConcreteBlock
    from brailix.ir.inline import InlineNode as ConcreteInline
    from brailix.renderer import LayoutOptions
    from brailix.renderer.layout import LayoutOptions as ConcreteLayoutOptions

    assert Block is ConcreteBlock
    assert InlineNode is ConcreteInline
    assert Span is ConcreteSpan
    assert LayoutOptions is ConcreteLayoutOptions


def test_all_registered_inline_nodes_are_reexported() -> None:
    """Every registered inline node type must be importable from the stable
    ``brailix.ir`` surface, not just ``brailix.ir.inline``.

    The manifest above is hand-maintained, so a newly added node (this is how
    ``PhoneticInline`` slipped through) could be registered and serialisable
    yet never re-exported — leaving downstream front-ends / plugins that
    follow the documented "import from ``brailix.ir``" rule unable to consume
    it. This guards the whole registry, not a hand-list.
    """
    import brailix.ir as ir
    from brailix.ir.inline import _INLINE_REGISTRY

    missing = sorted(
        cls.__name__
        for cls in _INLINE_REGISTRY.values()
        if not hasattr(ir, cls.__name__) or cls.__name__ not in ir.__all__
    )
    assert not missing, f"inline node types missing from brailix.ir: {missing}"


def test_every_error_type_is_reexported_from_core() -> None:
    """An exception a caller can be handed must be nameable from the shallow
    surface it is told to import from.

    ``brailix.core`` calls itself the stable facade for the error types, and
    the result types' docstrings send readers there — yet
    ``IncompatibleRendererError`` (raised by every ``result.render(name)``),
    ``BackendContractError`` and ``IncompatibleDependencyError`` were only
    reachable as ``brailix.core.errors.…``, a path the top-level policy calls
    internal and free to move. A caller wanting to catch just that case had to
    choose between an unsupported import and widening to ``BrailixError``,
    which also swallows every unrelated compile failure.

    Derived from the module rather than hand-listed, for the same reason the
    inline-node and block-type guards walk their registries: the manifest is
    hand-maintained, and "we forgot" is exactly how three of them stayed
    private.
    """
    import brailix.core as core
    import brailix.core.errors as errors

    defined = {
        name
        for name, obj in vars(errors).items()
        if not name.startswith("_")
        and isinstance(obj, type)
        and issubclass(obj, errors.BrailixError)
        and obj.__module__ == errors.__name__
    }
    missing = sorted(defined - set(core.__all__))
    assert not missing, (
        f"error types defined in brailix.core.errors but absent from "
        f"brailix.core.__all__: {missing} — a caller told to import error "
        f"types from the shallow surface cannot catch them there"
    )
    assert defined, "no error types found — the scan stopped working"


def test_all_registered_block_types_are_reexported() -> None:
    """Same guard on the block side: a serialisable block type a caller can
    get back from ``DocumentIR.from_dict`` must be nameable from
    ``brailix.ir``, or that caller cannot isinstance-check what it holds."""
    import brailix.ir as ir
    from brailix.ir.document import _BLOCK_REGISTRY

    missing = sorted(
        cls.__name__
        for cls in _BLOCK_REGISTRY.values()
        if not hasattr(ir, cls.__name__) or cls.__name__ not in ir.__all__
    )
    assert not missing, f"block types missing from brailix.ir: {missing}"


# ---------------------------------------------------------------------------
# The extension surface — a second audience, a second manifest
# ---------------------------------------------------------------------------

# Writing an adapter needs two things the end-user facade above does not carry:
# the Protocol to satisfy, and the registry to register a loader with. Those
# live deeper than the facades on purpose (a registry belongs with its
# subsystem), which used to mean the officially documented way to extend
# brailix ran entirely through paths the top-level policy called internal and
# free to move — an extender could follow the guide or stay inside the
# supported surface, never both. They are supported; this is where that promise
# is kept.
#
# Deliberately a SEPARATE manifest from ``_FACADE``: the audiences differ (an
# integrator calling ``translate_text`` versus a plugin author registering an
# adapter), so the two surfaces should be able to move independently, and a
# reader of either list should not have to guess which entries concern them.
_EXTENSION_SURFACE: dict[str, list[str]] = {
    # The one core *type* a contract names that no facade carries.
    # ``LanguageBackend``'s three methods all take ``profile: BrailleProfile``,
    # so an implementer has to be able to write that name — and the guide sends
    # them to the shallow ``brailix.core``, which does not export it (nor
    # should: re-exporting it there would drag the whole profile/table loader
    # into every ``import brailix.core``, and so into ``brailix.ir``, which
    # promises to load carrying core primitives alone). ``brailix.core.config``
    # is where it lives and keeps its own surface, exactly as
    # ``brailix.core.models`` does; this is the promise that it stays put.
    "brailix.core.config": ["BrailleProfile"],
    # The contracts. Every pluggable part of the library satisfies one.
    "brailix.core.protocols": [
        "Segmenter",
        "Normalizer",
        "ChineseAnalyzer",
        "PinyinResolver",
        "LanguageFrontend",
        "LanguageBackend",
        "MathSourceAdapter",
        "MusicSourceAdapter",
        "GraphicSourceAdapter",
        "InlineTextTranslator",
        "GraphicAssetResolver",
        "Renderer",
    ],
    # The registries, at their own subsystem's path.
    "brailix.frontend.segment": ["segmenter_registry"],
    "brailix.frontend.normalize": ["normalizer_registry"],
    "brailix.frontend.zh.analyzer.registry": ["analyzer_registry"],
    "brailix.frontend.zh.pinyin.registry": ["resolver_registry"],
    "brailix.frontend.ja.analyzer.registry": ["analyzer_registry"],
    "brailix.frontend.math.registry": ["math_source_registry"],
    "brailix.frontend.music.registry": ["music_source_registry"],
    "brailix.frontend.graphics.registry": ["graphic_source_registry"],
    "brailix.backend.dispatch": ["language_backend_registry"],
    # The two keyed on the language rather than on a source format, plus the
    # renderer registry, already ride the end-user facades — repeated here so
    # this list answers "where do I register?" on its own.
    "brailix.frontend": ["language_frontend_registry", "boundary_registry"],
    "brailix.renderer": ["renderer_registry"],
}


@pytest.mark.parametrize("module", sorted(_EXTENSION_SURFACE))
def test_extension_surface_resolves(module: str) -> None:
    """Every promised extension name still exists where the guide says.

    This is the check the end-user manifest could not give: these names are
    not in any ``__all__`` (a registry module has no facade of its own), so a
    rename or a move would break third-party adapters silently — the import
    fails in *their* code, at *their* install time.
    """
    mod = importlib.import_module(module)
    missing = [n for n in _EXTENSION_SURFACE[module] if not hasattr(mod, n)]
    assert not missing, (
        f"{module} lost extension names {missing} — third-party adapters "
        f"import these by path. Keep the name, or update the guide, the "
        f"top-level policy docstring and this manifest together."
    )


# The extension-surface entries that promise *types* rather than a place to
# register: the contracts themselves, and the one core type those contracts
# name. Everything else in the manifest is a registry, and is checked to still
# be one.
_EXTENSION_TYPE_MODULES = frozenset(
    {"brailix.core.protocols", "brailix.core.config"}
)


def test_every_registry_in_the_extension_surface_is_a_registry() -> None:
    """A promised registry name must still be something you can ``register``
    on — the promise is the capability, not just the attribute."""
    from brailix.core.registry import Registry

    bad: list[str] = []
    for module, names in _EXTENSION_SURFACE.items():
        if module in _EXTENSION_TYPE_MODULES:
            continue
        mod = importlib.import_module(module)
        for name in names:
            obj = getattr(mod, name)
            # ``boundary_registry`` is a plain dict by design (a handler is a
            # bare callable, with no protocol to validate), so accept either
            # shape as long as it can take a registration.
            if not isinstance(obj, (Registry, dict)):
                bad.append(f"{module}.{name} is {type(obj).__name__}")
    assert not bad, f"extension surface entries that aren't registries: {bad}"


def test_every_brailix_type_a_protocol_names_has_a_supported_import() -> None:
    """An adapter author must be able to *annotate* what they implement.

    Every brailix type appearing in a Protocol signature has to be importable
    from a surface this suite pins — a facade or the extension manifest —
    or the guide contradicts itself for whoever implements that protocol:
    it sends extenders to the shallow surfaces and calls every deeper path
    internal and free to move, so a type reachable only from a deeper path
    leaves the implementer choosing between an unannotated signature and an
    unsupported import.

    Two names had already fallen through, and each one shows why this is
    derived rather than hand-listed. ``GraphicsContext`` was missing from
    ``brailix.core`` when the graphics vertical landed — caught by the earlier
    version of this test, which matched ``\\w+Context`` and so could only ever
    find contexts. ``BrailleProfile`` is the one that shape could not see: it
    is named by all three ``LanguageBackend`` methods, and lives in
    ``brailix.core.config``, which no manifest mentioned.

    Read from the **imports** rather than from the annotation strings. A
    Protocol can only name a brailix type it imported, so the import block is
    the complete list, and it dodges two problems the string scan had: a local
    alias (``NormalizedItem = InlineNode | Segment``) is not an importable name
    and would be reported, while its components — the names that actually have
    to be reachable — appear only inside it.

    Membership in a manifest is the check; that each promised name really
    resolves at the module promising it is
    :func:`test_every_published_name_actually_resolves` /
    :func:`test_extension_surface_resolves`.
    """
    import ast
    import importlib.util

    spec = importlib.util.find_spec("brailix.core.protocols")
    assert spec is not None and spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))

    named = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("brailix")
        for alias in node.names
    }
    supported = {
        name
        for manifest in (_FACADE, _EXTENSION_SURFACE)
        for names in manifest.values()
        for name in names
    }

    missing = sorted(named - supported)
    assert not missing, (
        f"types named in a Protocol signature with no supported import path: "
        f"{missing} — an extender told to import from the pinned surfaces "
        f"cannot annotate against them. Re-export from a facade, or add the "
        f"module and name to _EXTENSION_SURFACE as a deliberate promise."
    )
    assert named, "no brailix types found — the import scan stopped working"


def test_every_protocol_is_named_in_the_extension_surface() -> None:
    """A new Protocol in ``core.protocols`` is a new extension point, so it
    belongs in the manifest (and therefore in the guide).

    Guards the whole module rather than a hand-list — the same reason the
    inline-node and block-type checks above walk their registries: the
    manifest is hand-maintained, and "we forgot to document it" is exactly how
    an extension point ends up existing but undiscoverable.
    """
    import typing

    import brailix.core.protocols as protocols

    declared = set(_EXTENSION_SURFACE["brailix.core.protocols"])
    found = {
        name
        for name, obj in vars(protocols).items()
        if not name.startswith("_")
        and isinstance(obj, type)
        and typing.Protocol in getattr(obj, "__bases__", ())
    }
    assert found == declared, (
        f"protocols missing from the extension manifest: "
        f"{sorted(found - declared)}; manifest names that are no longer "
        f"protocols: {sorted(declared - found)}"
    )


# ---------------------------------------------------------------------------
# The modules a third party is told to import from publish nothing private
# ---------------------------------------------------------------------------

# ``brailix.pipeline`` is not one of the documented facades, but it is where
# the top-level package takes its re-exports from, so its ``__all__`` is the
# one internal list that reads as a public promise. Pinned here for that
# reason — and because it is exactly where private helpers accumulated
# (``_ensure_block_span``, ``_block_surface``, ``_frontend_parse_*``...)
# before the review caught them.
_PIPELINE_ALL = [
    "Pipeline",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
]


def test_pipeline_all_is_pinned() -> None:
    import brailix.pipeline as pipeline

    assert set(pipeline.__all__) == set(_PIPELINE_ALL)


# Names a facade may bind without underscore despite not being in ``__all__``.
# Only for a deliberate, documented decision — not a parking spot for whatever
# the check currently reports.
#
# **Empty, and worth keeping empty.** Its one entry was
# ``brailix.frontend.apply_boundary``: importable, tab-completing, documented
# as carrying no compatibility promise — which is a promise a reader can only
# find by reading this file. It is ``_apply_boundary`` now, so the rule "what
# resolves at a facade is the API" holds with no exception to look up. The
# mechanism stays because the next candidate deserves an argument, not a
# silent alias; adding an entry means writing down why the name has to be
# reachable *and* unsupported.
_NAMESPACE_ALLOWLIST: dict[str, set[str]] = {}


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_facade_binds_no_unpublished_brailix_name(module: str) -> None:
    """A **facade** must not bind a ``brailix`` name it does not publish.

    ``__all__`` governs ``import *`` and the generated reference. It does not
    stop ``from brailix.frontend import LanguageFrontend`` — that imports
    cleanly, tab-completes, and gives a third party no way to tell it from
    ``segment`` sitting beside it. A facade is the address the documentation
    sends people to, so what resolves there is what they will treat as the API.

    Scoped to ``brailix``-owned names on purpose: ``from brailix.input import
    Path`` is untidy, but nobody mistakes ``pathlib.Path`` for our API, whereas
    ``Registry`` or ``DocumentIR`` at a facade path reads exactly like one.

    Read from the source rather than from ``vars(module)``: a re-exported
    *constant* (a suffix ``frozenset``) has no ``__module__`` to trace back,
    and those are exactly the ones a runtime check misses.

    **Not applied to ``brailix.pipeline``**, which is an implementation module
    rather than a facade — it imports ``Span``, ``DocumentIR`` and two dozen
    others because it *uses* them, and aliasing every one would be noise for a
    module nobody is directed to import from. What matters there is narrower
    and is checked by
    :func:`test_pipeline_namespace_offers_nothing_that_is_not_published_somewhere`:
    it may re-expose a name some supported path already carries, but not one
    that is published nowhere, because that name exists at no other address and
    so reads as its own API.
    """
    import ast
    import importlib.util

    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))

    mod = importlib.import_module(module)
    published = set(getattr(mod, "__all__", ()))
    allowed = _NAMESPACE_ALLOWLIST.get(module, set())

    leaked: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        source = node.module or ""
        if not source.startswith("brailix"):
            continue
        for alias in node.names:
            bound = alias.asname or alias.name
            if bound.startswith("_") or bound in published or bound in allowed:
                continue
            leaked.append(f"{bound} (from {source})")

    assert not leaked, (
        f"{module} binds brailix names it does not publish: {leaked}\n"
        f"Import them as ``... import X as _X`` so the namespace carries the "
        f"API and nothing else, add them to __all__ as a deliberate promise, "
        f"or record a documented exception in _NAMESPACE_ALLOWLIST."
    )


def test_pipeline_namespace_offers_nothing_that_is_not_published_somewhere() -> None:
    """``__all__`` governs ``import *`` and the generated reference — it does
    not stop ``from brailix.pipeline import CompilationSession``.

    So a plain ``from ._session import CompilationSession`` for internal use
    also *publishes* that name in every practical sense: it imports cleanly, it
    tab-completes, and a downstream author has no way to tell it apart from
    ``Pipeline`` sitting beside it. ``_session`` states it has no public API and
    that only Pipeline may construct a session — a promise the package namespace
    was quietly contradicting, along with the frontend driver, ``compile_block``,
    ``compose_document_pages`` and the four fingerprint functions.

    The bar is **nothing new resolves here**, not "nothing but ``__all__``".
    ``brailix.pipeline`` is an implementation module rather than a facade: it
    imports ``Paragraph``, ``Span``, ``DocumentIR`` and two dozen more because
    it *uses* them, and every one of those is already supported at the address
    the documentation sends people to. Reaching one of them through this module
    is untidy; it is not a new promise, and aliasing them all would be noise.

    What counts is a name published *nowhere* — and the previous version of
    this check could not see those. It asked whether the object's
    ``__module__`` started with ``brailix.pipeline``, a proxy for "our own
    internal" that let four names through: ``load_profile`` and
    ``translate_document``, entry points belonging to other layers, and the two
    ``BackendContext.options`` keys, which are backend wire protocol. All four
    sat at ``brailix.pipeline`` next to ``Pipeline`` with nothing to mark them
    apart. This asks the question directly instead.

    Read from the source, like the facade check: a re-exported *constant* has
    no ``__module__`` to trace, and those are exactly the ones a runtime check
    misses — the two option keys are strings.
    """
    import ast
    import importlib.util

    published = {
        name
        for names in (*_FACADE.values(), *_EXTENSION_SURFACE.values())
        for name in names
    }

    import brailix.pipeline as pipeline

    spec = importlib.util.find_spec("brailix.pipeline")
    assert spec is not None and spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))

    leaked: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        source = node.module or ""
        if not source.startswith("brailix"):
            continue
        for alias in node.names:
            bound = alias.asname or alias.name
            if (
                bound.startswith("_")
                or bound in pipeline.__all__
                or bound in published
            ):
                continue
            leaked.append(f"{bound} (from {source})")

    assert not leaked, (
        f"brailix.pipeline binds names no documented surface publishes: "
        f"{leaked} — import them as ``from ._module import X as _X`` so the "
        f"package namespace offers nothing a third party cannot already get "
        f"from a supported path, or publish them (in _FACADE / "
        f"_EXTENSION_SURFACE, or in __all__ and _PIPELINE_ALL) as a deliberate "
        f"promise"
    )


@pytest.mark.parametrize("module", [*sorted(_FACADE), "brailix.pipeline"])
def test_importable_surface_publishes_nothing_private(module: str) -> None:
    """``__all__`` says "supported"; a leading underscore says "private".

    A module that lists both tells a third party two contradictory things,
    and ``from <mod> import *`` resolves the contradiction the wrong way —
    the private helper becomes a compatibility burden. In-repo callers import
    such helpers from the module that defines them
    (``brailix.pipeline._helpers`` and friends), which needs no ``__all__``
    entry at all.

    Scoped to the modules callers are pointed at. Internal split-packages
    (``brailix.core.config.loader`` and friends) deliberately re-export their
    private helpers through ``__all__`` so a name survives the split for
    in-repo callers; that is a re-export convention, not a promise, and this
    guard would misread it.
    """
    mod = importlib.import_module(module)
    private = sorted(n for n in getattr(mod, "__all__", ()) if n.startswith("_"))
    assert not private, f"{module}.__all__ publishes private names: {private}"


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_facade_namespace_at_runtime_holds_nothing_unpublished(
    module: str,
) -> None:
    """The same rule as
    :func:`test_facade_binds_no_unpublished_brailix_name`, checked on the
    imported module instead of on its source.

    The AST check reads top-level ``from brailix... import X``, which is how
    every facade is written today — and is the *only* shape it can see. A name
    can reach a facade namespace four other ways: ``import brailix.core.span as
    Span`` (an ``ast.Import``, not an ``ImportFrom``), an alias assignment, a
    function or class defined right there without a leading underscore, and an
    attribute set at import time by something else. All four read identically
    to a published name from outside — they import, they tab-complete, and
    nothing distinguishes them from ``segment`` sitting beside them.

    The two are complementary, not redundant: this one cannot see a re-exported
    *constant* (a suffix ``frozenset`` has no ``__module__`` to trace back to
    this package), which is exactly what the AST check catches.

    Submodules are the one thing exempt, because binding them is not a choice:
    importing ``brailix.ir.braille`` sets ``braille`` on ``brailix.ir`` whether
    anyone wanted it or not. Only under its *own* name, though — ``import
    brailix.core.span as Span`` is a decision, and it is reported.
    """
    import types

    mod = importlib.import_module(module)
    published = set(getattr(mod, "__all__", ()))
    allowed = _NAMESPACE_ALLOWLIST.get(module, set())

    leaked: list[str] = []
    for name, value in vars(mod).items():
        if name.startswith("_") or name in published or name in allowed:
            continue
        if isinstance(value, types.ModuleType):
            owner = value.__name__
            if owner.startswith("brailix") and name != owner.rsplit(".", 1)[-1]:
                leaked.append(f"{name} (module {owner} under another name)")
            continue
        owner = getattr(value, "__module__", None) or getattr(
            type(value), "__module__", ""
        )
        if str(owner).startswith("brailix"):
            leaked.append(f"{name} (defined in {owner})")

    assert not leaked, (
        f"{module} resolves brailix names it does not publish: {leaked}\n"
        f"Bind them under an underscore alias, add them to __all__ (and to "
        f"_FACADE) as a deliberate promise, or record a documented exception "
        f"in _NAMESPACE_ALLOWLIST."
    )
