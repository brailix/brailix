"""The stable public import surface of ``brailix``.

The library exposes a shallow facade — the top-level package plus the
``brailix.ir`` / ``brailix.core`` / ``brailix.core.models`` /
``brailix.renderer`` sub-packages — so downstream callers (a
proofreading front-end, CLI front-ends, ...) import from there rather than
reaching into concrete internal modules (``brailix.ir.inline``,
``brailix.core.span``, ...).  Pinning that surface here means a refactor
that drops or renames a re-export fails loudly instead of silently
breaking every downstream import site.
"""

from __future__ import annotations

import importlib

import pytest

_FACADE: dict[str, list[str]] = {
    "brailix": [
        "Pipeline",
        "TranslationResult",
        "CompiledBlock",
        "TreeSubcache",
        "block_hash",
    ],
    "brailix.ir": [
        "Block",
        "DocumentIR",
        "Heading",
        "Paragraph",
        "List",
        "Table",
        "MathBlock",
        "ScoreBlock",
        "InlineNode",
        "Word",
        "HanziChar",
        "MathInline",
        "MusicInline",
        "Space",
        "Number",
        "Punct",
        "PhoneticInline",
        "BrailleCell",
        "BrailleDocument",
        "BLANK_CELL",
    ],
    "brailix.core": [
        "Span",
        "merge_spans",
        "Warning",
        "WarningCollector",
        "BrailixError",
        "FrontendContext",
        "BackendContext",
        "MathContext",
        "MusicContext",
        "RunMode",
    ],
    "brailix.core.models": [
        "ModelAsset",
        "all_assets",
        "get_model_dir",
        "get_models_root",
        "set_managed_download",
        "is_managed_download",
    ],
    "brailix.renderer": [
        "renderer_registry",
        "LayoutOptions",
        "LayoutRenderer",
        "cell_to_char",
    ],
}


@pytest.mark.parametrize("module", sorted(_FACADE))
def test_facade_exposes_documented_names(module: str) -> None:
    mod = importlib.import_module(module)
    declared = set(getattr(mod, "__all__", ()))
    for name in _FACADE[module]:
        assert hasattr(mod, name), f"{module}.{name} missing from facade"
        assert name in declared, f"{name} not listed in {module}.__all__"


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

    The ``_FACADE`` list above is a representative subset, so a newly added
    node (this is how ``PhoneticInline`` slipped through) could be registered
    and serialisable yet never re-exported — leaving downstream front-ends /
    plugins that follow the documented "import from ``brailix.ir``" rule
    unable to consume it. This guards the whole registry, not a hand-list.
    """
    import brailix.ir as ir
    from brailix.ir.inline import _INLINE_REGISTRY

    missing = sorted(
        cls.__name__
        for cls in _INLINE_REGISTRY.values()
        if not hasattr(ir, cls.__name__) or cls.__name__ not in ir.__all__
    )
    assert not missing, f"inline node types missing from brailix.ir: {missing}"
