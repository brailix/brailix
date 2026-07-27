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
"""

from __future__ import annotations

import importlib

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
    ],
    "brailix.core": [
        "Span",
        "merge_spans",
        "BackendContext",
        "FrontendContext",
        "MathContext",
        "MusicContext",
        "DEFAULT_NORMALIZER",
        "DEFAULT_PINYIN_RESOLVER",
        "DEFAULT_RENDERER",
        "DEFAULT_SEGMENTER",
        "DEFAULT_ZH_ANALYZER",
        "BrailixError",
        "ConfigurationError",
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
        "apply_boundary",
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
