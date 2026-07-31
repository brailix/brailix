"""Figure-generator source adapter: a figure spec → SVG.

Reads a JSON figure spec, dispatches on its ``"kind"`` field to the
matching generator (:mod:`brailix.frontend.graphics.generate`), and
renders the resulting primitives spec to SVG via
:func:`~brailix.frontend.graphics.adapters.primitives.primitives_to_svg`.
This is the source-format face of the parametric generators — so
``Pipeline(profile="cn_current").translate_graphic(spec, source_format="figure",
braille_profile="cn_current").render("bmp")`` produces a fully-labelled tactile
chart end to end.

An unknown / missing ``kind`` soft-fails into an empty ``<svg>`` with a
``GRAPHICS_UNKNOWN_FIGURE`` warning (when a context is supplied), and a spec
the chosen generator cannot draw does the same with ``GRAPHICS_INVALID_SPEC``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from brailix.core.context import GraphicsContext
from brailix.core.errors import WarningCollector
from brailix.frontend.graphics._numbers import non_finite_paths
from brailix.frontend.graphics.adapters.primitives import (
    primitives_to_svg,
    svg_error_wrap,
)
from brailix.frontend.graphics.generate import (
    FigureSpecError,
    generator_kinds,
    get_generator,
)


@dataclass(slots=True)
class FigureSourceAdapter:
    """Adapter: a JSON figure spec (``{"kind": ..., ...}``) → SVG."""

    source: str = "figure"

    def to_svg(
        self, src: str | bytes, ctx: GraphicsContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return svg_error_wrap(repr(src), reason="non-utf8 bytes")
        text = src.strip() if isinstance(src, str) else ""
        if not text:
            return svg_error_wrap("", reason="empty figure spec")
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            return svg_error_wrap(text, reason=f"invalid JSON: {e}")
        if not isinstance(spec, dict):
            return svg_error_wrap(
                type(spec).__name__, reason="figure spec must be an object"
            )
        kind = spec.get("kind")
        generator = get_generator(kind) if isinstance(kind, str) else None
        if generator is None:
            if ctx is not None:
                ctx.warnings.warn(
                    code="GRAPHICS_UNKNOWN_FIGURE",
                    message=f"unknown figure kind {kind!r}; "
                    f"known kinds: {list(generator_kinds())}",
                    source="frontend.graphics",
                )
            return svg_error_wrap("", reason=f"unknown figure kind {kind!r}")
        warnings = ctx.warnings if ctx is not None else None
        # Two ways a spec with a known kind still describes nothing drawable,
        # and both are the author's to fix rather than the generator's to
        # muddle through. They are checked here, at the source boundary, for
        # the reason every other check in this method is: below it the spec
        # has become geometry, and a diagnostic phrased in coordinates cannot
        # be traced back to the field that was wrong.
        bad = non_finite_paths(spec)
        if bad:
            return self._invalid(
                warnings,
                "figure spec carries a value that is not a number: "
                + ", ".join(bad),
            )
        try:
            primitives = generator(spec)
        except FigureSpecError as e:
            return self._invalid(warnings, str(e))
        return primitives_to_svg(primitives, warnings)

    @staticmethod
    def _invalid(warnings: WarningCollector | None, reason: str) -> str:
        if warnings is not None:
            warnings.warn(
                code="GRAPHICS_INVALID_SPEC",
                message=reason,
                source="frontend.graphics",
            )
        return svg_error_wrap("", reason=reason)


def _load() -> FigureSourceAdapter:
    return FigureSourceAdapter()
