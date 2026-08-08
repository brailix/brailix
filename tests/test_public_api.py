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

import ast
import importlib
import importlib.util
import inspect
import re
import types
import typing
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
        "EmbeddedBlock",
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
        "CodeInline",
        "Connector",
        "Date",
        "DateComponent",
        "InlineNode",
        "LatinWord",
        "MathInline",
        "Number",
        "PhoneticInline",
        "Punct",
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
        "Segment",
        "BackendContext",
        "FrontendContext",
        "GraphicsContext",
        "MathContext",
        "MusicContext",
        "BackendContractError",
        "BrailixError",
        "ConfigurationError",
        "FrontendContractError",
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
        # What ``parse_file`` routes, derived from its own table. Published
        # because the question "can this application open that file?" is asked
        # from outside — a desktop file dialog builds its filter from it — and
        # the answer was previously re-typed by each caller, which is how
        # ``.abc`` and ``.mid`` became formats the library read and the
        # desktop opened as plain text.
        "ROUTED_SUFFIXES",
    ],
    "brailix.frontend": [
        "segment",
        "normalize",
        "tokenize_zh",
        "annotate_pinyin",
        "parse_math_tree",
        "language_frontend_registry",
        # Discovery for a front-end that must offer a language's pluggable
        # parts without knowing which languages exist: the registry says which
        # are registered, these two say what each one offers and what to call
        # it. Published because every front-end needs them — the CLI's
        # ``--list-analyzers``, an editor's engine picker — and the alternative
        # is what the CLI did, importing ``frontend.zh`` / ``frontend.ja`` by
        # name and going stale the moment a third language registers.
        "language_display_name",
        "list_language_adapters",
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


def test_the_lazy_root_facade_resolves_exactly_what_it_publishes() -> None:
    """``brailix.__all__`` and its lazy-export table are one list, twice.

    The top-level facade resolves every published name on first attribute
    access (``brailix.__getattr__``), so that it can be imported without
    dragging in a layer — see ``tests/test_core_layering.py`` for why that is a
    layering rule and not a startup tweak. The cost is a second list: a mapping
    from name to the module that defines it. Two lists mean two ways to be
    wrong, and neither is loud. A name in ``__all__`` but not in the table
    raises ``AttributeError`` at the caller's ``from brailix import X`` (and
    ``import *`` fails outright); a name in the table but not in ``__all__`` is
    an unpublished name that resolves at the address the documentation sends
    everybody to — which is the thing this file exists to prevent.

    ``test_every_published_name_actually_resolves`` covers the first direction
    by consequence; this covers both by construction, and says which list to
    edit when it fails.
    """
    import brailix

    table = set(brailix._EXPORTS)
    published = set(brailix.__all__)
    assert table == published, (
        f"brailix._EXPORTS and brailix.__all__ disagree: "
        f"published but unresolvable {sorted(published - table)}; "
        f"resolvable but unpublished {sorted(table - published)}"
    )
    # And the modules named really define them, rather than the name happening
    # to be reachable from somewhere else too.
    for name, module in brailix._EXPORTS.items():
        assert hasattr(importlib.import_module(module), name), (
            f"brailix._EXPORTS sends {name!r} to {module}, which has no such "
            f"attribute"
        )


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
        "LanguageFrontend",
        "LanguageBackend",
        "MathSourceAdapter",
        "MusicSourceAdapter",
        "GraphicSourceAdapter",
        "InlineTextTranslator",
        "GraphicAssetResolver",
        "Renderer",
    ],
    # A language's own contracts + intermediate types, under that language.
    # Symmetrical on purpose: an adapter author learns ONE shape ("look under
    # the language"), and a third language has a consistent example to copy.
    # These used to differ — Chinese's protocol sat in ``core.protocols`` and
    # its token in ``brailix.ir`` while Japanese kept both in its own package.
    #
    # ``ChineseToken`` is promised at ONE address, and it is neither
    # subsystem's. It is the mediator the analyzer and the resolver hand
    # between them, and ``brailix.frontend.zh.tokens`` says in as many words
    # that belonging to neither end is what keeps the two independently
    # replaceable — so promising it from an end too would tie the shared
    # format's compatibility to a package that exists to be swappable.
    # (Japanese has no equivalent line because it has one subsystem: with no
    # second consumer to stay independent of, ``JapaneseToken`` lives in the
    # analyzer that emits it.)
    "brailix.frontend.zh.analyzer": ["ChineseAnalyzer"],
    "brailix.frontend.zh.pinyin": ["PinyinResolver"],
    "brailix.frontend.zh.tokens": ["ChineseToken"],
    "brailix.frontend.ja.analyzer": ["JapaneseAnalyzer", "JapaneseToken"],
    # The registries, at their own subsystem's path.
    "brailix.frontend.segmentation": ["segmenter_registry"],
    "brailix.frontend.normalization": ["normalizer_registry"],
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


def test_no_manifest_lists_a_name_twice() -> None:
    """A manifest is read as much as it is asserted against.

    Every check in this file converts its list to a ``set``, so a duplicate can
    never fail one — and two arrived unnoticed when ``HanziChar`` became
    ``Word`` and ``LatinAcronym`` became ``LatinWord`` in place, leaving each
    name listed twice in the same block. A list that calls itself *the*
    manifest of the public surface should at least be able to say what it
    contains: a reader counting the published inline nodes against
    ``brailix.ir`` got two different numbers and no check disagreed.

    :data:`_PIPELINE_ALL` is checked here too, because it is a manifest by the
    same definition (a hand-written list of names, compared as a set) and was
    simply not on the list — the one that pins the namespace the top-level
    package re-exports from.
    """
    dupes = {
        f"{label}[{module!r}]": sorted({n for n in names if names.count(n) > 1})
        for label, manifest in (
            ("_FACADE", _FACADE),
            ("_EXTENSION_SURFACE", _EXTENSION_SURFACE),
            ("_PIPELINE_ALL", {"brailix.pipeline": _PIPELINE_ALL}),
        )
        for module, names in manifest.items()
        if len(names) != len(set(names))
    }
    assert not dupes, f"manifest entries listed more than once: {dupes}"


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


@pytest.mark.parametrize("module", sorted(_EXTENSION_SURFACE))
def test_an_extension_module_is_reachable_as_a_module(module: str) -> None:
    """A promised extension path must resolve to a *module* the way anybody
    would actually write it.

    :func:`test_extension_surface_resolves` above goes through
    ``importlib.import_module``, which reads ``sys.modules`` directly — so it
    passes even when the ordinary spelling does not. Two of these paths were in
    exactly that state: ``brailix.frontend`` bound ``segment`` and
    ``normalize`` as functions, and a package attribute wins over a same-named
    submodule, so ``import brailix.frontend.segment as m`` handed back the
    function and ``m.segmenter_registry`` raised ``AttributeError`` at the one
    path the extension guide names. The from-import form worked, the dotted
    form did not, and nothing here could tell the difference.

    ``exec`` of a real ``import`` statement, because that is the thing under
    test: the attribute lookup the statement performs on the parent package is
    precisely what a helper taking the module name would skip.
    """
    namespace: dict[str, object] = {}
    exec(f"import {module} as m", namespace)  # noqa: S102 — the statement IS the test
    resolved = namespace["m"]
    assert isinstance(resolved, types.ModuleType), (
        f"`import {module}` resolved to {type(resolved).__name__}, not a "
        f"module — a name bound on the parent package is shadowing the "
        f"submodule, so an extender following the guide gets the wrong object."
    )
    assert resolved.__name__ == module
    for name in _EXTENSION_SURFACE[module]:
        assert hasattr(resolved, name)


# Modules whose ``__all__`` serves a second audience as well: a language's
# analyzer / resolver package is the subsystem entry point the orchestrator
# calls (``tokenize`` / ``annotate`` / ``analyze``) AND the home of that
# language's contracts. Its ``__all__`` answers the first job; the manifest
# promises the second, so its ``__all__`` is legitimately wider than the
# manifest — by *these names*, listed here.
#
# An explicit set, not a skip. The previous shape excluded these modules from
# the equality check outright, and the hole was immediate: ``zh.pinyin``
# published ``ChineseToken``, a name the manifest promises at a different
# address on purpose, and no check could see it because the module was on the
# exemption list. "Publishes a second thing as well" is a reason to say what
# that second thing is, not a reason to stop looking.
_SUBSYSTEM_ENTRY_POINTS: dict[str, set[str]] = {
    "brailix.frontend.zh.analyzer": {
        "tokenize",
        "list_analyzers",
        # The picker counterpart of ``list_analyzers``: what is installed,
        # not merely what is registered.
        "available_analyzers",
        "shift_token_spans",
        "tokens_to_inline",
        "insert_cross_kind_boundary_spaces",
    },
    "brailix.frontend.zh.pinyin": {
        "annotate",
        "list_resolvers",
        "available_resolvers",
    },
    # Japanese's counterpart of the Chinese entry above. Shorter because the
    # language has one subsystem and no resolver: there is no
    # ``available_analyzers`` picker and no span-shifting helper.
    "brailix.frontend.ja.analyzer": {
        "analyze",
        "list_analyzers",
        "tokens_to_inline",
    },
    # The two language-neutral frontend stages. Each is promised here for its
    # registry and publishes the stage function the orchestrator calls, which
    # the ``brailix.frontend`` facade re-exports under the same name.
    "brailix.frontend.segmentation": {"segment"},
    "brailix.frontend.normalization": {"normalize"},
}


@pytest.mark.parametrize(
    "module",
    sorted(set(_EXTENSION_SURFACE) - set(_FACADE)),
)
def test_extension_module_publishes_no_more_than_it_promises(module: str) -> None:
    """The other direction, which presence-only could not give: a module on
    this manifest must not ``__all__`` more than the manifest names.

    The end-user facades are checked for exact set equality; these were checked
    only for "the promised name still exists", and the asymmetry showed:
    ``brailix.core.config`` is promised for :class:`BrailleProfile` alone, both
    here and in the top-level extension policy, while its ``__all__`` published
    six names — a loader, a validator, a package-root ``Path``. Nothing was
    lying to the guard; the guard simply never asked.

    Equality rather than a subset check, so a name cannot be promised here and
    then quietly dropped from ``__all__`` either.

    **A missing ``__all__`` fails.** It used to return early — "the normal
    shape for a registry module, and what it offers is pinned by the presence
    check above" — which quietly exempted eight of the sixteen modules on this
    manifest from the only check that looks the other way. What they published
    in the meantime is what an exemption always turns out to have been hiding:
    ``brailix.frontend.ja.analyzer`` handed ``Span``, ``InlineNode``, ``Space``
    and ``Word`` to ``import *`` at an address the extension guide sends
    adapter authors to; every registry module offered ``Registry`` and its
    subsystem's protocol; ``brailix.backend.dispatch`` offered two dozen IR
    node types. None of it was ever promised, and the presence check cannot
    see any of it — it only asks whether the promised names are still there.

    The rule this restores is the top-level policy's own: ``__all__`` *is* the
    promise, so a module that makes one has to state it. Writing the list is
    also the cheapest half of the fix — it costs one literal per module and it
    is what ``import *`` reads.

    Modules that are *also* end-user facades (``brailix.frontend``,
    ``brailix.renderer`` — they carry a registry as well as their own surface)
    are excluded: their ``__all__`` is pinned exactly by ``_FACADE``, and the
    extension entry repeats a subset of it so this list answers "where do I
    register?" on its own.

    A language subsystem publishes its contract *and* the entry points the
    orchestrator calls, so for those modules the expected set is the manifest
    plus :data:`_SUBSYSTEM_ENTRY_POINTS` — named there rather than waved
    through, because the last exemption is how a mediator type ended up
    published from one of the two subsystems it deliberately sits between.
    """
    mod = importlib.import_module(module)
    published = getattr(mod, "__all__", None)
    expected = set(_EXTENSION_SURFACE[module]) | _SUBSYSTEM_ENTRY_POINTS.get(
        module, set()
    )
    assert published is not None, (
        f"{module} is on the extension manifest but declares no __all__, so "
        f"``from {module} import *`` publishes every non-underscore name it "
        f"binds — implementation imports included — and no check can say what "
        f"it promises. Add ``__all__ = {tuple(sorted(expected))!r}``."
    )
    assert set(published) == expected, (
        f"{module}.__all__ is {sorted(published)} but the extension manifest "
        f"(plus its declared subsystem entry points) promises "
        f"{sorted(expected)}. ``__all__`` is the "
        f"promise: publish it here (and in the top-level policy docstring and "
        f"the extension guide) as a deliberate widening, or take it out of "
        f"``__all__`` — an explicit ``from <mod> import <name>`` never "
        f"consulted ``__all__`` and keeps working for in-repo callers."
    )


# The extension-surface entries that promise *types* rather than a place to
# register: the contracts themselves, the one core type those contracts name,
# and each language's own contracts + token type. Everything else in the
# manifest is a registry, and is checked to still be one.
_EXTENSION_TYPE_MODULES = frozenset(
    {
        "brailix.core.protocols",
        "brailix.core.config",
        "brailix.frontend.zh.analyzer",
        "brailix.frontend.zh.pinyin",
        "brailix.frontend.zh.tokens",
        "brailix.frontend.ja.analyzer",
    }
)


# The one promised registry that is a plain ``dict`` by design: a boundary
# handler is a bare callable with no protocol to validate, so there is nothing
# for a :class:`Registry` to do that a mapping does not. Named by its full
# path rather than allowed by shape — "any dict counts as a registry" would
# pass a real :class:`Registry` that had *degraded* into one (a module-level
# ``some_registry = {}`` left behind by a refactor), which is exactly the
# regression the check below exists to catch, in exactly the place an
# extender's ``.register(...)`` would then fail.
_DICT_REGISTRIES = frozenset({"brailix.frontend.boundary_registry"})


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
            if isinstance(obj, Registry):
                continue
            if f"{module}.{name}" in _DICT_REGISTRIES and isinstance(obj, dict):
                continue
            bad.append(f"{module}.{name} is {type(obj).__name__}")
    assert not bad, (
        f"extension surface entries that aren't registries: {bad} — a "
        f"promised registry must still take a registration. If a name is "
        f"deliberately a plain mapping, add its fully-qualified path to "
        f"_DICT_REGISTRIES with the reason."
    )


def test_the_dict_registry_exception_is_still_needed() -> None:
    """Each name allowed to be a plain mapping is checked to still *be* one.

    An exception that outlives its reason stops being an exception and starts
    being a hole: if ``boundary_registry`` became a real
    :class:`~brailix.core.registry.Registry`, the entry would go on excusing
    whatever else took its name.
    """
    for path in _DICT_REGISTRIES:
        module, _, name = path.rpartition(".")
        obj = getattr(importlib.import_module(module), name)
        assert isinstance(obj, dict), (
            f"{path} is no longer a plain dict — drop it from "
            f"_DICT_REGISTRIES"
        )


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
    is named by every ``LanguageBackend`` method, and lives in
    ``brailix.core.config``, which no manifest mentioned.

    Counted rather than written out, because the count drifted: this said
    "all three" until the protocol lost ``translate_hanzi_char`` and became
    two. The number was never what mattered — that the type is named at all
    is — and the check itself reads the protocol, so only the prose could go
    stale.

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

    # ``alias.name``, never the local ``as`` spelling: what has to be
    # importable is the type's real name at a supported address, and this
    # module aliases the ones it binds at runtime (``BrailleProfile as
    # _BrailleProfile``) to keep a plain foreign-looking name out of its own
    # namespace. The underscore is that module's private business; the promise
    # an extender relies on is the unprefixed name.
    named = {
        alias.name
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
    """Compared as **lists**, not sets.

    The set form answered "the same names" and nothing about how many times
    each is written, so a name listed twice in ``__all__`` — which
    ``import *`` and every reader take as one promise made twice — passed
    unnoticed, in the one list this file exists to pin. Order is part of it
    for the same reason: the manifest here is meant to be read against the
    module side by side.
    """
    import brailix.pipeline as pipeline

    assert list(pipeline.__all__) == _PIPELINE_ALL


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


def _unpublished_bindings(source: str, published: set[str]) -> list[str]:
    """Brailix names ``source`` binds at module level without publishing them.

    Both spellings of a re-export count. An absolute one is recognised by its
    module path; a **relative** one — ``from ._internal import Foo`` — is
    recognised by being relative at all, since inside ``brailix`` that can only
    name a brailix module, whatever it resolves to. Underscore-aliased names are
    not bindings a reader can mistake for API, so they pass.
    """
    bound_names: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ImportFrom):
            continue
        origin = node.module or ""
        if node.level:
            origin = "." * node.level + origin
        elif not origin.startswith("brailix"):
            continue
        for alias in node.names:
            bound = alias.asname or alias.name
            if bound.startswith("_") or bound in published:
                continue
            bound_names.append(f"{bound} (from {origin})")
    return bound_names


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

    A **relative** re-export counts the same. ``from ._internal import Foo``
    binds ``Foo`` at the facade exactly as the absolute spelling does, but the
    check asked whether ``node.module`` started with ``brailix`` — and a
    relative one never does, so the whole family was filed as somebody else's
    name and skipped. Combined with the blind spot above, a relatively
    re-exported *constant* was invisible to both halves of the rule. Inside
    ``brailix``, a relative import always names a brailix module, whatever it
    resolves to, so no resolution is needed to answer the question this asks.

    ``brailix.pipeline`` is held to the same rule by
    :func:`test_pipeline_publishes_its_all_and_binds_nothing_else` rather than
    from this list, because it is not a *documented* facade — nobody is sent
    there — while still being the address the top-level package re-exports
    from, and the one every traceback names. The rule is identical; only the
    manifest differs (:data:`_PIPELINE_ALL`, not :data:`_FACADE`).
    """
    import importlib.util

    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    source_text = Path(spec.origin).read_text(encoding="utf-8")

    mod = importlib.import_module(module)
    published = set(getattr(mod, "__all__", ()))
    allowed = _NAMESPACE_ALLOWLIST.get(module, set())
    leaked = _unpublished_bindings(source_text, published | allowed)

    assert not leaked, (
        f"{module} binds brailix names it does not publish: {leaked}\n"
        f"Import them as ``... import X as _X`` so the namespace carries the "
        f"API and nothing else, add them to __all__ as a deliberate promise, "
        f"or record a documented exception in _NAMESPACE_ALLOWLIST."
    )


def test_pipeline_publishes_its_all_and_binds_nothing_else() -> None:
    """``__all__`` governs ``import *`` and the generated reference — it does
    not stop ``from brailix.pipeline import CompilationSession``.

    So a plain ``from ._session import CompilationSession`` for internal use
    also *publishes* that name in every practical sense: it imports cleanly, it
    tab-completes, and a downstream author has no way to tell it apart from
    ``Pipeline`` sitting beside it. ``_session`` states it has no public API and
    that only Pipeline may construct a session — a promise the package namespace
    was quietly contradicting, along with the frontend driver, ``compile_block``,
    ``compose_document_pages`` and the four fingerprint functions.

    The bar here is the strict one the documented facades are held to:
    **nothing but ``__all__``**. It used to be the weaker "nothing NEW
    resolves here", which let ``Paragraph``, ``Span``, ``DocumentIR``,
    ``BackendContext``, ``InputLimits``, ``MathInline`` and two dozen more
    keep their plain names on the reasoning that each is already supported at
    ``brailix.ir`` / ``brailix.core`` / ``brailix.input``, so reaching one
    through here was untidy rather than a new promise. Both halves of that are
    true and it still left ``from brailix.pipeline import Paragraph``
    working — an import path a downstream author can come to depend on, at the
    one internal address the top-level package re-exports from and every
    traceback and "go to definition" lands on. Deleting the binding would then
    break code that had every reason to think it was fine.

    What changed is that the module stopped having to choose. The
    orchestrator's implementation moved to
    :mod:`brailix.pipeline._pipeline`, where it imports the types it uses
    under their own names, and this package became a facade that binds its
    eight published names and nothing else — so the strict rule costs no
    underscores at all.

    Read from the source, like the facade check — and through the same
    :func:`_unpublished_bindings`, so the relative spelling counts here too:
    a re-exported *constant* has no ``__module__`` to trace, and those are
    exactly the ones a runtime check misses (the two ``BackendContext``
    option keys this once let through are strings).
    """
    import importlib.util

    import brailix.pipeline as pipeline

    spec = importlib.util.find_spec("brailix.pipeline")
    assert spec is not None and spec.origin is not None
    leaked = _unpublished_bindings(
        Path(spec.origin).read_text(encoding="utf-8"),
        set(pipeline.__all__),
    )

    assert not leaked, (
        f"brailix.pipeline binds brailix names it does not publish: {leaked}\n"
        f"It is a facade: the implementation lives in "
        f"``brailix.pipeline._pipeline`` and the helpers in their own "
        f"modules, which is where in-repo callers import them from. Add the "
        f"name to __all__ and _PIPELINE_ALL as a deliberate promise, or take "
        f"the import out of the facade."
    )


def test_pipeline_namespace_at_runtime_holds_nothing_unpublished() -> None:
    """The runtime half of the same rule, for the same reason the facades get
    one: a name can reach a package namespace in ways no import statement
    shows — an alias assignment, a class defined in place, an attribute set by
    something else at import time.

    Submodules under their own name are exempt, because binding them is not a
    choice: ``from brailix.pipeline.frontend_driver import FrontendDriver as
    _FrontendDriver`` sets ``frontend_driver`` on the package whether anyone
    wanted it or not.
    """
    import brailix.pipeline as pipeline

    published = set(pipeline.__all__)
    leaked: list[str] = []
    for name, value in vars(pipeline).items():
        if (
            name.startswith("_")
            or name in published
            or name in _UNAVOIDABLE_BINDINGS
        ):
            continue
        if isinstance(value, types.ModuleType):
            if value.__name__.rsplit(".", 1)[-1] == name:
                continue
        owner = getattr(value, "__module__", None) or getattr(
            type(value), "__module__", ""
        )
        leaked.append(f"{name} (from {owner})")

    assert not leaked, (
        f"brailix.pipeline resolves names it does not publish: {leaked}"
    )


def test_no_module_publishes_a_private_name() -> None:
    """``__all__`` says "supported"; a leading underscore says "private".

    A module that lists both tells a third party two contradictory things,
    and ``from <mod> import *`` resolves the contradiction the wrong way —
    the private helper becomes a compatibility burden. In-repo callers import
    such helpers from the module that defines them
    (``brailix.pipeline._helpers`` and friends), which needs no ``__all__``
    entry at all — an explicit ``from x import _y`` never consulted ``__all__``
    in the first place.

    Every module in the package, not only the ones callers are pointed at. The
    check used to run on the facades alone, because an internal split-package
    listed forty private helpers in its ``__all__`` "for backward compat" —
    which is the contradiction itself, stated as a justification: the top-level
    policy says ``__all__`` IS the promise, and that list said "supported" and
    "internal, free to move" about the same helper. Exempting the module made
    the guard agree with whichever module was loudest.

    Read statically rather than by importing: a module under an uninstalled
    extra must be covered too, and importing every module to ask what it
    publishes would make coverage depend on which extras happen to be present.
    """
    import ast
    import importlib.util

    spec = importlib.util.find_spec("brailix")
    assert spec is not None and spec.origin is not None
    offenders: list[str] = []
    package_root = Path(spec.origin).resolve().parent
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign)
                else []
            )
            if not any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in targets
            ):
                continue
            names = [
                el.value
                for el in getattr(node.value, "elts", [])
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            ]
            private = sorted(n for n in names if n.startswith("_"))
            if private:
                rel = path.relative_to(package_root.parent).as_posix()
                offenders.append(f"{rel}: {private}")

    assert not offenders, (
        "__all__ publishes private names:\n  " + "\n  ".join(offenders)
    )


def _all_definition_defects(source: str) -> list[str]:
    """Ways of writing ``__all__`` that the static guards cannot read.

    The scans above read the names straight out of the syntax tree, which is
    what lets them cover a module sitting behind an uninstalled extra. That
    holds only while ``__all__`` *is* a literal: a computed list, a
    concatenation, a later ``+=`` or ``.append`` all leave the scan looking at
    an empty element list and reporting nothing — a guard that passes because
    it went blind, which reads the same as a clean tree in the report.

    A literal is also the right shape on its own terms. ``__all__`` is the
    compatibility promise; one assembled at import time cannot be checked by
    reading it, and can differ between two installs of the same version.
    """
    tree = ast.parse(source)

    def _is_name_list(value: ast.expr | None) -> bool:
        return isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(el, ast.Constant) and isinstance(el.value, str)
            for el in value.elts
        )

    defects: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ) and not _is_name_list(node.value):
                defects.append(
                    f"line {node.lineno}: __all__ is not a literal list/tuple "
                    f"of name strings"
                )
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "__all__"
                and not _is_name_list(node.value)
            ):
                defects.append(
                    f"line {node.lineno}: __all__ is not a literal list/tuple "
                    f"of name strings"
                )
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                defects.append(f"line {node.lineno}: __all__ is extended in place")
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "__all__"
            ):
                defects.append(
                    f"line {node.lineno}: __all__.{func.attr}(...) changes the "
                    f"promise after the fact"
                )
    return defects


def test_every_all_in_the_package_is_a_literal() -> None:
    """``__all__`` is written out, once, as a list of names — never computed.

    Every module in the package, for the same reason the private-name scan
    covers every module: the rule is about what a module publishes, and the
    guards that enforce it read the source rather than the imported object.
    """
    import importlib.util

    spec = importlib.util.find_spec("brailix")
    assert spec is not None and spec.origin is not None
    package_root = Path(spec.origin).resolve().parent
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        for defect in _all_definition_defects(path.read_text(encoding="utf-8")):
            rel = path.relative_to(package_root.parent).as_posix()
            offenders.append(f"{rel}: {defect}")

    assert not offenders, (
        "__all__ written in a form the static API guards cannot read:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# "public" is a word about the compatibility promise, not about scope
# ---------------------------------------------------------------------------

# Files whose "public entry point(s)" wording is about names that really are on
# the supported surface. Every other file in the package is internal, so the
# phrase there says the opposite of the top-level policy.
#
# The wording matters because a module docstring is the *only* thing most
# readers consult before importing. ``brailix.backend.math`` had a docstring
# spelling out "the whole package is internal" and, two paragraphs later, a
# ``# Public entry points`` banner over the same two functions — a reader could
# take away either. The subsystem facades said "one public entry point:
# parse_math_tree" while ``brailix.frontend`` said, of the same function at the
# same path, "internal, free to move between releases".
_PUBLIC_WORDING_ALLOWED = {
    # The policy statement itself, and the errors it points at.
    "brailix/__init__.py",
    "brailix/core/__init__.py",
    # This file: the checks below quote the phrase in order to ban it.
    "tests/test_public_api.py",
    # ``parse_markdown`` / ``parse_docx`` / ``parse_doc`` ARE published — the
    # ``brailix.input`` facade re-exports each one — so naming them public
    # where they are defined is accurate. What is internal there is the
    # *path*, which those docstrings now say.
    "brailix/input/markdown.py",
    "brailix/input/docx/__init__.py",
}

# The vocabulary an internal module uses instead, established by
# ``brailix.frontend``'s own subsystem table: "subsystem entry point" for a
# vertical's single way in, "package"/"module entry point" for a file's.
#
# The nouns after "public" are the ones that name *a way in* — the thing whose
# scope is in question. "public entry point" was the only spelling checked, and
# the identical claim in other words walked past: ``brailix.pipeline`` said the
# frontend subsystems each have a "single-callable public interface" while
# ``brailix.frontend``, of the same functions, said "orchestration entry
# points, not published API".
#
# Deliberately NOT here: bare "public API" and "public surface". Those name the
# compatibility promise itself rather than one entry point, and the facades,
# ``brailix/__init__``'s policy statement and a good deal of test prose use
# them correctly ("Not public API", "the public surface is ``Pipeline``'s
# ``translate_*`` methods"). A check that flagged those would be answered by
# rewording true sentences, which is how a guard stops being read. Chasing
# every synonym is the wrong direction anyway: the point is that internal
# modules have one vocabulary for this, and it is the one above.
_PUBLIC_WORDING = re.compile(
    r"public\s+(?:entry\s+point|interface|parse\s+(?:entry|function))",
    re.IGNORECASE,
)


def _public_wording_offenders(root: Path) -> list[str]:
    """``file:line: text`` for every line under ``root`` using the phrase.

    Read as text, not as docstrings: the drift lives in section banners
    (``# Public entry points``) as much as in prose, and a banner is what a
    reader skims to.
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).as_posix()
        if rel in _PUBLIC_WORDING_ALLOWED:
            continue
        source = path.read_text(encoding="utf-8")
        for n, line in enumerate(source.splitlines(), 1):
            if _PUBLIC_WORDING.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    return offenders


def test_no_internal_module_calls_its_entry_point_public() -> None:
    """An internal module names its entry point by *scope*, not by "public"."""
    import importlib.util

    spec = importlib.util.find_spec("brailix")
    assert spec is not None and spec.origin is not None
    offenders = _public_wording_offenders(Path(spec.origin).resolve().parent)

    assert not offenders, (
        "internal modules calling their entry point 'public' — everything "
        "outside the facades and the extension surface is internal, so say "
        "'subsystem entry point' / 'package entry point' instead:\n  "
        + "\n  ".join(offenders)
    )


def test_no_test_calls_an_internal_entry_point_public() -> None:
    """The same word, held to the same meaning in the suite that tests it.

    A test docstring is design documentation — often the *first* thing a
    contributor reads about a seam, since it says what the seam is for and what
    must stay true of it. So the drift the package check above pins came back in
    through the tests: the cross-vertical soft-failure contract opened with
    "math, music and graphics each expose one public parse entry", of which only
    ``parse_math_tree`` is published at all. Read as an invitation, that
    sentence argues for adding the other two to the facade "for symmetry" —
    exactly the change the compatibility promise is meant to make deliberate.
    """
    offenders = _public_wording_offenders(Path(__file__).resolve().parent)

    assert not offenders, (
        "tests calling an internal entry point 'public' — a test docstring is "
        "read as design documentation, so it has to use the same vocabulary "
        "the package does ('subsystem entry point' / 'parse entry point'):\n  "
        + "\n  ".join(offenders)
    )


def test_the_public_wording_allowlist_is_still_accurate() -> None:
    """Each allowlisted file is allowed because the names it calls public are
    really re-exported by a facade. If one stops being published, the entry
    becomes a licence to mislabel — so the reason is checked, not just stated.
    """
    import brailix
    import brailix.core

    published = set(brailix.__all__) | set(brailix.core.__all__)
    import brailix.input

    published |= set(brailix.input.__all__)
    for name in ("parse_markdown", "parse_docx", "parse_doc"):
        assert name in published, (
            f"{name} is no longer on the supported surface, so the file that "
            f"defines it must stop calling it a public entry point "
            f"(_PUBLIC_WORDING_ALLOWED)"
        )


class TestTheWordingDetector:
    """The scan runs on real files, so a clean tree and a pattern that stopped
    matching produce the same green. These pin what it looks for."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "its own public entry point",
            "# Public entry points",
            "a single-callable public interface",
            "math and music each expose one public parse function",
            "one public parse entry per subsystem",
        ],
    )
    def test_each_spelling_of_the_claim_is_caught(self, phrase: str) -> None:
        assert _PUBLIC_WORDING.search(phrase), phrase

    @pytest.mark.parametrize(
        "phrase",
        [
            "Not public API: callers go through Pipeline.translate_block",
            "Nothing here is public API",
            "The public surface is the translate_* / parse_* methods",
        ],
    )
    def test_the_promise_nouns_are_left_alone(self, phrase: str) -> None:
        """"public API" / "public surface" name the compatibility promise, not
        one entry point, and internal modules use them truthfully — usually to
        deny having one. Flagging those would be answered by rewording correct
        sentences."""
        assert not _PUBLIC_WORDING.search(phrase), phrase


class TestTheFacadeBindingDetector:
    """Each way a facade can bind a name, asserted caught — the check runs on
    real files, so a clean tree and a blind scan produce the same green."""

    def test_an_absolute_reexport_is_reported(self) -> None:
        assert _unpublished_bindings(
            "from brailix.core.span import Span", published=set()
        )

    def test_a_relative_reexport_is_reported(self) -> None:
        # The form that walked past: ``node.module`` is ``_internal``, which
        # starts with no package name at all.
        assert _unpublished_bindings("from ._internal import Span", published=set())

    def test_a_deeper_relative_reexport_is_reported(self) -> None:
        assert _unpublished_bindings("from ..core.span import Span", published=set())

    def test_a_published_name_is_accepted(self) -> None:
        assert (
            _unpublished_bindings(
                "from brailix.core.span import Span", published={"Span"}
            )
            == []
        )

    def test_an_underscore_alias_is_accepted(self) -> None:
        assert (
            _unpublished_bindings(
                "from ._internal import Span as _Span", published=set()
            )
            == []
        )

    def test_a_third_party_import_is_left_alone(self) -> None:
        assert _unpublished_bindings("from pathlib import Path", published=set()) == []


class TestTheLiteralAllDetector:
    """What the detector must catch, and what it must leave alone — otherwise
    the check above passes on a clean tree and on a blind scan alike."""

    def test_a_literal_tuple_is_accepted(self) -> None:
        assert _all_definition_defects('__all__ = ("Pipeline", "block_hash")') == []

    def test_a_literal_list_is_accepted(self) -> None:
        assert _all_definition_defects('__all__ = ["Pipeline"]') == []

    def test_an_annotated_literal_is_accepted(self) -> None:
        assert (
            _all_definition_defects('__all__: tuple[str, ...] = ("Pipeline",)') == []
        )

    def test_a_computed_list_is_reported(self) -> None:
        assert _all_definition_defects("__all__ = sorted(_NAMES)")

    def test_a_concatenation_is_reported(self) -> None:
        assert _all_definition_defects('__all__ = ["Pipeline"] + _EXTRA')

    def test_a_comprehension_is_reported(self) -> None:
        assert _all_definition_defects("__all__ = [n for n in _NAMES]")

    def test_an_in_place_extension_is_reported(self) -> None:
        assert _all_definition_defects('__all__ = ["Pipeline"]\n__all__ += _EXTRA\n')

    def test_an_append_is_reported(self) -> None:
        assert _all_definition_defects(
            '__all__ = ["Pipeline"]\n__all__.append("Sneaky")\n'
        )

    def test_an_extend_is_reported(self) -> None:
        assert _all_definition_defects('__all__ = ["Pipeline"]\n__all__.extend(x)\n')

    def test_an_unrelated_name_is_left_alone(self) -> None:
        assert _all_definition_defects("_NAMES = sorted(x)\n_NAMES.append('y')\n") == []


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


# A binding a module cannot avoid, whatever it publishes. ``annotations`` is
# what ``from __future__ import annotations`` leaves behind — the one import in
# Python that cannot be aliased — so a facade carries it no matter what.
_UNAVOIDABLE_BINDINGS = frozenset({"annotations"})


def _foreign_bindings(module: str) -> list[str]:
    """Non-underscore module-level names a facade holds that are NOT its API.

    Everything the two ``brailix``-scoped checks above skip: standard-library
    and third-party objects bound under their plain names. ``os``, ``Path``,
    ``Callable``, ``dataclass``, ``field`` and ``TYPE_CHECKING`` all resolved
    at a facade path, and ``dir()`` offered them beside the published names.

    Submodules are exempt under their own name, for the same reason the
    runtime check exempts them: importing ``brailix.input.markdown`` sets
    ``markdown`` on ``brailix.input`` whether anyone wanted it or not.
    """
    import types

    mod = importlib.import_module(module)
    published = set(getattr(mod, "__all__", ()))
    allowed = _NAMESPACE_ALLOWLIST.get(module, set())

    out: list[str] = []
    for name, value in vars(mod).items():
        if (
            name.startswith("_")
            or name in published
            or name in allowed
            or name in _UNAVOIDABLE_BINDINGS
        ):
            continue
        if isinstance(value, types.ModuleType):
            if value.__name__.rsplit(".", 1)[-1] == name:
                continue  # a submodule under its own name
        owner = getattr(value, "__module__", None) or getattr(
            type(value), "__module__", ""
        )
        out.append(f"{name} (from {owner})")
    return out


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_a_facade_namespace_holds_no_foreign_plain_binding(
    module: str,
) -> None:
    """A facade's namespace is a promise as much as its ``__all__`` is, and
    the promise is about **everything** that resolves there.

    The two checks above ask only about ``brailix``-owned names, on the
    reasoning that nobody mistakes ``pathlib.Path`` for our API. True of
    ``Path`` in isolation; not true of the namespace as a whole. ``from
    brailix import TYPE_CHECKING`` worked, and the root package's own
    ``__dir__`` listed it among the published names, so completion offered it
    in the same breath as ``Pipeline`` — in a package whose docstring says a
    name which merely *resolves* at a facade is what this suite exists to
    prevent. ``brailix.input`` carried ``os``, ``Path``, ``Callable``,
    ``dataclass`` and ``field`` the same way, right beside the brailix names
    it had carefully aliased.

    This is the **runtime** half of the rule, and it runs on facades because
    they are the namespaces worth checking by import rather than by reading:
    a facade is assembled from re-exports, so a name can arrive there in ways
    no import statement shows (an alias assignment, a submodule import as a
    side effect). Every module in the package — facade or not — is held to the
    same rule at the source level by
    :func:`test_no_module_binds_a_foreign_name_under_a_plain_name`.

    The fix is the one the library uses everywhere: bind it as
    ``import x as _x``, or, if the name is only ever written in an annotation,
    move the import under ``if _TYPE_CHECKING:``.
    """
    leaked = _foreign_bindings(module)
    assert not leaked, (
        f"{module} holds non-brailix names under plain bindings: {leaked}\n"
        f"They resolve at a published address and appear in dir(), which is "
        f"the whole of what 'published' means to a caller. Import them as "
        f"``import x as _x`` / ``from y import x as _x``, or record a "
        f"documented exception in _NAMESPACE_ALLOWLIST."
    )


# ---------------------------------------------------------------------------
# The same rule, for every module in the package
# ---------------------------------------------------------------------------

# Documented exceptions to the tree-wide rule below, ``module path: {names}``.
# Empty, and that is the point: an entry here is a name a third party can
# import from a brailix module and be handed somebody else's object, so it
# should cost an argument in review rather than a moment's convenience.
_FOREIGN_BINDING_EXCEPTIONS: dict[str, set[str]] = {}


def _plain_foreign_imports(source: str) -> list[str]:
    """Module-level imports that bind a **non-brailix** name plainly.

    Read from the source rather than from ``vars(module)`` for two reasons.
    An adapter module whose optional extra is not installed cannot be
    imported at all, and those are exactly the modules a contributor adds
    without the extra in their environment. And a constant has no
    ``__module__`` to trace — ``INVERT_LEVELS`` (a ``bytes``) and
    ``MUSIC_SUFFIXES`` (a ``frozenset``) are brailix's own, but a runtime
    check sees ``builtins`` and would report the package's own names as
    foreign. The import statement says where a name came from; the object
    often cannot.
    """
    tree = ast.parse(source)
    published: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            published = {
                el.value
                for el in ast.walk(node.value)
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }

    offenders: list[str] = []
    for node in tree.body:  # module level only: an ``if TYPE_CHECKING:`` body
        # binds nothing at runtime, which is the other half of the fix
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # relative: a brailix module by construction
            if node.module.split(".")[0] in ("brailix", "__future__"):
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                if not bound.startswith("_") and bound not in published:
                    offenders.append(f"{bound} (from {node.module})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "brailix":
                    continue
                bound = alias.asname or alias.name.split(".")[0]
                if not bound.startswith("_") and bound not in published:
                    offenders.append(f"{bound} (import {alias.name})")
    return offenders


class TestTheForeignImportDetector:
    """The scan runs on real files, so a clean tree and a detector that stopped
    detecting look the same. These pin each shape it must judge."""

    def test_a_plain_stdlib_import_is_reported(self) -> None:
        assert _plain_foreign_imports("import os")

    def test_an_aliased_one_is_accepted(self) -> None:
        assert _plain_foreign_imports("import os as _os") == []

    def test_a_plain_from_import_is_reported(self) -> None:
        assert _plain_foreign_imports("from pathlib import Path")

    def test_a_dotted_import_reports_the_name_it_actually_binds(self) -> None:
        # ``import xml.etree.ElementTree`` binds ``xml``, not ``ElementTree``.
        assert _plain_foreign_imports("import xml.etree.ElementTree") == [
            "xml (import xml.etree.ElementTree)"
        ]

    def test_a_brailix_import_is_a_different_rules_business(self) -> None:
        # Covered by the facade / pipeline checks above, which ask whether the
        # name is *published* somewhere rather than whether it is ours.
        assert _plain_foreign_imports("from brailix.core.span import Span") == []
        assert _plain_foreign_imports("from ._helpers import block_hash") == []

    def test_the_future_import_is_exempt(self) -> None:
        assert _plain_foreign_imports("from __future__ import annotations") == []

    def test_an_import_under_type_checking_binds_nothing(self) -> None:
        assert (
            _plain_foreign_imports("if _TYPE_CHECKING:\n    from typing import Any\n")
            == []
        )

    def test_a_function_level_import_is_not_a_module_binding(self) -> None:
        assert _plain_foreign_imports("def f():\n    import os\n") == []

    def test_a_published_name_is_accepted(self) -> None:
        assert (
            _plain_foreign_imports('__all__ = ("Path",)\nfrom pathlib import Path\n')
            == []
        )


def _package_sources() -> list[tuple[str, Path]]:
    spec = importlib.util.find_spec("brailix")
    assert spec is not None and spec.origin is not None
    root = Path(spec.origin).resolve().parent
    return [
        (p.relative_to(root.parent).as_posix(), p) for p in sorted(root.rglob("*.py"))
    ]


@pytest.mark.parametrize(
    ("rel", "path"), _package_sources(), ids=[r for r, _ in _package_sources()]
)
def test_no_module_binds_a_foreign_name_under_a_plain_name(
    rel: str, path: Path
) -> None:
    """No module in the package offers a name that is not the package's.

    The rule used to hold for the seven facades only, on the reasoning that a
    facade is the address the documentation sends people to while an
    implementation module is not. True of where people are *sent*; not true of
    where they *arrive*. "Go to definition" on ``Pipeline`` lands in
    ``brailix.pipeline``, a traceback names the module that raised, and an
    editor completes whatever resolves at either. ``from brailix.pipeline
    import Path`` worked, and nothing about that address said it was not ours
    to promise.

    So the default is inverted: **every** module is checked, and an exemption
    has to be written down (:data:`_FOREIGN_BINDING_EXCEPTIONS`). The cost is
    an underscore on imports the module uses at runtime; annotations pay
    nothing, because the package is ``from __future__ import annotations``
    throughout and a type-only import belongs under ``if _TYPE_CHECKING:``
    where it never becomes a binding at all.

    Two families deliberately stay *bound* (aliased, not moved), because
    something resolves those annotations at runtime and a name that is not
    there resolves to nothing:

    * ``ClassVar`` / ``InitVar`` / ``KW_ONLY`` anywhere — :mod:`dataclasses`
      matches a string annotation by looking the identifier up in the defining
      module's globals, so moving ``ClassVar`` under ``TYPE_CHECKING`` turns a
      class variable into a **field** silently, with no error anywhere;
    * everything in ``brailix/ir/`` — its wire-type checking resolves each
      dataclass's annotations with :func:`typing.get_type_hints`
      (:mod:`brailix.ir._serde`), which evaluates them against the module
      namespace. ``tests/ir/test_wire_types.py`` is the check that would catch
      a regression here.

    This is the *source* half of the rule; facades additionally get the
    runtime half above, which sees names an import statement cannot show.
    """
    offenders = [
        name
        for name in _plain_foreign_imports(path.read_text(encoding="utf-8"))
        if name.split(" ")[0] not in _FOREIGN_BINDING_EXCEPTIONS.get(rel, set())
    ]
    assert not offenders, (
        f"{rel} binds names from outside brailix under plain names: "
        f"{offenders}\nThey resolve at that module's address and appear in "
        f"dir(), which is the whole of what 'published' means to a caller. "
        f"Import them as ``import x as _x`` / ``from y import x as _x``, or — "
        f"if the name only ever appears in an annotation — move the import "
        f"under ``if _TYPE_CHECKING:`` (but NOT for ClassVar / InitVar, nor "
        f"anywhere in brailix/ir/; see this test's docstring)."
    )


@pytest.mark.parametrize("module", sorted(_EXTENSION_SURFACE))
def test_an_extension_module_still_resolves_everything_it_promises(
    module: str,
) -> None:
    """The narrower promise the extension surface actually makes.

    Its namespace is not claimed to be exhaustive (see the facade check above
    for why), so what is left to check is the half that IS promised: every
    manifest name resolves at that address, under that spelling, without a
    leading underscore — and is re-exported deliberately rather than by
    accident of a submodule import.
    """
    mod = importlib.import_module(module)
    missing = [
        name for name in _EXTENSION_SURFACE[module] if not hasattr(mod, name)
    ]
    assert not missing, (
        f"{module} no longer resolves promised extension names: {missing}"
    )


def test_a_moved_extension_path_is_gone_rather_than_aliased() -> None:
    """A registry that moves moves — it leaves no second address behind.

    ``brailix.frontend.segment`` / ``.normalize`` were renamed to end a name
    collision with the facade's own :func:`~brailix.frontend.segment` function,
    and for a while the old addresses stayed resolvable through
    :data:`sys.modules` shims that warned on read. They are deleted: an alias
    kept "for one release" is a second published address for a registry with
    state, a second thing every check here has to reason about, and a promise
    that has to be un-made later anyway. The manifest above is the whole
    extension surface, and these paths are not on it.

    Asserted rather than assumed, because a shim is exactly the kind of thing
    that comes back: a ``segment.py`` file added under ``frontend/`` would
    resolve the old import *and*, as a side effect of loading, bind itself onto
    the package under the name the published function holds — re-arming the
    collision the rename existed to end, for every caller in the process. So
    both halves are checked: the old address does not resolve, and the facade
    name is still the function.
    """
    import brailix.frontend as facade

    for old in ("brailix.frontend.segment", "brailix.frontend.normalize"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(old)

    assert callable(facade.segment) and not isinstance(
        facade.segment, types.ModuleType
    )
    assert callable(facade.normalize) and not isinstance(
        facade.normalize, types.ModuleType
    )


def _annotated_public_objects() -> list[tuple[str, object]]:
    """Every published class / function, plus each class's public methods.

    Addressed by where it is *published* rather than where it is defined,
    because that is what the manifest promises and what a caller writes down.
    Re-exports are visited once — the object is the thing being checked, and a
    second address for it would only repeat the same evaluation.
    """
    seen: set[int] = set()
    out: list[tuple[str, object]] = []
    published = {**_FACADE, **_EXTENSION_SURFACE}
    for address, names in sorted(published.items()):
        module = importlib.import_module(address)
        for name in names:
            obj = getattr(module, name)
            candidates = [(f"{address}.{name}", obj)]
            if isinstance(obj, type):
                for attr, member in sorted(vars(obj).items()):
                    if attr.startswith("_"):
                        continue
                    if isinstance(member, (staticmethod, classmethod)):
                        member = member.__func__
                    elif isinstance(member, property):
                        member = member.fget
                    candidates.append((f"{address}.{name}.{attr}", member))
            for at, target in candidates:
                if not isinstance(target, type) and not inspect.isfunction(
                    target
                ):
                    continue
                if id(target) in seen:
                    continue
                seen.add(id(target))
                out.append((at, target))
    return out


_ANNOTATED_PUBLIC = _annotated_public_objects()

# The one registered exemption to the rule below, and what it is allowed to
# cover: modules that annotate against a package they must not *import* at
# runtime. Written as "which package the name comes from", not as a list of
# names, so it cannot quietly grow to cover an unrelated one — the check reads
# the module's own ``if TYPE_CHECKING:`` block to turn this into the set of
# names it excuses (:func:`_deferred_by_layering`).
_LAYERING_DEFERRED: dict[str, tuple[str, ...]] = {
    # ``brailix.core`` may annotate against the IR but must never import it at
    # runtime (``test_core_layering.test_core_does_not_import_ir_at_runtime``),
    # and ``brailix.core.context`` imports this module at runtime for its own
    # accessor annotations, so binding it back would close that cycle.
    "brailix.core.protocols": ("brailix.ir", "brailix.core.context"),
}


def _deferred_by_layering(module: str) -> frozenset[str]:
    """The names ``module`` is excused from resolving, read from its source."""
    packages = _LAYERING_DEFERRED.get(module, ())
    if not packages:
        return frozenset()
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        for stmt in ast.walk(node)
        if isinstance(stmt, ast.ImportFrom)
        and any((stmt.module or "").startswith(p) for p in packages)
        for alias in stmt.names
    )


def test_the_layering_exemption_is_the_cycle_it_claims_to_be() -> None:
    """Each excused package is one the excused module genuinely cannot bind.

    An exemption nobody re-derives is a comment. ``brailix.ir`` is refused to
    ``brailix.core`` by :mod:`tests.test_core_layering`; the other half is
    checked here, by looking for the runtime edge that makes the reverse one a
    cycle. If ``brailix.core.context`` ever stops importing
    ``brailix.core.protocols`` at runtime, the exemption stops being true and
    this fails rather than quietly covering a fixable annotation.
    """
    for module, packages in _LAYERING_DEFERRED.items():
        for package in packages:
            if package.startswith("brailix.ir"):
                continue  # owned by tests/test_core_layering.py
            spec = importlib.util.find_spec(package)
            assert spec is not None and spec.origin is not None
            tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
            imports_it = any(
                (node.module or "") == module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and not _under_type_checking(tree, node)
            )
            assert imports_it, (
                f"{module} defers names from {package} to avoid a cycle, but "
                f"{package} no longer imports {module} at runtime — there is "
                f"no cycle left to avoid, so bind them and drop the entry."
            )


def _under_type_checking(tree: ast.Module, target: ast.stmt) -> bool:
    """Whether ``target`` sits inside an ``if TYPE_CHECKING:`` block."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and any(
            stmt is target for stmt in ast.walk(node)
        ):
            return True
    return False


@pytest.mark.parametrize(
    ("address", "obj"), _ANNOTATED_PUBLIC, ids=[a for a, _ in _ANNOTATED_PUBLIC]
)
def test_a_published_objects_annotations_resolve_at_runtime(
    address: str, obj: object
) -> None:
    """Every published signature can be read back by
    :func:`typing.get_type_hints`.

    An annotation is not only for a type checker. Dependency-injection
    frameworks, schema and documentation generators, plugin registries and
    ``inspect.signature(..., eval_str=True)`` all evaluate them at runtime, and
    they evaluate them **against the defining module's globals** — so a name
    that only exists under ``if TYPE_CHECKING:`` is not deferred, it is absent,
    and the call raises ``NameError`` on a class the manifest calls public.

    This is the other half of
    :func:`test_no_module_binds_a_foreign_name_under_a_plain_name`, and the two
    are satisfied together by one habit: import the foreign name **aliased**
    (``from collections.abc import Mapping as _Mapping``) and write the alias
    in the annotation. Nothing lands in ``dir()`` under a name that is not
    ours, and everything still resolves. Reaching for ``TYPE_CHECKING`` to
    satisfy the other test is what broke 29 published objects at once —
    ``Pipeline`` on a missing ``Mapping``, ``translate_graphic`` on a missing
    ``TactileProfile``, ``merge_spans`` on a missing ``Iterable``.

    The one exemption is :data:`_LAYERING_DEFERRED`: a module that annotates
    against a package it is forbidden to import at runtime cannot bind the
    name, and the layer rule outranks introspection. It is registered, its
    extent is derived from the module's own source, and
    :func:`test_the_layering_exemption_is_the_cycle_it_claims_to_be` re-derives
    the reason.
    """
    module = getattr(obj, "__module__", "?")
    try:
        typing.get_type_hints(obj)
    except NameError as e:
        excused = _deferred_by_layering(module)
        if e.name in excused:
            return
        pytest.fail(
            f"{address} has an annotation that does not resolve at runtime: "
            f"{e}\nThe name is missing from {module}'s globals — most likely "
            f"imported under ``if TYPE_CHECKING:``. Import it aliased "
            f"(``... as _Name``) and spell the alias in the annotation, which "
            f"keeps it out of that module's public namespace too. If the "
            f"import is genuinely forbidden at runtime (a layer boundary), "
            f"register the package in _LAYERING_DEFERRED with its reason."
        )
