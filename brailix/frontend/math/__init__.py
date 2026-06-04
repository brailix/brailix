"""Math frontend subsystem — one public entry point: :func:`parse_math_tree`.

Source adapters (``latex`` / ``mathml`` / ``asciimath`` / ...) live in
``adapters/`` and are picked from an internal registry based on
:class:`~brailix.core.context.MathContext`. The MathML tree returned
by an adapter, after normalisation, is the math IR itself — there is no
separate IR-builder layer (see ``ARCHITECTURE.md``).

Callers only need :func:`parse_math_tree`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core.context import MathContext
from brailix.core.errors import MissingExtraError
from brailix.frontend.math.normalizer import normalize


def parse_math_tree(
    formula: str | bytes, ctx: MathContext
) -> ET.Element | None:
    """Convert a single math fragment to a normalised :class:`ET.Element`
    tree.

    Steps: pick the source adapter from ``ctx.source`` → produce a
    MathML string → run the normalizer (strip namespace / collapse
    singleton ``<mrow>`` / trim whitespace) → return the resulting
    :class:`ET.Element` rooted at ``<math>``.

    Returns ``None`` (and records a ``MATH_ADAPTER_MISSING`` warning
    via ``ctx.warnings``) when the requested source adapter is absent
    or its optional dependency isn't installed; the pipeline keeps
    running.
    """
    from brailix.frontend.math.registry import math_source_registry

    try:
        adapter = math_source_registry.get(ctx.source)
    except MissingExtraError as e:
        ctx.warnings.warn(
            code="MATH_ADAPTER_MISSING",
            message=str(e),
            source="frontend.math",
        )
        return None
    except KeyError as e:
        ctx.warnings.warn(
            code="MATH_ADAPTER_MISSING",
            message=str(e),
            surface=formula if isinstance(formula, str) else None,
            candidates=tuple(math_source_registry.names()),
            source="frontend.math",
        )
        return None

    mathml = adapter.to_mathml(formula, ctx)
    return normalize(mathml)


__all__ = ("parse_math_tree",)
