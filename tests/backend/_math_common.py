"""Shared helpers for the math backend test modules (``test_math_*.py``).

The backend walks the MathML :class:`ET.Element` tree directly — there is
no separate IR layer. Each test constructs a small MathML fragment via
:func:`mml`, runs the backend through :func:`emit`, and inspects the
resulting :class:`BrailleCell` list / :class:`WarningCollector`.

The module-scoped ``profile`` fixture these helpers pair with lives in
``tests/backend/conftest.py`` so it is auto-discovered without an import.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math import emit_tree, translate
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.frontend.math.normalizer import normalize
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import MathInline


def mml(xml: str) -> ET.Element:
    """Parse a MathML fragment via the normalizer.

    Strips the MathML namespace, collapses singleton mrows, and trims
    whitespace — same path the math frontend takes for end-to-end input.
    """
    return normalize(xml)


def emit(
    tree: ET.Element, profile, mode: RunMode | None = None
) -> tuple[list[BrailleCell], WarningCollector]:
    """Run the backend's ``translate()`` on a fake MathInline carrying ``tree``.

    Returns (cells, warning_collector) so each test can assert on both
    output cells and diagnostic side effects.
    """
    wc = WarningCollector(mode=mode or RunMode.NORMAL)
    ctx = BackendContext(profile="cn_current", warnings=wc)
    node = MathInline(surface="", source="mathml", span=None, math=tree)
    cells = translate(node, ctx, profile)
    return cells, wc


def emit_via_tree(tree: ET.Element, profile) -> tuple[list[BrailleCell], WarningCollector]:
    """Same as :func:`emit` but exercises :func:`emit_tree` directly."""
    wc = WarningCollector(mode=RunMode.NORMAL)
    ctx = BackendContext(profile="cn_current", warnings=wc)
    cells = emit_tree(tree, ctx, profile)
    return cells, wc


def roles(cells):
    return [c.role for c in cells]


def dots(cells):
    return [c.dots for c in cells]
