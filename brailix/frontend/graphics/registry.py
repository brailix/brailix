"""Registry for graphics source-format adapters.

Adapters convert a graphic from a specific source format (raw SVG,
geometry primitives, a raster image, a chart spec, ...) into a normalized
SVG string. The tactile backend then walks the SVG element tree directly
— there is no separate IR-builder layer. Adding a new source format means
adding exactly one adapter; the backend doesn't change.

Optional adapters that need a third-party library (raster-image import,
full external-SVG rasterization) register with ``extra=`` so a missing
import becomes a friendly :class:`~brailix.core.errors.MissingExtraError`.
"""

from __future__ import annotations

from brailix.core.protocols import GraphicSourceAdapter
from brailix.core.registry import Registry

graphic_source_registry: Registry[GraphicSourceAdapter] = Registry(
    "graphics.source", protocol=GraphicSourceAdapter
)


def _register_builtin() -> None:
    from brailix.frontend.graphics.adapters import (  # noqa: F401
        figure,
        image,
        primitives,
        svg,
    )

    graphic_source_registry.register("svg", svg._load)
    graphic_source_registry.register("primitives", primitives._load)
    graphic_source_registry.register("figure", figure._load)
    # Raster import needs Pillow to read the image; ``extra=`` turns a
    # missing install into a friendly MissingExtraError at first use.
    graphic_source_registry.register("image", image._load, extra="graphics")


_register_builtin()
