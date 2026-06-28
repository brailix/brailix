"""Renderer layer: BrailleIR → final output.

Renderers do not understand Chinese, math, or any source language —
they only convert :class:`~brailix.ir.braille.BrailleCell` instances
into a target encoding (Unicode braille, BRF, cells, layout,
...).

Selection happens by name through :data:`renderer_registry`. Each
renderer module self-registers via a loader so the registry stays
populated even on a bare install:

    from brailix.renderer import renderer_registry

    out = renderer_registry.get("unicode").render(braille_doc)

Adding a new renderer means writing one module under
``brailix/renderer/`` with a class that satisfies the
:class:`~brailix.core.protocols.Renderer` protocol, and calling
``renderer_registry.register(name, loader)``.
"""

from __future__ import annotations

from brailix.core.protocols import Renderer
from brailix.core.registry import Registry

renderer_registry: Registry[Renderer] = Registry("renderer", protocol=Renderer)


def _register_builtin() -> None:
    # Imported lazily inside the function so module import order stays
    # simple — each module just defines its class, and the registry is
    # created before any registration runs.
    from brailix.renderer import brf, cells, layout, unicode_braille

    renderer_registry.register("unicode", unicode_braille._load)
    renderer_registry.register("brf", brf._load)
    renderer_registry.register("cells", cells._load)
    renderer_registry.register("layout", layout._load)


_register_builtin()


# Stable public surface — re-export so callers import from
# ``brailix.renderer`` rather than the concrete renderer modules.
from brailix.renderer.layout import LayoutOptions, LayoutRenderer  # noqa: E402
from brailix.renderer.unicode_braille import cell_to_char  # noqa: E402

__all__ = (
    "renderer_registry",
    "LayoutOptions",
    "LayoutRenderer",
    "cell_to_char",
)
