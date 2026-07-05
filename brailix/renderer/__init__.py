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
    #
    # The braille renderers (which consume a braille IR) and the
    # tactile-graphics renderers (which consume a ``TactileRaster``) share
    # this one registry: a tactile renderer is just another file satisfying
    # the single :class:`~brailix.core.protocols.Renderer` protocol, selected
    # by name — there is no parallel registry (``ARCHITECTURE.md``
    # §3.1). Each result type passes its own IR to the renderer it names
    # (a braille :class:`~brailix.pipeline.TranslationResult` to a braille
    # renderer; a :class:`~brailix.pipeline.GraphicResult` to a tactile one).
    from brailix.renderer import (
        bmp,
        brf,
        cells,
        layout,
        pdf,
        png,
        tactile_preview,
        unicode_braille,
    )

    renderer_registry.register("unicode", unicode_braille._load)
    renderer_registry.register("brf", brf._load)
    renderer_registry.register("cells", cells._load)
    renderer_registry.register("layout", layout._load)
    renderer_registry.register("bmp", bmp._load)
    renderer_registry.register("png", png._load)
    renderer_registry.register("pdf", pdf._load)
    renderer_registry.register("tactile_preview", tactile_preview._load)


_register_builtin()


def braille_renderer_names() -> list[str]:
    """Names of renderers that consume a braille IR — the subset a
    braille-only front-end (the CLI's ``--to`` / ``--list-renderers``) offers.

    A renderer self-describes via a ``consumes`` attribute (``"braille"`` by
    default; the tactile-graphics renderers set ``"tactile_raster"``), so the
    tactile renderers (``bmp`` / ``png`` / ``pdf`` / ``tactile_preview``) — reached via
    :meth:`~brailix.pipeline.GraphicResult.render`, not text translation — are
    excluded here. A renderer whose loader fails (a missing optional
    dependency) is skipped: it isn't selectable anyway. The builtin renderers
    are pure-stdlib, so this stays cheap."""
    from brailix.core.errors import BrailixError

    names: list[str] = []
    for name in renderer_registry.names():
        try:
            renderer = renderer_registry.get(name)
        except BrailixError:
            continue
        if getattr(renderer, "consumes", "braille") == "braille":
            names.append(name)
    return names


# Stable public surface — re-export so callers import from
# ``brailix.renderer`` rather than the concrete renderer modules.
from brailix.renderer.layout import LayoutOptions, LayoutRenderer  # noqa: E402
from brailix.renderer.unicode_braille import cell_to_char  # noqa: E402

__all__ = (
    "renderer_registry",
    "braille_renderer_names",
    "LayoutOptions",
    "LayoutRenderer",
    "cell_to_char",
)
