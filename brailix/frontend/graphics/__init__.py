"""Tactile-graphics frontend — one public entry point: :func:`parse_graphic_tree`.

SVG is the graphics vertical's normalized intermediate format — the
direct analogue of MathML for math and MusicXML for music. A source
adapter turns one source format (raw SVG, geometry primitives, a raster
image, a chart spec, ...) into an SVG string; the :mod:`.normalizer`
parses and tidies it into an :class:`xml.etree.ElementTree.Element` tree,
and **that tree is the IR** — there is no separate vector model. The
tactile backend (:mod:`brailix.backend.tactile`) then walks the tree by
element tag, exactly as the math / music backends walk MathML / MusicXML.

Adapters self-register in the sibling :mod:`.registry` module so the
registry stays populated on a bare install. See
``ARCHITECTURE.md`` for the full data flow.

Callers only need :func:`parse_graphic_tree` — the graphics counterpart
of :func:`brailix.frontend.math.parse_math_tree` and
:func:`brailix.frontend.music.parse_music_tree`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core.context import GraphicsContext
from brailix.core.errors import MissingExtraError
from brailix.frontend.graphics.normalizer import normalize


def parse_graphic_tree(
    src: str | bytes, ctx: GraphicsContext
) -> ET.Element:
    """Convert one graphic source to a normalised SVG :class:`ET.Element`
    tree.

    Steps: pick the source adapter from ``ctx.source`` → produce an SVG
    string → run the normalizer (strip namespace / whitespace, assign
    ``data-bk-gid`` element ids) → return the tree rooted at ``<svg>``.

    Unlike its math / music counterparts this never returns ``None`` —
    the graphics vertical's soft-failure contract is "always a tree":
    a missing adapter (unknown source name, or an optional dependency
    such as Pillow not installed) records a ``GRAPHICS_ADAPTER_MISSING``
    warning and degrades to an empty ``<svg data-bk-error="...">``,
    which the tactile backend rasterises to a blank page plus a
    ``GRAPHICS_SOFT_FAIL`` diagnostic; the pipeline keeps running.

    Soft-failure backstop: an adapter that raises anyway — the registry
    is open to third-party adapters — degrades to the same error-marked
    tree instead of crashing the caller.
    """
    from brailix.frontend.graphics.adapters.svg import svg_error_wrap
    from brailix.frontend.graphics.registry import graphic_source_registry

    surface = src if isinstance(src, str) else repr(src)
    try:
        adapter = graphic_source_registry.get(ctx.source)
    except MissingExtraError as e:
        ctx.warnings.warn(
            code="GRAPHICS_ADAPTER_MISSING",
            message=str(e),
            source="frontend.graphics",
        )
        return normalize(svg_error_wrap(surface[:200], reason=str(e)))
    except KeyError as e:
        ctx.warnings.warn(
            code="GRAPHICS_ADAPTER_MISSING",
            message=str(e),
            surface=src if isinstance(src, str) else None,
            candidates=tuple(graphic_source_registry.names()),
            source="frontend.graphics",
        )
        return normalize(svg_error_wrap(surface[:200], reason=str(e)))

    try:
        return normalize(adapter.to_svg(src, ctx))
    except Exception as e:  # noqa: BLE001 — pipeline must never crash
        # Adapters promise soft failure (an error-marked SVG) and the
        # normalizer promises never to raise, but the registry is
        # deliberately open, so a third-party adapter that raises still
        # degrades to the standard error tree — the backend surfaces it
        # as GRAPHICS_SOFT_FAIL and translation continues.
        return normalize(
            svg_error_wrap(surface[:200], reason=f"adapter failure: {e!r}")
        )


__all__ = ("parse_graphic_tree",)
